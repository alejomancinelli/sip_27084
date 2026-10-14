"""
Tests de los hooks de la cinta: qué le entrega `build_hooks()` al motor desde `process:`.

Lo que se prueba es el cableado, no la cuenta ni el dibujo, que tienen sus propios tests:
que no haya classifier, que el analyzer mida contra el rectángulo de cinta declarado y con
las clases de `CLASS_NAMES`, y que el annotator dibuje ese mismo rectángulo.
"""

import numpy as np

from system.inference import metrics
from system.inference.hooks import EngineHooks, build_hooks
from system.inference.pipeline import CLASS_NAMES
from system.inference.result import Detection, InferenceResult

_SLOT = "camera_1"
_SIDE_PX = 100
# Un cuarto del frame: la misma superficie detectada pesa cuatro veces más contra el ROI.
_BELT_ROI_PX = {_SLOT: {"x_px": 0, "y_px": 0, "width_px": 50, "height_px": 50}}


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = dict(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


def _config(**overrides) -> _MockConfig:
    return _MockConfig(**{"process.belt_roi_px": _BELT_ROI_PX, **overrides})


def _result(camera_slot: str = _SLOT, detections: list | None = None) -> InferenceResult:
    return InferenceResult(camera_slot=camera_slot, detections=detections or [],
                           source_bgr=np.zeros((_SIDE_PX, _SIDE_PX, 3), np.uint8))


def _pellet_band(rows: int) -> Detection:
    """Detección de pellet con la máscara llena sobre las primeras `rows` filas."""
    mask = np.ones((rows, _SIDE_PX), np.uint8)
    return Detection(class_index=CLASS_NAMES.index("pellet"), class_name="pellet",
                     confidence_pct=90.0, bbox_px=(0, 0, _SIDE_PX, rows),
                     area_px=int(mask.sum()), mask=mask)


class TestBeltHooks:
    def test_it_returns_the_hooks_object(self):
        assert isinstance(build_hooks(_config()), EngineHooks)

    def test_the_belt_does_not_filter_detections(self):
        """El fondo oscuro lo descarta el pipeline; el classifier no tiene nada que hacer."""
        assert build_hooks(_config()).classifier is None

    def test_every_declared_class_reaches_the_metrics(self):
        """Una clase sin detecciones sale en cero: si faltara, su registro se congelaría."""
        values = build_hooks(_config()).analyzer(_result())
        assert {f"pct_{name}_norm" for name in CLASS_NAMES} <= set(values)

    def test_the_load_is_measured_against_the_declared_belt(self):
        values = build_hooks(_config()).analyzer(_result(detections=[_pellet_band(10)]))
        # 1000 px detectados: 10 % del frame, 40 % del ROI de cinta.
        assert values["pct_pellet"] == 10
        assert values[metrics.LOAD_KEY] == 40.0

    def test_the_belt_rectangle_is_drawn_on_its_camera(self):
        """Sin métricas el panel no dibuja nada: lo que aparece es el rectángulo."""
        frame = np.zeros((_SIDE_PX, _SIDE_PX, 3), np.uint8)
        build_hooks(_config()).annotator(frame, _result())
        assert frame.any()

    def test_the_belt_rectangle_is_not_drawn_on_other_cameras(self):
        frame = np.zeros((_SIDE_PX, _SIDE_PX, 3), np.uint8)
        build_hooks(_config()).annotator(frame, _result(camera_slot="camera_2"))
        assert not frame.any()

    def test_without_a_belt_rectangle_the_load_is_against_the_whole_frame(self):
        hooks = build_hooks(_MockConfig())
        values = hooks.analyzer(_result(detections=[_pellet_band(10)]))
        assert values[metrics.LOAD_KEY] == 10.0
