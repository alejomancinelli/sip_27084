"""Tests del ciclo de medición: permiso por frame, ronda entre cámaras, agregación
de los N resultados, ciclo vacío, vencimiento y apagado de captura entre ciclos.

Sin cámaras, sin motor y sin event loop de Qt: el scheduler no tiene timers propios,
así que el tiempo lo trae `tick()` y los tests lo mueven con timeouts chicos."""

import time

import pytest
from PySide6.QtCore import Qt

from system.camera.capture_scheduler import REASON_EMPTY_CYCLE, CaptureScheduler
from system.inference.result import InferenceResult

_PIPELINE = "pipeline_1"
_OTHER_PIPELINE = "pipeline_2"
_SLOT = "camera_1"
_OTHER_SLOT = "camera_2"

# Plazo corto y espera que lo pasa holgada. En Windows time.sleep() puede volver hasta un
# tick del timer antes de lo pedido, así que un margen justo hace el test intermitente.
_SHORT_WAIT_S = 0.01
_PAST_WAIT_S = 0.05


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            "cameras": {_SLOT: {}, _OTHER_SLOT: {}},
            f"inference.pipelines.{_PIPELINE}.cameras": [_SLOT],
        }
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _Collector:
    """Junta lo que emite una señal."""

    def __init__(self):
        self.results: list = []
        self.captures: list[tuple[str, bool]] = []

    def on_cycle(self, result):
        self.results.append(result)

    def on_capture(self, camera_slot: str, enabled: bool):
        self.captures.append((camera_slot, enabled))


def _config(frames_per_cycle: int = 3, **overrides) -> _MockConfig:
    values = {f"inference.pipelines.{_PIPELINE}.frames_per_cycle": frames_per_cycle}
    values.update(overrides)
    return _MockConfig(**values)


def _scheduler(config: _MockConfig | None = None,
               pipeline_slots: tuple[str, ...] = (_PIPELINE)) -> CaptureScheduler:
    slots = pipeline_slots if isinstance(pipeline_slots, tuple) else (pipeline_slots,)
    scheduler = CaptureScheduler(config or _config(), slots)
    scheduler.start()
    return scheduler


def _collect(scheduler: CaptureScheduler) -> _Collector:
    collector = _Collector()
    scheduler.cycle_complete.connect(collector.on_cycle, Qt.ConnectionType.DirectConnection)
    scheduler.capture_wanted.connect(collector.on_capture, Qt.ConnectionType.DirectConnection)
    return collector


def _result(camera_slot: str = _SLOT, pipeline_slot: str = _PIPELINE, *,
            is_valid: bool = True, **metrics) -> InferenceResult:
    return InferenceResult(camera_slot=camera_slot, pipeline_slot=pipeline_slot,
                           metrics=dict(metrics), is_valid=is_valid)


def _feed(scheduler: CaptureScheduler, results: list[InferenceResult],
          camera_slot: str = _SLOT):
    """Pide el permiso y entrega el resultado, tantas veces como resultados haya."""
    for result in results:
        assert scheduler.take_frame(camera_slot, _PIPELINE), "el permiso estaba cerrado"
        scheduler.on_result_ready(result)


# ── El permiso ───────────────────────────────────────────────────────────────

class TestTakeFrame:
    def test_it_opens_once_per_requested_frame(self):
        """Se consume: dos frames seguidos sin resultado sería medir de más."""
        scheduler = _scheduler()
        assert scheduler.take_frame(_SLOT, _PIPELINE) is True
        assert scheduler.take_frame(_SLOT, _PIPELINE) is False

    def test_it_reopens_when_the_result_arrives(self):
        scheduler = _scheduler()
        scheduler.take_frame(_SLOT, _PIPELINE)
        scheduler.on_result_ready(_result())
        assert scheduler.take_frame(_SLOT, _PIPELINE) is True

    def test_a_camera_outside_the_round_is_refused(self):
        assert _scheduler().take_frame(_OTHER_SLOT, _PIPELINE) is False

    def test_an_unknown_pipeline_is_let_through(self):
        """El scheduler no gobierna lo que no se le pasó: no puede frenarlo."""
        assert _scheduler().take_frame(_SLOT, _OTHER_PIPELINE) is True

    def test_a_stopped_scheduler_refuses(self):
        scheduler = _scheduler()
        scheduler.stop()
        assert scheduler.take_frame(_SLOT, _PIPELINE) is False


