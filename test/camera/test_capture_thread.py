"""Tests del hilo de captura: lectura de su propia config, conexión y
reconexión, entrega de frames, telemetría sin retoques y parada limpia.

El driver es un doble programable: lo que se verifica acá es el bucle del hilo,
no el hardware. Las constantes de tiempo del módulo se achican para que los
tests corran en milisegundos."""

import logging
import os
import sys
import threading
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt


import system.camera.capture_thread as ct
from system.camera.capture_thread import CaptureThread
from tools.camera.abstract_driver import AbstractCameraDriver

_SLOT = "camera_1"
_WAIT_TIMEOUT_MS = 3000
_SETTLE_S = 0.3          # tiempo de corrida de un test que necesita ver el bucle girar

_STATUS = {"connected": True, "capture_enabled": True, "temperature": 41.5,
           "fps_estimated": 7.5}


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {f"cameras.{_SLOT}": {"driver": "mock", "enabled": True}}
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _FakeDriver(AbstractCameraDriver):
    """
    Driver programable: cada test decide cuándo conecta, qué entrega y qué rompe.

    Cuenta las llamadas para poder afirmar sobre lo que el hilo hizo con él.
    """

    def __init__(self, *, connect_failures: int = 0, status: dict | None = None,
                 yields_frames: bool = True, raise_on_frame_after: int | None = None,
                 raise_on_status_after: int | None = None, is_config_error: bool = False):
        super().__init__({})
        self._connect_failures = connect_failures
        self._status = dict(status if status is not None else _STATUS)
        self._yields_frames = yields_frames
        self._raise_on_frame_after = raise_on_frame_after
        self._raise_on_status_after = raise_on_status_after
        self.is_config_error = is_config_error

        self.connect_calls = 0
        self.disconnect_calls = 0
        self.frame_calls = 0
        self.status_calls = 0

    def connect(self) -> bool:
        self.connect_calls += 1
        if self.connect_calls <= self._connect_failures:
            return False
        self.is_connected = True
        return True

    def disconnect(self):
        self.disconnect_calls += 1
        self.is_connected = False

    def get_frame(self, timeout_ms: int = 500) -> np.ndarray | None:
        self.frame_calls += 1
        if self._raise_on_frame_after is not None and self.frame_calls > self._raise_on_frame_after:
            raise RuntimeError("bus caído")
        # Ritmo de cámara: sin esto el bucle gira decenas de miles de veces por test.
        time.sleep(0.002)
        if not self._yields_frames:
            return None
        return np.zeros((4, 4, 3), np.uint8)

    def get_status(self) -> dict:
        self.status_calls += 1
        if self._raise_on_status_after is not None and self.status_calls > self._raise_on_status_after:
            raise RuntimeError("nodemap caído")
        return {**self._status, "connected": self.is_connected}


class _Collector:
    """Junta lo que emiten las señales desde el hilo de captura."""

    def __init__(self):
        self._lock = threading.Lock()
        self.frames = []
        self.statuses = []

    def on_frame(self, frame, camera_slot: str):
        with self._lock:
            self.frames.append((frame, camera_slot))

    def on_status(self, status, camera_slot: str):
        with self._lock:
            self.statuses.append((status, camera_slot))

    def frame_count(self) -> int:
        with self._lock:
            return len(self.frames)

    def status_count(self) -> int:
        with self._lock:
            return len(self.statuses)


@pytest.fixture(autouse=True)
def fast_timings(monkeypatch):
    """Las esperas reales del módulo son de segundos; acá sobran milisegundos."""
    monkeypatch.setattr(ct, "_STATUS_INTERVAL_S", 0.05)
    monkeypatch.setattr(ct, "_RECONNECT_DELAY_MS", 100)
    monkeypatch.setattr(ct, "_ERROR_DELAY_MS", 20)
    monkeypatch.setattr(ct, "_SLEEP_STEP_MS", 10)
    monkeypatch.setattr(ct, "_IDLE_SLEEP_MS", 1)
    monkeypatch.setattr(ct, "_DISABLED_POLL_MS", 20)


