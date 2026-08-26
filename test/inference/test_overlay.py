"""Tests del dibujado: qué respeta cada flag, qué colores usa y que el frame de cámara
nunca se toque.

Se verifica que el dibujado modifique los píxeles que corresponden, no cómo queda: el
aspecto no es afirmable, la mutación sí.
"""

import numpy as np
import pytest

from system.inference import overlay
from system.inference.result import Detection, InferenceResult

_NO_EXTRAS = {"draw_summary": False, "draw_timestamp": False, "draw_roi": False}


def _frame(level: int = 128) -> np.ndarray:
    return np.full((120, 200, 3), level, np.uint8)


def _detection(**overrides) -> Detection:
    fields = {"class_index": 0, "class_name": "grain", "confidence_pct": 90.0,
              "bbox_px": (40, 40, 90, 90)}
    fields.update(overrides)
    return Detection(**fields)


def _result(**overrides) -> InferenceResult:
    fields = {"camera_slot": "camera_1", "source_bgr": _frame(),
              "detections": [_detection()], "inference_time_ms": 12.0,
              "confidence_pct": 90.0}
    fields.update(overrides)
    return InferenceResult(**fields)


def _changed_pixels(before: np.ndarray, after: np.ndarray) -> int:
    return int(np.count_nonzero(np.any(before != after, axis=2)))


class TestClassColors:
    def test_the_palette_cycles(self):
        first = overlay.get_class_color_bgr(0)
        assert overlay.get_class_color_bgr(len(overlay.DEFAULT_CLASS_COLORS_BGR)) == first

    def test_two_classes_get_different_colors(self):
        assert overlay.get_class_color_bgr(0) != overlay.get_class_color_bgr(1)

    def test_the_config_palette_wins(self):
        assert overlay.get_class_color_bgr(0, ((1, 2, 3),)) == (1, 2, 3)


class TestAnnotate:
    def test_it_returns_a_new_frame(self):
        result = _result()
        annotated = overlay.annotate(result)
        assert annotated is not None
        assert annotated is not result.source_bgr

    def test_the_camera_frame_is_never_touched(self):
        result = _result()
        overlay.annotate(result)
        assert np.array_equal(result.source_bgr, _frame())

    def test_without_a_frame_there_is_nothing_to_annotate(self):
        assert overlay.annotate(_result(source_bgr=None)) is None

    def test_a_gray_frame_comes_back_in_color(self):
        result = _result(source_bgr=np.full((120, 200), 128, np.uint8))
        assert overlay.annotate(result).shape == (120, 200, 3)

    def test_the_box_is_drawn(self):
        options = overlay.OverlayOptions(draw_labels=False, **_NO_EXTRAS)
        annotated = overlay.annotate(_result(), options)
        assert _changed_pixels(_frame(), annotated) > 0

    def test_boxes_can_be_turned_off(self):
        options = overlay.OverlayOptions(draw_boxes=False, draw_labels=False, **_NO_EXTRAS)
        assert _changed_pixels(_frame(), overlay.annotate(_result(), options)) == 0

    def test_a_detection_outside_the_frame_is_skipped(self):
        options = overlay.OverlayOptions(draw_labels=False, **_NO_EXTRAS)
        result = _result(detections=[_detection(bbox_px=(500, 500, 600, 600))])
        assert _changed_pixels(_frame(), overlay.annotate(result, options)) == 0

    def test_the_label_adds_pixels_over_the_box(self):
        boxes_only = overlay.OverlayOptions(draw_labels=False, **_NO_EXTRAS)
        with_label = overlay.OverlayOptions(draw_labels=True, **_NO_EXTRAS)
        assert (_changed_pixels(_frame(), overlay.annotate(_result(), with_label))
                > _changed_pixels(_frame(), overlay.annotate(_result(), boxes_only)))

    def test_the_mask_tints_the_inside_of_the_box(self):
        options = overlay.OverlayOptions(draw_boxes=False, draw_labels=False, **_NO_EXTRAS)
        result = _result(detections=[_detection(mask=np.ones((50, 50), np.uint8))])
        assert _changed_pixels(_frame(), overlay.annotate(result, options)) == 50 * 50

    def test_masks_can_be_turned_off(self):
        options = overlay.OverlayOptions(draw_boxes=False, draw_labels=False,
                                         draw_masks=False, **_NO_EXTRAS)
        result = _result(detections=[_detection(mask=np.ones((50, 50), np.uint8))])
        assert _changed_pixels(_frame(), overlay.annotate(result, options)) == 0

    def test_a_mask_that_does_not_match_its_bbox_is_ignored(self):
        """Contra el borde del frame el bbox se recorta y la máscara ya no encaja."""
        options = overlay.OverlayOptions(draw_boxes=False, draw_labels=False, **_NO_EXTRAS)
        result = _result(detections=[_detection(bbox_px=(180, 100, 230, 150),
                                               mask=np.ones((50, 50), np.uint8))])
        assert _changed_pixels(_frame(), overlay.annotate(result, options)) == 0

    def test_the_roi_is_marked(self):
        options = overlay.OverlayOptions(draw_boxes=False, draw_labels=False,
                                         draw_summary=False, draw_timestamp=False)
        result = _result(detections=[], roi_px=(10, 10, 100, 60))
        assert _changed_pixels(_frame(), overlay.annotate(result, options)) > 0


