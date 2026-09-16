"""Tests de la cuenta del proceso: reparto por clase contado por unión, los dos
denominadores, la composición normalizada y el refinamiento de fondo oscuro.

Módulo puro: las detecciones y los frames se arman a mano, sin modelo y sin config.
"""

import numpy as np

from system.inference import metrics
from system.inference.result import Detection, InferenceResult

_SLOT = "camera_1"
# Frame chico y de números redondos: 100x100 = 10 000 px, así que 1 % es 100 px y las
# cuentas del test se leen sin calculadora.
_SIDE_PX = 100
_FRAME_AREA_PX = _SIDE_PX * _SIDE_PX


def _frame(level: int = 200) -> np.ndarray:
    return np.full((_SIDE_PX, _SIDE_PX, 3), level, dtype=np.uint8)


def _detection(class_name: str, bbox_px: tuple, *, class_index: int = 0,
               confidence_pct: float = 90.0) -> Detection:
    """Detección con la máscara llena: el bbox entero cuenta como superficie."""
    x1, y1, x2, y2 = bbox_px
    mask = np.ones((y2 - y1, x2 - x1), dtype=np.uint8)
    return Detection(class_index=class_index, class_name=class_name,
                     confidence_pct=confidence_pct, bbox_px=bbox_px,
                     area_px=int(mask.sum()), mask=mask)


def _result(detections: list, *, frame: np.ndarray | None = None,
            confidence_pct: float = 90.0) -> InferenceResult:
    return InferenceResult(camera_slot=_SLOT, detections=detections,
                           confidence_pct=confidence_pct, inference_time_ms=12.5,
                           source_bgr=_frame() if frame is None else frame)


def _analyzer(*, belt_roi_px: dict | None = None,
              dark_background_threshold: dict | None = None):
    return metrics.build_analyzer(
        class_names=["desmenuzado", "pellet"],
        belt_roi_px=belt_roi_px or {},
        dark_background_threshold=dark_background_threshold or {},
    )


# ── Reparto por clase ────────────────────────────────────────────────────────

class TestClassFractions:
    def test_a_class_covering_a_tenth_reports_ten_percent(self):
        result = _result([_detection("pellet", (0, 0, _SIDE_PX, 10))])
        assert _analyzer()(result)["pct_pellet"] == 10

    def test_a_declared_class_without_detections_reports_zero(self):
        """Un registro que deja de publicarse se queda con su último valor: el PLC no
        puede distinguir eso de una medición que no cambió."""
        assert _analyzer()(_result([]))["pct_desmenuzado"] == 0

    def test_the_rest_of_the_frame_is_reported_as_belt(self):
        result = _result([_detection("pellet", (0, 0, _SIDE_PX, 25))])
        assert _analyzer()(result)[metrics.FRAME_FRACTION_KEY] == 75

    def test_a_full_frame_leaves_no_belt_in_sight(self):
        result = _result([_detection("pellet", (0, 0, _SIDE_PX, _SIDE_PX))])
        assert _analyzer()(result)[metrics.FRAME_FRACTION_KEY] == 0


class TestUnionCounting:
    def test_two_overlapping_detections_count_the_shared_pixels_once(self):
        """Sumar el área de cada instancia contaría dos veces lo que dos polígonos se
        pisan e inflaría la carga sin que pase nada en la cinta."""
        result = _result([_detection("pellet", (0, 0, _SIDE_PX, 20)),
                          _detection("pellet", (0, 10, _SIDE_PX, 30))])
        assert _analyzer()(result)["pct_pellet"] == 30

    def test_where_two_classes_overlap_the_last_one_wins(self):
        """El mismo criterio con el que se pintan las máscaras del overlay: lo que se ve y
        lo que se mide no pueden discrepar."""
        computed = _analyzer()(_result([
            _detection("pellet", (0, 0, _SIDE_PX, 20)),
            _detection("desmenuzado", (0, 0, _SIDE_PX, 20)),
        ]))
        assert (computed["pct_pellet"], computed["pct_desmenuzado"]) == (0, 20)

    def test_a_detection_without_a_mask_does_not_count(self):
        bare = Detection(class_index=1, class_name="pellet", confidence_pct=90.0,
                         bbox_px=(0, 0, 10, 10), area_px=100)
        assert _analyzer()(_result([bare]))["pct_pellet"] == 0


# ── Los dos denominadores ────────────────────────────────────────────────────

