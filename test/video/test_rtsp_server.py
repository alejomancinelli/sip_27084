"""Tests del servidor RTSP: armado del pipeline, escalado, resolución de interfaces,
almacén de frames, ritmo del stream y ciclo de vida.

Nada de acá levanta GStreamer: el pipeline se prueba como texto y el ritmo del
stream en el `_FramePacer`, que no lo conoce. La parte que sí depende de GStreamer
se ejercita con `manual_test/rtsp_video/rtsp_streams.py`.
"""

import logging
import socket
import threading
import time
from collections import namedtuple

import numpy as np
import pytest

import system.video.rtsp_server as rtsp
from system.video.rtsp_server import (
    RtspVideoServer,
    _FramePacer,
    _FrameStore,
    _Watchers,
    _build_launch_string,
    _build_stream_urls,
    _compute_scaled_size,
    _mount_path,
    _resolve_addresses,
)

_RAW_1 = ("camera_1", "raw")
_ANNOTATED_1 = ("camera_1", "annotated")
_RAW_2 = ("camera_2", "raw")

_WAIT_TIMEOUT_S = 2.0
_FAST_FPS = 500             # período de 2 ms: el ritmo no interfiere con el test

# psutil devuelve namedtuples con estos dos campos, más los de máscara y broadcast
# que este módulo no mira.
_Addr = namedtuple("_Addr", "family address")

_ETHERNET = _Addr(socket.AF_INET, "192.168.0.10")
_WIFI = _Addr(socket.AF_INET, "10.0.0.5")
_MAC_ONLY = _Addr(socket.AF_PACKET if hasattr(socket, "AF_PACKET") else -1, "aa:bb:cc:dd:ee:ff")


class _MockConfig:
    """ConfigManager mínimo: `get` y `set` con clave punteada, sin archivo ni Lock."""

    # `video.rtsp.log_connections` queda afuera a propósito: así los tests que no lo
    # fijan pasan por el default del módulo.
    def __init__(self, **overrides):
        self._values = {
            "video.rtsp.enabled": True,
            "video.rtsp.port": 8554,
            "video.rtsp.codec": "h264_sw",
            "video.rtsp.fps": 15,
            "video.rtsp.frame_width_px": 0,
            "video.rtsp.net_interfaces": [],
            "cameras": {"camera_1": {}, "camera_2": {}},
        }
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self._values[key] = value


def _frame(marker: int = 1, width_px: int = 64, height_px: int = 48) -> np.ndarray:
    """Frame BGR uniforme, con `marker` como valor: sirve para reconocer cuál es."""
    return np.full((height_px, width_px, 3), marker, np.uint8)


def _marker_of(frame_bgr: np.ndarray) -> int:
    return int(frame_bgr[0, 0, 0])


def _fake_interfaces(monkeypatch, addrs_by_iface: dict):
    monkeypatch.setattr(rtsp.psutil, "net_if_addrs", lambda: addrs_by_iface)


def _no_gstreamer(monkeypatch):
    """Deja el módulo como en un equipo sin GStreamer, esté instalado o no."""
    monkeypatch.setattr(rtsp, "_GSTREAMER_AVAILABLE", False)
    monkeypatch.setattr(rtsp, "_IMPORT_ERROR", "No module named 'gi'")


def _unreachable_interface(monkeypatch):
    """
    Config que pasa el chequeo de GStreamer y falla en la interfaz.

    Es la forma de probar lo que valida `start()` después de GStreamer sin depender
    de tenerlo instalado: la resolución de interfaces corta antes de tocar Gst.
    """
    monkeypatch.setattr(rtsp, "_GSTREAMER_AVAILABLE", True)
    _fake_interfaces(monkeypatch, {"Ethernet": [_ETHERNET]})
    return {"video.rtsp.net_interfaces": ["eth0"]}


def _logged(caplog: pytest.LogCaptureFixture, text: str) -> bool:
    return any(text in record.getMessage() for record in caplog.records)


class TestMountPath:
    def test_the_path_carries_the_slot_and_the_mode(self):
        assert _mount_path(_RAW_1) == "/camera_1/raw"
        assert _mount_path(_ANNOTATED_1) == "/camera_1/annotated"


