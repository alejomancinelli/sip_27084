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
# Cómo se dibuja la máscara de una detección. Con pocas instancias el relleno se lee
# mejor; con cientos —granos, semillas, partículas— se convierte en una mancha y el
# contorno es lo único que deja ver dónde termina cada una.
MASK_STYLE_FILL = "fill"          # sólo el relleno translúcido
MASK_STYLE_OUTLINE = "outline"    # sólo el contorno
MASK_STYLE_BOTH = "both"          # contorno nítido y relleno translúcido
MASK_STYLES = (MASK_STYLE_FILL, MASK_STYLE_OUTLINE, MASK_STYLE_BOTH)

_MASK_ALPHA = 0.45        # peso del color de la máscara sobre el frame
_MARGIN_PX = 10           # aire entre el panel y el borde del frame
_INNER_PAD_PX = 8
_LINE_PAD_PX = 6
_TIMESTAMP_RATIO = 0.8    # el timestamp va más chico que el panel: es referencia, no dato
_LABEL_SCALE = 0.55       # etiqueta de una referencia dibujada con draw_line()

# Escala automática (`font_scale: 0`). El texto se calcula para que la línea más larga
# ocupe esta fracción del ancho del frame, con topes para que no quede ilegible en un
# recorte chico ni gigante en un frame completo.
_AUTO_TEXT_WIDTH_FRACTION = 0.95
_AUTO_FONT_MIN = 0.25
_AUTO_FONT_MAX = 1.00
# Escala hasta la que el trazo del texto es de un píxel. Es la escala por defecto, así
# que el texto de siempre se dibuja igual y sólo engorda cuando el texto crece.
_TEXT_THICKNESS_STEP = 0.5
# Línea nominal con la que la etiqueta de una detección calcula su escala automática. Se
# mide contra un largo fijo y no contra su propio texto: si no, «N6» se dibujaría del
# ancho del frame y «N12 87 %» mucho más chico, y son la misma etiqueta.
_AUTO_LABEL_LINE = "M" * 44


@dataclass
class OverlayOptions:
    """
    Qué se dibuja y con qué tamaño. El motor la arma desde `inference.overlay`.

    `class_colors_bgr` vacío usa DEFAULT_CLASS_COLORS_BGR.

    `mask_style` elige entre relleno translúcido, contorno o los dos; `mask_alpha` es
    cuánto tiñe el relleno, y con cientos de instancias conviene bajarlo.

    `crop_to_roi` devuelve anotado sólo el recorte que se analizó, en vez del frame
    entero con el ROI marcado: es lo que quiere ver el operador cuando el ROI es una
    fracción chica del sensor.

    `font_scale` en 0 calcula el tamaño del texto a partir del ancho del frame. Es lo que
    hace falta cuando el lienzo cambia de tamaño —un recorte de 130 px y un frame de
    1920—: una escala fija que se lee bien en uno tapa al otro o desaparece.
    """

    draw_boxes: bool = True
    draw_masks: bool = True
    mask_style: str = MASK_STYLE_FILL
    mask_alpha: float = _MASK_ALPHA
    draw_labels: bool = True
    draw_roi: bool = True
    draw_timestamp: bool = True
    crop_to_roi: bool = False
    font_scale: float = 0.6   # 0 = automática, según el ancho del frame
    thickness: int = 2
    class_colors_bgr: tuple = field(default_factory=tuple)


def get_class_color_bgr(class_index: int, colors_bgr: tuple = ()) -> tuple[int, int, int]:
    """Color de una clase por índice, recorriendo la paleta en círculo."""
    palette = tuple(colors_bgr) or DEFAULT_CLASS_COLORS_BGR
    color = palette[class_index % len(palette)]
    return (int(color[0]), int(color[1]), int(color[2]))


