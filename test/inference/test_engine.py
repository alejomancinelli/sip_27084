"""Tests del hilo de inferencia: fan-in de varias cámaras, always-fresh por cámara,
recorte del ROI, compuertas de validez, analyzer inyectado y parada limpia.

El pipeline es un doble programable: lo que se verifica acá es el ciclo del motor, no un
modelo. Sin GPU, sin cámara y sin event loop de Qt.
"""

import threading
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt

from system.inference.abstract_pipeline import AbstractPipeline
from system.inference.engine import InferenceThread
from system.inference.result import (REASON_DARK_FRAME, REASON_ERROR, REASON_FEW_DETECTIONS,
                                     REASON_LOW_CONFIDENCE, REASON_NO_MODEL, Detection)

_PIPELINE = "pipeline_1"
_SLOT = "camera_1"
_OTHER_SLOT = "camera_2"
_WAIT_TIMEOUT_MS = 3000
_WAIT_TIMEOUT_S = 3.0


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            f"inference.pipelines.{_PIPELINE}.enabled": True,
            f"inference.pipelines.{_PIPELINE}.cameras": [],
        }
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _FakePipeline(AbstractPipeline):
    """
    Pipeline programable: cada test decide qué detecta, cuánto tarda y qué rompe.

    Devuelve detecciones nuevas en cada corrida, porque el motor las corre in-place al
    espacio del frame de cámara y reusar los objetos acumularía el desplazamiento.
    """

    model_slots = ("model_1",)

    def __init__(self, specs: list[tuple] | None = None, *, loaded: bool = True,
                 synthetic: bool = False, raises: bool = False, delay_s: float = 0.0,
                 labels: dict | None = None):
        super().__init__(_MockConfig(), _PIPELINE)
        self._specs = list(specs or [])
        self._labels_to_set = dict(labels or {})
        self._loaded = loaded
        self._synthetic = synthetic
        self._raises = raises
        self._delay_s = delay_s

        self.frames: list[np.ndarray] = []
        self.load_calls = 0
        self.unload_calls = 0

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def is_synthetic(self) -> bool:
        return self._synthetic

    def load(self):
        self.load_calls += 1

    def unload(self):
        self.unload_calls += 1

    def _run(self, frame_bgr: np.ndarray, camera_slot: str) -> list[Detection]:
        self.frames.append(frame_bgr)
        for name, value in self._labels_to_set.items():
            self._set_label(name, value)
        if self._raises:
            raise RuntimeError("etapa caída")
        if self._delay_s:
            time.sleep(self._delay_s)
        return [Detection(class_index=index, class_name=f"class_{index}",
                          confidence_pct=confidence_pct, bbox_px=bbox_px)
                for index, confidence_pct, bbox_px in self._specs]


class _Collector:
    """Junta los resultados que emite el hilo de inferencia."""

    def __init__(self):
        self._lock = threading.Lock()
        self.results = []

    def on_result(self, result):
        with self._lock:
            self.results.append(result)

    def count(self) -> int:
        with self._lock:
            return len(self.results)

    def camera_slots(self) -> list[str]:
        with self._lock:
            return sorted(result.camera_slot for result in self.results)

    def last(self):
        with self._lock:
            return self.results[-1]


def _frame(width_px: int = 320, height_px: int = 240, level: int = 128) -> np.ndarray:
    return np.full((height_px, width_px, 3), level, np.uint8)


def _engine(pipeline: _FakePipeline, config: _MockConfig | None = None,
            **kwargs) -> InferenceThread:
    return InferenceThread(config or _MockConfig(), pipeline, **kwargs)


def _wait_until(condition, timeout_s: float = _WAIT_TIMEOUT_S) -> bool:
    deadline_s = time.monotonic() + timeout_s
    while time.monotonic() < deadline_s:
        if condition():
            return True
        time.sleep(0.01)
    return False


def _run_frames(engine: InferenceThread, frames: list[tuple], expected: int = 1) -> _Collector:
    """
    Corre el hilo, pushea (frame, slot) y espera `expected` resultados.

    Conecta la señal en directo: en la app la entrega el event loop de Qt, que en los
    tests no corre.
    """
    collector = _Collector()
    engine.result_ready.connect(collector.on_result, Qt.ConnectionType.DirectConnection)
    engine.start()
    try:
        for frame, camera_slot in frames:
            engine.push_frame(frame, camera_slot)
        assert _wait_until(lambda: collector.count() >= expected), \
            f"se esperaban {expected} resultados y llegaron {collector.count()}"
    finally:
        engine.requestInterruption()
        assert engine.wait(_WAIT_TIMEOUT_MS)
    return collector