class TestSummary:
    def test_it_always_reports_time_confidence_and_count(self):
        lines = overlay.build_summary_lines(_result())
        assert lines == ["12 ms", "conf 90%", "det 1"]

    def test_the_metrics_of_the_project_follow(self):
        lines = overlay.build_summary_lines(_result(metrics={"coverage_pct": 42.5}))
        assert lines[-1] == "coverage_pct 42.5"

    def test_an_invalid_result_leads_with_its_reason(self):
        lines = overlay.build_summary_lines(_result(is_valid=False, invalid_reason="dark_frame"))
        assert lines[0] == "! dark_frame"

    def test_booleans_are_readable(self):
        lines = overlay.build_summary_lines(_result(metrics={"alarm": True}))
        assert lines[-1] == "alarm yes"

    def test_the_labels_come_before_the_metrics(self):
        """El veredicto dice si los números aplican, así que se lee primero."""
        lines = overlay.build_summary_lines(
            _result(labels={"belt": "full"}, metrics={"coverage_pct": 42.5}))
        assert lines[-2:] == ["belt full", "coverage_pct 42.5"]

    def test_the_panel_is_drawn_over_the_frame(self):
        options = overlay.OverlayOptions(draw_boxes=False, draw_labels=False,
                                         draw_roi=False, draw_timestamp=False)
        assert _changed_pixels(_frame(), overlay.annotate(_result(), options)) > 0

    def test_an_empty_panel_draws_nothing(self):
        frame = _frame()
        overlay.draw_text_panel(frame, [])
        assert _changed_pixels(_frame(), frame) == 0


class TestDrawLine:
    def test_it_draws_across_the_frame(self):
        """Cruza el ancho completo; el antialiasing tiñe además las filas vecinas."""
        frame = _frame()
        overlay.draw_line(frame, (0, 60), (200, 60), (0, 0, 255))
        assert np.all(frame[60] != 128)
        assert np.all(frame[57] == 128) and np.all(frame[63] == 128)

    def test_the_label_adds_pixels(self):
        plain, labelled = _frame(), _frame()
        overlay.draw_line(plain, (0, 60), (200, 60), (0, 0, 255))
        overlay.draw_line(labelled, (0, 60), (200, 60), (0, 0, 255), label="MAX LOAD")
        assert _changed_pixels(_frame(), labelled) > _changed_pixels(_frame(), plain)

    def test_an_empty_frame_is_ignored(self):
        overlay.draw_line(np.zeros((0, 0, 3), np.uint8), (0, 0), (1, 1), (0, 0, 255))
