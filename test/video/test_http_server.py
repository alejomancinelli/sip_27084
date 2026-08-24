"""Tests del servidor HTTP de video: reducción de frame, gating por cliente
conectado, espera por número de secuencia, ciclo de vida y endpoints."""

import logging
import os
import socket
import struct
import sys
import threading
import time
from http.client import HTTPConnection, HTTPResponse

import cv2
import numpy as np
import pytest


import system.video.http_server as hvs
from system.video.http_server import (
    HttpVideoServer,
    _FrameStore,
    _Subscribers,
    _build_part_header,
    _downscale,
    _is_client_gone,
)

_LOOPBACK = "127.0.0.1"
_CLIENT_TIMEOUT_S = 2.0

_RAW_1 = ("camera_1", "raw")
_ANNOTATED_1 = ("camera_1", "annotated")
_RAW_2 = ("camera_2", "raw")
_ANNOTATED_2 = ("camera_2", "annotated")
_ALL_KEYS = (_RAW_1, _ANNOTATED_1, _RAW_2, _ANNOTATED_2)


class _MockConfig:
    """ConfigManager mínimo: `get` y `set` con clave punteada, sin archivo ni Lock."""

    # `video.http.log_connections` queda afuera a propósito: así los tests que no lo
    # fijan pasan por el default del módulo.
    def __init__(self, **overrides):
        self._values = {
            "video.http.enabled": True,
            "video.http.port": 0,          # 0 = puerto libre elegido por el SO
            "video.http.jpeg_quality": 80,
            "video.http.frame_width_px": 960,
            "cameras": {"camera_1": {}, "camera_2": {}},
        }
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self._values[key] = value


def _frame(width_px: int = 1920, height_px: int = 1200) -> np.ndarray:
    """Frame BGR con gradiente, para que el JPEG tenga un tamaño representativo."""
    frame_bgr = np.zeros((height_px, width_px, 3), np.uint8)
    frame_bgr[:, :, 1] = np.linspace(0, 255, width_px, dtype=np.uint8)[None, :]
    return frame_bgr


def _decoded_size(jpeg: bytes) -> tuple[int, int]:
    """(ancho, alto) del JPEG almacenado."""
    decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    return decoded.shape[1], decoded.shape[0]


def _watched_server(*keys: tuple[str, str], **overrides) -> HttpVideoServer:
    """Servidor sin socket, con un cliente simulado en cada stream de `keys`."""
    server = HttpVideoServer(_MockConfig(**overrides))
    server._is_active = True          # start() real abriría un puerto
    for index, key in enumerate(keys):
        server._subs.touch(key, index)
    return server


@pytest.fixture
def server(monkeypatch):
    """Servidor escuchando en loopback, en un puerto que elige el SO."""
    # En 0.0.0.0 Windows pide autorización del firewall para escuchar.
    monkeypatch.setattr(hvs, "_HOST", _LOOPBACK)
    listening_server = HttpVideoServer(_MockConfig())
    listening_server.start()
    yield listening_server
    listening_server.stop()


def _port_of(server: HttpVideoServer) -> int:
    return server._server.server_address[1]


def _get(server: HttpVideoServer, path: str) -> HTTPResponse:
    connection = HTTPConnection(_LOOPBACK, _port_of(server), timeout=_CLIENT_TIMEOUT_S)
    connection.request("GET", path)
    return connection.getresponse()


def _open_stream(server: HttpVideoServer, path: str) -> socket.socket:
    """Socket crudo con el request de stream enviado y las cabeceras ya en camino."""
    sock = socket.create_connection((_LOOPBACK, _port_of(server)), timeout=_CLIENT_TIMEOUT_S)
    sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {_LOOPBACK}\r\n\r\n".encode())
    return sock


def _reset(sock: socket.socket):
    """Corta con RST, como un navegador al que le cierran la pestaña."""
    # SO_LINGER en 0 fuerza el RST; un close() normal cerraría ordenado.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()


def _read_one_part(response: HTTPResponse) -> bytes:
    """Lee la primera parte multipart del stream y devuelve el JPEG."""
    assert response.readline().strip() == f"--{hvs._BOUNDARY}".encode()
    size = 0
    while True:
        line = response.readline().strip()
        if not line:
            break
        name, _, value = line.decode().partition(":")
        if name.lower() == "content-length":
            size = int(value)
    return response.read(size)


