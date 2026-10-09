"""Tests de la maquinaria del pipeline: carga de las etapas por slot, tiempos por etapa,
encadenado en cascada y el pipeline de una etapa de este proyecto.

Los modelos son dobles: acá se verifica el andamio de las etapas, no un framework.
"""

import numpy as np
import pytest

from system.inference.models.abstract_model import AbstractModel
from system.inference.abstract_pipeline import AbstractPipeline
from system.inference.pipeline import BeltPipeline
from system.inference.result import Detection

_PIPELINE = "pipeline_1"
_SLOT = "camera_1"


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **sections):
        self._values = {}
        for model_slot, section in sections.items():
            self._values[f"inference.models.{model_slot}"] = section
            for key, value in section.items():
                self._values[f"inference.models.{model_slot}.{key}"] = value

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _FakeModel(AbstractModel):
    """Modelo programable: registra qué frame y qué detecciones previas le llegaron."""

    def __init__(self, config_manager, model_slot: str, *, loads: bool = True):
        super().__init__(config_manager, model_slot)
        self._loads = loads
        self.calls: list[tuple] = []
        # Lo que devuelve `predict()`. None = una detección por defecto, que es lo que
        # necesitan los tests del andamio; los del postproceso la programan.
        self.detections: list | None = None

    def load(self):
        if self._loads:
            self._set_loaded()
        else:
            self._set_error("pesos ausentes")

    def predict(self, frame_bgr, previous=None) -> list[Detection]:
        self.calls.append((frame_bgr.shape, list(previous or [])))
        if self.detections is not None:
            return list(self.detections)
        return [Detection(class_index=0, class_name=self.model_slot,
                          confidence_pct=90.0, bbox_px=(0, 0, 10, 10))]

    def unload(self):
        self._status = "unloaded"


def _frame(width_px: int = 64, height_px: int = 48) -> np.ndarray:
    return np.full((height_px, width_px, 3), 128, np.uint8)


def _install_models(monkeypatch, models: dict):
    """Reemplaza la fábrica: la elección de la clase concreta se testea en su propio test."""
    import system.inference.abstract_pipeline as ap
    monkeypatch.setattr(ap, "create_model", lambda config, model_slot: models[model_slot])


class _LabellingPipeline(AbstractPipeline):
    """Deja un veredicto de frame y saltea la segunda etapa cuando no hace falta."""

    model_slots = ("classifier", "segmenter")

    def _run(self, frame_bgr, camera_slot):
        verdict = self._run_stage("classifier", frame_bgr)
        self._set_label("belt", "full" if verdict else "empty")
        if not verdict:
            return []
        return self._run_stage("segmenter", frame_bgr)


class _TwoStagePipeline(AbstractPipeline):
    """Cascada: la segunda etapa recibe lo que encontró la primera."""

    model_slots = ("detector", "classifier")

    def _run(self, frame_bgr, camera_slot):
        detections = self._run_stage("detector", frame_bgr)
        return self._run_stage("classifier", frame_bgr, detections)


class TestLoading:
    def test_it_loads_one_model_per_declared_slot(self, monkeypatch):
        config = _MockConfig(segmenter={"type": "mock"})
        models = {"segmenter": _FakeModel(config, "segmenter")}
        _install_models(monkeypatch, models)
        pipeline = BeltPipeline(config, _PIPELINE)
        pipeline.load()
        assert pipeline.is_loaded is True
        assert set(pipeline.get_status()["models"]) == {"segmenter"}

    def test_loading_twice_does_not_reload(self, monkeypatch):
        config = _MockConfig(segmenter={"type": "mock"})
        created = []
        import system.inference.abstract_pipeline as ap
        monkeypatch.setattr(ap, "create_model", lambda config, model_slot:
                            created.append(model_slot) or _FakeModel(config, model_slot))
        pipeline = BeltPipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.load()
        assert created == ["segmenter"]

    def test_a_stage_that_could_not_load_leaves_the_pipeline_unloaded(self, monkeypatch):
        config = _MockConfig(segmenter={"type": "mock"})
        _install_models(monkeypatch, {"segmenter": _FakeModel(config, "segmenter", loads=False)})
        pipeline = BeltPipeline(config, _PIPELINE)
        pipeline.load()
        assert pipeline.is_loaded is False
        assert pipeline.get_status()["models"]["segmenter"]["error"] == "pesos ausentes"

    def test_a_pipeline_without_stages_is_never_loaded(self):
        class _EmptyPipeline(AbstractPipeline):
            def _run(self, frame_bgr, camera_slot):
                return []

        pipeline = _EmptyPipeline(_MockConfig(), _PIPELINE)
        pipeline.load()
        assert pipeline.is_loaded is False

    def test_unload_releases_every_stage(self, monkeypatch):
        config = _MockConfig(segmenter={"type": "mock"})
        _install_models(monkeypatch, {"segmenter": _FakeModel(config, "segmenter")})
        pipeline = BeltPipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        pipeline.unload()
        assert pipeline.get_status()["models"] == {}
        assert (pipeline.stage_times_ms, pipeline.labels) == ({}, {})