# ── Sin ciclo: pass-through ──────────────────────────────────────────────────

class TestPassThrough:
    def test_one_frame_per_cycle_is_not_cycled(self):
        assert _scheduler(_config(frames_per_cycle=1)).is_cycled(_PIPELINE) is False

    def test_it_always_lets_the_frame_through(self):
        scheduler = _scheduler(_config(frames_per_cycle=1))
        assert [scheduler.take_frame(_SLOT, _PIPELINE) for _ in range(3)] == [True] * 3

    def test_every_result_comes_out_untouched(self):
        """Un proyecto en tiempo real no puede cambiar de comportamiento por esto."""
        scheduler = _scheduler(_config(frames_per_cycle=1))
        collector = _collect(scheduler)
        result = _result(load_pct=42.0)
        scheduler.on_result_ready(result)
        assert collector.results == [result]
        assert collector.results[0].metrics == {"load_pct": 42.0}

    def test_it_asks_for_nothing_about_the_cameras(self):
        scheduler = CaptureScheduler(_config(frames_per_cycle=1), (_PIPELINE,))
        collector = _collect(scheduler)
        scheduler.start()
        assert collector.captures == []


# ── Agregación ───────────────────────────────────────────────────────────────

class TestAggregation:
    def test_the_cycle_closes_on_the_nth_frame(self):
        scheduler = _scheduler()
        collector = _collect(scheduler)
        _feed(scheduler, [_result(load_pct=10.0), _result(load_pct=20.0)])
        assert collector.results == []
        _feed(scheduler, [_result(load_pct=30.0)])
        assert len(collector.results) == 1

    def test_the_metrics_are_the_average_of_the_cycle(self):
        scheduler = _scheduler()
        collector = _collect(scheduler)
        _feed(scheduler, [_result(load_pct=10.0), _result(load_pct=20.0),
                          _result(load_pct=30.0)])
        assert collector.results[0].metrics["load_pct"] == 20.0

    def test_it_carries_the_dispersion_and_the_sample_count(self):
        """Es el dato que dice si el proceso está estable; el promedio solo lo esconde."""
        scheduler = _scheduler()
        collector = _collect(scheduler)
        _feed(scheduler, [_result(load_pct=10.0), _result(load_pct=20.0),
                          _result(load_pct=30.0)])
        metrics = collector.results[0].metrics
        assert (metrics["load_pct_min"], metrics["load_pct_max"]) == (10.0, 30.0)
        assert metrics["load_pct_std"] > 0
        assert metrics["sample_count"] == 3

    def test_the_clean_name_is_not_duplicated_by_the_mean(self):
        """El nombre limpio es el que va al PLC: `load_pct` y `load_pct_mean` sobran."""
        scheduler = _scheduler()
        collector = _collect(scheduler)
        _feed(scheduler, [_result(load_pct=10.0)] * 3)
        assert "load_pct_mean" not in collector.results[0].metrics

    def test_the_representative_carries_the_frames(self):
        """Lo que se guarda y se muestra es el frame más cercano al promedio."""
        scheduler = _scheduler()
        collector = _collect(scheduler)
        _feed(scheduler, [_result(load_pct=10.0), _result(load_pct=20.0),
                          _result(load_pct=90.0)])
        assert collector.results[0].metrics["load_pct"] == 40.0

    def test_an_invalid_frame_does_not_enter_the_average(self):
        scheduler = _scheduler()
        collector = _collect(scheduler)
        _feed(scheduler, [_result(load_pct=10.0), _result(load_pct=10.0, is_valid=False),
                          _result(load_pct=20.0)])
        metrics = collector.results[0].metrics
        assert (metrics["load_pct"], metrics["sample_count"]) == (15.0, 2)

    def test_the_original_result_is_not_mutated(self):
        """Ese objeto ya viajó a la UI y a los streams: pisarlo cambiaría lo que ven."""
        scheduler = _scheduler()
        _collect(scheduler)
        results = [_result(load_pct=10.0), _result(load_pct=20.0), _result(load_pct=30.0)]
        _feed(scheduler, results)
        assert [r.metrics["load_pct"] for r in results] == [10.0, 20.0, 30.0]


