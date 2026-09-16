"""Tests del modelo de segmentación: guardas de `load()`, traducción de la salida de
ultralytics a detecciones del contrato y vuelta de coordenadas cuando se reduce el frame.

Ultralytics es un doble: acá se verifica el postproceso —qué máscara, qué caja, qué
umbral—, no el framework. Sin GPU y sin pesos.
"""

import sys
import types

import numpy as np
import pytest

from system.inference.models import yolo_seg_model
from system.inference.models.abstract_model import (
    STATUS_ERROR, STATUS_LOADED, TASK_SEGMENTATION,
)
from system.inference.models.yolo_seg_model import YoloSegModel

_SLOT = "segmenter"
_FRAME_SIDE_PX = 100


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            f"inference.models.{_SLOT}.path": "pesos.engine",
            f"inference.models.{_SLOT}.min_confidence_pct": 20.0,
            f"inference.models.{_SLOT}.max_side_px": 0,
            f"inference.models.{_SLOT}.class_names": ["desmenuzado", "pellet"],
            f"inference.models.{_SLOT}.params": {},
        }
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _FakeTensor(list):
    """Lo que ultralytics devuelve en `boxes.cls` / `boxes.conf`: tiene `tolist()`."""

    def tolist(self) -> list:
        return list(self)


class _FakeBoxes:
    def __init__(self, class_indices: list, confidences: list):
        self.cls = _FakeTensor(class_indices)
        self.conf = _FakeTensor(confidences)

    def __len__(self) -> int:
        return len(self.cls)


class _FakeMasks:
    def __init__(self, polygons: list):
        self.xy = polygons


class _FakePrediction:
    def __init__(self, polygons: list, class_indices: list, confidences: list):
        self.masks = _FakeMasks(polygons) if polygons is not None else None
        self.boxes = _FakeBoxes(class_indices, confidences)


class _FakeYolo:
    """El objeto que devuelve `YOLO(path, task=...)`: declara su tarea y predice."""

    def __init__(self, prediction=None, *, task: str = "segment"):
        self.task = task
        self.calls: list[dict] = []
        self._prediction = prediction

    def predict(self, **kwargs):
        self.calls.append(kwargs)
        return [] if self._prediction is None else [self._prediction]


def _square(x1: int, y1: int, x2: int, y2: int) -> np.ndarray:
    return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32)


def _frame() -> np.ndarray:
    return np.full((_FRAME_SIDE_PX, _FRAME_SIDE_PX, 3), 120, dtype=np.uint8)


def _install(monkeypatch, fake_yolo: _FakeYolo | None, *, protected: bool = False,
             weights: bytes = b"pesos"):
    """Instala el doble de ultralytics y evita tocar el disco al leer los pesos."""
    module = types.ModuleType("ultralytics")
    module.YOLO = lambda path, task=None: fake_yolo
    monkeypatch.setitem(sys.modules, "ultralytics", module)
    monkeypatch.setattr(yolo_seg_model, "_is_protected_file", lambda path: protected)
    monkeypatch.setattr(YoloSegModel, "_read_weights", lambda self, path: weights)
    monkeypatch.setattr(yolo_seg_model, "_pick_device", lambda: "cpu")


def _loaded(monkeypatch, prediction) -> YoloSegModel:
    model = YoloSegModel(_MockConfig(), _SLOT)
    _install(monkeypatch, _FakeYolo(prediction))
    model.load()
    assert model.is_loaded, model.error
    return model


# ── Carga ────────────────────────────────────────────────────────────────────