class TestRunning:
    def test_the_template_pipeline_runs_its_single_stage(self, monkeypatch):
        config = _MockConfig(segmenter={"type": "mock"})
        model = _FakeModel(config, "segmenter")
        _install_models(monkeypatch, {"segmenter": model})
        pipeline = BeltPipeline(config, _PIPELINE)
        pipeline.load()
        detections = pipeline.run(_frame(), _SLOT)
        assert [d.class_name for d in detections] == ["segmenter"]
        assert len(model.calls) == 1

    def test_a_stage_without_a_model_returns_nothing(self, monkeypatch):
        config = _MockConfig(segmenter={"type": "mock"})
        _install_models(monkeypatch, {"segmenter": _FakeModel(config, "segmenter", loads=False)})
        pipeline = BeltPipeline(config, _PIPELINE)
        pipeline.load()
        assert pipeline.run(_frame(), _SLOT) == []

    def test_each_stage_gets_its_own_time(self, monkeypatch):
        config = _MockConfig(detector={"type": "mock"}, classifier={"type": "mock"})
        _install_models(monkeypatch, {"detector": _FakeModel(config, "detector"),
                                      "classifier": _FakeModel(config, "classifier")})
        pipeline = _TwoStagePipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        assert set(pipeline.stage_times_ms) == {"detector", "classifier"}

    def test_the_times_are_those_of_the_last_run(self, monkeypatch):
        config = _MockConfig(detector={"type": "mock"}, classifier={"type": "mock"})
        _install_models(monkeypatch, {"detector": _FakeModel(config, "detector"),
                                      "classifier": _FakeModel(config, "classifier")})
        pipeline = _TwoStagePipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        pipeline._models.pop("classifier")
        pipeline.run(_frame(), _SLOT)
        assert set(pipeline.stage_times_ms) == {"detector"}

    def test_a_cascade_hands_the_previous_detections_over(self, monkeypatch):
        """Es lo que hace posible detectar y después clasificar cada detección."""
        config = _MockConfig(detector={"type": "mock"}, classifier={"type": "mock"})
        classifier = _FakeModel(config, "classifier")
        _install_models(monkeypatch, {"detector": _FakeModel(config, "detector"),
                                      "classifier": classifier})
        pipeline = _TwoStagePipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        _, previous = classifier.calls[0]
        assert [d.class_name for d in previous] == ["detector"]

    def test_the_first_stage_gets_no_previous_detections(self, monkeypatch):
        config = _MockConfig(segmenter={"type": "mock"})
        model = _FakeModel(config, "segmenter")
        _install_models(monkeypatch, {"segmenter": model})
        pipeline = BeltPipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        assert model.calls[0][1] == []

    def test_a_failing_stage_propagates_to_the_caller(self, monkeypatch):
        """El motor es el que decide qué hacer con el error, no el pipeline."""
        config = _MockConfig(segmenter={"type": "mock"})
        model = _FakeModel(config, "segmenter")
        model.predict = lambda frame_bgr, previous=None: (_ for _ in ()).throw(RuntimeError("cuda"))
        _install_models(monkeypatch, {"segmenter": model})
        pipeline = BeltPipeline(config, _PIPELINE)
        pipeline.load()
        with pytest.raises(RuntimeError):
            pipeline.run(_frame(), _SLOT)
        assert "segmenter" in pipeline.stage_times_ms

    def test_a_stage_can_leave_a_frame_verdict(self, monkeypatch):
        config = _MockConfig(classifier={"type": "mock"}, segmenter={"type": "mock"})
        _install_models(monkeypatch, {"classifier": _FakeModel(config, "classifier"),
                                      "segmenter": _FakeModel(config, "segmenter")})
        pipeline = _LabellingPipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        assert pipeline.labels == {"belt": "full"}

    def test_a_skipped_stage_leaves_the_verdict_that_skipped_it(self, monkeypatch):
        """La etapa que no corrió no deja tiempo, pero se ve por qué no corrió."""
        config = _MockConfig(classifier={"type": "mock"}, segmenter={"type": "mock"})
        classifier = _FakeModel(config, "classifier")
        classifier.predict = lambda frame_bgr, previous=None: []
        _install_models(monkeypatch, {"classifier": classifier,
                                      "segmenter": _FakeModel(config, "segmenter")})
        pipeline = _LabellingPipeline(config, _PIPELINE)
        pipeline.load()
        assert pipeline.run(_frame(), _SLOT) == []
        assert pipeline.labels == {"belt": "empty"}
        assert set(pipeline.stage_times_ms) == {"classifier"}

    def test_a_verdict_does_not_survive_the_next_frame(self, monkeypatch):
        config = _MockConfig(classifier={"type": "mock"}, segmenter={"type": "mock"})
        classifier = _FakeModel(config, "classifier")
        _install_models(monkeypatch, {"classifier": classifier,
                                      "segmenter": _FakeModel(config, "segmenter")})
        pipeline = _LabellingPipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        classifier.predict = lambda frame_bgr, previous=None: []
        pipeline.run(_frame(), _SLOT)
        assert pipeline.labels == {"belt": "empty"}

    def test_the_labels_are_a_copy(self, monkeypatch):
        config = _MockConfig(classifier={"type": "mock"}, segmenter={"type": "mock"})
        _install_models(monkeypatch, {"classifier": _FakeModel(config, "classifier"),
                                      "segmenter": _FakeModel(config, "segmenter")})
        pipeline = _LabellingPipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        pipeline.labels["belt"] = "empty"
        assert pipeline.labels == {"belt": "full"}

    def test_a_synthetic_stage_marks_the_pipeline(self, monkeypatch):
        config = _MockConfig(segmenter={"type": "mock"})
        model = _FakeModel(config, "segmenter")
        model.is_synthetic = True
        _install_models(monkeypatch, {"segmenter": model})
        pipeline = BeltPipeline(config, _PIPELINE)
        pipeline.load()
        assert pipeline.is_synthetic is True


