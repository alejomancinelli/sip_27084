"""
Dibujado del resultado sobre el frame: cajas, máscaras, etiquetas, panel de información
y timestamp.

Módulo puro: sin Qt, sin ConfigManager, sin hilos y sin I/O. Recibe un resultado y
devuelve una copia anotada del frame.

Maquinaria genérica: reutilizable entre proyectos. Todo resultado de inferencia tiene un
frame anotado —es lo que mira el operador en la UI, lo que sale por el stream
`annotated` y lo que guarda el dataset—, y sale de acá una sola vez: el mismo array
viaja a los tres destinos en vez de que cada uno vuelva a dibujar lo mismo.

Por eso también lo puede importar `ui/`: es nivel 1 y no sabe que existe una GUI. Un
panel que quiera redibujar un resultado guardado llama a `annotate()` y obtiene
exactamente lo que vio el operador.

Acá va sólo lo que sirve en cualquier fork: lo que sale de la inferencia —cajas,
máscaras, etiquetas, el panel, el ROI, el timestamp— y las primitivas con las que se
dibuja cualquier referencia (`draw_line`, `draw_text_panel`). Los dibujos propios de una
instalación —un límite de carga, una zona de rechazo— se arman con esas primitivas en
`annotations.py` y entran por el `annotator` del motor, no acá: este archivo se copia al
próximo proyecto tal cual.

Dos restricciones que no se ven en las firmas:
  - **El texto dibujado es ASCII.** Las fuentes Hershey de OpenCV no tienen acentos ni
    eñes: lo que se pase con tildes sale con signos de pregunta. Los nombres de clase y
    las claves de métricas ya vienen en inglés desde el config, así que alcanza con no
    traducir las etiquetas acá.
  - **`annotate()` copia el frame** y los `draw_*` dibujan in-place sobre lo que reciben.
    El frame de la cámara no se toca nunca: lo comparten la captura, el stream crudo y
    el dataset.
"""

import datetime
from dataclasses import dataclass, field

import cv2
import numpy as np

from .result import Detection, InferenceResult

# Paleta por índice de clase, en BGR. Se recorre en círculo, así que sirve para
# cualquier cantidad de clases; el config la puede reemplazar entera.
DEFAULT_CLASS_COLORS_BGR = (
    (217, 116, 50),    # azul
    (10, 120, 255),    # naranja
    (95, 200, 95),     # verde
    (59, 235, 255),    # amarillo
    (200, 120, 220),   # violeta
    (60, 60, 230),     # rojo
)

_FONT = cv2.FONT_HERSHEY_SIMPLEX
_PANEL_BG_BGR = (0, 0, 0)
_PANEL_TEXT_BGR = (235, 235, 235)
_INVALID_TEXT_BGR = (60, 60, 230)
_ROI_BGR = (180, 180, 180)
_LABEL_TEXT_BGR = (20, 20, 20)
_MASK_ALPHA = 0.45        # peso del color de la máscara sobre el frame
_MARGIN_PX = 10           # aire entre el panel y el borde del frame
_INNER_PAD_PX = 8
_LINE_PAD_PX = 6
_TIMESTAMP_SCALE = 0.55   # el timestamp va más chico que el panel: es referencia, no dato


@dataclass
class OverlayOptions:
    """
    Qué se dibuja y con qué tamaño. El motor la arma desde `inference.overlay`.

    `class_colors_bgr` vacío usa DEFAULT_CLASS_COLORS_BGR.
    """

    draw_boxes: bool = True
    draw_masks: bool = True
    draw_labels: bool = True
    draw_summary: bool = True
    draw_roi: bool = True
    draw_timestamp: bool = True
    font_scale: float = 0.6
    thickness: int = 2
    class_colors_bgr: tuple = field(default_factory=tuple)


def get_class_color_bgr(class_index: int, colors_bgr: tuple = ()) -> tuple[int, int, int]:
    """Color de una clase por índice, recorriendo la paleta en círculo."""
    palette = tuple(colors_bgr) or DEFAULT_CLASS_COLORS_BGR
    color = palette[class_index % len(palette)]
    return (int(color[0]), int(color[1]), int(color[2]))