def _process_one(engine: InferenceThread, frame: np.ndarray, camera_slot: str = _SLOT):
    return _run_frames(engine, [(frame, camera_slot)]).last()


# ── Construcción y estado ────────────────────────────────────────────────────

class TestConstruction:
    def test_the_slot_comes_from_the_pipeline(self):
        assert _engine(_FakePipeline()).pipeline_slot == _PIPELINE

    def test_cameras_are_read_from_its_own_section(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.cameras": [_SLOT, _OTHER_SLOT]})
        assert _engine(_FakePipeline(), config).camera_slots == (_SLOT, _OTHER_SLOT)

    def test_status_is_available_before_starting(self):
        status = _engine(_FakePipeline()).get_status()
        assert status["pipeline_slot"] == _PIPELINE
        assert status["running"] is False
        assert status["processed"] == 0

    def test_status_reports_the_models_of_the_pipeline(self):
        status = _engine(_FakePipeline()).get_status()
        assert "models" in status and status["loaded"] is True


# ── Cola de frames ───────────────────────────────────────────────────────────

class TestQueue:
    def test_an_empty_frame_is_ignored(self):
        engine = _engine(_FakePipeline())
        engine.push_frame(None, _SLOT)
        engine.push_frame(np.zeros((0, 0, 3), np.uint8), _SLOT)
        assert engine.get_status()["queued"] == 0

    def test_a_camera_outside_the_pipeline_is_rejected(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.cameras": [_SLOT]})
        engine = _engine(_FakePipeline(), config)
        engine.push_frame(_frame(), _OTHER_SLOT)
        assert engine.get_status()["queued"] == 0

    def test_without_a_declared_list_every_camera_is_accepted(self):
        engine = _engine(_FakePipeline())
        engine.push_frame(_frame(), _OTHER_SLOT)
        assert engine.get_status()["queued"] == 1

    def test_a_disabled_pipeline_takes_no_frames(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.enabled": False})
        engine = _engine(_FakePipeline(), config)
        engine.push_frame(_frame(), _SLOT)
        assert engine.get_status()["queued"] == 0

    def test_the_latest_frame_of_a_camera_wins(self):
        """Always-fresh: el frame sin consumir se descarta y se cuenta."""
        engine = _engine(_FakePipeline())
        engine.push_frame(_frame(level=10), _SLOT)
        engine.push_frame(_frame(level=200), _SLOT)
        status = engine.get_status()
        assert (status["queued"], status["dropped"]) == (1, 1)

    def test_frames_of_different_cameras_coexist(self):
        engine = _engine(_FakePipeline())
        engine.push_frame(_frame(), _SLOT)
        engine.push_frame(_frame(), _OTHER_SLOT)
        status = engine.get_status()
        assert (status["queued"], status["dropped"]) == (2, 0)

    def test_the_frame_is_copied_on_push(self):
        """El driver reusa su buffer en cuanto la llamada vuelve."""
        pipeline = _FakePipeline()
        engine = _engine(pipeline)
        frame = _frame(level=10)
        engine.push_frame(frame, _SLOT)
        frame[:] = 200
        _run_frames(engine, [])
        assert int(pipeline.frames[0][0, 0, 0]) == 10

    def test_the_turn_rotates_between_cameras(self):
        """Con el modelo más lento que la captura, una cámara no puede tapar a la otra."""
        engine = _engine(_FakePipeline())
        engine.push_frame(_frame(), _SLOT)
        engine.push_frame(_frame(), _OTHER_SLOT)
        taken = [engine._take_next()[0], engine._take_next()[0]]
        assert sorted(taken) == [_SLOT, _OTHER_SLOT]
        assert engine._take_next() is None

    def test_the_interval_drops_frames_that_arrive_early(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.interval_s": 60})
        engine = _engine(_FakePipeline(), config)
        engine.push_frame(_frame(), _SLOT)
        engine.push_frame(_frame(), _SLOT)
        status = engine.get_status()
        assert (status["queued"], status["dropped"]) == (1, 0)

    def test_ignore_interval_forces_the_frame(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.interval_s": 60})
        engine = _engine(_FakePipeline(), config)
        engine.push_frame(_frame(), _SLOT)
        engine._take_next()
        engine.push_frame(_frame(), _SLOT, ignore_interval=True)
        assert engine.get_status()["queued"] == 1


# ── ROI e iluminación ────────────────────────────────────────────────────────

class TestRoiAndIllumination:
    def test_the_pipeline_receives_the_cropped_roi(self):
        config = _MockConfig(**{f"cameras.{_SLOT}.roi": {
            "enabled": True, "x_px": 20, "y_px": 10, "width_px": 100, "height_px": 50}})
        pipeline = _FakePipeline()
        result = _process_one(_engine(pipeline, config), _frame())
        assert pipeline.frames[0].shape[:2] == (50, 100)
        assert result.roi_px == (20, 10, 100, 50)

    def test_detections_come_back_in_camera_coordinates(self):
        """El modelo trabaja en el espacio del recorte; el consumidor ve el frame entero."""
        config = _MockConfig(**{f"cameras.{_SLOT}.roi": {
            "enabled": True, "x_px": 20, "y_px": 10, "width_px": 100, "height_px": 50}})
        pipeline = _FakePipeline([(0, 90.0, (5, 5, 15, 15))])
        result = _process_one(_engine(pipeline, config), _frame())
        assert result.detections[0].bbox_px == (25, 15, 35, 25)
        assert result.detections[0].centroid_px == (30, 20)

    def test_a_zero_size_roi_reaches_the_border(self):
        config = _MockConfig(**{f"cameras.{_SLOT}.roi": {
            "enabled": True, "x_px": 120, "y_px": 40, "width_px": 0, "height_px": 0}})
        pipeline = _FakePipeline()
        _process_one(_engine(pipeline, config), _frame())
        assert pipeline.frames[0].shape[:2] == (200, 200)

    def test_a_disabled_roi_infers_the_whole_frame(self):
        config = _MockConfig(**{f"cameras.{_SLOT}.roi": {"enabled": False, "x_px": 20}})
        pipeline = _FakePipeline()
        result = _process_one(_engine(pipeline, config), _frame())
        assert pipeline.frames[0].shape[:2] == (240, 320)
        assert result.roi_px is None

    def test_a_roi_outside_the_frame_falls_back_to_the_whole_frame(self):
        config = _MockConfig(**{f"cameras.{_SLOT}.roi": {
            "enabled": True, "x_px": 319, "y_px": 239, "width_px": 100, "height_px": 100}})
        pipeline = _FakePipeline()
        result = _process_one(_engine(pipeline, config), _frame())
        assert result.roi_px == (319, 239, 1, 1)
        assert pipeline.frames[0].shape[:2] == (1, 1)

    def test_illumination_is_measured_on_the_analyzed_region(self):
        """Es el brillo del ROI, no el del frame: es la zona que la luz ilumina."""
        config = _MockConfig(**{f"cameras.{_SLOT}.roi": {
            "enabled": True, "x_px": 0, "y_px": 0, "width_px": 40, "height_px": 40}})
        frame = _frame(level=0)
        frame[0:40, 0:40] = 255
        assert _process_one(_engine(_FakePipeline(), config), frame).illumination_pct == 100

    def test_a_dark_frame_never_reaches_the_model(self):
        """Una imagen negra no vale una pasada del modelo, y su resultado no mide nada."""
        config = _MockConfig(**{f"cameras.{_SLOT}.illumination_min": 50})
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 10, 10))])
        result = _process_one(_engine(pipeline, config), _frame(level=5))
        assert pipeline.frames == []
        assert (result.is_valid, result.invalid_reason) == (False, REASON_DARK_FRAME)
        assert result.detections == []


class TestPreprocessor:
    """El frame de referencia es lo que devuelve el preprocessor: el modelo y el overlay
    miden y dibujan sobre la misma imagen, así que no pueden divergir."""

    def test_it_receives_the_frame_and_the_camera_slot(self):
        seen = []

        def preprocessor(frame_bgr, camera_slot):
            seen.append((frame_bgr.shape, camera_slot))
            return frame_bgr

        _process_one(_engine(_FakePipeline(), preprocessor=preprocessor), _frame())
        assert seen == [((240, 320, 3), _SLOT)]

    def test_the_model_sees_what_it_returned(self):
        pipeline = _FakePipeline()
        engine = _engine(pipeline, preprocessor=lambda frame_bgr, camera_slot: _frame(level=7))
        _process_one(engine, _frame(level=200))
        assert int(pipeline.frames[0][0, 0, 0]) == 7

    def test_the_result_carries_it_as_the_source_frame(self):
        """Es el lienzo del overlay: si acá quedara el crudo, las cajas caerían corridas."""
        engine = _engine(_FakePipeline(),
                         preprocessor=lambda frame_bgr, camera_slot: _frame(level=7))
        result = _process_one(engine, _frame(level=200))
        assert int(result.source_bgr[0, 0, 0]) == 7

    def test_the_roi_is_read_on_the_preprocessed_frame(self):
        """Corregir el lente puede recortar: el ROI se acota contra la imagen corregida."""
        config = _MockConfig(**{f"cameras.{_SLOT}.roi": {
            "enabled": True, "x_px": 0, "y_px": 0, "width_px": 200, "height_px": 200}})
        pipeline = _FakePipeline()
        engine = _engine(pipeline, config,
                         preprocessor=lambda frame_bgr, camera_slot: _frame(120, 100))
        result = _process_one(engine, _frame())
        assert result.roi_px == (0, 0, 120, 100)
        assert pipeline.frames[0].shape[:2] == (100, 120)

    def test_illumination_is_measured_on_the_preprocessed_frame(self):
        engine = _engine(_FakePipeline(),
                         preprocessor=lambda frame_bgr, camera_slot: _frame(level=255))
        assert _process_one(engine, _frame(level=0)).illumination_pct == 100

    def test_a_failing_preprocessor_falls_back_to_the_camera_frame(self):
        def broken_preprocessor(frame_bgr, camera_slot):
            raise ValueError("calibracion mal cargada")

        pipeline = _FakePipeline()
        engine = _engine(pipeline, preprocessor=broken_preprocessor)
        result = _process_one(engine, _frame(level=200))
        assert int(pipeline.frames[0][0, 0, 0]) == 200
        assert result.is_valid is True

    @pytest.mark.parametrize("returned", [None, np.zeros((0, 0, 3), np.uint8)])
    def test_an_empty_result_falls_back_to_the_camera_frame(self, returned):
        pipeline = _FakePipeline()
        engine = _engine(pipeline, preprocessor=lambda frame_bgr, camera_slot: returned)
        _process_one(engine, _frame(level=200))
        assert int(pipeline.frames[0][0, 0, 0]) == 200

    def test_without_a_preprocessor_the_camera_frame_is_measured(self):
        pipeline = _FakePipeline()
        _process_one(_engine(pipeline), _frame(level=200))
        assert int(pipeline.frames[0][0, 0, 0]) == 200


# ── Validez del resultado ────────────────────────────────────────────────────

class TestClassifier:
    def test_without_a_classifier_the_detections_pass_through(self):
        result = _process_one(_engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))])), _frame())
        assert result.detection_count == 1

    def test_it_receives_the_camera_slot(self):
        """Es la identidad que el modelo no tiene: uno solo sirve a todas las cámaras."""
        seen = []

        def classifier(detections, camera_slot):
            seen.append(camera_slot)
            return detections

        _process_one(_engine(_FakePipeline(), classifier=classifier), _frame(), _OTHER_SLOT)
        assert seen == [_OTHER_SLOT]

    def test_it_receives_the_detections_in_reference_frame_space(self):
        """Está calibrado en ese espacio: recibirlas en el del ROI las movería."""
        config = _MockConfig(**{f"cameras.{_SLOT}.roi": {
            "enabled": True, "x_px": 20, "y_px": 10, "width_px": 60, "height_px": 40}})
        seen = []
        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]), config,
                         classifier=lambda detections, slot: seen.append(
                             detections[0].bbox_px) or detections)
        _process_one(engine, _frame())
        assert seen == [(20, 10, 25, 15)]

    def test_what_it_drops_does_not_reach_the_result(self):
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5)), (1, 90.0, (6, 6, 10, 10))])
        engine = _engine(pipeline, classifier=lambda detections, slot: detections[:1])
        assert _process_one(engine, _frame()).detection_count == 1

    def test_what_it_drops_does_not_drag_the_confidence(self):
        """Filtrar después del promedio dejaría la confianza de detecciones descartadas."""
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5)), (1, 10.0, (6, 6, 10, 10))])
        engine = _engine(pipeline, classifier=lambda detections, slot: detections[:1])
        assert _process_one(engine, _frame()).confidence_pct == 90.0

    def test_what_it_drops_does_not_count_for_min_detections(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.min_detections": 2})
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5)), (1, 90.0, (6, 6, 10, 10))])
        engine = _engine(pipeline, config, classifier=lambda detections, slot: detections[:1])
        result = _process_one(engine, _frame())
        assert (result.is_valid, result.invalid_reason) == (False, REASON_FEW_DETECTIONS)

    def test_the_class_it_stamps_travels_in_the_result(self):
        """Es lo que después colorea el overlay y cuenta `analysis.count_by_class`."""
        def classifier(detections, camera_slot):
            for detection in detections:
                detection.class_index = 3
                detection.class_name = "coarse"
            return detections

        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]), classifier=classifier)
        detection = _process_one(engine, _frame()).detections[0]
        assert (detection.class_index, detection.class_name) == (3, "coarse")

    def test_dropping_everything_leaves_no_detections(self):
        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]),
                         classifier=lambda detections, slot: None)
        assert _process_one(engine, _frame()).detections == []

    def test_a_failing_classifier_does_not_break_the_result(self):
        def broken_classifier(detections, camera_slot):
            raise ValueError("escala sin configurar")

        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]),
                         classifier=broken_classifier)
        result = _process_one(engine, _frame())
        assert (result.is_valid, result.detection_count) == (True, 1)