def annotate(result: InferenceResult, options: OverlayOptions | None = None, *,
             canvas_adjust=None) -> np.ndarray | None:
    """
    Devuelve una copia anotada de `result.source_bgr`, o None si no hay frame.

    Dibuja, en este orden: el recuadro del ROI, las máscaras, las cajas con su etiqueta,
    el panel de información y el timestamp. Un frame en escala de grises se convierte a
    BGR para que el color de cada clase se distinga.

    Con `crop_to_roi` lo que vuelve es sólo el recorte analizado. Las detecciones están en
    coordenadas del frame de referencia, así que se dibujan con el origen del ROI restado;
    no se las modifica, porque el mismo resultado viaja al dataset y al PLC.

    `canvas_adjust` es `(frame) -> frame` y se aplica al lienzo **antes** de dibujar. Es
    para que el anotado se vea como el stream crudo, que sale ajustado para una persona: un
    frame medido con poca luz es igual de ilegible con máscaras encima. Va antes y no
    después porque después le correría el color a las máscaras, a las referencias y al
    texto, que son justamente lo que el ajuste no tiene que tocar. Lo que se mide no cambia:
    `source_bgr` queda como estaba.
    """
    frame = result.source_bgr
    if frame is None or frame.size == 0:
        return None
    opts = options or OverlayOptions()

    annotated = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame.copy()
    if canvas_adjust is not None:
        annotated = canvas_adjust(annotated)

    offset_px = (0, 0)
    cropped = opts.crop_to_roi and result.roi_px is not None
    if cropped:
        x_px, y_px, width_px, height_px = result.roi_px
        annotated = np.ascontiguousarray(
            annotated[y_px:y_px + height_px, x_px:x_px + width_px])
        offset_px = (-x_px, -y_px)

    # Recuadrar un recorte es marcar su propio borde: no dice nada.
    if opts.draw_roi and result.roi_px is not None and not cropped:
        draw_roi(annotated, result.roi_px)
    draw_detections(annotated, result.detections, options=opts, offset_px=offset_px)
    if opts.draw_timestamp:
        draw_timestamp(annotated, result.timestamp_s, options=opts)
    return annotated


