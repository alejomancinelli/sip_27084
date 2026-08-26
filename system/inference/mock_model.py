"""
Modelo sintético: detecciones sin framework y sin pesos.

Maquinaria genérica: reutilizable entre proyectos. Es lo que deja correr la app, la UI
y la suite de tests en un equipo sin torch, sin CUDA y sin el modelo del proyecto: el
pipeline, el overlay, el dataset y la telemetría se ejercitan igual.

Las detecciones son deterministas —una fila de cajas repartidas sobre el frame, con la
confianza bajando de la primera a la última—, así que un test puede afirmar sobre
ellas. `is_synthetic` viaja hasta el resultado para que nadie las publique como una
medición del proceso.
"""

import cv2
import numpy as np

from system.logger import logger

from .abstract_model import STATUS_UNLOADED, TASK_DETECTION, AbstractModel
from .result import Detection

_DEFAULT_DETECTION_COUNT = 3
_MAX_DETECTION_COUNT = 64        # techo: el mock dibuja, no estresa
_TOP_CONFIDENCE_PCT = 95.0
_CONFIDENCE_STEP_PCT = 7.0       # cuánto baja la confianza de una detección a la siguiente
_FLOOR_CONFIDENCE_PCT = 5.0
_BOX_MARGIN = 0.15               # fracción de la celda que queda de aire alrededor de la caja


class MockModel(AbstractModel):
    """
    Devuelve detecciones sintéticas repartidas sobre el frame, sin cargar nada.

    Dos extras propios en `params` de su sección del config:
        detection_count : cuántas detecciones devuelve por frame
        with_masks      : agrega a cada detección una máscara elíptica del tamaño del bbox
    """

    is_synthetic = True
    task = TASK_DETECTION

    def load(self):
        if self.is_loaded:
            return
        # Pasa por la misma guarda que un modelo real, con la tarea que declara su propio
        # config: un slot configurado como segmentación no puede quedar sirviendo cajas.
        if not self._verify_task(str(self._get_params().get("task", ""))):
            return
        self._set_loaded()
        logger.info(
            f"[Inference] Modelo '{self.model_slot}': mock. Las detecciones son "
            f"sintéticas y no son una medición del proceso."
        )

    def predict(self, frame_bgr: np.ndarray,
                previous: list[Detection] | None = None) -> list[Detection]:
        """Ignora `previous`: el mock no encadena, mira el frame y devuelve sus cajas."""
        if not self.is_loaded or frame_bgr is None or frame_bgr.size == 0:
            return []

        height_px, width_px = frame_bgr.shape[:2]
        count = self._get_detection_count()
        with_masks = bool(self._get_params().get("with_masks", False))
        min_confidence_pct = self.min_confidence_pct

        cell_width_px = width_px / count
        margin_px = int(cell_width_px * _BOX_MARGIN)
        box_height_px = int(height_px * (1.0 - 2 * _BOX_MARGIN))

        detections = []
        for i in range(count):
            confidence_pct = max(_FLOOR_CONFIDENCE_PCT,
                                 _TOP_CONFIDENCE_PCT - i * _CONFIDENCE_STEP_PCT)
            if confidence_pct < min_confidence_pct:
                continue
            x1 = int(i * cell_width_px) + margin_px
            x2 = int((i + 1) * cell_width_px) - margin_px
            y1 = int(height_px * _BOX_MARGIN)
            y2 = y1 + box_height_px
            if x2 <= x1 or y2 <= y1:
                continue
            mask = _build_ellipse_mask(x2 - x1, y2 - y1) if with_masks else None
            detections.append(Detection(
                class_index=i,
                class_name=self._get_class_name(i),
                confidence_pct=confidence_pct,
                bbox_px=(x1, y1, x2, y2),
                area_px=0 if mask is None else int(mask.sum()),
                mask=mask,
            ))
        return detections

    def unload(self):
        self._status = STATUS_UNLOADED

    def _get_detection_count(self) -> int:
        count = int(self._get_params().get("detection_count", _DEFAULT_DETECTION_COUNT))
        return max(1, min(_MAX_DETECTION_COUNT, count))


def _build_ellipse_mask(width_px: int, height_px: int) -> np.ndarray:
    """Máscara 0/1 del tamaño del bbox, con una elipse inscripta."""
    mask = np.zeros((height_px, width_px), dtype=np.uint8)
    cv2.ellipse(mask, (width_px // 2, height_px // 2),
                (max(1, width_px // 2 - 1), max(1, height_px // 2 - 1)),
                0, 0, 360, 1, thickness=cv2.FILLED)
    return mask