class TestValidity:
    def test_a_full_result_is_valid(self):
        result = _process_one(_engine(_FakePipeline([(0, 90.0, (0, 0, 10, 10))])), _frame())
        assert (result.is_valid, result.invalid_reason) == (True, "")

    def test_without_a_loaded_model_nothing_is_measured(self):
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 10, 10))], loaded=False)
        result = _process_one(_engine(pipeline), _frame())
        assert pipeline.frames == []
        assert (result.is_valid, result.invalid_reason) == (False, REASON_NO_MODEL)

    def test_a_failing_stage_invalidates_the_result_without_killing_the_thread(self):
        result = _process_one(_engine(_FakePipeline(raises=True)), _frame())
        assert (result.is_valid, result.invalid_reason) == (False, REASON_ERROR)

    def test_too_few_detections_invalidate_the_result(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.min_detections": 2})
        result = _process_one(_engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]), config), _frame())
        assert (result.is_valid, result.invalid_reason) == (False, REASON_FEW_DETECTIONS)

    def test_low_average_confidence_invalidates_the_result(self):
        config = _MockConfig(
            **{f"inference.pipelines.{_PIPELINE}.min_result_confidence_pct": 80})
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5)), (1, 50.0, (6, 6, 10, 10))])
        result = _process_one(_engine(pipeline, config), _frame())
        assert result.confidence_pct == 70.0
        assert (result.is_valid, result.invalid_reason) == (False, REASON_LOW_CONFIDENCE)

    def test_an_invalid_result_keeps_its_detections(self):
        """El anotado las muestra igual y el recolector de dataset puede filtrar por ellas."""
        config = _MockConfig(
            **{f"inference.pipelines.{_PIPELINE}.min_result_confidence_pct": 99})
        result = _process_one(_engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]), config), _frame())
        assert result.detection_count == 1

    def test_confidence_is_the_average_of_the_detections(self):
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5)), (1, 70.0, (6, 6, 10, 10))])
        assert _process_one(_engine(pipeline), _frame()).confidence_pct == 80.0

    def test_the_synthetic_marker_travels_in_the_result(self):
        result = _process_one(_engine(_FakePipeline(synthetic=True)), _frame())
        assert result.is_synthetic is True

    def test_invalid_results_are_counted(self):
        engine = _engine(_FakePipeline(raises=True))
        _run_frames(engine, [(_frame(), _SLOT)])
        status = engine.get_status()
        assert (status["processed"], status["invalid"]) == (1, 1)