def draw_detections(frame_bgr: np.ndarray, detections: list[Detection], *,
                    options: OverlayOptions | None = None,
                    offset_px: tuple[int, int] = (0, 0)):
    """
    Dibuja in-place las máscaras, las cajas y las etiquetas, con el color de cada clase.

    `offset_px` se suma a cada coordenada antes de dibujar, para pintar sobre un recorte
    sin tocar las detecciones: son las mismas que van al dataset y al PLC.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return
    opts = options or OverlayOptions()
    height_px, width_px = frame_bgr.shape[:2]
    offset_x_px, offset_y_px = offset_px

    # Relleno por clase, acumulado y teñido una sola vez al final: ver `_stack_fill`.
    fill_by_color: dict = {}

    for detection in detections:
        color = get_class_color_bgr(detection.class_index, opts.class_colors_bgr)
        raw_x1, raw_y1, raw_x2, raw_y2 = (int(value) for value in detection.bbox_px)
        x1, y1 = raw_x1 + offset_x_px, raw_y1 + offset_y_px
        x2, y2 = raw_x2 + offset_x_px, raw_y2 + offset_y_px
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
            if opts.mask_style in (MASK_STYLE_FILL, MASK_STYLE_BOTH):
                _stack_fill(fill_by_color, color, detection.mask, (x1, y1, x2, y2),
                            (height_px, width_px))
            if opts.mask_style in (MASK_STYLE_OUTLINE, MASK_STYLE_BOTH):
                _outline_mask(frame_bgr, detection.mask, (x1, y1, x2, y2), color)
        if opts.draw_boxes:
            cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), color, opts.thickness)
        if opts.draw_labels:
            label = f"{detection.class_name} {detection.confidence_pct:.0f}%"
            _draw_label(frame_bgr, label, (x1, y1), color, opts)

    for color, fill in fill_by_color.items():
        _blend_fill(frame_bgr, fill, color, opts.mask_alpha)


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
                _FONT, _LABEL_SCALE, color_bgr, text_thickness(_LABEL_SCALE), cv2.LINE_AA)


def resolve_font_scale(frame_bgr: np.ndarray, lines: list[str],
                       options: OverlayOptions | None = None) -> float:
    """
    Escala de texto a usar: la del config, o una calculada si está en 0.

    La automática mide la línea más larga a escala 1 y la ajusta para que ocupe una
    fracción del ancho. Medir en vez de estimar es lo que hace que el panel entre igual
    con dos clases que con cinco, y con nombres de clase cortos o largos.
    """
    opts = options or OverlayOptions()
    if opts.font_scale > 0:
        return opts.font_scale
    if frame_bgr is None or frame_bgr.size == 0 or not lines:
        return _AUTO_FONT_MIN

    widest_px = max(cv2.getTextSize(line, _FONT, 1.0, 1)[0][0] for line in lines)
    # Descontando el margen y el relleno del panel: si se mide contra el ancho pelado, el
    # texto entra en la cuenta y se corta contra el borde igual.
    available_px = max(1, frame_bgr.shape[1] - _MARGIN_PX - 2 * _INNER_PAD_PX)
    target_px = available_px * _AUTO_TEXT_WIDTH_FRACTION
    return max(_AUTO_FONT_MIN, min(_AUTO_FONT_MAX, target_px / max(1, widest_px)))


def text_thickness(font_scale: float) -> int:
    """
    Grosor del trazo para ese tamaño de texto.

    El tamaño ya se adapta al ancho del frame; el grosor tiene que acompañarlo. Un trazo
    de un píxel en un texto grande se ve pálido y roto, que es justo lo que pasa en el
    frame completo después de haberlo ajustado en un recorte.
    """
    return max(1, int(round(font_scale / _TEXT_THICKNESS_STEP)))


def draw_text_panel(frame_bgr: np.ndarray, lines: list[str], *,
                    options: OverlayOptions | None = None,
                    text_bgr: tuple[int, int, int] = _PANEL_TEXT_BGR,
                    line_colors_bgr: tuple = ()):
    """
    Dibuja in-place un panel de texto con fondo opaco, arriba a la izquierda.

    El fondo opaco no es estética: sobre un frame claro el texto sin fondo no se lee, y
    el operador mira esto para decidir.

    `line_colors_bgr` pinta línea por línea, en el mismo orden que `lines`; lo que sobra,
    falta o venga en `None` cae en `text_bgr`. Es lo que deja que un panel que enumera
    clases use el color con el que están dibujadas: dos referencias del mismo dato que no
    coinciden de color obligan a leer el nombre para saber qué es cuál.
    """
    if frame_bgr is None or frame_bgr.size == 0 or not lines:
        return
    opts = options or OverlayOptions()

    font_scale = resolve_font_scale(frame_bgr, lines, opts)
    thickness = text_thickness(font_scale)
    sizes = [cv2.getTextSize(line, _FONT, font_scale, thickness)[0] for line in lines]
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
        color = line_colors_bgr[i] if i < len(line_colors_bgr) else None
        cv2.putText(frame_bgr, line, (_MARGIN_PX + _INNER_PAD_PX, y_px),
                    _FONT, font_scale, color or text_bgr, thickness, cv2.LINE_AA)


def draw_timestamp(frame_bgr: np.ndarray, timestamp_s: float, *,
                   options: OverlayOptions | None = None):
    """
    Dibuja in-place la fecha y hora del resultado, abajo a la derecha.

    Con `font_scale` en 0 se adapta al ancho del frame igual que el panel, un punto más
    chico: en un recorte angosto un timestamp de tamaño fijo se come media imagen.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return
    text = datetime.datetime.fromtimestamp(timestamp_s).strftime("%Y-%m-%d %H:%M:%S")
    font_scale = resolve_font_scale(frame_bgr, [text], options) * _TIMESTAMP_RATIO
    thickness = text_thickness(font_scale)
    (text_width_px, text_height_px), baseline_px = cv2.getTextSize(
        text, _FONT, font_scale, thickness)
    height_px, width_px = frame_bgr.shape[:2]
    x_px = width_px - text_width_px - _MARGIN_PX
    y_px = height_px - _MARGIN_PX
    cv2.rectangle(frame_bgr, (x_px - _INNER_PAD_PX, y_px - text_height_px - baseline_px),
                  (width_px, height_px), _PANEL_BG_BGR, cv2.FILLED)
    cv2.putText(frame_bgr, text, (x_px, y_px - baseline_px // 2),
                _FONT, font_scale, _PANEL_TEXT_BGR, thickness, cv2.LINE_AA)


def _stack_fill(fill_by_color: dict, color: tuple[int, int, int], mask: np.ndarray,
                bbox_px: tuple[int, int, int, int], frame_shape: tuple[int, int]):
    """
    Acumula la máscara sobre el lienzo de su color, en lugar de teñir el frame ya.

    **El relleno se pinta por UNIÓN y no por instancia.** Teñir cada máscara por separado
    mezcla el color tantas veces como instancias se superpongan, así que una zona con
    muchas detecciones encimadas sale más saturada que una con pocas aunque las dos estén
    igual de cubiertas: el color deja de decir qué clase es y pasa a decir cuántos
    polígonos se pisaron ahí. Y lo que se ve deja de coincidir con lo que se mide, que
    cuenta cada píxel una sola vez.

    Un píxel que dos clases se disputan queda de la última que lo reclame, que es el mismo
    criterio con el que se cuenta.
    """
    x1, y1, x2, y2 = bbox_px
    if mask.shape[:2] != (y2 - y1, x2 - x1):
        # La máscara es del bbox sin recortar: si el bbox se cortó contra el borde del
        # frame, no se la puede indexar contra la región.
        return
    selected = mask > 0
    if not selected.any():
        return
    for other_color, other_fill in fill_by_color.items():
        if other_color != color:
            other_fill[y1:y2, x1:x2][selected] = False
    fill = fill_by_color.get(color)
    if fill is None:
        fill = np.zeros(frame_shape, dtype=bool)
        fill_by_color[color] = fill
    fill[y1:y2, x1:x2][selected] = True


def _blend_fill(frame_bgr: np.ndarray, fill: np.ndarray, color: tuple[int, int, int],
                alpha: float = _MASK_ALPHA):
    """Tiñe in-place, de una sola pasada, todos los píxeles de una clase."""
    if not fill.any():
        return
    tint = np.empty_like(frame_bgr)
    tint[:] = color
    alpha = min(1.0, max(0.0, float(alpha)))
    blended = cv2.addWeighted(frame_bgr, 1.0 - alpha, tint, alpha, 0)
    frame_bgr[fill] = blended[fill]


def _outline_mask(frame_bgr: np.ndarray, mask: np.ndarray,
                  bbox_px: tuple[int, int, int, int], color: tuple[int, int, int]):
    """
    Contornea in-place la forma de la máscara, sin teñir su interior.

    Con cientos de instancias el relleno se superpone hasta que no se distingue una de
    otra; el contorno deja ver el tamaño y la forma de cada una, que es lo que se está
    midiendo. El grosor es 1 fijo y no `thickness`: con instancias chicas una línea de
    dos píxeles tapa la instancia entera.
    """
    x1, y1, x2, y2 = bbox_px
    if mask.shape[:2] != (y2 - y1, x2 - x1):
        return
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        cv2.drawContours(frame_bgr, contours, -1, color, 1, offset=(x1, y1))


def _draw_label(frame_bgr: np.ndarray, text: str, origin_px: tuple[int, int],
                color: tuple[int, int, int], options: OverlayOptions):
    """Etiqueta con fondo del color de la clase, pegada arriba de la caja."""
    x_px, y_px = origin_px
    # Con la escala en automático la etiqueta también se adapta: leer `font_scale` crudo
    # la dibujaba en cero, o sea invisible.
    font_scale = resolve_font_scale(frame_bgr, [_AUTO_LABEL_LINE], options)
    thickness = text_thickness(font_scale)
    (text_width_px, text_height_px), baseline_px = cv2.getTextSize(
        text, _FONT, font_scale, thickness)
    top_px = y_px - text_height_px - baseline_px - 2
    # Contra el borde de arriba la etiqueta se dibuja dentro de la caja.
    if top_px < 0:
        top_px = y_px + 2
    cv2.rectangle(frame_bgr, (x_px, top_px),
                  (x_px + text_width_px + 4, top_px + text_height_px + baseline_px + 2),
                  color, cv2.FILLED)
    cv2.putText(frame_bgr, text, (x_px + 2, top_px + text_height_px + 2),
                _FONT, font_scale, _LABEL_TEXT_BGR, thickness, cv2.LINE_AA)
