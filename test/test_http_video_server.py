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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import system.http_video_server as hvs
from system.http_video_server import (
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

    # `http_video.log_connections` queda afuera a propósito: así los tests que no lo
    # fijan pasan por el default del módulo.
    def __init__(self, **overrides):
        self._values = {
            "http_video.enabled": True,
            "http_video.port": 0,          # 0 = puerto libre elegido por el SO
            "http_video.jpeg_quality": 80,
            "http_video.frame_width_px": 960,
            "cameras": {"camera_1": {}, "camera_2": {}},
        }
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self._values[key] = value


def _frame(width_px: int = 1920, height_px: int = 1200) -> np.ndarray:
    """Frame BGR con gradiente, para que el JPEG tenga un tamaño representativo."""
    _frame_bgr = np.zeros((height_px, width_px, 3), np.uint8)
    _frame_bgr[:, :, 1] = np.linspace(0, 255, width_px, dtype=np.uint8)[None, :]
    return _frame_bgr


def _decoded_size(jpeg: bytes) -> tuple[int, int]:
    """(ancho, alto) del JPEG almacenado."""
    _decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    return _decoded.shape[1], _decoded.shape[0]


def _watched_server(*keys: tuple[str, str], **overrides) -> HttpVideoServer:
    """Servidor sin socket, con un cliente simulado en cada stream de `keys`."""
    _server = HttpVideoServer(_MockConfig(**overrides))
    _server._is_active = True          # start() real abriría un puerto
    for _index, _key in enumerate(keys):
        _server._subs.touch(_key, _index)
    return _server


@pytest.fixture
def server(monkeypatch):
    """Servidor escuchando en loopback, en un puerto que elige el SO."""
    # En 0.0.0.0 Windows pide autorización del firewall para escuchar.
    monkeypatch.setattr(hvs, "_HOST", _LOOPBACK)
    _server = HttpVideoServer(_MockConfig())
    _server.start()
    yield _server
    _server.stop()


def _port_of(server: HttpVideoServer) -> int:
    return server._server.server_address[1]


def _get(server: HttpVideoServer, path: str) -> HTTPResponse:
    _connection = HTTPConnection(_LOOPBACK, _port_of(server), timeout=_CLIENT_TIMEOUT_S)
    _connection.request("GET", path)
    return _connection.getresponse()


def _open_stream(server: HttpVideoServer, path: str) -> socket.socket:
    """Socket crudo con el request de stream enviado y las cabeceras ya en camino."""
    _sock = socket.create_connection((_LOOPBACK, _port_of(server)), timeout=_CLIENT_TIMEOUT_S)
    _sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {_LOOPBACK}\r\n\r\n".encode())
    return _sock


def _reset(sock: socket.socket):
    """Corta con RST, como un navegador al que le cierran la pestaña."""
    # SO_LINGER en 0 fuerza el RST; un close() normal cerraría ordenado.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()


def _read_one_part(response: HTTPResponse) -> bytes:
    """Lee la primera parte multipart del stream y devuelve el JPEG."""
    assert response.readline().strip() == f"--{hvs._BOUNDARY}".encode()
    _size = 0
    while True:
        _line = response.readline().strip()
        if not _line:
            break
        _name, _, _value = _line.decode().partition(":")
        if _name.lower() == "content-length":
            _size = int(_value)
    return response.read(_size)


def _wait_until(condition, timeout_s: float = _CLIENT_TIMEOUT_S) -> bool:
    """Espera a que `condition()` sea verdadera. Evita sleeps fijos en los tests."""
    _deadline_s = time.monotonic() + timeout_s
    while time.monotonic() < _deadline_s:
        if condition():
            return True
        time.sleep(0.01)
    return False


class TestDownscale:
    def test_keeps_the_aspect_ratio(self):
        assert _downscale(_frame(1920, 1200), 960).shape[:2] == (600, 960)

    def test_width_zero_leaves_the_native_resolution(self):
        _source = _frame(1920, 1200)
        assert _downscale(_source, 0) is _source

    def test_negative_width_leaves_the_native_resolution(self):
        _source = _frame(1920, 1200)
        assert _downscale(_source, -1) is _source

    def test_never_upscales(self):
        _source = _frame(640, 400)
        assert _downscale(_source, 960) is _source

    def test_the_native_width_is_not_copied(self):
        _source = _frame(960, 600)
        assert _downscale(_source, 960) is _source

    def test_an_extreme_aspect_ratio_does_not_collapse_the_height(self):
        # Sin el max(1, ...) el alto redondearía a 0 y cv2.resize fallaría.
        assert _downscale(_frame(1920, 3), 100).shape[0] >= 1


class TestFrameStore:
    def test_store_starts_empty(self):
        assert _FrameStore().get(_RAW_1) == (None, 0)

    def test_update_stores_jpeg_bytes(self):
        _store = _FrameStore()
        _store.update(_RAW_1, _frame())
        assert isinstance(_store.get(_RAW_1)[0], bytes)

    def test_update_encodes_at_the_requested_width(self):
        _store = _FrameStore()
        _store.update(_RAW_1, _frame(), frame_width_px=960)
        assert _decoded_size(_store.get(_RAW_1)[0]) == (960, 600)

    def test_width_zero_encodes_the_native_resolution(self):
        _store = _FrameStore()
        _store.update(_RAW_1, _frame(), frame_width_px=0)
        assert _decoded_size(_store.get(_RAW_1)[0]) == (1920, 1200)

    def test_downscaling_reduces_the_jpeg_size(self):
        """El objetivo del reescalado: menos píxeles, menos bytes por la red."""
        _store = _FrameStore()
        _store.update(_RAW_1, _frame(), frame_width_px=0)
        _native = len(_store.get(_RAW_1)[0])
        _store.update(_RAW_1, _frame(), frame_width_px=960)
        assert len(_store.get(_RAW_1)[0]) < _native

    def test_lower_quality_yields_fewer_bytes(self):
        _store = _FrameStore()
        _store.update(_RAW_1, _frame(), jpeg_quality=95)
        _high = len(_store.get(_RAW_1)[0])
        _store.update(_RAW_1, _frame(), jpeg_quality=20)
        assert len(_store.get(_RAW_1)[0]) < _high

    def test_each_stream_is_independent(self):
        _store = _FrameStore()
        _store.update(_RAW_1, _frame())
        assert _store.get(_RAW_1)[0] is not None
        for _key in (_ANNOTATED_1, _RAW_2, _ANNOTATED_2):
            assert _store.get(_key)[0] is None

    def test_clear_discards_the_last_jpeg(self):
        _store = _FrameStore()
        _store.update(_RAW_1, _frame())
        _store.clear(_RAW_1)
        assert _store.get(_RAW_1)[0] is None


class TestSequence:
    """La secuencia es lo que permite no reenviar un frame ya enviado."""

    def test_each_update_increments_the_sequence(self):
        _store = _FrameStore()
        _seqs = []
        for _ in range(3):
            _store.update(_RAW_1, _frame())
            _seqs.append(_store.get(_RAW_1)[1])
        assert _seqs == [1, 2, 3]

    def test_the_sequence_is_per_stream(self):
        _store = _FrameStore()
        _store.update(_RAW_1, _frame())
        _store.update(_RAW_1, _frame())
        assert _store.get(_RAW_1)[1] == 2
        assert _store.get(_RAW_2)[1] == 0

    def test_get_does_not_consume_the_frame(self):
        _store = _FrameStore()
        _store.update(_RAW_1, _frame())
        assert _store.get(_RAW_1) == _store.get(_RAW_1)

    def test_clear_increments_the_sequence(self):
        """Si no cambiara, el cliente que ya vio ese seq no detectaría el reinicio."""
        _store = _FrameStore()
        _store.update(_RAW_1, _frame())
        _before = _store.get(_RAW_1)[1]
        _store.clear(_RAW_1)
        assert _store.get(_RAW_1)[1] > _before

    def test_the_consumer_only_sends_what_is_new(self):
        """Simula el loop de envío: 20 vueltas contra 5 frames codificados."""
        _store = _FrameStore()
        _last_seq, _sent = -1, 0
        for _round in range(20):
            if _round % 4 == 0:
                _store.update(_RAW_1, _frame())
            _jpeg, _seq = _store.get(_RAW_1)
            if _jpeg and _seq != _last_seq:
                _sent += 1
                _last_seq = _seq
        assert _sent == 5


class TestWaitForChange:
    def test_returns_at_once_when_a_newer_frame_already_exists(self):
        _store = _FrameStore()
        _store.update(_RAW_1, _frame())
        _started_s = time.monotonic()
        _store.wait_for_change(_RAW_1, 0, timeout_s=_CLIENT_TIMEOUT_S)
        assert time.monotonic() - _started_s < 0.5

    def test_a_frame_pushed_between_get_and_wait_is_not_missed(self):
        """La razón de contar secuencias: con un Event, este aviso se perdía."""
        _store = _FrameStore()
        _jpeg, _seq = _store.get(_RAW_1)
        _store.update(_RAW_1, _frame())
        _started_s = time.monotonic()
        _store.wait_for_change(_RAW_1, _seq, timeout_s=_CLIENT_TIMEOUT_S)
        assert time.monotonic() - _started_s < 0.5

    def test_wakes_up_when_the_next_frame_arrives(self):
        _store = _FrameStore()
        threading.Timer(0.05, lambda: _store.update(_RAW_1, _frame())).start()
        _started_s = time.monotonic()
        _store.wait_for_change(_RAW_1, 0, timeout_s=_CLIENT_TIMEOUT_S)
        assert time.monotonic() - _started_s < 1.0
        assert _store.get(_RAW_1)[1] == 1

    def test_another_stream_does_not_leave_a_frame_to_send(self):
        """Con un solo Condition el aviso llega a todos; el que despierta de más ve
        que su secuencia no cambió y vuelve a esperar."""
        _store = _FrameStore()
        _store.update(_RAW_2, _frame())
        _store.wait_for_change(_RAW_1, 0, timeout_s=0.05)
        assert _store.get(_RAW_1) == (None, 0)

    def test_timeout_ends_the_wait(self):
        _started_s = time.monotonic()
        _FrameStore().wait_for_change(_RAW_1, 0, timeout_s=0.05)
        assert time.monotonic() - _started_s < 1.0

    def test_close_wakes_every_waiting_client(self):
        _store = _FrameStore()
        _woken = []
        _lock = threading.Lock()

        def _wait_once():
            _store.wait_for_change(_RAW_1, 0, timeout_s=_CLIENT_TIMEOUT_S)
            with _lock:
                _woken.append(_store.is_closed)

        _clients = [threading.Thread(target=_wait_once) for _ in range(3)]
        for _client in _clients:
            _client.start()
        time.sleep(0.1)          # que los tres lleguen a la espera antes del close
        _store.close()
        for _client in _clients:
            _client.join(timeout=_CLIENT_TIMEOUT_S)
        assert _woken == [True, True, True]

    def test_a_closed_store_does_not_wait(self):
        _store = _FrameStore()
        _store.close()
        _started_s = time.monotonic()
        _store.wait_for_change(_RAW_1, 0, timeout_s=_CLIENT_TIMEOUT_S)
        assert time.monotonic() - _started_s < 0.5


class TestSubscribers:
    def test_no_leases_counts_zero(self):
        assert _Subscribers().count(_RAW_1) == 0
        assert _Subscribers().count_all() == 0

    def test_touch_registers_a_client(self):
        _subs = _Subscribers()
        _subs.touch(_RAW_1, 1)
        assert _subs.count(_RAW_1) == 1

    def test_touching_twice_does_not_duplicate(self):
        _subs = _Subscribers()
        _subs.touch(_RAW_1, 1)
        _subs.touch(_RAW_1, 1)
        assert _subs.count(_RAW_1) == 1

    def test_the_count_is_per_stream(self):
        _subs = _Subscribers()
        _subs.touch(_RAW_1, 1)
        assert _subs.count(_ANNOTATED_1) == 0
        assert _subs.count(_RAW_2) == 0
        assert _subs.count_all() == 1

    def test_several_clients_on_the_same_stream(self):
        _subs = _Subscribers()
        _subs.touch(_RAW_1, 1)
        _subs.touch(_RAW_1, 2)
        assert _subs.count(_RAW_1) == 2
        assert _subs.unregister(_RAW_1, 1) == 1
        assert _subs.unregister(_RAW_1, 2) == 0

    def test_unregistering_an_unknown_client_is_harmless(self):
        _subs = _Subscribers()
        assert _subs.unregister(_RAW_1, 99) == 0
        _subs.touch(_RAW_1, 1)
        assert _subs.unregister(_RAW_1, 99) == 1

    def test_an_expired_lease_stops_counting(self, monkeypatch):
        """Una conexión semiabierta no puede quedar habilitando la codificación."""
        _subs = _Subscribers()
        _subs.touch(_RAW_1, 1)
        assert _subs.count(_RAW_1) == 1
        _real_monotonic = hvs.time.monotonic
        monkeypatch.setattr(hvs.time, "monotonic",
                            lambda: _real_monotonic() + hvs._LEASE_TTL_S + 1.0)
        assert _subs.count(_RAW_1) == 0
        assert _subs.count_all() == 0

    def test_touch_revives_an_expired_lease(self, monkeypatch):
        _subs = _Subscribers()
        _subs.touch(_RAW_1, 1)
        _real_monotonic = hvs.time.monotonic
        monkeypatch.setattr(hvs.time, "monotonic",
                            lambda: _real_monotonic() + hvs._LEASE_TTL_S + 1.0)
        assert _subs.count(_RAW_1) == 0
        _subs.touch(_RAW_1, 1)          # el cliente sigue vivo y renueva
        assert _subs.count(_RAW_1) == 1


def _gone_within(sock: socket.socket, timeout_s: float = _CLIENT_TIMEOUT_S) -> bool:
    """_is_client_gone en bucle, como lo usa el loop de envío.

    Con timeout 0 select() es un muestreo: el FIN puede no haber llegado todavía al
    loopback en la primera pasada. Lo que importa es que se detecte en alguna vuelta.
    """
    return _wait_until(lambda: _is_client_gone(sock), timeout_s)


class TestIsClientGone:
    def test_a_live_connection_is_not_reported_gone(self):
        _near, _far = socket.socketpair()
        try:
            assert _gone_within(_near, 0.5) is False
        finally:
            _near.close()
            _far.close()

    def test_a_closed_peer_is_detected(self):
        _near, _far = socket.socketpair()
        try:
            _far.close()
            assert _gone_within(_near) is True
        finally:
            _near.close()

    def test_unexpected_data_is_discarded_without_cutting(self):
        # Un byte suelto no es una desconexión: se consume y se sigue transmitiendo.
        _near, _far = socket.socketpair()
        try:
            _far.sendall(b"basura")
            assert _gone_within(_near, 0.5) is False
            assert _is_client_gone(_near) is False
        finally:
            _near.close()
            _far.close()

    def test_a_close_after_data_is_detected(self):
        _near, _far = socket.socketpair()
        try:
            _far.sendall(b"basura")
            _far.close()
            assert _gone_within(_near) is True
        finally:
            _near.close()

    def test_a_closed_socket_does_not_raise(self):
        # select() sobre un fd cerrado tira ValueError, no OSError: sin atrapar los
        # dos, la excepción escapa del handler.
        _near, _far = socket.socketpair()
        _near.close()
        _far.close()
        assert _is_client_gone(_near) is True


class TestPartHeader:
    def test_header_declares_the_boundary_and_the_size(self):
        _header = _build_part_header(1234)
        assert _header.startswith(f"--{hvs._BOUNDARY}\r\n".encode())
        assert b"Content-Type: image/jpeg\r\n" in _header
        assert b"Content-Length: 1234\r\n" in _header

    def test_header_ends_with_an_empty_line(self):
        """Sin la línea en blanco el cliente lee la cabecera como parte del JPEG."""
        assert _build_part_header(10).endswith(b"\r\n\r\n")


class TestPushGating:
    """Lo que ahorra el gating: sin nadie mirando un stream no se codifica nada."""

    def test_nothing_is_encoded_without_clients(self):
        _server = _watched_server()
        _server.push_raw("camera_1", _frame())
        assert _server._store.get(_RAW_1)[0] is None

    def test_a_watched_stream_is_encoded(self):
        _server = _watched_server(_RAW_1)
        _server.push_raw("camera_1", _frame())
        assert _decoded_size(_server._store.get(_RAW_1)[0]) == (960, 600)

    def test_the_gating_is_per_stream(self):
        # El caso real: cuatro streams publicados, uno solo mirado.
        _server = _watched_server(_RAW_1)
        for _slot in ("camera_1", "camera_2"):
            _server.push_raw(_slot, _frame())
            _server.push_annotated(_slot, _frame())
        assert _server._store.get(_RAW_1)[0] is not None
        for _key in (_ANNOTATED_1, _RAW_2, _ANNOTATED_2):
            assert _server._store.get(_key)[0] is None

    def test_annotated_is_encoded_when_watched(self):
        _server = _watched_server(_ANNOTATED_2)
        _server.push_annotated("camera_2", _frame())
        assert _server._store.get(_ANNOTATED_2)[0] is not None

    def test_an_inactive_server_does_not_encode(self):
        _server = HttpVideoServer(_MockConfig())
        _server._subs.touch(_RAW_1, 1)
        _server.push_raw("camera_1", _frame())
        assert _server._store.get(_RAW_1)[0] is None

    def test_a_none_frame_is_ignored(self):
        _server = _watched_server(_RAW_1, _ANNOTATED_1)
        _server.push_raw("camera_1", None)
        _server.push_annotated("camera_1", None)
        assert _server._store.get(_RAW_1)[0] is None

    def test_an_unknown_slot_is_a_no_op(self):
        _server = _watched_server(_RAW_1)
        _server.push_raw("camera_9", _frame())
        assert _server._store.get(("camera_9", "raw"))[0] is None

    def test_frame_width_comes_from_config(self):
        _server = _watched_server(_RAW_1, **{"http_video.frame_width_px": 0})
        _server.push_raw("camera_1", _frame())
        assert _decoded_size(_server._store.get(_RAW_1)[0]) == (1920, 1200)

    def test_jpeg_quality_comes_from_config(self):
        _high = _watched_server(_RAW_1, **{"http_video.jpeg_quality": 95})
        _low = _watched_server(_RAW_1, **{"http_video.jpeg_quality": 20})
        _high.push_raw("camera_1", _frame())
        _low.push_raw("camera_1", _frame())
        assert len(_low._store.get(_RAW_1)[0]) < len(_high._store.get(_RAW_1)[0])

    def test_the_client_predicates_are_per_stream_and_mode(self):
        """Sirven para no dibujar anotaciones que nadie va a ver."""
        _server = _watched_server(_RAW_1, _ANNOTATED_2)
        assert _server.has_raw_clients("camera_1") is True
        assert _server.has_annotated_clients("camera_1") is False
        assert _server.has_raw_clients("camera_2") is False
        assert _server.has_annotated_clients("camera_2") is True
        assert _server.get_client_count() == 2


class TestSlots:
    def test_slots_keep_the_config_order(self, server):
        assert server._server.slots == ("camera_1", "camera_2")

    def test_slots_include_disabled_cameras(self, monkeypatch):
        """`enabled` cambia en caliente: la ruta no puede aparecer y desaparecer."""
        monkeypatch.setattr(hvs, "_HOST", _LOOPBACK)
        _server = HttpVideoServer(_MockConfig(cameras={"camera_1": {"enabled": False}}))
        _server.start()
        try:
            assert _server._server.slots == ("camera_1",)
        finally:
            _server.stop()

    def test_no_cameras_still_starts(self, monkeypatch):
        monkeypatch.setattr(hvs, "_HOST", _LOOPBACK)
        _server = HttpVideoServer(_MockConfig(cameras={}))
        _server.start()
        try:
            assert _server.status == "active"
            assert _server._server.slots == ()
        finally:
            _server.stop()


class TestLifecycle:
    def test_disabled_in_config_does_not_listen(self, monkeypatch):
        monkeypatch.setattr(hvs, "_HOST", _LOOPBACK)
        _server = HttpVideoServer(_MockConfig(**{"http_video.enabled": False}))
        _server.start()
        assert _server.status == "disabled"
        assert _server._server is None

    def test_start_reports_active(self, server):
        assert server.status == "active"

    def test_stop_releases_the_port(self, server):
        _port = _port_of(server)
        server.stop()
        assert server.status == "disabled"
        # Sin server_close() este bind fallaría con "puerto en uso".
        with socket.socket() as _probe:
            _probe.bind((_LOOPBACK, _port))

    def test_stop_is_idempotent(self, server):
        server.stop()
        server.stop()
        assert server.status == "disabled"

    def test_stop_without_start_does_nothing(self):
        _server = HttpVideoServer(_MockConfig())
        _server.stop()
        assert _server.status == "disabled"

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
        with socket.socket() as _taken:
            _taken.bind((_LOOPBACK, 0))
            _taken.listen(1)
            _server = HttpVideoServer(
                _MockConfig(**{"http_video.port": _taken.getsockname()[1]})
            )
            _server.start()
        assert _server.status == "error"
        assert _server._server is None


class TestEndpoints:
    def test_the_info_page_links_every_stream(self, server):
        _response = _get(server, "/")
        assert _response.status == 200
        assert _response.getheader("Content-Type") == "text/html; charset=utf-8"
        _body = _response.read()
        for _slot, _mode in _ALL_KEYS:
            assert f'href="/{_slot}/{_mode}"'.encode() in _body

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
        _server_response = _get(server, "/camera_1/raw")
        assert _server_response.status == 200
        assert _server_response.getheader("Content-Type") == (
            f"multipart/x-mixed-replace; boundary={hvs._BOUNDARY}"
        )
        _server_response.close()

    def test_connecting_enables_the_encoding(self, server):
        """La cadena completa: conectarse cuenta como cliente y habilita el push."""
        _response = _get(server, "/camera_1/raw")
        assert _wait_until(lambda: server.has_raw_clients("camera_1"))
        server.push_raw("camera_1", _frame())
        assert _decoded_size(_read_one_part(_response)) == (960, 600)
        _response.close()

    def test_two_clients_get_the_same_frame(self, server):
        _first = _get(server, "/camera_1/raw")
        _second = _get(server, "/camera_1/raw")
        assert _wait_until(lambda: server.get_client_count() == 2)
        server.push_raw("camera_1", _frame())
        assert _read_one_part(_first) == _read_one_part(_second)
        _first.close()
        _second.close()

    def test_each_camera_serves_its_own_frames(self, server):
        _first = _get(server, "/camera_1/raw")
        _second = _get(server, "/camera_2/raw")
        assert _wait_until(lambda: server.get_client_count() == 2)
        server.push_raw("camera_1", _frame(1920, 1200))
        server.push_raw("camera_2", _frame(640, 480))
        assert _decoded_size(_read_one_part(_first)) == (960, 600)
        assert _decoded_size(_read_one_part(_second)) == (640, 480)
        _first.close()
        _second.close()

    def test_stopping_the_server_ends_the_open_streams(self, server):
        """Sin cerrar el almacén, estos hilos seguirían mandando keepalives."""
        _sock = _open_stream(server, "/camera_1/raw")
        try:
            assert _wait_until(lambda: server.has_raw_clients("camera_1"))
            server.stop()
            _sock.settimeout(_CLIENT_TIMEOUT_S)
            _received = b""
            while True:
                _chunk = _sock.recv(4096)
                if not _chunk:
                    break                       # el servidor cerró la conexión
                _received += _chunk
            assert _received.startswith(b"HTTP/1.0 200")
        finally:
            _sock.close()


class TestClientDisconnect:
    """Cortar la conexión tiene que dejar de costar CPU y ancho de banda enseguida."""

    def test_leaving_stops_the_encoding(self, server):
        _sock = _open_stream(server, "/camera_1/raw")
        assert _wait_until(lambda: server.has_raw_clients("camera_1"))
        server.push_raw("camera_1", _frame())
        assert server._store.get(_RAW_1)[0] is not None

        _reset(_sock)
        assert _wait_until(lambda: not server.has_raw_clients("camera_1"))
        # El último cliente que se va deja el stream sin JPEG guardado.
        assert _wait_until(lambda: server._store.get(_RAW_1)[0] is None)
        server.push_raw("camera_1", _frame())
        assert server._store.get(_RAW_1)[0] is None

    def test_one_client_leaving_does_not_cut_the_other(self, server):
        _leaving = _open_stream(server, "/camera_1/raw")
        _staying = _get(server, "/camera_1/raw")
        assert _wait_until(lambda: server.get_client_count() == 2)

        _reset(_leaving)
        assert _wait_until(lambda: server.get_client_count() == 1)
        server.push_raw("camera_1", _frame())
        assert _decoded_size(_read_one_part(_staying)) == (960, 600)
        _staying.close()

    def test_a_reset_prints_no_traceback(self, server, capsys):
        _sock = _open_stream(server, "/camera_1/raw")
        assert _wait_until(lambda: server.has_raw_clients("camera_1"))
        _reset(_sock)
        assert _wait_until(lambda: not server.has_raw_clients("camera_1"))
        assert "Traceback" not in capsys.readouterr().err

    def test_the_server_keeps_serving_after_a_reset(self, server):
        _reset(_open_stream(server, "/camera_1/raw"))
        assert _get(server, "/").status == 200
        assert server.status == "active"


def _logged(caplog: pytest.LogCaptureFixture, text: str) -> bool:
    return any(text in _record.getMessage() for _record in caplog.records)


class TestConnectionLogging:
    """Una línea por cliente que entra y sale es útil probando y ruido en producción,
    así que la decide el config."""

    def _connect_and_leave(self, server: HttpVideoServer):
        _sock = _open_stream(server, "/camera_1/raw")
        assert _wait_until(lambda: server.has_raw_clients("camera_1"))
        _reset(_sock)
        assert _wait_until(lambda: not server.has_raw_clients("camera_1"))

    def test_connections_are_logged_by_default(self, server, caplog):
        with caplog.at_level(logging.INFO):
            self._connect_and_leave(server)
            assert _wait_until(lambda: _logged(caplog, "Cliente desconectado"))
        assert _logged(caplog, "Cliente conectado a /camera_1/raw")

    def test_nothing_is_logged_when_disabled(self, monkeypatch, caplog):
        monkeypatch.setattr(hvs, "_HOST", _LOOPBACK)
        _server = HttpVideoServer(_MockConfig(**{"http_video.log_connections": False}))
        _server.start()
        try:
            with caplog.at_level(logging.INFO):
                self._connect_and_leave(_server)
                time.sleep(0.2)             # tiempo de sobra para que el hilo loguee
                assert not _logged(caplog, "Cliente")
        finally:
            _server.stop()

    def test_the_option_is_read_at_each_event(self, server, caplog):
        """Se lee en el momento: apagarla en caliente calla la desconexión."""
        with caplog.at_level(logging.INFO):
            _sock = _open_stream(server, "/camera_1/raw")
            assert _wait_until(lambda: _logged(caplog, "Cliente conectado"))

            server._config.set("http_video.log_connections", False)
            _reset(_sock)
            assert _wait_until(lambda: not server.has_raw_clients("camera_1"))
            time.sleep(0.2)
            assert not _logged(caplog, "Cliente desconectado")

    def test_the_lifecycle_lines_are_not_affected(self, monkeypatch, caplog):
        """Que el servidor arrancó y se detuvo se dice siempre: no es ruido por cliente."""
        monkeypatch.setattr(hvs, "_HOST", _LOOPBACK)
        with caplog.at_level(logging.INFO):
            _server = HttpVideoServer(_MockConfig(**{"http_video.log_connections": False}))
            _server.start()
            _server.stop()
        assert _logged(caplog, "Servidor activo")
        assert _logged(caplog, "Servidor detenido")