# ── Analyzer inyectado ───────────────────────────────────────────────────────

class TestLabels:
    def test_the_verdict_of_the_pipeline_reaches_the_result(self):
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5))], labels={"belt": "full"})
        assert _process_one(_engine(pipeline), _frame()).labels == {"belt": "full"}

    def test_without_labels_the_result_has_none(self):
        assert _process_one(_engine(_FakePipeline()), _frame()).labels == {}

    def test_a_frame_that_never_ran_has_no_labels(self):
        """Sin modelo no hay veredicto: no se arrastra el del frame anterior."""
        pipeline = _FakePipeline(labels={"belt": "full"}, loaded=False)
        assert _process_one(_engine(pipeline), _frame()).labels == {}

    def test_the_analyzer_can_read_the_labels(self):
        """Es como un veredicto de texto termina siendo un bit para el PLC."""
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5))], labels={"belt": "full"})
        engine = _engine(pipeline,
                         analyzer=lambda result: {"belt_full": result.labels.get("belt") == "full"})
        assert _process_one(engine, _frame()).metrics == {"belt_full": True}


class TestAnalyzer:
    def test_the_analyzer_fills_the_metrics(self):
        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]),
                         analyzer=lambda result: {"count": result.detection_count})
        assert _process_one(engine, _frame()).metrics == {"count": 1}

    def test_without_an_analyzer_the_metrics_stay_empty(self):
        assert _process_one(_engine(_FakePipeline()), _frame()).metrics == {}

    def test_an_invalid_result_is_not_analyzed(self):
        """Una métrica calculada sobre un frame que no midió nada es peor que ninguna."""
        calls = []
        engine = _engine(_FakePipeline(raises=True),
                         analyzer=lambda result: calls.append(result) or {"count": 1})
        result = _process_one(engine, _frame())
        assert (calls, result.metrics) == ([], {})

    def test_a_failing_analyzer_does_not_break_the_result(self):
        def broken_analyzer(result):
            raise ValueError("cuenta mal escrita")

        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]), analyzer=broken_analyzer)
        result = _process_one(engine, _frame())
        assert (result.is_valid, result.metrics) == (True, {})

    def test_the_analyzer_sees_the_inference_time(self):
        """Un proyecto que publica el tiempo al PLC publicaría un cero medido."""
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5))], delay_s=0.02)
        engine = _engine(pipeline,
                         analyzer=lambda result: {"elapsed_ms": result.inference_time_ms})
        result = _process_one(engine, _frame())
        assert result.metrics["elapsed_ms"] >= 20.0