def _wait_until(condition, timeout_s: float = _CLIENT_TIMEOUT_S) -> bool:
    """Espera a que `condition()` sea verdadera. Evita sleeps fijos en los tests."""
    deadline_s = time.monotonic() + timeout_s
    while time.monotonic() < deadline_s:
        if condition():
            return True
        time.sleep(0.01)
    return False


class TestDownscale:
    def test_keeps_the_aspect_ratio(self):
        assert _downscale(_frame(1920, 1200), 960).shape[:2] == (600, 960)

    def test_width_zero_leaves_the_native_resolution(self):
        source = _frame(1920, 1200)
        assert _downscale(source, 0) is source

    def test_negative_width_leaves_the_native_resolution(self):
        source = _frame(1920, 1200)
        assert _downscale(source, -1) is source

    def test_never_upscales(self):
        source = _frame(640, 400)
        assert _downscale(source, 960) is source

    def test_the_native_width_is_not_copied(self):
        source = _frame(960, 600)
        assert _downscale(source, 960) is source

    def test_an_extreme_aspect_ratio_does_not_collapse_the_height(self):
        # Sin el max(1, ...) el alto redondearía a 0 y cv2.resize fallaría.
        assert _downscale(_frame(1920, 3), 100).shape[0] >= 1


class TestFrameStore:
    def test_store_starts_empty(self):
        assert _FrameStore().get(_RAW_1) == (None, 0)

    def test_update_stores_jpeg_bytes(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame())
        assert isinstance(store.get(_RAW_1)[0], bytes)

    def test_update_encodes_at_the_requested_width(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame(), frame_width_px=960)
        assert _decoded_size(store.get(_RAW_1)[0]) == (960, 600)

    def test_width_zero_encodes_the_native_resolution(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame(), frame_width_px=0)
        assert _decoded_size(store.get(_RAW_1)[0]) == (1920, 1200)

    def test_downscaling_reduces_the_jpeg_size(self):
        """El objetivo del reescalado: menos píxeles, menos bytes por la red."""
        store = _FrameStore()
        store.update(_RAW_1, _frame(), frame_width_px=0)
        native = len(store.get(_RAW_1)[0])
        store.update(_RAW_1, _frame(), frame_width_px=960)
        assert len(store.get(_RAW_1)[0]) < native

    def test_lower_quality_yields_fewer_bytes(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame(), jpeg_quality=95)
        high = len(store.get(_RAW_1)[0])
        store.update(_RAW_1, _frame(), jpeg_quality=20)
        assert len(store.get(_RAW_1)[0]) < high

    def test_each_stream_is_independent(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame())
        assert store.get(_RAW_1)[0] is not None
        for key in (_ANNOTATED_1, _RAW_2, _ANNOTATED_2):
            assert store.get(key)[0] is None

    def test_clear_discards_the_last_jpeg(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame())
        store.clear(_RAW_1)
        assert store.get(_RAW_1)[0] is None


class TestSequence:
    """La secuencia es lo que permite no reenviar un frame ya enviado."""

    def test_each_update_increments_the_sequence(self):
        store = _FrameStore()
        seqs = []
        for _ in range(3):
            store.update(_RAW_1, _frame())
            seqs.append(store.get(_RAW_1)[1])
        assert seqs == [1, 2, 3]

    def test_the_sequence_is_per_stream(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame())
        store.update(_RAW_1, _frame())
        assert store.get(_RAW_1)[1] == 2
        assert store.get(_RAW_2)[1] == 0

    def test_get_does_not_consume_the_frame(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame())
        assert store.get(_RAW_1) == store.get(_RAW_1)

    def test_clear_increments_the_sequence(self):
        """Si no cambiara, el cliente que ya vio ese seq no detectaría el reinicio."""
        store = _FrameStore()
        store.update(_RAW_1, _frame())
        before = store.get(_RAW_1)[1]
        store.clear(_RAW_1)
        assert store.get(_RAW_1)[1] > before

    def test_the_consumer_only_sends_what_is_new(self):
        """Simula el loop de envío: 20 vueltas contra 5 frames codificados."""
        store = _FrameStore()
        last_seq, sent = -1, 0
        for round in range(20):
            if round % 4 == 0:
                store.update(_RAW_1, _frame())
            jpeg, seq = store.get(_RAW_1)
            if jpeg and seq != last_seq:
                sent += 1
                last_seq = seq
        assert sent == 5


