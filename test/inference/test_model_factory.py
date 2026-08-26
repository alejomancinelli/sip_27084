"""Tests de la fábrica de modelos, el NullModel, el MockModel y los helpers de config
del contrato.

Sin framework de inferencia instalado: el mock es justamente lo que permite ejercitar el
contrato completo en un equipo sin GPU.
"""

import numpy as np
import pytest

from system.inference.abstract_model import (STATUS_ERROR, STATUS_LOADED, STATUS_UNLOADED,
                                             TASK_CLASSIFICATION, TASK_DETECTION,
                                             TASK_SEGMENTATION, AbstractModel,
                                             normalize_task)
from system.inference.mock_model import MockModel
from system.inference.model_factory import REGISTERED_MODELS, create_model
from system.inference.null_model import NullModel

_SLOT = "model_1"
_STATUS_KEYS = {"model_slot", "task", "status", "loaded", "synthetic", "error"}


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, section: dict | None = None):
        self._values = {}
        if section is not None:
            self._values[f"inference.models.{_SLOT}"] = section
            for key, value in section.items():
                self._values[f"inference.models.{_SLOT}.{key}"] = value

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


def _frame(width_px: int = 300, height_px: int = 200) -> np.ndarray:
    return np.full((height_px, width_px, 3), 128, np.uint8)


def _mock(**section) -> MockModel:
    section.setdefault("type", "mock")
    model = MockModel(_MockConfig(section), _SLOT)
    model.load()
    return model


class TestModelFactory:
    def test_mock_model_created_for_mock(self):
        assert isinstance(create_model(_MockConfig({"type": "mock"}), _SLOT), MockModel)

    def test_model_type_is_normalized(self):
        assert isinstance(create_model(_MockConfig({"type": "  MOCK  "}), _SLOT), MockModel)

    def test_model_is_abstract_subclass(self):
        assert isinstance(create_model(_MockConfig({"type": "mock"}), _SLOT), AbstractModel)

    def test_the_model_keeps_its_slot_as_identity(self):
        assert create_model(_MockConfig({"type": "mock"}), _SLOT).model_slot == _SLOT

    def test_registered_models_matches_what_the_factory_builds(self):
        """La lista que se publica para la UI y los mensajes no puede mentir."""
        for model_type in REGISTERED_MODELS:
            model = create_model(_MockConfig({"type": model_type}), _SLOT)
            assert not model.is_config_error, model_type

    def test_the_factory_does_not_load_the_model(self):
        """Cargar pesos puede tardar minutos: lo decide quien arma el pipeline."""
        assert create_model(_MockConfig({"type": "mock"}), _SLOT).status == STATUS_UNLOADED


class TestModelFactoryConfigErrors:
    """Una config inválida NO debe caer en el mock: detecciones sintéticas con estado
    'cargado' le esconderían el error a la UI y al PLC."""

    @pytest.mark.parametrize("section", [
        None,                              # sección ausente
        {},                                # sección vacía
        {"path": "weights.pt"},            # sin 'type'
        {"type": None},                    # type: null en YAML
        {"type": "   "},                   # string vacío
        {"type": "modelo_inexistente"},    # sin registrar
    ])
    def test_an_invalid_section_returns_a_null_model(self, section):
        model = create_model(_MockConfig(section), _SLOT)
        assert isinstance(model, NullModel)
        assert model.is_config_error is True

    def test_the_null_model_never_loads_nor_detects(self):
        model = create_model(_MockConfig(None), _SLOT)
        model.load()
        assert model.status == STATUS_ERROR
        assert model.is_loaded is False
        assert model.predict(_frame()) == []

    def test_the_null_model_reports_why(self):
        model = create_model(_MockConfig({"type": "modelo_inexistente"}), _SLOT)
        assert model.error and "modelo_inexistente" in model.error

    def test_the_error_survives_unload(self):
        model = create_model(_MockConfig(None), _SLOT)
        model.unload()
        assert model.status == STATUS_ERROR


class TestModelStatus:
    def test_status_keys_are_stable(self):
        assert set(_mock().get_status()) == _STATUS_KEYS

    def test_a_loaded_model_reports_loaded(self):
        model = _mock()
        assert (model.status, model.is_loaded, model.error) == (STATUS_LOADED, True, None)

    def test_unload_goes_back_to_unloaded(self):
        model = _mock()
        model.unload()
        assert model.status == STATUS_UNLOADED

    def test_the_synthetic_marker_is_published(self):
        assert _mock().get_status()["synthetic"] is True

    def test_the_task_is_published(self):
        """Es lo que dice cómo se decodifica la salida: la UI y los tests lo leen de acá."""
        assert _mock().get_status()["task"] == TASK_DETECTION