def annotate(result: InferenceResult, options: OverlayOptions | None = None) -> np.ndarray | None:
    """
    Devuelve una copia anotada de `result.source_bgr`, o None si no hay frame.

    Dibuja, en este orden: el recuadro del ROI, las máscaras, las cajas con su etiqueta,
    el panel de información y el timestamp. Un frame en escala de grises se convierte a
    BGR para que el color de cada clase se distinga.
    """
    frame = result.source_bgr
    if frame is None or frame.size == 0:
        return None
    opts = options or OverlayOptions()

    annotated = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame.copy()

    if opts.draw_roi and result.roi_px is not None:
        draw_roi(annotated, result.roi_px)
    draw_detections(annotated, result.detections, options=opts)
    if opts.draw_summary:
        draw_text_panel(
            annotated, build_summary_lines(result), options=opts,
            text_bgr=_PANEL_TEXT_BGR if result.is_valid else _INVALID_TEXT_BGR)
    if opts.draw_timestamp:
        draw_timestamp(annotated, result.timestamp_s)
    return annotated


def draw_detections(frame_bgr: np.ndarray, detections: list[Detection], *,
                    options: OverlayOptions | None = None):
    """Dibuja in-place las máscaras, las cajas y las etiquetas, con el color de cada clase."""
    if frame_bgr is None or frame_bgr.size == 0:
        return
    opts = options or OverlayOptions()
    height_px, width_px = frame_bgr.shape[:2]

    for detection in detections:
        color = get_class_color_bgr(detection.class_index, opts.class_colors_bgr)
        x1, y1, x2, y2 = (int(value) for value in detection.bbox_px)
        # Una detección que no toca el frame no se dibuja: acotarla contra el borde
        # dejaría una marca en la esquina que no corresponde a nada.
        if x2 <= 0 or y2 <= 0 or x1 >= width_px or y1 >= height_px:
            continue
        x1 = max(0, min(width_px - 1, x1))
        y1 = max(0, min(height_px - 1, y1))
        x2 = max(0, min(width_px, x2))
        y2 = max(0, min(height_px, y2))
        if x2 <= x1 or y2 <= y1:
            continue

        if opts.draw_masks and detection.mask is not None:
            _blend_mask(frame_bgr, detection.mask, (x1, y1, x2, y2), color)
        if opts.draw_boxes:
            cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), color, opts.thickness)
        if opts.draw_labels:
            label = f"{detection.class_name} {detection.confidence_pct:.0f}%"
            _draw_label(frame_bgr, label, (x1, y1), color, opts)


def draw_roi(frame_bgr: np.ndarray, roi_px: tuple[int, int, int, int]):
    """Marca in-place el recorte que se analizó, para que se vea qué quedó afuera."""
    if frame_bgr is None or frame_bgr.size == 0:
        return
    x_px, y_px, width_px, height_px = roi_px
    cv2.rectangle(frame_bgr, (int(x_px), int(y_px)),
                  (int(x_px + width_px), int(y_px + height_px)), _ROI_BGR, 1)