class TestWaitForChange:
    def test_returns_at_once_when_a_newer_frame_already_exists(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame())
        started_s = time.monotonic()
        store.wait_for_change(_RAW_1, 0, timeout_s=_CLIENT_TIMEOUT_S)
        assert time.monotonic() - started_s < 0.5

    def test_a_frame_pushed_between_get_and_wait_is_not_missed(self):
        """La razón de contar secuencias: con un Event, este aviso se perdía."""
        store = _FrameStore()
        jpeg, seq = store.get(_RAW_1)
        store.update(_RAW_1, _frame())
        started_s = time.monotonic()
        store.wait_for_change(_RAW_1, seq, timeout_s=_CLIENT_TIMEOUT_S)
        assert time.monotonic() - started_s < 0.5

    def test_wakes_up_when_the_next_frame_arrives(self):
        store = _FrameStore()
        threading.Timer(0.05, lambda: store.update(_RAW_1, _frame())).start()
        started_s = time.monotonic()
        store.wait_for_change(_RAW_1, 0, timeout_s=_CLIENT_TIMEOUT_S)
        assert time.monotonic() - started_s < 1.0
        assert store.get(_RAW_1)[1] == 1

    def test_another_stream_does_not_leave_a_frame_to_send(self):
        """Con un solo Condition el aviso llega a todos; el que despierta de más ve
        que su secuencia no cambió y vuelve a esperar."""
        store = _FrameStore()
        store.update(_RAW_2, _frame())
        store.wait_for_change(_RAW_1, 0, timeout_s=0.05)
        assert store.get(_RAW_1) == (None, 0)

    def test_timeout_ends_the_wait(self):
        started_s = time.monotonic()
        _FrameStore().wait_for_change(_RAW_1, 0, timeout_s=0.05)
        assert time.monotonic() - started_s < 1.0

    def test_close_wakes_every_waiting_client(self):
        store = _FrameStore()
        woken = []
        lock = threading.Lock()

        def wait_once():
            store.wait_for_change(_RAW_1, 0, timeout_s=_CLIENT_TIMEOUT_S)
            with lock:
                woken.append(store.is_closed)

        clients = [threading.Thread(target=wait_once) for _ in range(3)]
        for client in clients:
            client.start()
        time.sleep(0.1)          # que los tres lleguen a la espera antes del close
        store.close()
        for client in clients:
            client.join(timeout=_CLIENT_TIMEOUT_S)
        assert woken == [True, True, True]

    def test_a_closed_store_does_not_wait(self):
        store = _FrameStore()
        store.close()
        started_s = time.monotonic()
        store.wait_for_change(_RAW_1, 0, timeout_s=_CLIENT_TIMEOUT_S)
        assert time.monotonic() - started_s < 0.5


