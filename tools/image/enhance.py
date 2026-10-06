"""
Ajuste de imagen: brillo y contraste local.

Librería portable: sin Qt, sin ConfigManager, sin I/O y sin nada del repo. Recibe un
frame y devuelve uno nuevo.

Hay dos caminos que no se mezclan, y cuál se usa es una decisión del proceso, no una
preferencia de pantalla:

    cámara ──► preprocessor ──► modelo + dataset   `build_image_adjust()`, por cámara
           ──► streams + UI                         `build_display_adjust()`, para mirar

  - **Para mirar** (`video.display`): gamma y contraste local entre el frame y lo que ve un
    humano. El gamma es una curva que levanta las sombras sin saturar los claros, que es
    lo que se quiere para ver una escena oscura. El modelo y el dataset siguen recibiendo
    el frame de la cámara.
  - **Para medir** (`cameras.<slot>.image_adjust`): un factor de brillo lineal —cada píxel
    por el factor, saturado en 255— y contraste local, en el `preprocessor` del motor. Es
    lineal y no gamma porque existe para **reproducir** un equipo o un modelo entrenado
    sobre imágenes multiplicadas así, y otra curva le daría al modelo imágenes que no vio
    al entrenar. Satura a propósito: el equipo que se reproduce también saturaba. Cambia
    el frame de referencia, así que el dataset guarda la imagen ajustada y el brillo del
    ROI que decide `illumination_min` se mide después del ajuste. En una instalación
    nueva va en 1.0: la escena oscura se arregla con la ganancia y el modelo se entrena
    con lo que entrega el sensor, porque desde ahí se puede derivar cualquier ajuste y un
    píxel saturado, en cambio, no se recupera.

Ninguno de los dos va en el driver: el driver es el único que toca el frame de todos, y
subirle el brillo ahí lo cambiaría también para la vista cruda y para la nitidez de la
óptica, que se miden sobre lo que entrega el sensor.

Los dos builders devuelven None cuando la configuración no cambia nada, así el llamador se
saltea la llamada en vez de copiar el frame para dejarlo igual.
"""

from collections.abc import Callable

import cv2
import numpy as np

# Rangos aceptados. Fuera de estos el ajuste no mejora nada: gamma < 0.1 quema la imagen y
# un clip de CLAHE alto convierte el ruido del sensor en textura.
GAMMA_MIN = 0.1
GAMMA_MAX = 5.0
CLAHE_CLIP_MAX = 8.0
# El factor se satura a este rango en vez de ignorarse: es un número que copia el de otro
# equipo, y descartarlo dejaría al modelo midiendo sin el ajuste con el que se entrenó.
BRIGHTNESS_FACTOR_MIN = 0.1
BRIGHTNESS_FACTOR_MAX = 5.0

_CLAHE_GRID = (8, 8)      # celdas en las que CLAHE ecualiza por separado
_NEUTRAL_GAMMA = 1.0
_NEUTRAL_FACTOR = 1.0
_NEUTRAL_TOLERANCE = 0.01
_LUT_SIZE = 256

CameraAdjuster = Callable[[np.ndarray, str], np.ndarray]


def build_display_adjust(*, gamma: float = _NEUTRAL_GAMMA,
                         clahe_clip: float = 0.0) -> Callable[[np.ndarray], np.ndarray] | None:
    """
    Devuelve la función de ajuste para esos parámetros, o None si no hay nada que ajustar.

    Precalcula la tabla de gamma una sola vez: aplicarla después es un `cv2.LUT`, que a
    2 MP cuesta un par de milisegundos. CLAHE es un orden de magnitud más caro, así que se
    prende sólo cuando la escena lo necesita.
    """
    gamma = float(gamma or _NEUTRAL_GAMMA)
    clahe_clip = _clamp_clahe_clip(clahe_clip)
    use_gamma = (GAMMA_MIN <= gamma <= GAMMA_MAX
                 and abs(gamma - _NEUTRAL_GAMMA) > _NEUTRAL_TOLERANCE)
    if not use_gamma and clahe_clip <= 0:
        return None
    return _build_lut_adjust(_build_gamma_lut(gamma) if use_gamma else None, clahe_clip,
                             name=f"display_adjust(gamma={gamma}, clahe_clip={clahe_clip})")