class TestLaunchString:
    def test_the_frames_enter_through_a_live_appsrc(self):
        """appsrc con is-live: el ritmo lo pone quien empuja, no el pipeline."""
        launch = _build_launch_string("h264_sw")
        assert launch.startswith(f"appsrc name={rtsp._APPSRC_NAME} ")
        assert "is-live=true" in launch
        assert "format=GST_FORMAT_TIME" in launch

    def test_the_scale_filter_is_named_and_starts_open(self):
        """Nace sin caps: el tamaño se fija con el primer frame, no en el config."""
        launch = _build_launch_string("h264_sw")
        assert f"! videoscale ! capsfilter name={rtsp._SCALE_CAPS_NAME} " in launch
        assert "width=" not in launch

    @pytest.mark.parametrize("codec, encoder", [
        ("h264_sw", "x264enc"),
        ("h264_hw", "nvv4l2h264enc"),
        ("h265_sw", "x265enc"),
        ("h265_hw", "nvv4l2h265enc"),
        ("mjpeg", "jpegenc"),
    ])
    def test_each_codec_picks_its_encoder(self, codec, encoder):
        assert encoder in _build_launch_string(codec)

    @pytest.mark.parametrize("codec", list(rtsp._CODEC_PIPELINES))
    def test_every_codec_ends_in_pay0(self, codec):
        """gst-rtsp-server busca la salida por ese nombre: sin pay0 no hay stream."""
        assert "name=pay0" in _build_launch_string(codec)

    def test_an_unknown_codec_falls_back_and_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            launch = _build_launch_string("av1_hw")
        assert launch == _build_launch_string(rtsp._DEFAULT_CODEC)
        assert _logged(caplog, "Codec 'av1_hw' desconocido")

    def test_the_scaling_comes_before_the_encoder(self):
        """Escalar después de codificar no ahorraría nada: el costo es el encoder."""
        launch = _build_launch_string("h264_sw")
        assert launch.index("videoscale") < launch.index("x264enc")


class TestScaledSize:
    def test_no_target_width_leaves_the_native_resolution(self):
        assert _compute_scaled_size(1920, 1200, 0) is None

    def test_a_negative_target_width_leaves_the_native_resolution(self):
        assert _compute_scaled_size(1920, 1200, -1) is None

    def test_never_upscales(self):
        assert _compute_scaled_size(640, 480, 960) is None

    def test_the_native_width_is_not_scaled(self):
        assert _compute_scaled_size(960, 600, 960) is None

    def test_keeps_the_aspect_ratio(self):
        assert _compute_scaled_size(1920, 1200, 960) == (960, 600)

    def test_both_sides_are_even(self):
        """El submuestreo de croma de I420 no acepta un lado impar."""
        scaled_width_px, scaled_height_px = _compute_scaled_size(1920, 1235, 641)
        assert scaled_width_px % 2 == 0
        assert scaled_height_px % 2 == 0

    def test_an_extreme_aspect_ratio_does_not_collapse_the_height(self):
        # Sin el max(2, ...) el alto redondearía a 0 y el pipeline no negociaría.
        assert _compute_scaled_size(1920, 3, 100)[1] >= 2