class TestSubscribers:
    def test_no_leases_counts_zero(self):
        assert _Subscribers().count(_RAW_1) == 0
        assert _Subscribers().count_all() == 0

    def test_touch_registers_a_client(self):
        subs = _Subscribers()
        subs.touch(_RAW_1, 1)
        assert subs.count(_RAW_1) == 1

    def test_touching_twice_does_not_duplicate(self):
        subs = _Subscribers()
        subs.touch(_RAW_1, 1)
        subs.touch(_RAW_1, 1)
        assert subs.count(_RAW_1) == 1

    def test_the_count_is_per_stream(self):
        subs = _Subscribers()
        subs.touch(_RAW_1, 1)
        assert subs.count(_ANNOTATED_1) == 0
        assert subs.count(_RAW_2) == 0
        assert subs.count_all() == 1

    def test_several_clients_on_the_same_stream(self):
        subs = _Subscribers()
        subs.touch(_RAW_1, 1)
        subs.touch(_RAW_1, 2)
        assert subs.count(_RAW_1) == 2
        assert subs.unregister(_RAW_1, 1) == 1
        assert subs.unregister(_RAW_1, 2) == 0

    def test_unregistering_an_unknown_client_is_harmless(self):
        subs = _Subscribers()
        assert subs.unregister(_RAW_1, 99) == 0
        subs.touch(_RAW_1, 1)
        assert subs.unregister(_RAW_1, 99) == 1

    def test_an_expired_lease_stops_counting(self, monkeypatch):
        """Una conexión semiabierta no puede quedar habilitando la codificación."""
        subs = _Subscribers()
        subs.touch(_RAW_1, 1)
        assert subs.count(_RAW_1) == 1
        real_monotonic = hvs.time.monotonic
        monkeypatch.setattr(hvs.time, "monotonic",
                            lambda: real_monotonic() + hvs._LEASE_TTL_S + 1.0)
        assert subs.count(_RAW_1) == 0
        assert subs.count_all() == 0

    def test_touch_revives_an_expired_lease(self, monkeypatch):
        subs = _Subscribers()
        subs.touch(_RAW_1, 1)
        real_monotonic = hvs.time.monotonic
        monkeypatch.setattr(hvs.time, "monotonic",
                            lambda: real_monotonic() + hvs._LEASE_TTL_S + 1.0)
        assert subs.count(_RAW_1) == 0
        subs.touch(_RAW_1, 1)          # el cliente sigue vivo y renueva
        assert subs.count(_RAW_1) == 1


def _gone_within(sock: socket.socket, timeout_s: float = _CLIENT_TIMEOUT_S) -> bool:
    """_is_client_gone en bucle, como lo usa el loop de envío.

    Con timeout 0 select() es un muestreo: el FIN puede no haber llegado todavía al
    loopback en la primera pasada. Lo que importa es que se detecte en alguna vuelta.
    """
    return _wait_until(lambda: _is_client_gone(sock), timeout_s)


class TestIsClientGone:
    def test_a_live_connection_is_not_reported_gone(self):
        near, far = socket.socketpair()
        try:
            assert _gone_within(near, 0.5) is False
        finally:
            near.close()
            far.close()

    def test_a_closed_peer_is_detected(self):
        near, far = socket.socketpair()
        try:
            far.close()
            assert _gone_within(near) is True
        finally:
            near.close()

    def test_unexpected_data_is_discarded_without_cutting(self):
        # Un byte suelto no es una desconexión: se consume y se sigue transmitiendo.
        near, far = socket.socketpair()
        try:
            far.sendall(b"basura")
            assert _gone_within(near, 0.5) is False
            assert _is_client_gone(near) is False
        finally:
            near.close()
            far.close()

    def test_a_close_after_data_is_detected(self):
        near, far = socket.socketpair()
        try:
            far.sendall(b"basura")
            far.close()
            assert _gone_within(near) is True
        finally:
            near.close()

    def test_a_closed_socket_does_not_raise(self):
        # select() sobre un fd cerrado tira ValueError, no OSError: sin atrapar los
        # dos, la excepción escapa del handler.
        near, far = socket.socketpair()
        near.close()
        far.close()
        assert _is_client_gone(near) is True


class TestPartHeader:
    def test_header_declares_the_boundary_and_the_size(self):
        header = _build_part_header(1234)
        assert header.startswith(f"--{hvs._BOUNDARY}\r\n".encode())
        assert b"Content-Type: image/jpeg\r\n" in header
        assert b"Content-Length: 1234\r\n" in header

    def test_header_ends_with_an_empty_line(self):
        """Sin la línea en blanco el cliente lee la cabecera como parte del JPEG."""
        assert _build_part_header(10).endswith(b"\r\n\r\n")