def _build_measurement_adjust(*, brightness_factor: float = _NEUTRAL_FACTOR,
                              clahe_clip: float = 0.0) -> Callable[[np.ndarray], np.ndarray] | None:
    """
    Devuelve el ajuste de medición para esos parámetros, o None si no hay nada que ajustar.

    El brillo es lineal: cada píxel por `brightness_factor`, saturado en 255 —x0.1
    oscurece, x1.0 no cambia, x5.0 aclara—. Va antes que el contraste local, que así
    ecualiza la imagen ya aclarada, como la ve el modelo.
    """
    factor = max(BRIGHTNESS_FACTOR_MIN,
                 min(BRIGHTNESS_FACTOR_MAX, float(brightness_factor or _NEUTRAL_FACTOR)))
    clahe_clip = _clamp_clahe_clip(clahe_clip)
    use_factor = abs(factor - _NEUTRAL_FACTOR) > _NEUTRAL_TOLERANCE
    if not use_factor and clahe_clip <= 0:
        return None
    return _build_lut_adjust(
        _build_factor_lut(factor) if use_factor else None, clahe_clip,
        name=f"measurement_adjust(brightness_factor={factor}, clahe_clip={clahe_clip})")


def build_image_adjust(adjust_by_camera: dict) -> CameraAdjuster | None:
    """
    Devuelve el preprocessor que ajusta cada cámara con sus parámetros, o None si ninguna
    ajusta nada.

    `adjust_by_camera` es `{camera_slot: {"brightness_factor": ..., "clahe_clip": ...}}`,
    tal como sale del config. Una cámara sin ajuste, o con uno neutro, pasa sin tocar y sin
    copiar, así que la misma función sirve para un pipeline con cámaras ajustadas y sin
    ajustar. La firma es la del `preprocessor` del motor, `(frame, camera_slot) -> frame`.
    """
    adjust_by_slot = {}
    for camera_slot, params in (adjust_by_camera or {}).items():
        params = params if isinstance(params, dict) else {}
        adjust = _build_measurement_adjust(
            brightness_factor=params.get("brightness_factor", _NEUTRAL_FACTOR),
            clahe_clip=params.get("clahe_clip", 0.0))
        if adjust is not None:
            adjust_by_slot[camera_slot] = adjust
    if not adjust_by_slot:
        return None

    def adjust_camera(frame_bgr: np.ndarray, camera_slot: str) -> np.ndarray:
        adjust = adjust_by_slot.get(camera_slot)
        return frame_bgr if adjust is None else adjust(frame_bgr)

    adjust_camera.__name__ = f"image_adjust({', '.join(sorted(adjust_by_slot))})"
    return adjust_camera


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


def _build_lut_adjust(lut: np.ndarray | None, clahe_clip: float, *,
                      name: str) -> Callable[[np.ndarray], np.ndarray]:
    """Tabla por nivel y CLAHE, en ese orden. Uno de los dos tiene que estar."""
    clahe = cv2.createCLAHE(clipLimit=clahe_clip, tileGridSize=_CLAHE_GRID) if clahe_clip else None

    def adjust(frame_bgr: np.ndarray) -> np.ndarray:
        if frame_bgr is None or frame_bgr.size == 0:
            return frame_bgr
        adjusted = frame_bgr if lut is None else cv2.LUT(frame_bgr, lut)
        if clahe is not None:
            adjusted = _apply_clahe(adjusted, clahe)
        # Sin tabla ni CLAHE no se llega acá, así que siempre hay copia: el llamador puede
        # publicar el resultado sin miedo a estar tocando el frame de cámara.
        return adjusted if adjusted is not frame_bgr else frame_bgr.copy()

    adjust.__name__ = name
    return adjust


def _clamp_clahe_clip(clahe_clip: object) -> float:
    return max(0.0, min(CLAHE_CLIP_MAX, float(clahe_clip or 0.0)))


def _build_factor_lut(factor: float) -> np.ndarray:
    """
    Tabla de 256 entradas que multiplica por `factor` y satura en 255.

    Es la cuenta de `np.clip(frame.astype(np.float32) * factor, 0, 255).astype(np.uint8)`
    —la de los equipos que este ajuste reproduce— resuelta una vez por nivel: el resultado
    es el mismo píxel a píxel y aplicarla cuesta un `cv2.LUT`.
    """
    levels = np.arange(_LUT_SIZE, dtype=np.float32) * factor
    return np.clip(levels, 0, 255).astype(np.uint8)


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