class TestResolveAddresses:
    def test_no_interfaces_listens_on_all(self):
        assert _resolve_addresses([]) == [rtsp._ALL_ADDRESSES]

    def test_none_listens_on_all(self):
        assert _resolve_addresses(None) == [rtsp._ALL_ADDRESSES]

    def test_a_configured_interface_resolves_to_its_ipv4(self, monkeypatch):
        _fake_interfaces(monkeypatch, {"Ethernet": [_MAC_ONLY, _ETHERNET]})
        assert _resolve_addresses(["Ethernet"]) == ["192.168.0.10"]

    def test_one_address_per_interface_in_the_configured_order(self, monkeypatch):
        _fake_interfaces(monkeypatch, {"Ethernet": [_ETHERNET], "Wi-Fi": [_WIFI]})
        assert _resolve_addresses(["Wi-Fi", "Ethernet"]) == ["10.0.0.5", "192.168.0.10"]

    def test_an_unknown_interface_is_skipped_and_warned(self, monkeypatch, caplog):
        """Los nombres no se cross-portean: 'eth0' en Jetson, 'Ethernet' en Windows."""
        _fake_interfaces(monkeypatch, {"Ethernet": [_ETHERNET]})
        with caplog.at_level(logging.WARNING):
            assert _resolve_addresses(["eth0", "Ethernet"]) == ["192.168.0.10"]
        assert _logged(caplog, "La interfaz 'eth0' no existe")

    def test_an_interface_without_ipv4_is_skipped(self, monkeypatch):
        _fake_interfaces(monkeypatch, {"Ethernet": [_MAC_ONLY]})
        assert _resolve_addresses(["Ethernet"]) == []

    def test_two_interfaces_on_the_same_address_bind_once(self, monkeypatch):
        """Dos servidores en la misma dirección y puerto: el segundo no podría atarse."""
        _fake_interfaces(monkeypatch, {"Ethernet": [_ETHERNET], "Alias": [_ETHERNET]})
        assert _resolve_addresses(["Ethernet", "Alias"]) == ["192.168.0.10"]


class TestStreamUrls:
    def test_one_url_per_address_slot_and_mode(self):
        urls = _build_stream_urls(["10.0.0.5"], 8554, ("camera_1", "camera_2"))
        assert urls == [
            "rtsp://10.0.0.5:8554/camera_1/raw",
            "rtsp://10.0.0.5:8554/camera_1/annotated",
            "rtsp://10.0.0.5:8554/camera_2/raw",
            "rtsp://10.0.0.5:8554/camera_2/annotated",
        ]

    def test_listening_on_all_reports_the_real_addresses(self, monkeypatch):
        """`0.0.0.0` no se puede pegar en un reproductor."""
        monkeypatch.setattr(rtsp, "_list_host_addresses", lambda: ["192.168.0.10"])
        urls = _build_stream_urls([rtsp._ALL_ADDRESSES], 8554, ("camera_1",))
        assert urls == [
            "rtsp://192.168.0.10:8554/camera_1/raw",
            "rtsp://192.168.0.10:8554/camera_1/annotated",
        ]

    def test_a_host_without_addresses_still_reports_the_port(self, monkeypatch):
        monkeypatch.setattr(rtsp, "_list_host_addresses", lambda: [])
        assert _build_stream_urls([rtsp._ALL_ADDRESSES], 8554, ("camera_1",)) == [
            "rtsp://0.0.0.0:8554/camera_1/raw",
            "rtsp://0.0.0.0:8554/camera_1/annotated",
        ]

    def test_no_cameras_means_no_urls(self):
        assert _build_stream_urls(["10.0.0.5"], 8554, ()) == []


