"""
El `preprocessor` del motor: sobre qué imagen se mide.

Maquinaria genérica: reutilizable entre proyectos. Arma, desde la sección de cada cámara,
la corrección de lente (`tools/image/undistort.py`) y el ajuste de imagen
(`tools/image/enhance.py`), y los encadena en una sola función con la firma que pide el
motor: `(frame, camera_slot) -> frame`.

Lo que devuelve esa función es el **frame de referencia** del ciclo: el que ve el modelo,
el que queda en `source_bgr` —y con él en el dataset—, el espacio del ROI y el lienzo del
overlay. Lo usan la app y la corrida de imágenes sueltas, y por eso vive acá y no en
`main.py`: dos lugares que arman el preprocessor son dos lugares que pueden discrepar.
"""

from collections.abc import Callable

import numpy as np

from system.config_manager import ConfigManager
from system.logger import logger
from tools.image.enhance import build_image_adjust
from tools.image.undistort import build_undistorter

Preprocessor = Callable[[np.ndarray, str], np.ndarray]


def build_preprocessor(config: ConfigManager) -> Preprocessor | None:
    """
    Corrector de lente y ajuste de imagen, si alguna cámara declara alguno de los dos.

    Sin nada declarado devuelve None y el motor no gasta la pasada.

    **Primero la geometría y después el brillo.** El factor de brillo es por píxel y el
    orden no le cambia nada, pero el contraste local ecualiza por celdas: corrido antes de
    corregir el lente, las celdas quedarían deformadas sobre el frame que mide el modelo.
    """
    camera_slots = tuple(config.get("cameras", {}) or {})
    undistorter = build_undistorter({
        camera_slot: config.get(f"cameras.{camera_slot}.calibration", {}) or {}
        for camera_slot in camera_slots
    })
    if undistorter is not None:
        logger.info("[Inference] Corrección de lente activa: el frame de referencia va corregido.")
    image_adjust = build_image_adjust({
        camera_slot: config.get(f"cameras.{camera_slot}.image_adjust", {}) or {}
        for camera_slot in camera_slots
    })
    if image_adjust is not None:
        logger.info(f"[Inference] Ajuste de imagen activo — {image_adjust.__name__}: el modelo "
                    f"y el dataset reciben el frame ajustado.")
    return _chain_preprocessors(undistorter, image_adjust)


def _chain_preprocessors(*preprocessors: Preprocessor | None) -> Preprocessor | None:
    """
    Junta varios preprocessors en uno, en orden. El motor recibe una sola función.

    Los que vienen en None se descartan: sin ninguno devuelve None, y con uno solo lo
    devuelve tal cual, así el motor no paga una llamada de más por frame.
    """
    selected = [preprocessor for preprocessor in preprocessors if preprocessor is not None]
    if not selected:
        return None
    if len(selected) == 1:
        return selected[0]

    def preprocess(frame_bgr: np.ndarray, camera_slot: str) -> np.ndarray:
        for preprocessor in selected:
            frame_bgr = preprocessor(frame_bgr, camera_slot)
        return frame_bgr

    preprocess.__name__ = " + ".join(getattr(p, "__name__", repr(p)) for p in selected)
    return preprocess