# ── Frame anotado ────────────────────────────────────────────────────────────

class TestAnnotation:
    def test_the_result_carries_an_annotated_frame(self):
        result = _process_one(_engine(_FakePipeline([(0, 90.0, (10, 10, 40, 40))])), _frame())
        assert result.annotated_bgr is not None
        assert result.annotated_bgr.shape == (240, 320, 3)

    def test_the_camera_frame_is_never_touched(self):
        frame = _frame(level=128)
        result = _process_one(_engine(_FakePipeline([(0, 90.0, (10, 10, 40, 40))])), frame)
        assert np.array_equal(result.source_bgr, np.full((240, 320, 3), 128, np.uint8))

    def test_the_config_can_turn_the_overlay_off(self):
        config = _MockConfig(**{"inference.overlay.enabled": False})
        assert _process_one(_engine(_FakePipeline(), config), _frame()).annotated_bgr is None

    def test_nothing_is_drawn_when_nobody_is_watching(self):
        """Dibujar cuesta más que codificar: sin clientes no se anota."""
        engine = _engine(_FakePipeline(), annotate_gate=lambda camera_slot: False)
        assert _process_one(engine, _frame()).annotated_bgr is None

    def test_the_cycle_frame_is_drawn_even_when_nobody_is_watching(self):
        """
        `annotate()` saltea el gate a propósito.

        Es un dibujo por medición, no por frame, así que el costo que el gate cuida no
        aplica. Y como la pregunta se contesta en el momento del dibujo, respetarlo dejaba
        la medición sin imagen durante todo el ciclo por haber tenido la UI en crudo justo
        en ese instante — y sin nada guardado para el que se conectara después.
        """
        engine = _engine(_FakePipeline([(0, 90.0, (10, 10, 40, 40))]),
                         annotate_gate=lambda camera_slot: False)
        result = _process_one(engine, _frame())
        assert result.annotated_bgr is None          # el camino por frame sí lo respeta
        assert engine.annotate(result) is not None   # la medición, no

    def test_the_cycle_frame_still_obeys_the_config(self):
        """El gate es una optimización; apagar el overlay es una decisión."""
        config = _MockConfig(**{"inference.overlay.enabled": False})
        engine = _engine(_FakePipeline(), config)
        assert engine.annotate(_process_one(engine, _frame())) is None

    def test_the_annotator_draws_over_the_standard_overlay(self):
        seen = []

        def annotator(frame_bgr, result):
            seen.append((frame_bgr.shape, result.camera_slot))
            frame_bgr[0, 0] = (0, 0, 255)

        result = _process_one(_engine(_FakePipeline(), annotator=annotator), _frame())
        assert seen == [((240, 320, 3), _SLOT)]
        assert tuple(result.annotated_bgr[0, 0]) == (0, 0, 255)

    def test_the_annotator_does_not_run_when_nothing_is_drawn(self):
        """Sin frame anotado no hay dónde dibujar, y el gate ya dijo que nadie mira."""
        calls = []
        engine = _engine(_FakePipeline(), annotator=lambda frame_bgr, result: calls.append(1),
                         annotate_gate=lambda camera_slot: False)
        assert _process_one(engine, _frame()).annotated_bgr is None
        assert calls == []

    def test_a_failing_annotator_keeps_the_standard_frame(self):
        def broken_annotator(frame_bgr, result):
            raise ValueError("linea mal configurada")

        result = _process_one(_engine(_FakePipeline(), annotator=broken_annotator), _frame())
        assert result.annotated_bgr is not None

    def test_the_annotator_also_sees_invalid_results(self):
        """La referencia que dibuja sirve igual —o más— cuando la medición no vale."""
        seen = []
        engine = _engine(_FakePipeline(raises=True),
                         annotator=lambda frame_bgr, result: seen.append(result.is_valid))
        _process_one(engine, _frame())
        assert seen == [False]

    def test_the_gate_receives_the_camera_slot(self):
        seen = []
        engine = _engine(_FakePipeline(),
                         annotate_gate=lambda camera_slot: seen.append(camera_slot))
        _process_one(engine, _frame())
        assert seen == [_SLOT]