# ── Descarte de fondo oscuro ─────────────────────────────────────────────────

class TestDarkBackground:
    """
    El postproceso del proyecto: sacar de cada máscara la cinta que el contorno se tragó.

    Corre acá y no en el modelo porque el umbral depende de la iluminación, que es de cada
    cámara; y no en el analyzer porque ahí el overlay seguiría pintando lo que se descartó.
    """

    _OTHER_SLOT = "camera_2"

    def _detection(self, class_name: str = "desmenuzado") -> Detection:
        """Detección que cubre el frame entero, para que el recorte por luma se vea solo."""
        mask = np.ones((40, 40), np.uint8)
        return Detection(class_index=0, class_name=class_name, confidence_pct=90.0,
                         bbox_px=(0, 0, 40, 40), area_px=int(mask.sum()), mask=mask)

    def _frame(self) -> np.ndarray:
        """Mitad de arriba clara, mitad de abajo oscura."""
        frame = np.full((40, 40, 3), 200, np.uint8)
        frame[20:, :, :] = 10
        return frame

    def _pipeline(self, monkeypatch, thresholds: dict, *,
                  class_name: str = "desmenuzado") -> BeltPipeline:
        config = _MockConfig(segmenter={"type": "mock"})
        model = _FakeModel(config, "segmenter")
        model.detections = [self._detection(class_name)]
        _install_models(monkeypatch, {"segmenter": model})
        pipeline = BeltPipeline(config, _PIPELINE, dark_background_threshold=thresholds)
        pipeline.load()
        return pipeline

    def test_dark_pixels_leave_the_mask(self, monkeypatch):
        pipeline = self._pipeline(monkeypatch, {_SLOT: {"desmenuzado": 60}})
        assert pipeline.run(self._frame(), _SLOT)[0].area_px == 20 * 40

    def test_the_threshold_is_per_camera(self, monkeypatch):
        """Dos cámaras con distinta luz son dos umbrales: es la razón de estar acá."""
        pipeline = self._pipeline(monkeypatch, {_SLOT: {"desmenuzado": 60}})
        assert pipeline.run(self._frame(), self._OTHER_SLOT)[0].area_px == 40 * 40

    def test_a_class_without_a_threshold_is_untouched(self, monkeypatch):
        pipeline = self._pipeline(monkeypatch, {_SLOT: {"desmenuzado": 60}},
                                  class_name="pellet")
        assert pipeline.run(self._frame(), _SLOT)[0].area_px == 40 * 40

    def test_a_zero_threshold_disables_it(self, monkeypatch):
        pipeline = self._pipeline(monkeypatch, {_SLOT: {"desmenuzado": 0}})
        assert pipeline.run(self._frame(), _SLOT)[0].area_px == 40 * 40

    def test_without_thresholds_nothing_is_refined(self, monkeypatch):
        pipeline = self._pipeline(monkeypatch, {})
        assert pipeline.run(self._frame(), _SLOT)[0].area_px == 40 * 40

    def test_a_mask_left_empty_is_dropped(self, monkeypatch):
        """Una instancia que ya no cubre nada inflaría el conteo y la confianza."""
        pipeline = self._pipeline(monkeypatch, {_SLOT: {"desmenuzado": 255}})
        assert pipeline.run(self._frame(), _SLOT) == []

    def test_the_area_matches_the_refined_mask(self, monkeypatch):
        pipeline = self._pipeline(monkeypatch, {_SLOT: {"desmenuzado": 60}})
        detection = pipeline.run(self._frame(), _SLOT)[0]
        assert detection.area_px == int(detection.mask.sum())