class TestFrameStore:
    def test_store_starts_empty(self):
        assert _FrameStore().get(_RAW_1) == (None, 0)

    def test_update_keeps_the_frame_as_it_came(self):
        """Acá no se codifica ni se toca el frame: eso lo hace el pipeline."""
        store = _FrameStore()
        frame_bgr = _frame()
        store.update(_RAW_1, frame_bgr)
        assert store.get(_RAW_1)[0] is frame_bgr

    def test_only_the_last_frame_survives(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame(1))
        store.update(_RAW_1, _frame(2))
        assert _marker_of(store.get(_RAW_1)[0]) == 2

    def test_each_update_increments_the_sequence(self):
        store = _FrameStore()
        seqs = []
        for _ in range(3):
            store.update(_RAW_1, _frame())
            seqs.append(store.get(_RAW_1)[1])
        assert seqs == [1, 2, 3]

    def test_each_stream_is_independent(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame())
        assert store.get(_ANNOTATED_1) == (None, 0)
        assert store.get(_RAW_2) == (None, 0)

    def test_clear_discards_the_last_frame(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame())
        store.clear(_RAW_1)
        assert store.get(_RAW_1)[0] is None

    def test_clear_increments_the_sequence(self):
        """Si no cambiara, el pacer que ya vio ese seq no notaría que se vació."""
        store = _FrameStore()
        store.update(_RAW_1, _frame())
        before = store.get(_RAW_1)[1]
        store.clear(_RAW_1)
        assert store.get(_RAW_1)[1] > before

    def test_clear_only_touches_its_own_stream(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame())
        store.update(_ANNOTATED_1, _frame())
        store.clear(_RAW_1)
        assert store.get(_ANNOTATED_1)[0] is not None

    def test_wakes_up_when_the_next_frame_arrives(self):
        store = _FrameStore()
        threading.Timer(0.05, lambda: store.update(_RAW_1, _frame())).start()
        started_s = time.monotonic()
        store.wait_for_change(_RAW_1, 0, timeout_s=_WAIT_TIMEOUT_S)
        assert time.monotonic() - started_s < 1.0
        assert store.get(_RAW_1)[1] == 1

    def test_a_frame_pushed_between_get_and_wait_is_not_missed(self):
        """La razón de contar secuencias: con un Event, este aviso se perdía."""
        store = _FrameStore()
        _, seq = store.get(_RAW_1)
        store.update(_RAW_1, _frame())
        started_s = time.monotonic()
        store.wait_for_change(_RAW_1, seq, timeout_s=_WAIT_TIMEOUT_S)
        assert time.monotonic() - started_s < 0.5

    def test_another_stream_does_not_end_the_wait_early(self):
        store = _FrameStore()
        store.update(_RAW_2, _frame())
        store.wait_for_change(_RAW_1, 0, timeout_s=0.05)
        assert store.get(_RAW_1) == (None, 0)

    def test_close_wakes_every_waiting_pipeline(self):
        store = _FrameStore()
        woken = []
        lock = threading.Lock()

        def wait_once():
            store.wait_for_change(_RAW_1, 0, timeout_s=_WAIT_TIMEOUT_S)
            with lock:
                woken.append(store.is_closed)

        waiters = [threading.Thread(target=wait_once) for _ in range(3)]
        for waiter in waiters:
            waiter.start()
        time.sleep(0.1)             # que los tres lleguen a la espera antes del close
        store.close()
        for waiter in waiters:
            waiter.join(timeout=_WAIT_TIMEOUT_S)
        assert woken == [True, True, True]

    def test_a_closed_store_does_not_wait(self):
        store = _FrameStore()
        store.close()
        started_s = time.monotonic()
        store.wait_for_change(_RAW_1, 0, timeout_s=_WAIT_TIMEOUT_S)
        assert time.monotonic() - started_s < 0.5


