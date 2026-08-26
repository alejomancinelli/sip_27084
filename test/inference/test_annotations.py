"""Tests de los annotators del proyecto: el límite de carga por cámara y la composición
de varios en uno.

Módulo puro: los annotators reciben la geometría ya resuelta, así que acá no hay config
ni frames de cámara.
"""

import numpy as np
import pytest

from system.inference.annotations import chain, max_load_line_annotator
from system.inference.result import InferenceResult

_SLOT = "camera_1"
_OTHER_SLOT = "camera_2"


def _frame() -> np.ndarray:
    return np.full((120, 200, 3), 128, np.uint8)


def _result(camera_slot: str = _SLOT) -> InferenceResult:
    return InferenceResult(camera_slot=camera_slot)


def _painted_rows(frame_bgr: np.ndarray) -> set[int]:
    """
    Filas que dejaron de ser el fondo, para saber a qué altura se dibujó.

    El antialiasing de OpenCV reparte una línea de 2 px sobre unas cinco filas, así que
    los tests afirman sobre la fila pedida y sobre el entorno que puede teñir, no sobre
    un conteo exacto de píxeles.
    """
    return {int(row) for row in np.flatnonzero(np.any(frame_bgr != 128, axis=(1, 2)))}


def _near(y_px: int) -> set[int]:
    """Filas que puede teñir una línea de 2 px con antialiasing a la altura `y_px`."""
    return set(range(y_px - 2, y_px + 3))


class TestMaxLoadLine:
    def test_it_draws_at_the_configured_height(self):
        frame = _frame()
        max_load_line_annotator({_SLOT: 60}, label="")(frame, _result())
        rows = _painted_rows(frame)
        assert 60 in rows and rows <= _near(60)

    def test_it_spans_the_whole_width(self):
        frame = _frame()
        max_load_line_annotator({_SLOT: 60}, label="")(frame, _result())
        assert np.all(frame[60] != 128)

    def test_each_camera_gets_its_own_height(self):
        annotator = max_load_line_annotator({_SLOT: 30, _OTHER_SLOT: 90}, label="")
        first, second = _frame(), _frame()
        annotator(first, _result(_SLOT))
        annotator(second, _result(_OTHER_SLOT))
        assert _painted_rows(first) <= _near(30)
        assert _painted_rows(second) <= _near(90)

    def test_a_camera_without_a_height_gets_no_line(self):
        frame = _frame()
        max_load_line_annotator({_SLOT: 60})(frame, _result(_OTHER_SLOT))
        assert _painted_rows(frame) == set()

    @pytest.mark.parametrize("y_px", [-1, 120, 500])
    def test_a_height_outside_the_frame_draws_nothing(self, y_px):
        frame = _frame()
        max_load_line_annotator({_SLOT: y_px})(frame, _result())
        assert _painted_rows(frame) == set()

    def test_it_draws_with_no_detections(self):
        """Es la referencia contra la que se mira el material: no puede aparecer y desaparecer."""
        frame = _frame()
        max_load_line_annotator({_SLOT: 60}, label="")(frame, _result())
        assert _painted_rows(frame) != set()

    def test_an_empty_frame_is_ignored(self):
        max_load_line_annotator({_SLOT: 60})(np.zeros((0, 0, 3), np.uint8), _result())

    def test_the_label_is_drawn_over_the_line(self):
        plain, labelled = _frame(), _frame()
        max_load_line_annotator({_SLOT: 60}, label="")(plain, _result())
        max_load_line_annotator({_SLOT: 60}, label="MAX LOAD")(labelled, _result())
        assert len(_painted_rows(labelled)) > len(_painted_rows(plain))

    def test_it_names_itself_with_its_geometry(self):
        """El aviso del motor tiene que decir cuál de los annotators falló."""
        assert "60" in max_load_line_annotator({_SLOT: 60}).__name__


class TestChain:
    def test_it_runs_every_annotator(self):
        calls = []
        first = lambda frame_bgr, result: calls.append("first")
        second = lambda frame_bgr, result: calls.append("second")
        chain(first, second)(_frame(), _result())
        assert calls == ["first", "second"]

    def test_the_last_one_draws_on_top(self):
        frame = _frame()
        chain(lambda frame_bgr, result: frame_bgr[0, 0].fill(1),
              lambda frame_bgr, result: frame_bgr[0, 0].fill(2))(frame, _result())
        assert tuple(frame[0, 0]) == (2, 2, 2)

    def test_every_annotator_leaves_its_mark(self):
        frame = _frame()
        chain(max_load_line_annotator({_SLOT: 30}, label=""),
              max_load_line_annotator({_SLOT: 90}, label=""))(frame, _result())
        rows = _painted_rows(frame)
        assert 30 in rows and 90 in rows
        assert rows <= _near(30) | _near(90)

    def test_an_empty_chain_draws_nothing(self):
        frame = _frame()
        chain()(frame, _result())
        assert _painted_rows(frame) == set()

    def test_none_entries_are_skipped(self):
        """Deja armar la cadena con annotators opcionales sin filtrar en el llamador."""
        calls = []
        chain(None, lambda frame_bgr, result: calls.append(1))(_frame(), _result())
        assert calls == [1]

    def test_it_names_the_annotators_it_carries(self):
        name = chain(max_load_line_annotator({_SLOT: 60})).__name__
        assert "chain(" in name and "max_load_line" in name