class TestPushGating:
    """Lo que ahorra el gating: sin nadie mirando un stream no se codifica nada."""

    def test_nothing_is_encoded_without_clients(self):
        server = _watched_server()
        server.push_raw("camera_1", _frame())
        assert server._store.get(_RAW_1)[0] is None

    def test_a_watched_stream_is_encoded(self):
        server = _watched_server(_RAW_1)
        server.push_raw("camera_1", _frame())
        assert _decoded_size(server._store.get(_RAW_1)[0]) == (960, 600)

    def test_the_gating_is_per_stream(self):
        # El caso real: cuatro streams publicados, uno solo mirado.
        server = _watched_server(_RAW_1)
        for slot in ("camera_1", "camera_2"):
            server.push_raw(slot, _frame())
            server.push_annotated(slot, _frame())
        assert server._store.get(_RAW_1)[0] is not None
        for key in (_ANNOTATED_1, _RAW_2, _ANNOTATED_2):
            assert server._store.get(key)[0] is None

    def test_annotated_is_encoded_when_watched(self):
        server = _watched_server(_ANNOTATED_2)
        server.push_annotated("camera_2", _frame())
        assert server._store.get(_ANNOTATED_2)[0] is not None

    def test_an_inactive_server_does_not_encode(self):
        server = HttpVideoServer(_MockConfig())
        server._subs.touch(_RAW_1, 1)
        server.push_raw("camera_1", _frame())
        assert server._store.get(_RAW_1)[0] is None

    def test_a_none_frame_is_ignored(self):
        server = _watched_server(_RAW_1, _ANNOTATED_1)
        server.push_raw("camera_1", None)
        server.push_annotated("camera_1", None)
        assert server._store.get(_RAW_1)[0] is None

    def test_an_unknown_slot_is_a_no_op(self):
        server = _watched_server(_RAW_1)
        server.push_raw("camera_9", _frame())
        assert server._store.get(("camera_9", "raw"))[0] is None

    def test_frame_width_comes_from_config(self):
        server = _watched_server(_RAW_1, **{"video.http.frame_width_px": 0})
        server.push_raw("camera_1", _frame())
        assert _decoded_size(server._store.get(_RAW_1)[0]) == (1920, 1200)

    def test_jpeg_quality_comes_from_config(self):
        high = _watched_server(_RAW_1, **{"video.http.jpeg_quality": 95})
        low = _watched_server(_RAW_1, **{"video.http.jpeg_quality": 20})
        high.push_raw("camera_1", _frame())
        low.push_raw("camera_1", _frame())
        assert len(low._store.get(_RAW_1)[0]) < len(high._store.get(_RAW_1)[0])

    def test_the_client_predicates_are_per_stream_and_mode(self):
        """Sirven para no dibujar anotaciones que nadie va a ver."""
        server = _watched_server(_RAW_1, _ANNOTATED_2)
        assert server.has_raw_clients("camera_1") is True
        assert server.has_annotated_clients("camera_1") is False
        assert server.has_raw_clients("camera_2") is False
        assert server.has_annotated_clients("camera_2") is True
        assert server.get_client_count() == 2


class TestSlots:
    def test_slots_keep_the_config_order(self, server):
        assert server._server.slots == ("camera_1", "camera_2")

    def test_slots_include_disabled_cameras(self, monkeypatch):
        """`enabled` cambia en caliente: la ruta no puede aparecer y desaparecer."""
        monkeypatch.setattr(hvs, "_HOST", _LOOPBACK)
        server = HttpVideoServer(_MockConfig(cameras={"camera_1": {"enabled": False}}))
        server.start()
        try:
            assert server._server.slots == ("camera_1",)
        finally:
            server.stop()

    def test_no_cameras_still_starts(self, monkeypatch):
        monkeypatch.setattr(hvs, "_HOST", _LOOPBACK)
        server = HttpVideoServer(_MockConfig(cameras={}))
        server.start()
        try:
            assert server.status == "active"
            assert server._server.slots == ()
        finally:
            server.stop()


