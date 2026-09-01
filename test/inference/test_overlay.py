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


class TestCropToRoi:
    """`crop_to_roi`: lo que se muestra es lo que se analizó, y nada se mapea de vuelta."""

    _ROI = (40, 30, 100, 60)

    def test_it_returns_only_the_roi(self):
        annotated = overlay.annotate(
            _result(roi_px=self._ROI),
            overlay.OverlayOptions(crop_to_roi=True, **_NO_EXTRAS))
        assert annotated.shape[:2] == (60, 100)

    def test_without_a_roi_it_returns_the_whole_frame(self):
        annotated = overlay.annotate(
            _result(roi_px=None),
            overlay.OverlayOptions(crop_to_roi=True, **_NO_EXTRAS))
        assert annotated.shape[:2] == _frame().shape[:2]

    def test_it_does_not_frame_its_own_border(self):
        """Recuadrar el recorte marca el borde de la imagen: no dice nada."""
        options = overlay.OverlayOptions(crop_to_roi=True, draw_roi=True,
                                         draw_summary=False, draw_timestamp=False)
        annotated = overlay.annotate(_result(roi_px=self._ROI, detections=[]), options)
        assert _changed_pixels(annotated, np.full_like(annotated, 128)) == 0

    def test_the_detections_move_with_the_crop(self):
        """Se dibujan con el origen del ROI restado, sin tocar la detección."""
        detection = _detection(bbox_px=(50, 40, 70, 60))
        result = _result(roi_px=self._ROI, detections=[detection])
        annotated = overlay.annotate(
            result, overlay.OverlayOptions(crop_to_roi=True, draw_boxes=True,
                                           draw_labels=False, **_NO_EXTRAS))
        # La caja arranca en (50-40, 40-30) dentro del recorte.
        assert _changed_pixels(annotated[:5, :5], np.full((5, 5, 3), 128, np.uint8)) == 0
        assert _changed_pixels(annotated[10:30, 10:30],
                               np.full((20, 20, 3), 128, np.uint8)) > 0
        assert detection.bbox_px == (50, 40, 70, 60)   # el que va al dataset y al PLC


class TestMaskStyle:
    """
    Contorno y relleno son dueños de píxeles distintos.

    Se afirma sobre dos píxeles conocidos de una máscara llena en el bbox (40,40)-(90,90):
    el borde y el interior. Sin cajas ni etiquetas, que pintan encima del borde y taparían
    lo que se está midiendo.
    """

    _BORDER = (40, 40)
    _INSIDE = (60, 60)

    def _annotated(self, style: str) -> np.ndarray:
        detection = _detection(bbox_px=(40, 40, 90, 90), mask=np.ones((50, 50), np.uint8))
        return overlay.annotate(
            _result(detections=[detection]),
            overlay.OverlayOptions(draw_masks=True, mask_style=style,
                                   draw_boxes=False, draw_labels=False, **_NO_EXTRAS))

    def _pixel(self, style: str, at: tuple[int, int]) -> tuple:
        return tuple(int(value) for value in self._annotated(style)[at])

    def test_the_outline_leaves_the_inside_untouched(self):
        assert self._pixel(overlay.MASK_STYLE_OUTLINE, self._INSIDE) == (128, 128, 128)

    def test_the_fill_tints_the_inside_without_hiding_it(self):
        """Translúcido: ni el frame ni el color pelado de la clase."""
        inside = self._pixel(overlay.MASK_STYLE_FILL, self._INSIDE)
        assert inside not in ((128, 128, 128), overlay.get_class_color_bgr(0))

    def test_the_outline_is_the_class_color_without_blending(self):
        """Nítido a propósito: con cientos de instancias es lo único que separa una
        de la otra, y mezclado se pierde contra el relleno."""
        assert (self._pixel(overlay.MASK_STYLE_OUTLINE, self._BORDER)
                == overlay.get_class_color_bgr(0))

    def test_the_fill_alone_has_no_sharp_edge(self):
        """El borde de una máscara rellena se mezcla como el resto: si sale nítido, se
        está dibujando el contorno en un modo que no lo pidió."""
        assert (self._pixel(overlay.MASK_STYLE_FILL, self._BORDER)
                == self._pixel(overlay.MASK_STYLE_FILL, self._INSIDE))

    def test_both_draws_the_two_of_them(self):
        assert (self._pixel(overlay.MASK_STYLE_BOTH, self._BORDER)
                == self._pixel(overlay.MASK_STYLE_OUTLINE, self._BORDER))
        assert (self._pixel(overlay.MASK_STYLE_BOTH, self._INSIDE)
                == self._pixel(overlay.MASK_STYLE_FILL, self._INSIDE))


class TestFontScale:
    """`font_scale: 0` calcula el tamaño del texto a partir del ancho del frame."""

    def test_a_configured_scale_is_respected(self):
        options = overlay.OverlayOptions(font_scale=0.42)
        assert overlay.resolve_font_scale(_frame(), ["texto"], options) == 0.42

    def test_a_narrow_frame_gets_smaller_text(self):
        options = overlay.OverlayOptions(font_scale=0.0)
        lines = ["una linea larga de resumen"]
        angosto = overlay.resolve_font_scale(np.zeros((50, 120, 3), np.uint8), lines, options)
        ancho = overlay.resolve_font_scale(np.zeros((50, 900, 3), np.uint8), lines, options)
        assert angosto < ancho

    def test_it_stays_inside_the_bounds(self):
        options = overlay.OverlayOptions(font_scale=0.0)
        for width_px in (20, 120, 900, 4000):
            scale = overlay.resolve_font_scale(
                np.zeros((50, width_px, 3), np.uint8), ["x" * 40], options)
            assert overlay._AUTO_FONT_MIN <= scale <= overlay._AUTO_FONT_MAX

    def test_the_panel_fits_inside_the_frame(self):
        """
        Se mide contra el ancho menos el margen y el relleno del panel.

        El frame va angosto a propósito: más ancho la escala queda contra su tope y el
        panel entra igual, así que el test no diría nada.
        """
        frame = np.zeros((80, 200, 3), np.uint8)
        options = overlay.OverlayOptions(font_scale=0.0)
        lines = ["N12   87.3 %", "N6    12.7 %"]
        assert overlay.resolve_font_scale(frame, lines, options) < overlay._AUTO_FONT_MAX
        overlay.draw_text_panel(frame, lines, options=options)
        assert _changed_pixels(frame[:, -1:], np.zeros((80, 1, 3), np.uint8)) == 0


class TestTextThickness:
    """El grosor del trazo acompaña al tamaño: un texto grande con trazo de 1 px se ve
    pálido y roto, que es lo que pasaba después de escalarlo con el ancho del frame."""

    def test_it_grows_with_the_scale(self):
        assert (overlay.text_thickness(0.3) <= overlay.text_thickness(1.0)
                < overlay.text_thickness(2.5))

    def test_it_is_never_zero(self):
        assert overlay.text_thickness(0.0) == 1

    def test_the_default_scale_keeps_the_stroke_it_always_had(self):
        """Un fork con `font_scale` fijo tiene que seguir viéndose igual."""
        assert overlay.text_thickness(overlay.OverlayOptions().font_scale) == 1
