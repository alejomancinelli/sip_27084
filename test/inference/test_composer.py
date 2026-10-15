"""
Tests del composer: qué resultado pone el frame de una cámara y qué overlays van encima.

Módulo puro: el reloj entra como número y el dibujo lo hace un doble del motor, así que no
hay Qt, ni tiempo real, ni frames de verdad más allá de unos arrays chicos.
"""

import numpy as np

from system.inference.composer import FrameComposer
from system.inference.result import InferenceResult

_CAMERA = "camera_1"
_OTHER_CAMERA = "camera_2"
_GRAIN = "pipeline_1"
_PERSON = "pipeline_2"


def _result(pipeline_slot: str, timestamp_s: float, camera_slot: str = _CAMERA,
            **fields) -> InferenceResult:
    return InferenceResult(camera_slot=camera_slot, pipeline_slot=pipeline_slot,
                           timestamp_s=timestamp_s, **fields)


def _frame(level: int = 0) -> np.ndarray:
    return np.full((8, 8, 3), level, np.uint8)


class _Painter:
    """El motor de un pipeline: registra qué le pidieron dibujar y en qué orden."""

    def __init__(self, calls: list, level: int):
        self._calls = calls
        self._level = level

    def draw_onto(self, canvas_bgr, result):
        self._calls.append(("draw_onto", result.pipeline_slot))
        canvas_bgr[0, 0] = self._level

    def draw_timestamp(self, canvas_bgr, result):
        self._calls.append(("draw_timestamp", result.pipeline_slot))


class TestDriving:
    def test_the_first_result_of_a_camera_drives(self):
        assert FrameComposer().add(_result(_GRAIN, 10.0), now_s=0.0) is True

    def test_a_newer_result_drives(self):
        composer = FrameComposer()
        composer.add(_result(_GRAIN, 10.0), now_s=0.0)
        assert composer.add(_result(_PERSON, 10.1), now_s=0.1) is True

    def test_an_older_result_does_not_drive(self):
        """Un ciclo conserva el instante de su representativo: el video no salta atrás."""
        composer = FrameComposer()
        composer.add(_result(_PERSON, 10.5), now_s=0.5)
        assert composer.add(_result(_GRAIN, 10.0), now_s=0.6) is False

    def test_an_older_result_still_shows_up_on_the_next_frame(self):
        composer = FrameComposer()
        composer.add(_result(_PERSON, 10.5), now_s=0.5)
        composer.add(_result(_GRAIN, 10.0), now_s=0.6)
        composer.add(_result(_PERSON, 10.7), now_s=0.7)
        assert [r.pipeline_slot for r in composer.others(_CAMERA, _PERSON, 0.7)] == [_GRAIN]

    def test_each_camera_has_its_own_clock(self):
        composer = FrameComposer()
        composer.add(_result(_GRAIN, 10.5), now_s=0.0)
        assert composer.add(_result(_GRAIN, 10.0, camera_slot=_OTHER_CAMERA), now_s=0.0)


class TestOthers:
    def test_it_excludes_the_own_pipeline(self):
        composer = FrameComposer()
        composer.add(_result(_GRAIN, 10.0), now_s=0.0)
        composer.add(_result(_PERSON, 10.1), now_s=0.1)
        assert [r.pipeline_slot for r in composer.others(_CAMERA, _PERSON, 0.1)] == [_GRAIN]

    def test_it_excludes_other_cameras(self):
        composer = FrameComposer()
        composer.add(_result(_GRAIN, 10.0, camera_slot=_OTHER_CAMERA), now_s=0.0)
        assert composer.others(_CAMERA, _PERSON, 0.0) == []

    def test_it_keeps_only_the_latest_result_of_each_pipeline(self):
        composer = FrameComposer()
        composer.add(_result(_GRAIN, 10.0), now_s=0.0)
        composer.add(_result(_GRAIN, 10.5), now_s=0.5)
        assert [r.timestamp_s for r in composer.others(_CAMERA, _PERSON, 0.5)] == [10.5]

    def test_a_stale_result_is_dropped(self):
        """Un pipeline caído o con su compuerta cerrada no queda congelado en pantalla."""
        composer = FrameComposer({_GRAIN: 0.5})       # vigencia: 3 × 0.5 s = 1.5 s
        composer.add(_result(_GRAIN, 10.0), now_s=0.0)
        assert composer.others(_CAMERA, _PERSON, 1.4) != []
        assert composer.others(_CAMERA, _PERSON, 1.6) == []

    def test_a_short_interval_has_a_floor(self):
        """Con `interval_s` en 0 el período es el de la cámara: no hay de dónde sacar otro."""
        composer = FrameComposer({_GRAIN: 0.0})
        composer.add(_result(_GRAIN, 10.0), now_s=0.0)
        assert composer.others(_CAMERA, _PERSON, 0.9) != []
        assert composer.others(_CAMERA, _PERSON, 1.1) == []

    def test_new_intervals_keep_what_was_stored(self):
        composer = FrameComposer({_GRAIN: 0.0})
        composer.add(_result(_GRAIN, 10.0), now_s=0.0)
        composer.set_intervals({_GRAIN: 10.0})
        assert composer.others(_CAMERA, _PERSON, 20.0) != []

    def test_the_stored_result_has_no_frames(self):
        """Para dibujarlo encima alcanzan sus detecciones: dos frames por par sobran."""
        composer = FrameComposer()
        composer.add(_result(_GRAIN, 10.0, source_bgr=_frame(), annotated_bgr=_frame()),
                     now_s=0.0)
        [stored] = composer.others(_CAMERA, _PERSON, 0.0)
        assert (stored.source_bgr, stored.annotated_bgr) == (None, None)