class TestFramePacer:
    """El ritmo del stream: qué frame sale, cuándo, y con qué marca de tiempo."""

    def test_blocks_until_the_first_frame_arrives(self):
        """Un cliente que se conecta antes del primer frame espera, no falla."""
        store = _FrameStore()
        pacer = _FramePacer(store, _RAW_1, _FAST_FPS)
        threading.Timer(0.05, lambda: store.update(_RAW_1, _frame(7))).start()
        timed = pacer.get_next()
        assert _marker_of(timed.frame_bgr) == 7

    def test_closing_the_store_ends_the_wait(self):
        """Sin esto el pipeline queda esperando para siempre un frame que nadie empuja."""
        store = _FrameStore()
        pacer = _FramePacer(store, _RAW_1, _FAST_FPS)
        threading.Timer(0.05, store.close).start()
        started_s = time.monotonic()
        assert pacer.get_next() is None
        assert time.monotonic() - started_s < 1.0

    def test_a_closed_store_returns_none_at_once(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame())
        store.close()
        assert _FramePacer(store, _RAW_1, _FAST_FPS).get_next() is None

    def test_the_duration_matches_the_declared_fps(self):
        store = _FrameStore()
        store.update(_RAW_1, _frame())
        assert _FramePacer(store, _RAW_1, 25).get_next().duration_ns == 40_000_000

    def test_the_pts_advances_one_duration_per_frame(self):
        """Framerate fijo: es lo que declara el caps y lo que espera el reproductor."""
        store = _FrameStore()
        pacer = _FramePacer(store, _RAW_1, _FAST_FPS)
        stamps = []
        for marker in range(3):
            store.update(_RAW_1, _frame(marker))
            stamps.append(pacer.get_next())
        duration_ns = stamps[0].duration_ns
        assert [timed.pts_ns for timed in stamps] == [0, duration_ns, 2 * duration_ns]

    def test_only_the_newest_frame_is_sent(self):
        """Always-fresh: lo que se acumuló mientras se codificaba no se emite atrasado."""
        store = _FrameStore()
        pacer = _FramePacer(store, _RAW_1, _FAST_FPS)
        for marker in (1, 2, 3):
            store.update(_RAW_1, _frame(marker))
        assert _marker_of(pacer.get_next().frame_bgr) == 3

    def test_the_same_frame_is_not_sent_twice(self, monkeypatch):
        """Sin frame nuevo no se empuja: repetir cuesta encoder y ancho de banda."""
        monkeypatch.setattr(rtsp, "_KEEPALIVE_S", 30.0)
        store = _FrameStore()
        pacer = _FramePacer(store, _RAW_1, _FAST_FPS)
        store.update(_RAW_1, _frame())
        assert pacer.get_next() is not None

        threading.Timer(0.15, store.close).start()
        assert pacer.get_next() is None      # esperó al siguiente, no reenvió

    def test_the_last_frame_is_resent_after_the_keepalive(self, monkeypatch):
        """Una cámara que se cayó tiene que dejar al reproductor con imagen, no colgado."""
        monkeypatch.setattr(rtsp, "_KEEPALIVE_S", 0.05)
        store = _FrameStore()
        pacer = _FramePacer(store, _RAW_1, _FAST_FPS)
        store.update(_RAW_1, _frame(4))
        first = pacer.get_next()
        second = pacer.get_next()
        assert _marker_of(second.frame_bgr) == 4
        assert second.pts_ns == first.pts_ns + first.duration_ns

    def test_a_faster_camera_does_not_speed_up_the_stream(self):
        """El ritmo lo pone el `fps` declarado: de más frames se descartan."""
        store = _FrameStore()
        pacer = _FramePacer(store, _RAW_1, 20)          # período de 50 ms
        started_s = time.monotonic()
        for marker in range(3):
            store.update(_RAW_1, _frame(marker))
            assert pacer.get_next() is not None
        # El primero sale enseguida; los otros dos esperan su período.
        assert time.monotonic() - started_s >= 0.09

    def test_each_stream_has_its_own_pacer(self):
        store = _FrameStore()
        raw = _FramePacer(store, _RAW_1, _FAST_FPS)
        annotated = _FramePacer(store, _ANNOTATED_1, _FAST_FPS)
        store.update(_RAW_1, _frame(1))
        store.update(_ANNOTATED_1, _frame(2))
        assert _marker_of(raw.get_next().frame_bgr) == 1
        assert _marker_of(annotated.get_next().frame_bgr) == 2


