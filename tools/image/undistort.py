"""
Corrección de la distorsión del lente.

Librería portable: sin Qt, sin ConfigManager, sin I/O y sin nada del repo. Recibe las
matrices de calibración de cada cámara y devuelve la función que corrige sus frames.

**Va en el `preprocessor` del motor de inferencia, no en el camino de visualización.** Lo
que devuelve la corrección es el frame de referencia: es el que ve el modelo, el que queda
en `source_bgr`, el espacio en el que se expresan el ROI y las detecciones, y el lienzo
sobre el que dibuja el overlay. Con un solo espacio de coordenadas no hay nada que mapear
de vuelta y las cajas no pueden caer corridas.

Por eso la corrección geométrica no puede ir donde va el ajuste de brillo. El brillo
cambia cómo se ve un píxel; esto cambia dónde está. Un ajuste que sólo toca el camino de
visualización dejaría al modelo midiendo en un espacio y al operador mirando otro.

La contracara: un modelo entrenado con imágenes crudas mide peor sobre imágenes
corregidas. Las dos opciones honestas son reentrenar con el dataset corregido —el
recolector guarda lo que se le pase— o dejar el frame de referencia crudo y aplicar la
corrección después de anotar, sobre el camino de visualización, aceptando que las
etiquetas dibujadas se deformen con la imagen.

Los mapas de remapeo se calculan una vez por cámara, al construir la función: recalcularlos
por frame cuesta más que la corrección misma.
"""

from collections.abc import Callable

import cv2
import numpy as np

# Cuánto del sensor se conserva al corregir: 0 recorta hasta que no quede ningún píxel
# inventado, 1 conserva el frame completo y deja bordes negros donde el lente no llegaba.
_DEFAULT_KEEP_FIELD = 0.0

Undistorter = Callable[[np.ndarray, str], np.ndarray]


def build_undistorter(calibration_by_camera: dict, *,
                      keep_field: float = _DEFAULT_KEEP_FIELD) -> Undistorter | None:
    """
    Devuelve el preprocessor que corrige cada cámara según su calibración, o None si
    ninguna la declara.

    `calibration_by_camera` es `{camera_slot: {"camera_matrix": [[...]], "dist_coeffs":
    [...]}}`, tal como sale del config. Una cámara sin calibración pasa sin tocar, así que
    la misma función sirve para un pipeline con cámaras calibradas y sin calibrar.

    Los mapas se arman perezosamente, con el tamaño del primer frame de cada cámara: el
    config no declara la resolución y pedirla sería un dato más que puede quedar desfasado
    del sensor.
    """
    parsed = {}
    for camera_slot, calibration in (calibration_by_camera or {}).items():
        matrices = _parse_calibration(calibration)
        if matrices is not None:
            parsed[camera_slot] = matrices
    if not parsed:
        return None

    keep_field = max(0.0, min(1.0, float(keep_field)))
    maps_by_camera: dict[str, tuple] = {}

    def undistort(frame_bgr: np.ndarray, camera_slot: str) -> np.ndarray:
        matrices = parsed.get(camera_slot)
        if matrices is None or frame_bgr is None or frame_bgr.size == 0:
            return frame_bgr
        height_px, width_px = frame_bgr.shape[:2]
        cached = maps_by_camera.get(camera_slot)
        if cached is None or cached[0] != (width_px, height_px):
            camera_matrix, dist_coeffs = matrices
            maps_by_camera[camera_slot] = (
                (width_px, height_px),
                *_build_maps(camera_matrix, dist_coeffs, (width_px, height_px), keep_field),
            )
            cached = maps_by_camera[camera_slot]
        return cv2.remap(frame_bgr, cached[1], cached[2], cv2.INTER_LINEAR)

    undistort.__name__ = f"undistort({', '.join(sorted(parsed))})"
    return undistort


def _parse_calibration(calibration: object) -> tuple[np.ndarray, np.ndarray] | None:
    """
    Convierte la calibración del config en matrices, o None si no sirve.

    Una calibración incompleta o mal escrita no levanta excepción: la cámara queda sin
    corregir, que es lo mismo que no haberla declarado. Corregir con una matriz inventada
    desplazaría cada medición sin avisar.
    """
    if not isinstance(calibration, dict):
        return None
    try:
        camera_matrix = np.array(calibration.get("camera_matrix"), dtype=np.float64)
        dist_coeffs = np.array(calibration.get("dist_coeffs"), dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if camera_matrix.shape != (3, 3) or dist_coeffs.size not in (4, 5, 8, 12, 14):
        return None
    return camera_matrix, dist_coeffs.reshape(1, -1)


def _build_maps(camera_matrix: np.ndarray, dist_coeffs: np.ndarray,
                size_px: tuple[int, int], keep_field: float) -> tuple[np.ndarray, np.ndarray]:
    """Mapas de remapeo para ese tamaño de frame. Se calculan una sola vez por cámara."""
    new_matrix, _ = cv2.getOptimalNewCameraMatrix(
        camera_matrix, dist_coeffs, size_px, keep_field, size_px)
    return cv2.initUndistortRectifyMap(
        camera_matrix, dist_coeffs, None, new_matrix, size_px, cv2.CV_16SC2)
