"""
Dibujos propios de la instalación, encima del anotado estándar.

Módulo puro: sin Qt, sin ConfigManager y sin I/O. Cada factory devuelve un annotator —una
función que recibe el frame ya anotado y el resultado, y dibuja in-place— y `main.py` los
compone y se los pasa al motor:

    engine = InferenceThread(config, pipeline, analyzer=compute_metrics,
                             annotator=chain(
                                 max_load_line_annotator({"camera_1": 430}),
                                 ...))

**Específico del proyecto — no cross-portar tal cual.** Lo que sale de la inferencia lo
dibuja `overlay`, que es genérico; acá van las referencias que sólo existen en esta
planta: un límite de carga, el borde de una zona de rechazo, un eje de calibración.
Separado de `overlay` porque ese archivo se copia al próximo proyecto sin cambios, y esto
no.

Las factories reciben la geometría en píxeles y no leen el config: quién lee la sección
`process` es `main.py`, igual que con las condiciones de guardado del recolector. El mismo
valor alimenta al annotator que dibuja el límite y al analyzer que calcula el porcentaje
contra ese límite, así que se lee una vez y no pueden discrepar. Así un annotator se
testea con tres números y sin archivo, y la misma función sirve para una cámara o para
diez.

Cada annotator lleva su `__name__` armado con los valores que recibió, así el aviso del
motor dice cuál falló.
"""

from collections.abc import Callable

import numpy as np

from . import overlay
from .result import InferenceResult

Annotator = Callable[[np.ndarray, InferenceResult], None]

_MAX_LOAD_BGR = (60, 60, 230)   # rojo: es un límite, no una referencia neutra


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


def max_load_line_annotator(y_px_by_camera: dict[str, int], *,
                            label: str = "MAX LOAD") -> Annotator:
    """
    Dibuja el límite de carga de la cinta: una línea horizontal a la altura configurada.

    La altura es por cámara —cada cinta tiene la suya y el montaje de la cámara la
    mueve—, y una cámara que no figura en el dict no lleva línea. Se dibuja siempre,
    haya o no detecciones: es la referencia contra la que el operador mira el material,
    y una línea que aparece y desaparece no sirve para eso.
    """
    def draw(frame_bgr: np.ndarray, result: InferenceResult):
        y_px = y_px_by_camera.get(result.camera_slot)
        if y_px is None or frame_bgr is None or frame_bgr.size == 0:
            return
        height_px, width_px = frame_bgr.shape[:2]
        if not 0 <= int(y_px) < height_px:
            return
        overlay.draw_line(frame_bgr, (0, int(y_px)), (width_px, int(y_px)),
                          _MAX_LOAD_BGR, thickness=2, label=label)

    draw.__name__ = f"max_load_line({y_px_by_camera})"
    return draw