class TestLifecycle:
    """Sin GStreamer el módulo no levanta nada y expone la misma API."""

    def test_disabled_in_config_is_not_an_error(self, monkeypatch):
        _no_gstreamer(monkeypatch)
        server = RtspVideoServer(_MockConfig(**{"video.rtsp.enabled": False}))
        server.start()
        assert server.status == "disabled"
        assert server.is_running is False

    def test_enabled_without_gstreamer_reports_error(self, monkeypatch, caplog):
        _no_gstreamer(monkeypatch)
        server = RtspVideoServer(_MockConfig())
        with caplog.at_level(logging.ERROR):
            server.start()
        assert server.status == "error"
        assert server.is_running is False
        assert _logged(caplog, "GStreamer no está disponible")

    def test_no_reachable_interface_reports_error(self, monkeypatch, caplog):
        """Publicar por una interfaz que no está no es escuchar en todas."""
        server = RtspVideoServer(_MockConfig(**_unreachable_interface(monkeypatch)))
        with caplog.at_level(logging.ERROR):
            server.start()
        assert server.status == "error"
        assert server.is_running is False
        assert _logged(caplog, "Ninguna interfaz")

    def test_the_disabled_check_comes_first(self, monkeypatch, caplog):
        """Con el servidor apagado, que falte GStreamer no es un error que reportar."""
        _no_gstreamer(monkeypatch)
        server = RtspVideoServer(_MockConfig(**{"video.rtsp.enabled": False}))
        with caplog.at_level(logging.ERROR):
            server.start()
        assert not _logged(caplog, "GStreamer")

    def test_stop_without_start_does_nothing(self, monkeypatch):
        _no_gstreamer(monkeypatch)
        server = RtspVideoServer(_MockConfig())
        server.stop()
        assert server.status == "disabled"

    def test_stop_after_a_failed_start_does_nothing(self, monkeypatch):
        _no_gstreamer(monkeypatch)
        server = RtspVideoServer(_MockConfig())
        server.start()
        server.stop()
        assert server.is_running is False

    def test_no_clients_and_no_urls_when_inactive(self, monkeypatch):
        _no_gstreamer(monkeypatch)
        server = RtspVideoServer(_MockConfig())
        server.start()
        assert server.get_client_count() == 0
        assert server.get_stream_urls() == []

    def test_pushing_without_a_server_is_a_no_op(self, monkeypatch):
        """Los hilos de captura empujan igual: no preguntan si el servidor levantó."""
        _no_gstreamer(monkeypatch)
        server = RtspVideoServer(_MockConfig())
        server.start()
        server.push_raw("camera_1", _frame())
        server.push_annotated("camera_1", _frame())
        assert server._store.get(_RAW_1) == (None, 0)
        assert server._store.get(_ANNOTATED_1) == (None, 0)


class TestWatchers:
    """Las altas y bajas de media son lo único que dice quién está mirando qué."""

    def test_nothing_registered_counts_zero(self):
        assert _Watchers().count(_RAW_1) == 0

    def test_registering_counts_a_watcher(self):
        watchers = _Watchers()
        assert watchers.register(_RAW_1) == 1
        assert watchers.count(_RAW_1) == 1

    def test_the_count_is_per_stream(self):
        watchers = _Watchers()
        watchers.register(_RAW_1)
        assert watchers.count(_ANNOTATED_1) == 0
        assert watchers.count(_RAW_2) == 0

    def test_the_same_stream_on_two_interfaces_counts_twice(self):
        """El mismo endpoint publicado por dos interfaces tiene una media en cada una."""
        watchers = _Watchers()
        watchers.register(_RAW_1)
        assert watchers.register(_RAW_1) == 2
        assert watchers.unregister(_RAW_1) == 1
        assert watchers.count(_RAW_1) == 1

    def test_unregistering_the_last_one_leaves_it_empty(self):
        watchers = _Watchers()
        watchers.register(_RAW_1)
        assert watchers.unregister(_RAW_1) == 0
        assert watchers.count(_RAW_1) == 0

    def test_unregistering_an_unknown_stream_does_not_go_negative(self):
        """Una baja sin alta no puede dejar el contador en un número que nunca vuelve a 0."""
        watchers = _Watchers()
        assert watchers.unregister(_RAW_1) == 0
        watchers.register(_RAW_1)
        assert watchers.count(_RAW_1) == 1