# ── Ciclo vacío ──────────────────────────────────────────────────────────────

class TestEmptyCycle:
    def test_a_cycle_without_valid_frames_is_emitted(self):
        scheduler = _scheduler()
        collector = _collect(scheduler)
        _feed(scheduler, [_result(load_pct=10.0, is_valid=False)] * 3)
        assert len(collector.results) == 1
        assert collector.results[0].invalid_reason == REASON_EMPTY_CYCLE

    def test_it_publishes_zeros_and_not_the_last_good_value(self):
        """El PLC no puede seguir leyendo la medición de antes de que se quemó la lámpara."""
        scheduler = _scheduler()
        collector = _collect(scheduler)
        _feed(scheduler, [_result(load_pct=10.0)] * 3)
        _feed(scheduler, [_result(load_pct=10.0, is_valid=False)] * 3)
        assert collector.results[1].metrics["load_pct"] == 0
        assert collector.results[1].metrics["sample_count"] == 0

    def test_the_sample_count_is_zero_before_ever_measuring(self):
        scheduler = _scheduler()
        collector = _collect(scheduler)
        _feed(scheduler, [_result(is_valid=False)] * 3)
        assert collector.results[0].metrics == {"sample_count": 0}


# ── Ronda entre cámaras ──────────────────────────────────────────────────────

class TestRound:
    def _two_camera_scheduler(self) -> CaptureScheduler:
        config = _config(frames_per_cycle=2,
                         **{f"inference.pipelines.{_PIPELINE}.cameras": [_SLOT, _OTHER_SLOT]})
        return _scheduler(config)

    def test_it_serves_one_camera_at_a_time(self):
        scheduler = self._two_camera_scheduler()
        assert scheduler.take_frame(_OTHER_SLOT, _PIPELINE) is False
        assert scheduler.take_frame(_SLOT, _PIPELINE) is True

    def test_each_camera_gets_its_own_cycle_result(self):
        scheduler = self._two_camera_scheduler()
        collector = _collect(scheduler)
        _feed(scheduler, [_result(load_pct=10.0)] * 2, _SLOT)
        _feed(scheduler, [_result(_OTHER_SLOT, load_pct=50.0)] * 2, _OTHER_SLOT)
        assert [r.camera_slot for r in collector.results] == [_SLOT, _OTHER_SLOT]

    def test_the_round_starts_over_after_the_last_camera(self):
        scheduler = self._two_camera_scheduler()
        _feed(scheduler, [_result(load_pct=10.0)] * 2, _SLOT)
        _feed(scheduler, [_result(_OTHER_SLOT, load_pct=50.0)] * 2, _OTHER_SLOT)
        assert scheduler.take_frame(_SLOT, _PIPELINE) is True

    def test_a_result_from_another_camera_does_not_advance_the_cycle(self):
        scheduler = self._two_camera_scheduler()
        collector = _collect(scheduler)
        scheduler.take_frame(_SLOT, _PIPELINE)
        scheduler.on_result_ready(_result(_OTHER_SLOT))
        scheduler.on_result_ready(_result(_SLOT, load_pct=10.0))
        assert collector.results == []
        assert scheduler.get_status()["pipelines"][_PIPELINE]["collected"] == 1

    def test_no_declared_cameras_falls_back_to_all_of_them(self):
        config = _config(frames_per_cycle=2,
                         **{f"inference.pipelines.{_PIPELINE}.cameras": []})
        assert _scheduler(config).take_frame(_SLOT, _PIPELINE) is True