def _thread(monkeypatch, driver: _FakeDriver, slot: str = _SLOT) -> CaptureThread:
    """Hilo con el driver ya decidido: la fábrica no se ejercita acá."""
    monkeypatch.setattr(ct, "create_camera", lambda config_cam: driver)
    return CaptureThread(_MockConfig(), slot)


def _collected(thread: CaptureThread) -> _Collector:
    """
    Conecta las señales en directo: en la app las entrega el event loop de Qt, que
    en los tests no corre. Los slots pasan a ejecutarse en el hilo de captura, que
    es justo lo que hay que mirar acá.
    """
    collector = _Collector()
    thread.frame_ready.connect(collector.on_frame, Qt.ConnectionType.DirectConnection)
    thread.status_updated.connect(collector.on_status, Qt.ConnectionType.DirectConnection)
    return collector


def _run(thread: CaptureThread, seconds: float = _SETTLE_S) -> bool:
    """Corre el hilo, lo interrumpe y devuelve si paró dentro del timeout."""
    thread.start()
    time.sleep(seconds)
    thread.requestInterruption()
    return thread.wait(_WAIT_TIMEOUT_MS)


def _wait_until(condition, timeout_s: float = 2.0) -> bool:
    """Espera a que `condition()` sea verdadera. Evita sleeps fijos en los tests."""
    deadline_s = time.monotonic() + timeout_s
    while time.monotonic() < deadline_s:
        if condition():
            return True
        time.sleep(0.01)
    return False


def _logged(caplog: pytest.LogCaptureFixture, text: str) -> int:
    return sum(1 for record in caplog.records if text in record.getMessage())


# ── Construcción ─────────────────────────────────────────────────────────────

class TestConstruction:
    def test_the_thread_reads_its_own_camera_section(self, monkeypatch):
        """El hilo pide su slot al config: nadie le mastica el sub-dict."""
        seen = []
        monkeypatch.setattr(ct, "create_camera",
                            lambda config_cam: seen.append(config_cam) or _FakeDriver())
        CaptureThread(_MockConfig(), _SLOT)
        assert seen == [{"driver": "mock", "enabled": True}]

    def test_a_missing_slot_builds_the_driver_with_an_empty_config(self, monkeypatch):
        seen = []
        monkeypatch.setattr(ct, "create_camera",
                            lambda config_cam: seen.append(config_cam) or _FakeDriver())
        CaptureThread(_MockConfig(), "camera_inexistente")
        assert seen == [{}]

    def test_the_slot_is_public_identity(self, monkeypatch):
        assert _thread(monkeypatch, _FakeDriver()).camera_slot == _SLOT

    def test_synthetic_marker_comes_from_the_driver(self, monkeypatch):
        driver = _FakeDriver()
        driver.is_synthetic = True
        assert _thread(monkeypatch, driver).is_synthetic is True

    def test_a_real_driver_is_not_synthetic(self, monkeypatch):
        assert _thread(monkeypatch, _FakeDriver()).is_synthetic is False

    def test_status_is_available_before_starting(self, monkeypatch):
        """Una ventana que abre después de arrancar no tiene que esperar al período."""
        status = _thread(monkeypatch, _FakeDriver()).get_status()
        assert status["temperature"] == 41.5
        assert status["connected"] is False

    def test_get_status_returns_a_copy(self, monkeypatch):
        thread = _thread(monkeypatch, _FakeDriver())
        thread.get_status()["temperature"] = -1
        assert thread.get_status()["temperature"] == 41.5


# ── Config inválida ──────────────────────────────────────────────────────────