def draw_line(frame_bgr: np.ndarray, start_px: tuple[int, int], end_px: tuple[int, int],
              color_bgr: tuple[int, int, int], *, thickness: int = 1, label: str = ""):
    """
    Dibuja in-place una línea y, si se le da, su etiqueta pegada al extremo izquierdo.

    Primitiva genérica para las referencias que no salen de la inferencia: un límite de
    carga, un eje de calibración, el borde de una zona. La geometría no se decide acá —es
    de cada instalación—: llega en píxeles ya resueltos.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return
    cv2.line(frame_bgr, (int(start_px[0]), int(start_px[1])),
             (int(end_px[0]), int(end_px[1])), color_bgr, thickness, cv2.LINE_AA)
    if not label:
        return
    x_px = min(int(start_px[0]), int(end_px[0])) + _INNER_PAD_PX
    y_px = min(int(start_px[1]), int(end_px[1])) - _INNER_PAD_PX
    cv2.putText(frame_bgr, label, (x_px, max(_MARGIN_PX, y_px)),
                _FONT, _TIMESTAMP_SCALE, color_bgr, 1, cv2.LINE_AA)


def draw_text_panel(frame_bgr: np.ndarray, lines: list[str], *,
                    options: OverlayOptions | None = None,
                    text_bgr: tuple[int, int, int] = _PANEL_TEXT_BGR):
    """
    Dibuja in-place un panel de texto con fondo opaco, arriba a la izquierda.

    El fondo opaco no es estética: sobre un frame claro el texto sin fondo no se lee, y
    el operador mira esto para decidir.
    """
    if frame_bgr is None or frame_bgr.size == 0 or not lines:
        return
    opts = options or OverlayOptions()

    sizes = [cv2.getTextSize(line, _FONT, opts.font_scale, 1)[0] for line in lines]
    text_width_px = max(width for width, _ in sizes)
    line_height_px = max(height for _, height in sizes)

    panel_width_px = text_width_px + 2 * _INNER_PAD_PX
    panel_height_px = (len(lines) * line_height_px + (len(lines) - 1) * _LINE_PAD_PX
                       + 2 * _INNER_PAD_PX)
    cv2.rectangle(frame_bgr, (_MARGIN_PX, _MARGIN_PX),
                  (_MARGIN_PX + panel_width_px, _MARGIN_PX + panel_height_px),
                  _PANEL_BG_BGR, cv2.FILLED)

    for i, line in enumerate(lines):
        y_px = _MARGIN_PX + _INNER_PAD_PX + (i + 1) * line_height_px + i * _LINE_PAD_PX
        cv2.putText(frame_bgr, line, (_MARGIN_PX + _INNER_PAD_PX, y_px),
                    _FONT, opts.font_scale, text_bgr, 1, cv2.LINE_AA)


def draw_timestamp(frame_bgr: np.ndarray, timestamp_s: float):
    """Dibuja in-place la fecha y hora del resultado, abajo a la derecha."""
    if frame_bgr is None or frame_bgr.size == 0:
        return
    text = datetime.datetime.fromtimestamp(timestamp_s).strftime("%Y-%m-%d %H:%M:%S")
    (text_width_px, text_height_px), baseline_px = cv2.getTextSize(
        text, _FONT, _TIMESTAMP_SCALE, 1)
    height_px, width_px = frame_bgr.shape[:2]
    x_px = width_px - text_width_px - _MARGIN_PX
    y_px = height_px - _MARGIN_PX
    cv2.rectangle(frame_bgr, (x_px - _INNER_PAD_PX, y_px - text_height_px - baseline_px),
                  (width_px, height_px), _PANEL_BG_BGR, cv2.FILLED)
    cv2.putText(frame_bgr, text, (x_px, y_px - baseline_px // 2),
                _FONT, _TIMESTAMP_SCALE, _PANEL_TEXT_BGR, 1, cv2.LINE_AA)


def build_summary_lines(result: InferenceResult) -> list[str]:
    """
    Las líneas del panel: lo que toda inferencia tiene, más las métricas del proyecto.

    Arranca por el motivo cuando el resultado no es confiable, para que el operador no
    lea como medición unos números que no midieron nada.
    """
    lines = []
    if not result.is_valid:
        lines.append(f"! {result.invalid_reason or 'invalid'}")
    lines.append(f"{result.inference_time_ms:.0f} ms")
    lines.append(f"conf {result.confidence_pct:.0f}%")
    lines.append(f"det {result.detection_count}")
    # Los veredictos del frame van antes de los números: dicen si los números aplican.
    for name, value in result.labels.items():
        lines.append(f"{name} {value}")
    for key, value in result.metrics.items():
        lines.append(f"{key} {_format_value(value)}")
    return lines


def _format_value(value: object) -> str:
    """Valor de una métrica como texto corto y ASCII."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _blend_mask(frame_bgr: np.ndarray, mask: np.ndarray,
                bbox_px: tuple[int, int, int, int], color: tuple[int, int, int]):
    """Tiñe in-place los píxeles de la máscara dentro de su bbox."""
    x1, y1, x2, y2 = bbox_px
    region = frame_bgr[y1:y2, x1:x2]
    # La máscara viene del tamaño del bbox original: si el bbox se recortó contra el
    # borde del frame, la región es más chica y no se la puede indexar con la máscara.
    if region.shape[:2] != mask.shape[:2]:
        return
    selected = mask > 0
    if not selected.any():
        return
    tint = np.empty_like(region)
    tint[:] = color
    blended = cv2.addWeighted(region, 1.0 - _MASK_ALPHA, tint, _MASK_ALPHA, 0)
    region[selected] = blended[selected]


def _draw_label(frame_bgr: np.ndarray, text: str, origin_px: tuple[int, int],
                color: tuple[int, int, int], options: OverlayOptions):
    """Etiqueta con fondo del color de la clase, pegada arriba de la caja."""
    x_px, y_px = origin_px
    (text_width_px, text_height_px), baseline_px = cv2.getTextSize(
        text, _FONT, options.font_scale, 1)
    top_px = y_px - text_height_px - baseline_px - 2
    # Contra el borde de arriba la etiqueta se dibuja dentro de la caja.
    if top_px < 0:
        top_px = y_px + 2
    cv2.rectangle(frame_bgr, (x_px, top_px),
                  (x_px + text_width_px + 4, top_px + text_height_px + baseline_px + 2),
                  color, cv2.FILLED)
    cv2.putText(frame_bgr, text, (x_px + 2, top_px + text_height_px + 2),
                _FONT, options.font_scale, _LABEL_TEXT_BGR, 1, cv2.LINE_AA)