class TestLoad:
    def test_it_declares_segmentation(self):
        assert YoloSegModel(_MockConfig(), _SLOT).task == TASK_SEGMENTATION

    def test_it_loads_and_reports_itself(self, monkeypatch):
        model = YoloSegModel(_MockConfig(), _SLOT)
        _install(monkeypatch, _FakeYolo())
        model.load()
        assert model.get_status()["status"] == STATUS_LOADED

    def test_protected_weights_stop_it_with_the_reason(self, monkeypatch):
        """Ultralytics carga desde una ruta: unos pesos que sólo existen en memoria no
        entran por este camino, y decirlo es mejor que cargar el archivo cifrado."""
        model = YoloSegModel(_MockConfig(), _SLOT)
        _install(monkeypatch, _FakeYolo(), protected=True)
        model.load()
        assert model.status == STATUS_ERROR and "protegidos" in model.error

    def test_weights_that_cannot_be_read_stop_it(self, monkeypatch):
        model = YoloSegModel(_MockConfig(), _SLOT)
        _install(monkeypatch, _FakeYolo(), weights=b"")
        model.load()
        assert not model.is_loaded

    def test_a_task_disagreement_stops_it_before_the_first_frame(self, monkeypatch):
        """Un `.engine` de detección cargado como segmentador daría números creíbles con
        el postproceso equivocado."""
        model = YoloSegModel(_MockConfig(), _SLOT)
        _install(monkeypatch, _FakeYolo(task="detect"))
        model.load()
        assert model.status == STATUS_ERROR and "detection" in model.error

    def test_a_runtime_without_a_declared_task_is_not_a_disagreement(self, monkeypatch):
        model = YoloSegModel(_MockConfig(), _SLOT)
        _install(monkeypatch, _FakeYolo(task=""))
        model.load()
        assert model.is_loaded

    def test_loading_twice_does_not_reload(self, monkeypatch):
        model = YoloSegModel(_MockConfig(), _SLOT)
        _install(monkeypatch, _FakeYolo())
        model.load()
        monkeypatch.setattr(YoloSegModel, "_read_weights",
                            lambda self, path: pytest.fail("releyó los pesos"))
        model.load()

    def test_without_the_framework_it_reports_the_reason(self, monkeypatch):
        model = YoloSegModel(_MockConfig(), _SLOT)
        _install(monkeypatch, None)
        monkeypatch.delitem(sys.modules, "ultralytics")
        monkeypatch.setattr(sys, "path", [])
        model.load()
        assert model.status == STATUS_ERROR

    def test_unloading_leaves_it_ready_to_load_again(self, monkeypatch):
        model = _loaded(monkeypatch, None)
        model.unload()
        assert not model.is_loaded


# ── Detecciones ──────────────────────────────────────────────────────────────

class TestPredict:
    def test_without_loading_it_returns_nothing(self):
        assert YoloSegModel(_MockConfig(), _SLOT).predict(_frame()) == []

    def test_an_empty_frame_returns_nothing(self, monkeypatch):
        model = _loaded(monkeypatch, None)
        assert model.predict(np.zeros((0, 0, 3), np.uint8)) == []

    def test_a_polygon_becomes_a_detection_with_its_mask(self, monkeypatch):
        prediction = _FakePrediction([_square(10, 20, 30, 50)], [1], [0.9])
        detections = _loaded(monkeypatch, prediction).predict(_frame())
        assert len(detections) == 1
        detection = detections[0]
        assert detection.bbox_px == (10, 20, 30, 50)
        assert detection.mask.shape == (30, 20)

    def test_the_mask_is_cropped_to_the_bbox(self, monkeypatch):
        """Con cientos de instancias, una máscara del tamaño del frame no entra en RAM."""
        prediction = _FakePrediction([_square(10, 20, 30, 50)], [1], [0.9])
        detection = _loaded(monkeypatch, prediction).predict(_frame())[0]
        assert detection.area_px == detection.mask.sum() > 0

    def test_the_class_name_comes_from_the_config(self, monkeypatch):
        prediction = _FakePrediction([_square(0, 0, 10, 10)], [0], [0.9])
        assert _loaded(monkeypatch, prediction).predict(_frame())[0].class_name \
            == "desmenuzado"

    def test_the_confidence_is_reported_on_a_hundred(self, monkeypatch):
        prediction = _FakePrediction([_square(0, 0, 10, 10)], [1], [0.875])
        assert _loaded(monkeypatch, prediction).predict(_frame())[0].confidence_pct == 87.5

    def test_the_threshold_is_handed_to_the_framework(self, monkeypatch):
        """El descarte por detección lo aplica el modelo, no el llamador."""
        fake = _FakeYolo(_FakePrediction([], [], []))
        model = YoloSegModel(_MockConfig(), _SLOT)
        _install(monkeypatch, fake)
        model.load()
        model.predict(_frame())
        assert fake.calls[0]["conf"] == pytest.approx(0.2)

    def test_a_detection_without_a_polygon_is_dropped(self, monkeypatch):
        """Un segmentador que no devuelve forma no midió nada: el área del bbox sería un
        número inventado con la misma pinta que los buenos."""
        prediction = _FakePrediction([np.array([[1, 1], [2, 2]], np.float32)], [1], [0.9])
        assert _loaded(monkeypatch, prediction).predict(_frame()) == []

    def test_a_prediction_without_masks_returns_nothing(self, monkeypatch):
        assert _loaded(monkeypatch, _FakePrediction(None, [1], [0.9])).predict(_frame()) == []

    def test_a_framework_error_does_not_propagate(self, monkeypatch):
        """El motor marca el resultado, pero el hilo no se cae por un frame."""
        model = _loaded(monkeypatch, None)

        def boom(**kwargs):
            raise RuntimeError("CUDA out of memory")

        model._model.predict = boom
        assert model.predict(_frame()) == []