class TestTaskGuard:
    """Un .engine de detección cargado como segmentador tiene que fallar al cargar, no
    adentro del postproceso con la máquina ya instalada."""

    @pytest.mark.parametrize("reported, expected", [
        ("detect", TASK_DETECTION),
        ("DETECT", TASK_DETECTION),
        ("  segment  ", TASK_SEGMENTATION),
        ("classify", TASK_CLASSIFICATION),
        ("pose", "keypoints"),
        (TASK_SEGMENTATION, TASK_SEGMENTATION),
    ])
    def test_the_framework_vocabulary_is_understood(self, reported, expected):
        assert normalize_task(reported) == expected

    @pytest.mark.parametrize("reported", ["", None, "lo_que_sea"])
    def test_an_unknown_task_is_not_a_task(self, reported):
        assert normalize_task(reported) == ""

    def test_a_matching_task_passes(self):
        model = MockModel(_MockConfig({"type": "mock"}), _SLOT)
        assert model._verify_task("detect") is True

    def test_a_mismatch_leaves_the_model_in_error(self):
        model = MockModel(_MockConfig({"type": "mock"}), _SLOT)
        assert model._verify_task("segment") is False
        assert model.status == STATUS_ERROR
        assert "segmentation" in model.error and "detection" in model.error

    def test_metadata_that_says_nothing_cannot_disagree(self):
        """Un .pt de torch pelado no informa tarea: no poder verificar no es un error."""
        model = MockModel(_MockConfig({"type": "mock"}), _SLOT)
        assert model._verify_task("") is True
        assert model.status == STATUS_UNLOADED

    def test_the_guard_runs_inside_load(self):
        model = _mock(params={"task": "segment"})
        assert model.is_loaded is False
        assert model.status == STATUS_ERROR

    def test_a_model_that_failed_the_guard_detects_nothing(self):
        assert _mock(params={"task": "classify"}).predict(_frame()) == []


class TestModelConfig:
    def test_class_names_come_from_the_config(self):
        detections = _mock(class_names=["grain", "stone"]).predict(_frame())
        assert [d.class_name for d in detections][:2] == ["grain", "stone"]

    def test_a_class_without_a_name_falls_back_to_its_index(self):
        detections = _mock(class_names=["grain"]).predict(_frame())
        assert detections[1].class_name == "class_1"

    def test_the_model_applies_its_own_confidence_threshold(self):
        """El umbral por detección es del modelo: el llamador no vuelve a filtrar."""
        detections = _mock(min_confidence_pct=90).predict(_frame())
        assert [d.confidence_pct for d in detections] == [95.0]

    def test_a_relative_path_is_anchored_to_the_repo_root(self):
        model = _mock(path="./models/weights.pt")
        assert model._get_model_path().endswith("weights.pt")
        assert "models" in model._get_model_path()

    def test_an_empty_path_stays_empty(self):
        assert _mock(path="")._get_model_path() == ""


class TestMockModel:
    def test_it_detects_without_any_framework(self):
        assert len(_mock().predict(_frame())) == 3

    def test_the_detection_count_is_configurable(self):
        assert len(_mock(params={"detection_count": 5}).predict(_frame())) == 5

    def test_detections_fit_inside_the_frame(self):
        frame = _frame()
        for detection in _mock(params={"detection_count": 6}).predict(frame):
            x1, y1, x2, y2 = detection.bbox_px
            assert 0 <= x1 < x2 <= frame.shape[1]
            assert 0 <= y1 < y2 <= frame.shape[0]

    def test_confidence_decreases_along_the_frame(self):
        scores = [d.confidence_pct for d in _mock(min_confidence_pct=0).predict(_frame())]
        assert scores == sorted(scores, reverse=True)

    def test_masks_are_the_size_of_their_bbox(self):
        for detection in _mock(params={"with_masks": True}).predict(_frame()):
            x1, y1, x2, y2 = detection.bbox_px
            assert detection.mask.shape == (y2 - y1, x2 - x1)

    def test_the_area_of_a_mask_is_smaller_than_its_bbox(self):
        detection = _mock(params={"with_masks": True}).predict(_frame())[0]
        x1, y1, x2, y2 = detection.bbox_px
        assert 0 < detection.area_px < (x2 - x1) * (y2 - y1)

    def test_without_masks_the_area_is_the_bbox(self):
        detection = _mock().predict(_frame())[0]
        x1, y1, x2, y2 = detection.bbox_px
        assert detection.area_px == (x2 - x1) * (y2 - y1)

    def test_an_unloaded_model_detects_nothing(self):
        model = MockModel(_MockConfig({"type": "mock"}), _SLOT)
        assert model.predict(_frame()) == []

    def test_an_empty_frame_detects_nothing(self):
        assert _mock().predict(np.zeros((0, 0, 3), np.uint8)) == []