class TestLifecycle:
    def test_disabled_in_config_does_not_listen(self, monkeypatch):
        monkeypatch.setattr(hvs, "_HOST", _LOOPBACK)
        server = HttpVideoServer(_MockConfig(**{"video.http.enabled": False}))
        server.start()
        assert server.status == "disabled"
        assert server._server is None

    def test_start_reports_active(self, server):
        assert server.status == "active"

    def test_stop_releases_the_port(self, server):
        port = _port_of(server)
        server.stop()
        assert server.status == "disabled"
        # Sin server_close() este bind fallaría con "puerto en uso".
        with socket.socket() as probe:
            probe.bind((_LOOPBACK, port))

    def test_stop_is_idempotent(self, server):
        server.stop()
        server.stop()
        assert server.status == "disabled"

    def test_stop_without_start_does_nothing(self):
        server = HttpVideoServer(_MockConfig())
        server.stop()
        assert server.status == "disabled"

    def test_the_server_can_be_restarted(self, server):
        """El almacén se cierra al detener: si no se renovara, el segundo arranque
        serviría streams que cortan enseguida."""
        server.stop()
        server.start()
        assert server.status == "active"
        server.push_annotated("camera_1", _frame())     # sin clientes: no codifica
        assert server._store.is_closed is False
        assert _get(server, "/").status == 200

    def test_busy_port_reports_error_instead_of_raising(self, monkeypatch):
        monkeypatch.setattr(hvs, "_HOST", _LOOPBACK)
        with socket.socket() as taken:
            taken.bind((_LOOPBACK, 0))
            taken.listen(1)
            server = HttpVideoServer(
                _MockConfig(**{"video.http.port": taken.getsockname()[1]})
            )
            server.start()
        assert server.status == "error"
        assert server._server is None


class TestEndpoints:
    def test_the_info_page_links_every_stream(self, server):
        response = _get(server, "/")
        assert response.status == 200
        assert response.getheader("Content-Type") == "text/html; charset=utf-8"
        body = response.read()
        for slot, mode in _ALL_KEYS:
            assert f'href="/{slot}/{mode}"'.encode() in body

    @pytest.mark.parametrize("path", [
        "/nope",
        "/camera_9/raw",        # slot que no está en el config
        "/camera_1/color",      # modo inexistente
        "/camera_1",            # falta el modo
        "/camera_1/raw/extra",
    ])
    def test_unknown_paths_are_404(self, server, path):
        assert _get(server, path).status == 404

    def test_the_stream_declares_the_multipart_content_type(self, server):
        server_response = _get(server, "/camera_1/raw")
        assert server_response.status == 200
        assert server_response.getheader("Content-Type") == (
            f"multipart/x-mixed-replace; boundary={hvs._BOUNDARY}"
        )
        server_response.close()

    def test_connecting_enables_the_encoding(self, server):
        """La cadena completa: conectarse cuenta como cliente y habilita el push."""
        response = _get(server, "/camera_1/raw")
        assert _wait_until(lambda: server.has_raw_clients("camera_1"))
        server.push_raw("camera_1", _frame())
        assert _decoded_size(_read_one_part(response)) == (960, 600)
        response.close()

    def test_two_clients_get_the_same_frame(self, server):
        first = _get(server, "/camera_1/raw")
        second = _get(server, "/camera_1/raw")
        assert _wait_until(lambda: server.get_client_count() == 2)
        server.push_raw("camera_1", _frame())
        assert _read_one_part(first) == _read_one_part(second)
        first.close()
        second.close()

    def test_each_camera_serves_its_own_frames(self, server):
        first = _get(server, "/camera_1/raw")
        second = _get(server, "/camera_2/raw")
        assert _wait_until(lambda: server.get_client_count() == 2)
        server.push_raw("camera_1", _frame(1920, 1200))
        server.push_raw("camera_2", _frame(640, 480))
        assert _decoded_size(_read_one_part(first)) == (960, 600)
        assert _decoded_size(_read_one_part(second)) == (640, 480)
        first.close()
        second.close()

    def test_stopping_the_server_ends_the_open_streams(self, server):
        """Sin cerrar el almacén, estos hilos seguirían mandando keepalives."""
        sock = _open_stream(server, "/camera_1/raw")
        try:
            assert _wait_until(lambda: server.has_raw_clients("camera_1"))
            server.stop()
            sock.settimeout(_CLIENT_TIMEOUT_S)
            received = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break                       # el servidor cerró la conexión
                received += chunk
            assert received.startswith(b"HTTP/1.0 200")
        finally:
            sock.close()


