"""Tests del hilo de persistencia: armado del punto, cola que no bloquea,
ventana de publicación, aislamiento de un backend roto y vaciado al cerrar.

Los backends son dobles: lo que se verifica es el hilo, no InfluxDB ni MQTT."""

import logging
import os
import queue
import sys
import threading
import time

import pytest


import system.telemetry.persistence as pers
from system.telemetry.backends import (
    STATUS_CONNECTED,
    STATUS_DISABLED,
    AbstractTelemetryBackend,
)
from system.telemetry.persistence import PersistenceThread

_WAIT_TIMEOUT_MS = 3000
_FIELDS = {"temperature": 41.2}


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock.

    El hilo no lee ninguna clave; esto es lo que necesitan los backends que arma
    por defecto."""

    def __init__(self, **overrides):
        self._values = dict(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _FakeBackend(AbstractTelemetryBackend):
    """
    Backend que anota lo que le piden y, si se le indica, rompe.

    Implementa el contrato de verdad: si la interfaz cambia, estos tests lo dicen
    antes que la app.
    """

    def __init__(self, service_name: str = "fake", *, fails_on: str = "",
                 setup_gate: threading.Event | None = None):
        super().__init__(_MockConfig())
        self.service_name = service_name
        self._fails_on = fails_on
        # Traba el arranque del hilo hasta que el test decide: sin eso no hay forma
        # de dejar puntos encolados sin que la ventana en curso se los lleve.
        self._setup_gate = setup_gate
        self.setups = 0
        self.closes = 0
        self.batches = []
        self.calls = []          # orden en el que el hilo usó el backend

    def setup(self):
        self.calls.append("setup")
        if self._setup_gate is not None:
            self._setup_gate.wait(timeout=2.0)
        if self._fails_on == "setup":
            raise RuntimeError("backend roto")
        self.setups += 1
        self._status = STATUS_CONNECTED

    def write(self, batch: list):
        self.calls.append("write")
        if self._fails_on == "write":
            raise RuntimeError("backend roto")
        self.batches.append(batch)

    def close(self):
        self.calls.append("close")
        if self._fails_on == "close":
            raise RuntimeError("backend roto")
        self.closes += 1
        self._status = STATUS_DISABLED

    def points(self) -> list:
        return [record for batch in self.batches for record in batch]


@pytest.fixture(autouse=True)
def fast_timings(monkeypatch):
    """La ventana real es de 10 s; acá sobran milisegundos."""
    monkeypatch.setattr(pers, "_QUEUE_POLL_S", 0.01)
    monkeypatch.setattr(pers, "_PUBLISH_INTERVAL_S", 0.05)


def _thread(*backends: _FakeBackend) -> PersistenceThread:
    return PersistenceThread(_MockConfig(), backends=list(backends))


def _window(monkeypatch, seconds: float):
    """Ventana de publicación de este test."""
    monkeypatch.setattr(pers, "_PUBLISH_INTERVAL_S", seconds)


def _run(thread: PersistenceThread, until, timeout_s: float = 2.0) -> bool:
    """Corre el hilo hasta que `until()` sea verdadera, lo para y devuelve si paró."""
    thread.start()
    _wait_until(until, timeout_s)
    thread.requestInterruption()
    return thread.wait(_WAIT_TIMEOUT_MS)


def _wait_until(condition, timeout_s: float = 2.0) -> bool:
    """Espera a que `condition()` sea verdadera. Evita sleeps fijos en los tests."""
    deadline_s = time.monotonic() + timeout_s
    while time.monotonic() < deadline_s:
        if condition():
            return True
        time.sleep(0.005)
    return False


def _logged(caplog: pytest.LogCaptureFixture, text: str) -> int:
    return sum(1 for record in caplog.records if text in record.getMessage())


# ── Construcción ─────────────────────────────────────────────────────────────

class TestBackends:
    def test_by_default_the_repo_backends_are_armed(self):
        """Cada uno se deshabilita solo si su sección del config no lo habilita."""
        thread = PersistenceThread(_MockConfig())
        assert [backend.service_name for backend in thread._backends] == ["influxdb", "mqtt"]

    def test_an_explicit_list_replaces_the_default(self):
        backend = _FakeBackend("solo_este")
        assert _thread(backend)._backends == [backend]

    def test_an_empty_list_is_respected(self):
        """Sin backends el hilo sigue siendo válido: encola y descarta."""
        thread = PersistenceThread(_MockConfig(), backends=[])
        assert thread._backends == []
        thread.push_data("camera_1", _FIELDS)

    def test_the_status_of_a_backend_is_published_by_name(self):
        backend = _FakeBackend("influxdb")
        backend._status = STATUS_CONNECTED
        assert _thread(backend).backend_status("influxdb") == STATUS_CONNECTED

    def test_an_unknown_service_is_disabled(self):
        assert _thread(_FakeBackend("influxdb")).backend_status("mqtt") == STATUS_DISABLED


# ── Encolado ─────────────────────────────────────────────────────────────────

class TestPushData:
    def test_the_record_carries_measurement_fields_and_tags(self):
        thread = _thread(_FakeBackend())
        thread.push_data("camera_1", _FIELDS, tags={"slot": "camera_1"})
        record = thread._queue.get_nowait()
        assert record["measurement"] == "camera_1"
        assert record["fields"] == _FIELDS
        assert record["tags"] == {"slot": "camera_1"}

    def test_tags_are_optional(self):
        thread = _thread(_FakeBackend())
        thread.push_data("camera_1", _FIELDS)
        assert thread._queue.get_nowait()["tags"] == {}

    def test_the_stamp_is_the_time_of_the_push(self):
        """El valor vale por cuándo se midió, no por cuándo se escribió."""
        thread = _thread(_FakeBackend())
        before_s = time.time()
        thread.push_data("camera_1", _FIELDS)
        assert before_s <= thread._queue.get_nowait()["time"] <= time.time()

    def test_an_explicit_stamp_replaces_the_time_of_the_push(self):
        """Quien mide antes de publicar sella con el instante de la medición."""
        thread = _thread(_FakeBackend())
        measured_s = time.time() - 30
        thread.push_data("camera_1", _FIELDS, time_s=measured_s)
        assert thread._queue.get_nowait()["time"] == measured_s

    def test_pushing_never_blocks_when_the_queue_is_full(self):
        thread = _thread(_FakeBackend())
        for index in range(pers._QUEUE_MAXSIZE + 10):
            thread.push_data(f"punto_{index}", _FIELDS)
        assert thread._queue.qsize() == pers._QUEUE_MAXSIZE

    def test_a_full_queue_is_warned_once(self, caplog):
        """Sin el latch, cada push perdido escribiría una línea de log."""
        thread = _thread(_FakeBackend())
        with caplog.at_level(logging.WARNING, logger="app"):
            for _ in range(pers._QUEUE_MAXSIZE + 25):
                thread.push_data("camera_1", _FIELDS)
        assert _logged(caplog, "Cola llena") == 1

    def test_the_recovery_reports_what_was_lost(self, caplog):
        thread = _thread(_FakeBackend())
        for _ in range(pers._QUEUE_MAXSIZE + 3):
            thread.push_data("camera_1", _FIELDS)
        thread._queue.get_nowait()

        with caplog.at_level(logging.INFO, logger="app"):
            thread.push_data("camera_1", _FIELDS)
        assert _logged(caplog, "quedaron sin escribir 3 puntos") == 1
        assert thread._dropped_points == 0

    def test_pushing_from_several_threads_loses_nothing(self):
        """Los productores son los hilos de captura e inferencia, no uno solo."""
        thread = _thread(_FakeBackend())
        producers = [threading.Thread(target=lambda: [
            thread.push_data("camera_1", _FIELDS) for _ in range(20)]) for _ in range(5)]
        for producer in producers:
            producer.start()
        for producer in producers:
            producer.join()
        assert thread._queue.qsize() == 100


# ── Ventana de publicación ───────────────────────────────────────────────────

class TestBatching:
    def test_the_batch_reaches_every_backend(self):
        first, second = _FakeBackend("uno"), _FakeBackend("dos")
        thread = _thread(first, second)
        thread.push_data("camera_1", _FIELDS)
        assert _run(thread, lambda: first.batches and second.batches)
        assert first.points()[0]["measurement"] == "camera_1"
        assert second.points()[0]["measurement"] == "camera_1"

    def test_the_points_of_a_window_travel_together(self, monkeypatch):
        """Un write por punto sería un round-trip HTTP por medición."""
        _window(monkeypatch, 0.2)
        backend = _FakeBackend()
        thread = _thread(backend)
        for index in range(5):
            thread.push_data(f"punto_{index}", _FIELDS)
        assert _run(thread, lambda: backend.batches)
        assert len(backend.batches[0]) == 5

    def test_an_empty_window_does_not_write(self):
        backend = _FakeBackend()
        thread = _thread(backend)
        thread.start()
        time.sleep(0.2)
        thread.requestInterruption()
        assert thread.wait(_WAIT_TIMEOUT_MS)
        assert backend.batches == []

    def test_the_window_lasts_what_the_constant_says(self, monkeypatch):
        _window(monkeypatch, 0.2)
        started_s = time.monotonic()
        assert _thread(_FakeBackend())._collect_batch() == []
        assert time.monotonic() - started_s >= 0.2

    def test_the_window_is_not_stretched_by_the_queue_wait(self, monkeypatch):
        """La espera por punto se acota a lo que queda de ventana, no al revés."""
        monkeypatch.setattr(pers, "_QUEUE_POLL_S", 1.0)
        _window(monkeypatch, 0.05)
        started_s = time.monotonic()
        assert _thread(_FakeBackend())._collect_batch() == []
        assert time.monotonic() - started_s < 0.5


# ── Ciclo de vida ────────────────────────────────────────────────────────────

class TestLifecycle:
    def test_the_backends_are_opened_by_the_thread(self):
        """Abrirlos en el constructor haría el I/O en el hilo del llamador."""
        backend = _FakeBackend()
        thread = _thread(backend)
        assert backend.setups == 0
        assert _run(thread, lambda: backend.setups == 1)
        assert backend.setups == 1

    def test_the_backends_are_closed_on_shutdown(self):
        backend = _FakeBackend()
        assert _run(_thread(backend), lambda: backend.setups == 1)
        assert backend.closes == 1

    def _interrupted_with_points(self, monkeypatch, count: int) -> _FakeBackend:
        """
        Hilo que se interrumpe con puntos ya encolados y sin ventana que los junte.

        La traba en setup() es lo que hace determinista el caso: la interrupción
        llega antes de que el bucle empiece, así que la única salida que les queda a
        esos puntos es el vaciado del cierre.
        """
        _window(monkeypatch, 5)
        gate = threading.Event()
        backend = _FakeBackend(setup_gate=gate)
        thread = _thread(backend)
        thread.start()
        _wait_until(lambda: backend.calls == ["setup"])
        for index in range(count):
            thread.push_data(f"punto_{index}", _FIELDS)
        thread.requestInterruption()
        gate.set()
        assert thread.wait(_WAIT_TIMEOUT_MS)
        return backend

    def test_the_drain_writes_what_the_window_did_not_reach(self, monkeypatch):
        """Esos puntos el productor ya los dio por entregados."""
        backend = self._interrupted_with_points(monkeypatch, 3)
        assert [record["measurement"] for record in backend.points()] == [
            "punto_0", "punto_1", "punto_2"]

    def test_the_drain_happens_before_the_backends_close(self, monkeypatch):
        """Cerrado el cliente, esos puntos ya no tendrían por dónde salir."""
        assert self._interrupted_with_points(monkeypatch, 1).calls == [
            "setup", "write", "close"]

    def test_nothing_is_written_when_there_is_nothing_queued(self, monkeypatch):
        assert self._interrupted_with_points(monkeypatch, 0).calls == ["setup", "close"]

    def test_the_interruption_is_answered_quickly(self, monkeypatch):
        """La espera por punto acota la latencia de la parada."""
        _window(monkeypatch, 30)
        thread = _thread(_FakeBackend())
        thread.start()
        time.sleep(0.05)
        thread.requestInterruption()
        started_s = time.monotonic()
        assert thread.wait(_WAIT_TIMEOUT_MS)
        assert time.monotonic() - started_s < 1.0


# ── Backend roto ─────────────────────────────────────────────────────────────

class TestBrokenBackend:
    def test_a_backend_that_fails_on_setup_does_not_stop_the_others(self, caplog):
        broken, healthy = _FakeBackend("roto", fails_on="setup"), _FakeBackend("sano")
        thread = _thread(broken, healthy)
        with caplog.at_level(logging.ERROR, logger="app"):
            assert _run(thread, lambda: healthy.setups == 1)
        assert _logged(caplog, "roto falló en setup()") == 1

    def test_a_backend_that_fails_on_write_does_not_stop_the_others(self, caplog):
        broken, healthy = _FakeBackend("roto", fails_on="write"), _FakeBackend("sano")
        thread = _thread(broken, healthy)
        thread.push_data("camera_1", _FIELDS)
        with caplog.at_level(logging.ERROR, logger="app"):
            assert _run(thread, lambda: healthy.batches)
        assert healthy.points()[0]["measurement"] == "camera_1"
        assert _logged(caplog, "roto falló escribiendo") >= 1

    def test_a_backend_that_fails_on_close_does_not_stop_the_others(self, caplog):
        broken, healthy = _FakeBackend("roto", fails_on="close"), _FakeBackend("sano")
        thread = _thread(broken, healthy)
        with caplog.at_level(logging.ERROR, logger="app"):
            assert _run(thread, lambda: healthy.setups == 1)
        assert healthy.closes == 1
        assert _logged(caplog, "roto falló en close()") == 1

    def test_the_thread_finishes_even_with_every_backend_broken(self):
        thread = _thread(_FakeBackend("roto", fails_on="setup"))
        thread.push_data("camera_1", _FIELDS)
        assert _run(thread, lambda: thread.isFinished(), 0.3)
        assert thread.isFinished()


# ── Vaciado de la cola ───────────────────────────────────────────────────────

class TestDrain:
    def test_the_drain_empties_the_queue(self):
        thread = _thread(_FakeBackend())
        for index in range(3):
            thread.push_data(f"punto_{index}", _FIELDS)
        assert len(thread._drain_queue()) == 3
        assert thread._queue.empty()

    def test_draining_an_empty_queue_returns_nothing(self):
        assert _thread(_FakeBackend())._drain_queue() == []

    def test_the_drain_does_not_wait(self):
        started_s = time.monotonic()
        _thread(_FakeBackend())._drain_queue()
        assert time.monotonic() - started_s < 0.1
