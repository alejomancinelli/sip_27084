"""
Dibujos propios de la instalación, encima del anotado estándar.

Módulo puro: sin Qt, sin ConfigManager y sin I/O. Cada factory devuelve un annotator —una
función que recibe el frame ya anotado y el resultado, y dibuja in-place— y `main.py` los
compone y se los pasa al motor:

    engine = InferenceThread(config, pipeline, analyzer=analyzer,
                             annotator=chain(
                                 belt_roi_annotator({"camera_1": {...}}),
                                 composition_panel_annotator(["desmenuzado", "pellet"])))

**Específico del proyecto — no cross-portar tal cual.** Lo que sale de la inferencia lo
dibuja `overlay`, que es genérico; acá van las referencias que sólo existen en esta
planta: el rectángulo de la cinta y el panel con el que el operador lee la composición.
Separado de `overlay` porque ese archivo se copia al próximo proyecto sin cambios, y esto
no.

Las factories reciben la geometría en píxeles y los nombres de clase ya leídos, y no tocan
el config: quién lee la sección `process` es `main.py`. El mismo valor alimenta al
annotator que dibuja el rectángulo de cinta y al analyzer que calcula la carga contra ese
rectángulo, así que se lee una vez y no pueden discrepar. Así un annotator se testea con
tres números y sin archivo, y la misma función sirve para una cámara o para diez.

Cada annotator lleva su `__name__` armado con los valores que recibió, así el aviso del
motor dice cuál falló.

De `metrics` sólo se importa el **nombre** de la clave de carga, que es una constante con
dueño único: el panel lee de `result.metrics` y tiene que pedir la misma clave que el
analyzer escribió. Repetir el literal acá sería el bug que este límite evita.
"""

from collections.abc import Callable

import numpy as np

from . import metrics, overlay
from .result import InferenceResult

Annotator = Callable[[np.ndarray, InferenceResult], None]

_BELT_ROI_BGR = (0, 220, 220)   # cian: no se confunde con ninguna clase de la cinta
_BELT_ROI_THICKNESS = 2

# Colores del panel de composición, los mismos con los que las clases se pintan sobre la
# cinta: dos referencias del mismo dato que no coinciden de color obligan a leer el nombre
# para saber qué es cuál. Se buscan por nombre de clase, que es dato de configuración; una
# clase que no figure sale en el color neutro.
_PANEL_NEUTRAL_BGR = (210, 210, 210)
_CLASS_BGR = {"pellet": (46, 204, 113), "desmenuzado": (60, 76, 231)}


def chain(*annotators: Annotator) -> Annotator:
    """
    Junta varios annotators en uno. El motor recibe una sola función.

    Los corre en orden, así que el último dibuja encima. Uno que falla corta la cadena: el
    motor lo avisa y el frame conserva lo que se haya dibujado hasta ahí.
    """
    selected = [annotator for annotator in annotators if annotator is not None]

    def draw(frame_bgr: np.ndarray, result: InferenceResult):
        for annotator in selected:
            annotator(frame_bgr, result)

    draw.__name__ = f"chain({', '.join(getattr(a, '__name__', repr(a)) for a in selected)})"
    return draw


def belt_roi_annotator(roi_px_by_camera: dict[str, dict]) -> Annotator:
    """
    Marca el rectángulo de cinta: la referencia contra la que se mide la carga.

    Es el mismo `process.belt_roi_px` que recibe el analyzer, leído una sola vez en
    `main.py`, para que el operador no pueda estar mirando un rectángulo distinto del que
    se usó para calcular el porcentaje que salió al PLC.

    Se dibuja siempre, haya o no detecciones: una referencia que aparece y desaparece no
    sirve como referencia. Una cámara que no figura en el dict no lleva recuadro.
    """
    def draw(frame_bgr: np.ndarray, result: InferenceResult):
        roi_px = roi_px_by_camera.get(result.camera_slot)
        if not roi_px or frame_bgr is None or frame_bgr.size == 0:
            return
        x1 = int(roi_px.get("x_px", 0))
        y1 = int(roi_px.get("y_px", 0))
        x2 = x1 + int(roi_px.get("width_px", 0))
        y2 = y1 + int(roi_px.get("height_px", 0))
        if x2 <= x1 or y2 <= y1:
            return
        for start_px, end_px in (((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)),
                                 ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))):
            overlay.draw_line(frame_bgr, start_px, end_px, _BELT_ROI_BGR,
                              thickness=_BELT_ROI_THICKNESS)

    draw.__name__ = f"belt_roi({sorted(roi_px_by_camera)})"
    return draw


def composition_panel_annotator(class_names: list) -> Annotator:
    """
    Panel con la composición normalizada y la carga de la cinta.

    Es lo que el operador lee mientras la línea trabaja, y por eso va **quemado en el
    frame** y no en un widget: así viaja igual por los dos streams y queda escrito en la
    imagen que se guarda en el dataset. Los números salen de `metrics` y no se recalculan
    acá: dos cuentas del mismo dato terminan discrepando.

    `class_names` es la lista del modelo, en orden: fija qué clases se enumeran, en qué
    orden y con qué etiqueta. Un resultado sin métricas —una medición no confiable— no
    dibuja nada.
    """
    names = [str(name) for name in (class_names or [])]
    labels = [name.capitalize() for name in names]
    label_width = max((len(label) for label in labels), default=0) + 2

    def draw(frame_bgr: np.ndarray, result: InferenceResult):
        if frame_bgr is None or frame_bgr.size == 0 or not result.metrics:
            return
        lines = ["Composicion:"]
        colors_bgr = [_PANEL_NEUTRAL_BGR]
        for class_name, label in zip(names, labels):
            value_pct = result.metrics.get(f"pct_{class_name}_norm")
            if value_pct is None:
                continue
            lines.append(f"  {(label + ':'):<{label_width}} {float(value_pct):5.1f}%")
            colors_bgr.append(_CLASS_BGR.get(class_name, _PANEL_NEUTRAL_BGR))
        load_pct = result.metrics.get(metrics.LOAD_KEY)
        if load_pct is not None:
            lines.append(f"Carga cinta: {float(load_pct):5.1f}%")
            colors_bgr.append(_PANEL_NEUTRAL_BGR)
        if len(lines) == 1:
            return
        overlay.draw_text_panel(frame_bgr, lines, line_colors_bgr=tuple(colors_bgr))

    draw.__name__ = f"composition_panel({names})"
    return draw