class TestConfigError:
    """La cámara mal configurada no se reintenta a ciegas: se informa el motivo."""

    def _broken(self, monkeypatch) -> tuple[CaptureThread, _FakeDriver]:
        driver = _FakeDriver(
            is_config_error=True,
            status={"connected": False, "capture_enabled": False, "temperature": 0.0,
                    "fps_estimated": 0.0, "error": "Driver 'no_existe' no registrado en el core"},
        )
        return _thread(monkeypatch, driver), driver

    def test_the_reason_comes_from_the_driver_status(self, monkeypatch):
        """El motivo lo publica el driver en `error`; no es un atributo suelto."""
        thread, _ = self._broken(monkeypatch)
        assert thread.config_error == "Driver 'no_existe' no registrado en el core"

    def test_a_valid_camera_has_no_config_error(self, monkeypatch):
        assert _thread(monkeypatch, _FakeDriver()).config_error is None

    def test_a_driver_with_an_error_but_no_config_error_flag_is_not_a_config_error(self, monkeypatch):
        """Un `error` transitorio en el status no es una config rota."""
        driver = _FakeDriver(status={**_STATUS, "error": "cámara sin ruta"})
        assert _thread(monkeypatch, driver).config_error is None

    def test_connect_is_never_attempted(self, monkeypatch):
        thread, driver = self._broken(monkeypatch)
        assert _run(thread)
        assert driver.connect_calls == 0
        assert driver.frame_calls == 0

    def test_the_error_is_logged_once(self, monkeypatch, caplog):
        """Reintentar cada 100 ms inundaría el log con el mismo error."""
        thread, _ = self._broken(monkeypatch)
        with caplog.at_level(logging.ERROR, logger="app"):
            assert _run(thread, 0.5)
        assert _logged(caplog, "no registrado en el core") == 1

    def test_the_status_keeps_carrying_the_error(self, monkeypatch):
        thread, _ = self._broken(monkeypatch)
        collector = _collected(thread)
        assert _run(thread)
        assert collector.status_count() >= 1
        assert collector.statuses[-1][0]["error"] == "Driver 'no_existe' no registrado en el core"


# ── Conexión y reconexión ────────────────────────────────────────────────────

class TestConnection:
    def test_the_thread_connects_by_itself(self, monkeypatch):
        driver = _FakeDriver()
        thread = _thread(monkeypatch, driver)
        assert _run(thread, 0.1)
        assert driver.connect_calls == 1

    def test_a_failed_connect_is_retried_until_it_works(self, monkeypatch):
        driver = _FakeDriver(connect_failures=2)
        thread = _thread(monkeypatch, driver)
        collector = _collected(thread)
        thread.start()
        got_frames = _wait_until(lambda: collector.frame_count() > 0)
        thread.requestInterruption()
        assert thread.wait(_WAIT_TIMEOUT_MS)
        assert got_frames
        assert driver.connect_calls == 3

    def test_a_disconnected_camera_still_reports_status(self, monkeypatch):
        """Sin esto la UI y el PLC no se enteran de que la cámara está caída."""
        driver = _FakeDriver(connect_failures=99)
        thread = _thread(monkeypatch, driver)
        collector = _collected(thread)
        assert _run(thread)
        assert collector.status_count() >= 2
        assert collector.statuses[-1][0]["connected"] is False

    def test_the_driver_is_released_on_shutdown(self, monkeypatch):
        driver = _FakeDriver()
        assert _run(_thread(monkeypatch, driver), 0.1)
        assert driver.disconnect_calls >= 1
        assert driver.is_connected is False

    def test_a_long_retry_does_not_block_the_shutdown(self, monkeypatch):
        """El msleep monolítico sobrevivía al wait() y Qt abortaba el proceso."""
        monkeypatch.setattr(ct, "_RECONNECT_DELAY_MS", 5000)
        thread = _thread(monkeypatch, _FakeDriver(connect_failures=99))
        thread.start()
        time.sleep(0.1)
        thread.requestInterruption()
        started_s = time.monotonic()
        assert thread.wait(_WAIT_TIMEOUT_MS)
        assert time.monotonic() - started_s < 1.0


# ── Entrega de frames ────────────────────────────────────────────────────────

class TestFrames:
    def test_frames_travel_with_the_slot(self, monkeypatch):
        thread = _thread(monkeypatch, _FakeDriver())
        collector = _collected(thread)
        assert _run(thread)
        assert collector.frame_count() > 0
        frame, camera_slot = collector.frames[0]
        assert camera_slot == _SLOT
        assert frame.shape == (4, 4, 3)

    def test_a_driver_without_frames_emits_nothing(self, monkeypatch):
        thread = _thread(monkeypatch, _FakeDriver(yields_frames=False))
        collector = _collected(thread)
        assert _run(thread)
        assert collector.frame_count() == 0
        assert collector.status_count() >= 1

    def test_a_disabled_camera_polls_slowly(self, monkeypatch):
        """get_frame() sale enseguida y sin tocar el hardware: no hay que insistir."""
        driver = _FakeDriver(yields_frames=False,
                             status={**_STATUS, "capture_enabled": False})
        thread = _thread(monkeypatch, driver)
        thread._last_status = driver.get_status()
        assert thread._idle_sleep_ms() == ct._DISABLED_POLL_MS

    def test_a_capturing_camera_polls_fast(self, monkeypatch):
        thread = _thread(monkeypatch, _FakeDriver())
        thread._last_status = {"capture_enabled": True}
        assert thread._idle_sleep_ms() == ct._IDLE_SLEEP_MS

    def test_an_unknown_capture_state_polls_fast(self, monkeypatch):
        thread = _thread(monkeypatch, _FakeDriver())
        thread._last_status = {}
        assert thread._idle_sleep_ms() == ct._IDLE_SLEEP_MS