# ── Ciclo de vida ────────────────────────────────────────────────────────────

class TestLifecycle:
    def test_the_thread_loads_and_unloads_the_pipeline(self):
        pipeline = _FakePipeline()
        engine = _engine(pipeline)
        engine.start()
        assert _wait_until(lambda: pipeline.load_calls == 1)
        engine.requestInterruption()
        assert engine.wait(_WAIT_TIMEOUT_MS)
        assert (pipeline.load_calls, pipeline.unload_calls) == (1, 1)

    def test_it_stops_with_frames_still_queued(self):
        engine = _engine(_FakePipeline(delay_s=0.05))
        engine.start()
        for level in range(10):
            engine.push_frame(_frame(level=level * 20), f"camera_{level}")
        engine.requestInterruption()
        assert engine.wait(_WAIT_TIMEOUT_MS)

    def test_it_serves_every_camera_of_the_pipeline(self):
        """Fan-in: un solo hilo y una sola copia de los pesos para las dos cámaras."""
        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]))
        collector = _run_frames(engine, [(_frame(), _SLOT), (_frame(), _OTHER_SLOT)], expected=2)
        assert collector.camera_slots() == [_SLOT, _OTHER_SLOT]

    def test_the_result_names_its_pipeline_and_camera(self):
        result = _process_one(_engine(_FakePipeline()), _frame())
        assert (result.camera_slot, result.pipeline_slot) == (_SLOT, _PIPELINE)

    def test_the_inference_time_is_measured(self):
        result = _process_one(_engine(_FakePipeline(delay_s=0.02)), _frame())
        assert result.inference_time_ms >= 20