# ── Espera entre rondas ──────────────────────────────────────────────────────

class TestCycleInterval:
    def test_the_next_round_waits_for_the_interval(self):
        config = _config(**{f"inference.pipelines.{_PIPELINE}.cycle_interval_s": 30})
        scheduler = _scheduler(config)
        _feed(scheduler, [_result(load_pct=10.0)] * 3)
        assert scheduler.take_frame(_SLOT, _PIPELINE) is False

    def test_the_round_opens_once_the_interval_passed(self):
        config = _config(**{f"inference.pipelines.{_PIPELINE}.cycle_interval_s": _SHORT_WAIT_S})
        scheduler = _scheduler(config)
        _feed(scheduler, [_result(load_pct=10.0)] * 3)
        time.sleep(_PAST_WAIT_S)
        assert scheduler.take_frame(_SLOT, _PIPELINE) is True


# ── Vencimiento ──────────────────────────────────────────────────────────────

class TestTimeout:
    def test_a_frame_that_never_returns_expires_on_tick(self):
        """Sin esto el ciclo queda colgado mientras el heartbeat sigue latiendo."""
        config = _config(**{f"inference.pipelines.{_PIPELINE}.capture_timeout_s": _SHORT_WAIT_S})
        scheduler = _scheduler(config)
        scheduler.take_frame(_SLOT, _PIPELINE)
        time.sleep(_PAST_WAIT_S)
        scheduler.tick()
        assert scheduler.get_status()["pipelines"][_PIPELINE]["waiting"] is False

    def test_it_does_not_expire_before_the_timeout(self):
        config = _config(**{f"inference.pipelines.{_PIPELINE}.capture_timeout_s": 30})
        scheduler = _scheduler(config)
        scheduler.take_frame(_SLOT, _PIPELINE)
        scheduler.tick()
        assert scheduler.get_status()["pipelines"][_PIPELINE]["waiting"] is True

    def test_a_camera_that_gives_nothing_closes_its_cycle(self):
        """La ronda tiene que avanzar a la cámara siguiente, no reintentar contra una caída."""
        config = _config(**{f"inference.pipelines.{_PIPELINE}.capture_timeout_s": _SHORT_WAIT_S})
        scheduler = _scheduler(config)
        collector = _collect(scheduler)
        scheduler.take_frame(_SLOT, _PIPELINE)
        time.sleep(_PAST_WAIT_S)
        scheduler.tick()
        assert len(collector.results) == 1
        assert collector.results[0].metrics["sample_count"] == 0

    def test_a_partial_cycle_keeps_what_it_collected(self):
        config = _config(**{f"inference.pipelines.{_PIPELINE}.capture_timeout_s": _SHORT_WAIT_S})
        scheduler = _scheduler(config)
        collector = _collect(scheduler)
        _feed(scheduler, [_result(load_pct=10.0)])
        scheduler.take_frame(_SLOT, _PIPELINE)
        time.sleep(_PAST_WAIT_S)
        scheduler.tick()
        assert collector.results == []
        assert scheduler.get_status()["pipelines"][_PIPELINE]["collected"] == 1

    def test_a_stopped_scheduler_does_not_tick(self):
        config = _config(**{f"inference.pipelines.{_PIPELINE}.capture_timeout_s": _SHORT_WAIT_S})
        scheduler = _scheduler(config)
        collector = _collect(scheduler)
        scheduler.take_frame(_SLOT, _PIPELINE)
        scheduler.stop()
        time.sleep(_PAST_WAIT_S)
        scheduler.tick()
        assert collector.results == []