# ── Reducción del frame ──────────────────────────────────────────────────────

class TestMaxSide:
    def test_the_coordinates_come_back_in_the_frame_it_received(self, monkeypatch):
        """Quien llama nunca ve la escala interna del modelo."""
        config = _MockConfig(**{f"inference.models.{_SLOT}.max_side_px": 50})
        model = YoloSegModel(config, _SLOT)
        # El polígono llega en el espacio reducido (50 px): al volver se duplica.
        _install(monkeypatch, _FakeYolo(_FakePrediction([_square(5, 10, 15, 25)],
                                                        [1], [0.9])))
        model.load()
        assert model.predict(_frame())[0].bbox_px == (10, 20, 30, 50)

    def test_a_frame_that_already_fits_is_not_resized(self, monkeypatch):
        config = _MockConfig(**{f"inference.models.{_SLOT}.max_side_px": 500})
        model = YoloSegModel(config, _SLOT)
        fake = _FakeYolo(_FakePrediction([_square(10, 20, 30, 50)], [1], [0.9]))
        _install(monkeypatch, fake)
        model.load()
        assert model.predict(_frame())[0].bbox_px == (10, 20, 30, 50)


class TestBboxClipping:
    def test_a_polygon_outside_the_frame_is_dropped(self, monkeypatch):
        prediction = _FakePrediction([_square(200, 200, 260, 260)], [1], [0.9])
        assert _loaded(monkeypatch, prediction).predict(_frame()) == []

    def test_a_polygon_that_pokes_out_is_clipped(self, monkeypatch):
        prediction = _FakePrediction([_square(90, 90, 160, 160)], [1], [0.9])
        detection = _loaded(monkeypatch, prediction).predict(_frame())[0]
        assert detection.bbox_px == (90, 90, _FRAME_SIDE_PX, _FRAME_SIDE_PX)


# ── Refinamiento de fondo oscuro ─────────────────────────────────────────────

class TestDarkBackground:
    """
    El contorno del segmentador es aproximadamente convexo y se traga el fondo que hay
    entre partículas sueltas. Quitarlo acá —y no en el analyzer— es lo que hace que la
    máscara que se mide y la que se dibuja sean la misma.
    """

    def _split_frame(self) -> np.ndarray:
        """Mitad de arriba clara, mitad de abajo oscura."""
        frame = np.full((_FRAME_SIDE_PX, _FRAME_SIDE_PX, 3), 200, dtype=np.uint8)
        frame[50:, :, :] = 10
        return frame

    def _config(self, **params) -> _MockConfig:
        return _MockConfig(**{f"inference.models.{_SLOT}.params": params})

    def _predict(self, config: _MockConfig, monkeypatch, class_index: int = 0):
        model = YoloSegModel(config, _SLOT)
        _install(monkeypatch, _FakeYolo(_FakePrediction(
            [_square(0, 0, _FRAME_SIDE_PX, _FRAME_SIDE_PX)], [class_index], [0.9])))
        model.load()
        return model.predict(self._split_frame())

    def test_dark_pixels_leave_the_mask(self, monkeypatch):
        config = self._config(dark_background_threshold={"desmenuzado": 60})
        detection = self._predict(config, monkeypatch)[0]
        assert detection.area_px == 50 * _FRAME_SIDE_PX

    def test_a_class_without_a_threshold_is_untouched(self, monkeypatch):
        config = self._config(dark_background_threshold={"desmenuzado": 60})
        detection = self._predict(config, monkeypatch, class_index=1)[0]
        assert detection.area_px == _FRAME_SIDE_PX * _FRAME_SIDE_PX

    def test_a_zero_threshold_disables_it(self, monkeypatch):
        config = self._config(dark_background_threshold={"desmenuzado": 0})
        detection = self._predict(config, monkeypatch)[0]
        assert detection.area_px == _FRAME_SIDE_PX * _FRAME_SIDE_PX

    def test_without_the_param_nothing_is_refined(self, monkeypatch):
        detection = self._predict(self._config(), monkeypatch)[0]
        assert detection.area_px == _FRAME_SIDE_PX * _FRAME_SIDE_PX

    def test_a_mask_left_empty_is_dropped(self, monkeypatch):
        """Contar una instancia que ya no cubre nada sería contar una que no está."""
        config = self._config(dark_background_threshold={"desmenuzado": 255})
        assert self._predict(config, monkeypatch) == []

    def test_the_area_matches_the_refined_mask(self, monkeypatch):
        config = self._config(dark_background_threshold={"desmenuzado": 60})
        detection = self._predict(config, monkeypatch)[0]
        assert detection.area_px == int(detection.mask.sum())