class TestLoad:
    def test_load_is_measured_against_the_belt_roi(self):
        """La carga se mide contra el ROI de cinta y el reparto contra el frame: son
        denominadores distintos a propósito."""
        belt = {_SLOT: {"x_px": 0, "y_px": 0, "width_px": _SIDE_PX, "height_px": 50}}
        result = _result([_detection("pellet", (0, 0, _SIDE_PX, 25))])
        computed = _analyzer(belt_roi_px=belt)(result)
        assert (computed["pct_pellet"], computed[metrics.LOAD_KEY]) == (25, 50.0)

    def test_an_overflow_goes_past_one_hundred(self):
        """No se acota: pasar de 100 % es justamente el dato de que la cinta desbordó."""
        belt = {_SLOT: {"x_px": 0, "y_px": 0, "width_px": _SIDE_PX, "height_px": 20}}
        result = _result([_detection("pellet", (0, 0, _SIDE_PX, 40))])
        assert _analyzer(belt_roi_px=belt)(result)[metrics.LOAD_KEY] == 200.0

    def test_without_a_declared_roi_it_falls_back_to_the_frame(self):
        result = _result([_detection("pellet", (0, 0, _SIDE_PX, 30))])
        assert _analyzer()(result)[metrics.LOAD_KEY] == 30.0

    def test_a_roi_without_area_falls_back_to_the_frame(self):
        belt = {_SLOT: {"x_px": 0, "y_px": 0, "width_px": 0, "height_px": 0}}
        result = _result([_detection("pellet", (0, 0, _SIDE_PX, 30))])
        assert _analyzer(belt_roi_px=belt)(result)[metrics.LOAD_KEY] == 30.0


# ── Composición ──────────────────────────────────────────────────────────────

class TestComposition:
    def test_the_normalized_classes_add_up_to_one_hundred(self):
        computed = _analyzer()(_result([
            _detection("pellet", (0, 0, _SIDE_PX, 30)),
            _detection("desmenuzado", (0, 30, _SIDE_PX, 40)),
        ]))
        assert (computed["pct_pellet_norm"], computed["pct_desmenuzado_norm"]) == (75.0, 25.0)

    def test_an_empty_belt_has_no_composition(self):
        """Todas en cero, no un reparto en partes iguales: no hay composición que repartir."""
        computed = _analyzer()(_result([]))
        assert (computed["pct_pellet_norm"], computed["pct_desmenuzado_norm"]) == (0.0, 0.0)


# ── Refinamiento de fondo oscuro ─────────────────────────────────────────────

class TestDarkBackground:
    def _split_frame(self) -> np.ndarray:
        """Mitad de arriba clara, mitad de abajo oscura."""
        frame = np.full((_SIDE_PX, _SIDE_PX, 3), 200, dtype=np.uint8)
        frame[50:, :, :] = 10
        return frame

    def test_dark_pixels_are_dropped_from_the_configured_class(self):
        result = _result([_detection("desmenuzado", (0, 0, _SIDE_PX, _SIDE_PX))],
                         frame=self._split_frame())
        computed = _analyzer(dark_background_threshold={_SLOT: {"desmenuzado": 60}})(result)
        assert computed["pct_desmenuzado"] == 50

    def test_a_class_without_a_threshold_is_not_refined(self):
        result = _result([_detection("pellet", (0, 0, _SIDE_PX, _SIDE_PX))],
                         frame=self._split_frame())
        computed = _analyzer(dark_background_threshold={_SLOT: {"desmenuzado": 60}})(result)
        assert computed["pct_pellet"] == 100

    def test_dropped_pixels_become_belt_and_not_the_class_underneath(self):
        """Un píxel que una clase le ganó a otra y que después se descarta por oscuro queda
        como cinta: no vuelve a la clase que lo había perdido."""
        result = _result([_detection("pellet", (0, 0, _SIDE_PX, _SIDE_PX)),
                          _detection("desmenuzado", (0, 0, _SIDE_PX, _SIDE_PX))],
                         frame=self._split_frame())
        computed = _analyzer(dark_background_threshold={_SLOT: {"desmenuzado": 60}})(result)
        assert (computed["pct_pellet"], computed["pct_desmenuzado"]) == (0, 50)

    def test_a_zero_threshold_disables_the_refinement(self):
        result = _result([_detection("desmenuzado", (0, 0, _SIDE_PX, _SIDE_PX))],
                         frame=self._split_frame())
        computed = _analyzer(dark_background_threshold={_SLOT: {"desmenuzado": 0}})(result)
        assert computed["pct_desmenuzado"] == 100


# ── Escalares que van al PLC ─────────────────────────────────────────────────

class TestScalars:
    def test_confidence_and_time_travel_as_metrics(self):
        """El mapa de registros se indexa por nombre de métrica: es lo que los lleva al PLC."""
        computed = _analyzer()(_result([], confidence_pct=87.5))
        assert (computed["confidence_pct"], computed["inference_time_ms"]) == (87.5, 12.5)


class TestWithoutFrame:
    def test_a_result_without_a_frame_measures_nothing(self):
        """Sin frame no se sabe el área, y un porcentaje sin denominador es un invento."""
        assert _analyzer()(InferenceResult(camera_slot=_SLOT)) == {}


class TestDefaultAnalyzer:
    def test_it_counts_without_plant_parameters(self):
        result = _result([_detection("pellet", (0, 0, _SIDE_PX, 40))])
        computed = metrics.compute_metrics(result)
        assert (computed["pct_pellet"], computed[metrics.LOAD_KEY]) == (40, 40.0)