class TestPushGating:
    """Lo que ahorra el gating: un stream que nadie mira no retiene frames."""

    def _active_server(self, monkeypatch, *watched: tuple) -> RtspVideoServer:
        """Servidor sin GStreamer, con una media simulada en cada stream de `watched`."""
        _no_gstreamer(monkeypatch)
        server = RtspVideoServer(_MockConfig())
        server._is_active = True            # start() real necesitaría GStreamer
        for key in watched:
            server._watchers.register(key)
        return server

    def test_nothing_is_stored_without_watchers(self, monkeypatch):
        server = self._active_server(monkeypatch)
        server.push_raw("camera_1", _frame())
        server.push_annotated("camera_1", _frame())
        assert server._store.get(_RAW_1) == (None, 0)
        assert server._store.get(_ANNOTATED_1) == (None, 0)

    def test_a_watched_stream_stores_what_it_receives(self, monkeypatch):
        server = self._active_server(monkeypatch, _RAW_1, _ANNOTATED_1)
        server.push_raw("camera_1", _frame(1))
        server.push_annotated("camera_1", _frame(2))
        assert _marker_of(server._store.get(_RAW_1)[0]) == 1
        assert _marker_of(server._store.get(_ANNOTATED_1)[0]) == 2

    def test_the_gating_is_per_stream(self, monkeypatch):
        # El caso real: cuatro streams publicados, uno solo mirado.
        server = self._active_server(monkeypatch, _RAW_1)
        for slot in ("camera_1", "camera_2"):
            server.push_raw(slot, _frame())
            server.push_annotated(slot, _frame())
        assert server._store.get(_RAW_1)[0] is not None
        for key in (_ANNOTATED_1, _RAW_2, ("camera_2", "annotated")):
            assert server._store.get(key)[0] is None

    def test_the_client_predicates_are_per_stream_and_mode(self, monkeypatch):
        """Sirven para no dibujar anotaciones que nadie va a ver."""
        server = self._active_server(monkeypatch, _RAW_1, ("camera_2", "annotated"))
        assert server.has_raw_clients("camera_1") is True
        assert server.has_annotated_clients("camera_1") is False
        assert server.has_raw_clients("camera_2") is False
        assert server.has_annotated_clients("camera_2") is True

    def test_a_none_frame_is_ignored(self, monkeypatch):
        server = self._active_server(monkeypatch, _RAW_1)
        server.push_raw("camera_1", None)
        assert server._store.get(_RAW_1) == (None, 0)

    def test_an_inactive_server_does_not_store(self, monkeypatch):
        _no_gstreamer(monkeypatch)
        server = RtspVideoServer(_MockConfig())
        server._watchers.register(_RAW_1)
        server.push_raw("camera_1", _frame())
        assert server._store.get(_RAW_1) == (None, 0)

    def test_stopping_leaves_no_one_watching(self, monkeypatch):
        """Un servidor detenido no puede seguir diciendo que alguien mira sus streams."""
        server = self._active_server(monkeypatch, _RAW_1)
        assert server.has_raw_clients("camera_1") is True

        # Un hilo que ya terminó: sin hilo, stop() sale antes de limpiar nada.
        finished = threading.Thread(target=lambda: None)
        finished.start()
        finished.join()
        server._thread = finished

        server.stop()
        assert server.has_raw_clients("camera_1") is False

    def test_the_last_watcher_leaving_drops_the_stored_frame(self, monkeypatch):
        """Es lo que hace `clear`: sin nadie mirando no hay para quién guardarlo."""
        server = self._active_server(monkeypatch, _RAW_1)
        server.push_raw("camera_1", _frame())
        assert server._store.get(_RAW_1)[0] is not None

        server._watchers.unregister(_RAW_1)
        server._store.clear(_RAW_1)
        assert server._store.get(_RAW_1)[0] is None
        server.push_raw("camera_1", _frame())
        assert server._store.get(_RAW_1)[0] is None


class TestSlots:
    def test_slots_keep_the_config_order(self):
        server = RtspVideoServer(_MockConfig())
        assert server._read_camera_slots() == ("camera_1", "camera_2")

    def test_slots_include_disabled_cameras(self):
        """`enabled` cambia en caliente: el endpoint no puede aparecer y desaparecer."""
        server = RtspVideoServer(_MockConfig(cameras={"camera_1": {"enabled": False}}))
        assert server._read_camera_slots() == ("camera_1",)

    def test_no_cameras_is_warned_and_does_not_stop_the_start(self, monkeypatch, caplog):
        overrides = _unreachable_interface(monkeypatch)
        server = RtspVideoServer(_MockConfig(cameras={}, **overrides))
        with caplog.at_level(logging.WARNING):
            server.start()
        assert server._read_camera_slots() == ()
        assert _logged(caplog, "No hay cámaras en el config")