class TestCompose:
    def test_with_one_pipeline_the_engine_frame_goes_out_unchanged(self):
        annotated = _frame()
        shown = FrameComposer().compose(_result(_GRAIN, 10.0), annotated, 0.0, {})
        assert shown is annotated

    def test_a_result_that_does_not_drive_shows_nothing(self):
        composer = FrameComposer()
        composer.compose(_result(_PERSON, 10.5), _frame(), 0.5, {})
        assert composer.compose(_result(_GRAIN, 10.0), _frame(), 0.6, {}) is None

    def test_the_others_are_drawn_and_the_timestamp_goes_last(self):
        calls: list = []
        painters = {_GRAIN: _Painter(calls, 50), _PERSON: _Painter(calls, 90)}
        composer = FrameComposer()
        composer.compose(_result(_GRAIN, 10.0), _frame(), 0.0, painters)
        calls.clear()
        shown = composer.compose(_result(_PERSON, 10.1), _frame(), 0.1, painters)
        assert calls == [("draw_onto", _GRAIN), ("draw_timestamp", _PERSON)]
        assert tuple(shown[0, 0]) == (50, 50, 50)

    def test_the_own_annotated_frame_is_not_modified(self):
        """Es el que va al dataset, con el JSON de este pipeline solo."""
        calls: list = []
        painters = {_GRAIN: _Painter(calls, 50), _PERSON: _Painter(calls, 90)}
        composer = FrameComposer()
        composer.compose(_result(_GRAIN, 10.0), _frame(), 0.0, painters)
        annotated = _frame()
        shown = composer.compose(_result(_PERSON, 10.1), annotated, 0.1, painters)
        assert shown is not annotated
        assert np.array_equal(annotated, _frame())

    def test_a_pipeline_without_painter_is_skipped(self):
        composer = FrameComposer()
        composer.compose(_result(_GRAIN, 10.0), _frame(), 0.0, {})
        shown = composer.compose(_result(_PERSON, 10.1), _frame(), 0.1, {})
        assert np.array_equal(shown, _frame())


class TestFreshness:
    """Qué sigue vigente en una cámara, y qué se olvida en el acto."""

    def test_a_camera_with_a_recent_result_has_something_fresh(self):
        composer = FrameComposer({_GRAIN: 0.5})
        composer.add(_result(_GRAIN, 10.0), now_s=0.0)
        assert composer.has_fresh(_CAMERA, 1.0) is True

    def test_a_camera_whose_results_expired_has_nothing_fresh(self):
        composer = FrameComposer({_GRAIN: 0.5})
        composer.add(_result(_GRAIN, 10.0), now_s=0.0)
        assert composer.has_fresh(_CAMERA, 2.0) is False

    def test_another_camera_does_not_count(self):
        composer = FrameComposer()
        composer.add(_result(_GRAIN, 10.0, camera_slot=_OTHER_CAMERA), now_s=0.0)
        assert composer.has_fresh(_CAMERA, 0.0) is False

    def test_a_forgotten_pair_stops_being_drawn_at_once(self):
        """Es lo que hace el cableado cuando la compuerta corta: no espera a que venza."""
        composer = FrameComposer({_GRAIN: 10.0})
        composer.add(_result(_GRAIN, 10.0), now_s=0.0)
        composer.forget(_CAMERA, _GRAIN)
        assert composer.others(_CAMERA, _PERSON, 0.1) == []
        assert composer.has_fresh(_CAMERA, 0.1) is False

    def test_forgetting_keeps_the_other_pipelines(self):
        composer = FrameComposer()
        composer.add(_result(_GRAIN, 10.0), now_s=0.0)
        composer.add(_result(_PERSON, 10.1), now_s=0.1)
        composer.forget(_CAMERA, _GRAIN)
        assert composer.has_fresh(_CAMERA, 0.2) is True

    def test_a_slow_pipeline_stays_fresh_between_its_results(self):
        """`interval_s` en 0 y dos segundos por inferencia: no puede darse por muerto entre dos."""
        composer = FrameComposer({_GRAIN: 0.0})
        composer.add(_result(_GRAIN, 10.0), now_s=0.0)
        composer.add(_result(_GRAIN, 12.0), now_s=2.0)
        assert composer.has_fresh(_CAMERA, 4.5) is True
        assert composer.has_fresh(_CAMERA, 8.5) is False

    def test_a_pause_after_forgetting_is_not_taken_as_the_period(self):
        """Sin olvidar el período, una hora cortada dejaría al par vigente tres horas."""
        composer = FrameComposer({_GRAIN: 0.0})
        composer.add(_result(_GRAIN, 10.0), now_s=0.0)
        composer.forget(_CAMERA, _GRAIN)
        composer.add(_result(_GRAIN, 3610.0), now_s=3600.0)
        assert composer.has_fresh(_CAMERA, 3601.5) is False
