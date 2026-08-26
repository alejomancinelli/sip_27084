"""
Ajuste de imagen para que la vea una persona: gamma y contraste local.

Librería portable: sin Qt, sin ConfigManager, sin I/O y sin nada del repo. Recibe un
frame y devuelve uno nuevo.

**Es del camino de visualización, no del de medición.** Va entre el frame y lo que mira
un humano —los streams de video y la UI— y no entra ni al modelo ni al dataset:

    cámara ──► modelo        crudo, como se entrenó
           ──► dataset       crudo, para poder reentrenar
           ──► streams + UI  ajustado, para que se vea de noche

Esa separación es la razón de que el ajuste no esté en el driver: el driver es el único
que toca el frame de todos, y subirle el brillo ahí contaminaría el dataset. Si lo que se
quiere es que el modelo vea la imagen ajustada, el lugar es el `preprocessor` del motor de
inferencia, que sí cambia el frame de referencia.

`build_display_adjust()` devuelve None cuando la configuración no cambia nada, así el
llamador se saltea la llamada en vez de copiar el frame para dejarlo igual.
"""

from collections.abc import Callable

import cv2
import numpy as np

# Rangos aceptados. Fuera de estos el ajuste no mejora nada: gamma < 0.1 quema la imagen y
# un clip de CLAHE alto convierte el ruido del sensor en textura.
GAMMA_MIN = 0.1
GAMMA_MAX = 5.0
CLAHE_CLIP_MAX = 8.0

_CLAHE_GRID = (8, 8)      # celdas en las que CLAHE ecualiza por separado
_NEUTRAL_GAMMA = 1.0
_LUT_SIZE = 256


def build_display_adjust(*, gamma: float = _NEUTRAL_GAMMA,
                         clahe_clip: float = 0.0) -> Callable[[np.ndarray], np.ndarray] | None:
    """
    Devuelve la función de ajuste para esos parámetros, o None si no hay nada que ajustar.

    Precalcula la tabla de gamma una sola vez: aplicarla después es un `cv2.LUT`, que a
    2 MP cuesta un par de milisegundos. CLAHE es un orden de magnitud más caro, así que se
    prende sólo cuando la escena lo necesita.
    """
    gamma = float(gamma or _NEUTRAL_GAMMA)
    clahe_clip = max(0.0, min(CLAHE_CLIP_MAX, float(clahe_clip or 0.0)))
    use_gamma = GAMMA_MIN <= gamma <= GAMMA_MAX and abs(gamma - _NEUTRAL_GAMMA) > 0.01
    if not use_gamma and clahe_clip <= 0:
        return None

    lut = _build_gamma_lut(gamma) if use_gamma else None
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=_CLAHE_GRID) if clahe_clip else None

    def adjust(frame_bgr: np.ndarray) -> np.ndarray:
        if frame_bgr is None or frame_bgr.size == 0:
            return frame_bgr
        adjusted = frame_bgr if lut is None else cv2.LUT(frame_bgr, lut)
        if clahe is not None:
            adjusted = _apply_clahe(adjusted, clahe)
        # Con gamma neutro y CLAHE apagado no se llega acá, así que siempre hay copia: el
        # llamador puede publicar el resultado sin miedo a estar tocando el frame de cámara.
        return adjusted if adjusted is not frame_bgr else frame_bgr.copy()

    adjust.__name__ = f"display_adjust(gamma={gamma}, clahe_clip={clahe_clip})"
    return adjust


def apply_gamma(frame_bgr: np.ndarray, gamma: float) -> np.ndarray:
    """
    Aclara u oscurece sin recortar: gamma < 1 levanta las sombras, gamma > 1 las hunde.

    Devuelve un frame nuevo. Un gamma fuera de rango o neutro devuelve una copia sin
    cambios, para que el llamador no tenga que preguntar.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return frame_bgr
    if not GAMMA_MIN <= gamma <= GAMMA_MAX or abs(gamma - _NEUTRAL_GAMMA) <= 0.01:
        return frame_bgr.copy()
    return cv2.LUT(frame_bgr, _build_gamma_lut(gamma))


def apply_clahe(frame_bgr: np.ndarray, clip: float) -> np.ndarray:
    """
    Ecualización de contraste local sobre la luminancia, dejando el color como estaba.

    Es lo que rescata una escena con una zona quemada y otra en sombra, que el gamma solo
    no arregla. Sobre el canal L de LAB y no sobre los tres de BGR: ecualizar cada canal
    por separado le cambia el tinte a la imagen.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return frame_bgr
    clip = max(0.0, min(CLAHE_CLIP_MAX, float(clip or 0.0)))
    if clip <= 0:
        return frame_bgr.copy()
    return _apply_clahe(frame_bgr, cv2.createCLAHE(clipLimit=clip, tileGridSize=_CLAHE_GRID))


def _build_gamma_lut(gamma: float) -> np.ndarray:
    """
    Tabla de 256 entradas con la curva de gamma, para aplicarla con un solo cv2.LUT.

    La convención del módulo es `salida = entrada ** gamma` sobre 0–1, así que un gamma
    menor que 1 aclara. Es la que dice el nombre de la clave del config, y la que el
    operador espera: bajar el número aclara la imagen.
    """
    scale = np.arange(_LUT_SIZE, dtype=np.float32) / (_LUT_SIZE - 1)
    return np.clip(np.power(scale, gamma) * (_LUT_SIZE - 1), 0, 255).astype(np.uint8)


def _apply_clahe(frame_bgr: np.ndarray, clahe: cv2.CLAHE) -> np.ndarray:
    """Aplica un CLAHE ya construido al canal de luminancia. Devuelve un frame nuevo."""
    if frame_bgr.ndim == 2:
        return clahe.apply(frame_bgr)
    lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