# ── Captura entre ciclos ─────────────────────────────────────────────────────

class TestIdleCameras:
    def _idle_config(self, **overrides) -> _MockConfig:
        values = {
            f"inference.pipelines.{_PIPELINE}.cameras": [_SLOT, _OTHER_SLOT],
            f"inference.pipelines.{_PIPELINE}.idle_cameras_between_cycles": True,
        }
        values.update(overrides)
        return _config(frames_per_cycle=2, **values)

    def test_disabled_by_default_it_never_asks(self):
        """Apagar la cámara deja sin imagen al operador: no se impone."""
        scheduler = CaptureScheduler(_config(), (_PIPELINE,))
        collector = _collect(scheduler)
        scheduler.start()
        scheduler.tick()
        assert collector.captures == []

    def test_only_the_camera_being_measured_captures(self):
        scheduler = CaptureScheduler(self._idle_config(), (_PIPELINE,))
        collector = _collect(scheduler)
        scheduler.start()
        assert sorted(collector.captures) == [(_SLOT, True), (_OTHER_SLOT, False)]

    def test_the_turn_moves_to_the_next_camera(self):
        scheduler = CaptureScheduler(self._idle_config(), (_PIPELINE,))
        scheduler.start()
        collector = _collect(scheduler)
        _feed(scheduler, [_result(load_pct=10.0)] * 2, _SLOT)
        assert sorted(collector.captures) == [(_SLOT, False), (_OTHER_SLOT, True)]

    def test_nobody_captures_while_the_interval_runs(self):
        scheduler = CaptureScheduler(
            self._idle_config(**{f"inference.pipelines.{_PIPELINE}.cycle_interval_s": 30}),
            (_PIPELINE,))
        scheduler.start()
        _feed(scheduler, [_result(load_pct=10.0)] * 2, _SLOT)
        collector = _collect(scheduler)
        _feed(scheduler, [_result(_OTHER_SLOT, load_pct=10.0)] * 2, _OTHER_SLOT)
        assert collector.captures == [(_OTHER_SLOT, False)]

    def test_stopping_gives_the_cameras_back(self):
        """El stream crudo no depende de que haya una medición en curso."""
        scheduler = CaptureScheduler(self._idle_config(), (_PIPELINE,))
        scheduler.start()
        collector = _collect(scheduler)
        scheduler.stop()
        assert collector.captures == [(_OTHER_SLOT, True)]

    def test_it_only_asks_when_something_changed(self):
        scheduler = CaptureScheduler(self._idle_config(), (_PIPELINE,))
        scheduler.start()
        collector = _collect(scheduler)
        scheduler.tick()
        scheduler.tick()
        assert collector.captures == []


# ── Ciclo de vida ────────────────────────────────────────────────────────────

class TestLifecycle:
    def test_start_is_idempotent(self):
        scheduler = _scheduler()
        scheduler.take_frame(_SLOT, _PIPELINE)
        scheduler.start()
        assert scheduler.get_status()["pipelines"][_PIPELINE]["waiting"] is True

    def test_stop_is_idempotent(self):
        scheduler = _scheduler()
        scheduler.stop()
        scheduler.stop()
        assert scheduler.get_status()["running"] is False

    def test_stopping_drops_what_the_cycle_had_collected(self):
        """Media medición no es una medición: no se arrastra al arranque siguiente."""
        scheduler = _scheduler()
        _feed(scheduler, [_result(load_pct=10.0)])
        scheduler.stop()
        assert scheduler.get_status()["pipelines"][_PIPELINE]["collected"] == 0

    @pytest.mark.parametrize("frames_per_cycle", [0, 1, -3, None])
    def test_a_meaningless_frame_count_is_not_a_cycle(self, frames_per_cycle):
        config = _config(frames_per_cycle=frames_per_cycle)
        assert _scheduler(config).is_cycled(_PIPELINE) is False