class TestClientDisconnect:
    """Cortar la conexión tiene que dejar de costar CPU y ancho de banda enseguida."""

    def test_leaving_stops_the_encoding(self, server):
        sock = _open_stream(server, "/camera_1/raw")
        assert _wait_until(lambda: server.has_raw_clients("camera_1"))
        server.push_raw("camera_1", _frame())
        assert server._store.get(_RAW_1)[0] is not None

        _reset(sock)
        assert _wait_until(lambda: not server.has_raw_clients("camera_1"))
        # El último cliente que se va deja el stream sin JPEG guardado.
        assert _wait_until(lambda: server._store.get(_RAW_1)[0] is None)
        server.push_raw("camera_1", _frame())
        assert server._store.get(_RAW_1)[0] is None

    def test_one_client_leaving_does_not_cut_the_other(self, server):
        leaving = _open_stream(server, "/camera_1/raw")
        staying = _get(server, "/camera_1/raw")
        assert _wait_until(lambda: server.get_client_count() == 2)

        _reset(leaving)
        assert _wait_until(lambda: server.get_client_count() == 1)
        server.push_raw("camera_1", _frame())
        assert _decoded_size(_read_one_part(staying)) == (960, 600)
        staying.close()

    def test_a_reset_prints_no_traceback(self, server, capsys):
        sock = _open_stream(server, "/camera_1/raw")
        assert _wait_until(lambda: server.has_raw_clients("camera_1"))
        _reset(sock)
        assert _wait_until(lambda: not server.has_raw_clients("camera_1"))
        assert "Traceback" not in capsys.readouterr().err

    def test_the_server_keeps_serving_after_a_reset(self, server):
        _reset(_open_stream(server, "/camera_1/raw"))
        assert _get(server, "/").status == 200
        assert server.status == "active"


def _logged(caplog: pytest.LogCaptureFixture, text: str) -> bool:
    return any(text in record.getMessage() for record in caplog.records)


class TestConnectionLogging:
    """Una línea por cliente que entra y sale es útil probando y ruido en producción,
    así que la decide el config."""

    def _connect_and_leave(self, server: HttpVideoServer):
        sock = _open_stream(server, "/camera_1/raw")
        assert _wait_until(lambda: server.has_raw_clients("camera_1"))
        _reset(sock)
        assert _wait_until(lambda: not server.has_raw_clients("camera_1"))

    def test_connections_are_logged_by_default(self, server, caplog):
        with caplog.at_level(logging.INFO):
            self._connect_and_leave(server)
            assert _wait_until(lambda: _logged(caplog, "Cliente desconectado"))
        assert _logged(caplog, "Cliente conectado a /camera_1/raw")

    def test_nothing_is_logged_when_disabled(self, monkeypatch, caplog):
        monkeypatch.setattr(hvs, "_HOST", _LOOPBACK)
        server = HttpVideoServer(_MockConfig(**{"video.http.log_connections": False}))
        server.start()
        try:
            with caplog.at_level(logging.INFO):
                self._connect_and_leave(server)
                time.sleep(0.2)             # tiempo de sobra para que el hilo loguee
                assert not _logged(caplog, "Cliente")
        finally:
            server.stop()

    def test_the_option_is_read_at_each_event(self, server, caplog):
        """Se lee en el momento: apagarla en caliente calla la desconexión."""
        with caplog.at_level(logging.INFO):
            sock = _open_stream(server, "/camera_1/raw")
            assert _wait_until(lambda: _logged(caplog, "Cliente conectado"))

            server._config.set("video.http.log_connections", False)
            _reset(sock)
            assert _wait_until(lambda: not server.has_raw_clients("camera_1"))
            time.sleep(0.2)
            assert not _logged(caplog, "Cliente desconectado")

    def test_the_lifecycle_lines_are_not_affected(self, monkeypatch, caplog):
        """Que el servidor arrancó y se detuvo se dice siempre: no es ruido por cliente."""
        monkeypatch.setattr(hvs, "_HOST", _LOOPBACK)
        with caplog.at_level(logging.INFO):
            server = HttpVideoServer(_MockConfig(**{"video.http.log_connections": False}))
            server.start()
            server.stop()
        assert _logged(caplog, "Servidor activo")
        assert _logged(caplog, "Servidor detenido")
