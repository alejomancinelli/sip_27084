"""Tests de los annotators del proyecto: el rectángulo de cinta por cámara, el panel de
composición y la composición de varios en uno.

Módulo puro: los annotators reciben la geometría y los nombres de clase ya resueltos, así
que acá no hay config ni frames de cámara.
"""

import numpy as np

from system.inference import metrics
from system.inference.annotations import (belt_roi_annotator, chain,
                                          composition_panel_annotator)
from system.inference.result import InferenceResult

_SLOT = "camera_1"
_OTHER_SLOT = "camera_2"
_BACKGROUND = 128
_CLASS_NAMES = ["desmenuzado", "pellet"]


def _frame() -> np.ndarray:
    return np.full((120, 200, 3), _BACKGROUND, np.uint8)


def _result(camera_slot: str = _SLOT, metrics_values: dict | None = None) -> InferenceResult:
    return InferenceResult(camera_slot=camera_slot, metrics=metrics_values or {})


def _measured(camera_slot: str = _SLOT) -> InferenceResult:
    return _result(camera_slot, {
        "pct_desmenuzado_norm": 25.0,
        "pct_pellet_norm": 75.0,
        metrics.LOAD_KEY: 62.5,
    })


def _painted_rows(frame_bgr: np.ndarray) -> set[int]:
    """
    Filas que dejaron de ser el fondo, para saber a qué altura se dibujó.

    El antialiasing de OpenCV reparte una línea de 2 px sobre unas cinco filas, así que
    los tests afirman sobre la fila pedida y sobre el entorno que puede teñir, no sobre
    un conteo exacto de píxeles.
    """
    return {int(row) for row in np.flatnonzero(np.any(frame_bgr != _BACKGROUND, axis=(1, 2)))}


def _near(y_px: int) -> set[int]:
    """Filas que puede teñir una línea de 2 px con antialiasing a la altura `y_px`."""
    return set(range(y_px - 2, y_px + 3))


def _roi(x_px: int, y_px: int, width_px: int, height_px: int) -> dict:
    return {"x_px": x_px, "y_px": y_px, "width_px": width_px, "height_px": height_px}


# ── Rectángulo de cinta ──────────────────────────────────────────────────────

class TestBeltRoi:
    def test_it_draws_the_four_sides_at_the_configured_place(self):
        frame = _frame()
        belt_roi_annotator({_SLOT: _roi(20, 30, 100, 50)})(frame, _result())
        painted = _painted_rows(frame)
        assert painted & _near(30) and painted & _near(80)

    def test_each_camera_gets_its_own_rectangle(self):
        annotator = belt_roi_annotator({_SLOT: _roi(0, 20, 100, 20),
                                        _OTHER_SLOT: _roi(0, 90, 100, 20)})
        first, second = _frame(), _frame()
        annotator(first, _result(_SLOT))
        annotator(second, _result(_OTHER_SLOT))
        assert _painted_rows(first) & _near(20) and _painted_rows(second) & _near(90)

    def test_a_camera_without_a_rectangle_gets_nothing(self):
        frame = _frame()
        belt_roi_annotator({_SLOT: _roi(0, 20, 100, 20)})(frame, _result(_OTHER_SLOT))
        assert _painted_rows(frame) == set()

    def test_a_rectangle_without_area_draws_nothing(self):
        frame = _frame()
        belt_roi_annotator({_SLOT: _roi(10, 10, 0, 0)})(frame, _result())
        assert _painted_rows(frame) == set()

    def test_it_draws_with_no_detections_and_no_metrics(self):
        """Una referencia que aparece y desaparece no sirve como referencia."""
        frame = _frame()
        belt_roi_annotator({_SLOT: _roi(20, 30, 100, 50)})(frame, _result())
        assert _painted_rows(frame) != set()

    def test_an_empty_frame_is_ignored(self):
        belt_roi_annotator({_SLOT: _roi(0, 0, 10, 10)})(
            np.zeros((0, 0, 3), np.uint8), _result())

    def test_it_names_itself_with_the_cameras_it_covers(self):
        assert _SLOT in belt_roi_annotator({_SLOT: _roi(0, 0, 5, 5)}).__name__


# ── Panel de composición ─────────────────────────────────────────────────────

class TestCompositionPanel:
    def test_it_draws_when_there_are_metrics(self):
        frame = _frame()
        composition_panel_annotator(_CLASS_NAMES)(frame, _measured())
        assert _painted_rows(frame) != set()

    def test_a_result_without_metrics_draws_nothing(self):
        """Una medición no confiable no tiene números que mostrar."""
        frame = _frame()
        composition_panel_annotator(_CLASS_NAMES)(frame, _result())
        assert _painted_rows(frame) == set()

    def test_metrics_without_any_known_class_draw_nothing(self):
        """Sólo el encabezado no es un panel: si no hay ninguna fila, no se dibuja."""
        frame = _frame()
        composition_panel_annotator(["otra"])(frame, _result(_SLOT, {"pct_algo": 1}))
        assert _painted_rows(frame) == set()

    def test_it_draws_the_load_even_without_classes(self):
        frame = _frame()
        composition_panel_annotator([])(frame, _result(_SLOT, {metrics.LOAD_KEY: 40.0}))
        assert _painted_rows(frame) != set()

    def test_an_empty_frame_is_ignored(self):
        composition_panel_annotator(_CLASS_NAMES)(np.zeros((0, 0, 3), np.uint8), _measured())

    def test_it_names_itself_with_its_classes(self):
        assert "pellet" in composition_panel_annotator(_CLASS_NAMES).__name__


# ── Composición de annotators ────────────────────────────────────────────────

class TestChain:
    def test_it_runs_every_annotator(self):
        calls = []
        chain(lambda f, r: calls.append("first"),
              lambda f, r: calls.append("second"))(_frame(), _result())
        assert calls == ["first", "second"]

    def test_the_last_one_draws_on_top(self):
        frame = _frame()
        chain(lambda f, r: f.fill(10), lambda f, r: f.fill(20))(frame, _result())
        assert int(frame[0, 0, 0]) == 20

    def test_every_annotator_leaves_its_mark(self):
        frame = _frame()
        chain(belt_roi_annotator({_SLOT: _roi(0, 20, 100, 10)}),
              composition_panel_annotator(_CLASS_NAMES))(frame, _measured())
        painted = _painted_rows(frame)
        assert painted & _near(20) and painted & _near(30)

    def test_an_empty_chain_draws_nothing(self):
        frame = _frame()
        chain()(frame, _result())
        assert _painted_rows(frame) == set()

    def test_none_entries_are_skipped(self):
        calls = []
        chain(None, lambda f, r: calls.append("only"))(_frame(), _result())
        assert calls == ["only"]

    def test_it_names_the_annotators_it_carries(self):
        name = chain(belt_roi_annotator({_SLOT: _roi(0, 0, 5, 5)}),
                     composition_panel_annotator(_CLASS_NAMES)).__name__
        assert "belt_roi" in name and "composition_panel" in name