# ── Fallas del driver ────────────────────────────────────────────────────────

class TestDriverFailures:
    def test_an_exception_in_get_frame_forces_a_reconnection(self, monkeypatch):
        driver = _FakeDriver(raise_on_frame_after=1)
        thread = _thread(monkeypatch, driver)
        assert _run(thread)
        # disconnect() y no un `is_connected = False` escrito desde afuera: es lo
        # único que además libera lo que el driver tenga abierto.
        assert driver.disconnect_calls >= 2
        assert driver.connect_calls >= 2

    def test_the_thread_survives_an_exception_in_get_frame(self, monkeypatch):
        thread = _thread(monkeypatch, _FakeDriver(raise_on_frame_after=1))
        collector = _collected(thread)
        assert _run(thread)
        assert collector.status_count() >= 1
        assert thread.isFinished()

    def test_an_exception_in_get_status_does_not_kill_the_thread(self, monkeypatch):
        driver = _FakeDriver(raise_on_status_after=2)
        thread = _thread(monkeypatch, driver)
        collector = _collected(thread)
        assert _run(thread)
        assert thread.isFinished()
        assert collector.frame_count() > 0

    def test_a_status_that_fails_is_not_published(self, monkeypatch):
        """Publicar el último status conocido haría pasar por fresco algo viejo."""
        driver = _FakeDriver(raise_on_status_after=2)
        thread = _thread(monkeypatch, driver)
        collector = _collected(thread)
        assert _run(thread, 0.5)
        assert collector.status_count() <= 1


# ── Telemetría ───────────────────────────────────────────────────────────────

class TestTelemetry:
    def test_the_status_travels_as_the_driver_built_it(self, monkeypatch):
        thread = _thread(monkeypatch, _FakeDriver())
        collector = _collected(thread)
        assert _run(thread)
        status, camera_slot = collector.statuses[-1]
        assert camera_slot == _SLOT
        assert status["temperature"] == 41.5
        assert status["capture_enabled"] is True

    def test_the_measured_fps_is_not_recalculated(self, monkeypatch):
        """El fps lo mide el driver sobre los frames entregados; acá se pasa igual."""
        thread = _thread(monkeypatch, _FakeDriver())
        collector = _collected(thread)
        assert _run(thread)
        assert all(status["fps_estimated"] == 7.5 for status, _ in collector.statuses)

    def test_status_has_the_stable_keys_while_disconnected(self, monkeypatch):
        """El hilo ya no arma su propio dict: `capture_enabled` no puede faltar."""
        thread = _thread(monkeypatch, _FakeDriver(connect_failures=99))
        collector = _collected(thread)
        assert _run(thread)
        assert {"connected", "capture_enabled", "temperature", "fps_estimated"} <= set(
            collector.statuses[-1][0])

    def test_the_last_status_is_the_one_that_was_emitted(self, monkeypatch):
        thread = _thread(monkeypatch, _FakeDriver())
        collector = _collected(thread)
        assert _run(thread)
        assert thread.get_status() == collector.statuses[-1][0]

    def test_telemetry_is_periodic(self, monkeypatch):
        """Un status por frame ahogaría a la UI; uno cada _STATUS_INTERVAL_S alcanza."""
        thread = _thread(monkeypatch, _FakeDriver())
        collector = _collected(thread)
        assert _run(thread, 0.5)
        assert 2 <= collector.status_count() <= 20
        assert collector.frame_count() > collector.status_count()
