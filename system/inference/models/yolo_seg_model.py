"""
Modelo de segmentación YOLO sobre ultralytics: pellet y desmenuzado sobre la cinta.

**Específico del proyecto — no cross-portar tal cual.** Es el modelo de esta instalación;
otro fork implementa el suyo y lo registra en `model_factory`. Lo que sí es genérico —el
ciclo de vida, la lectura de los pesos, el umbral por detección y los nombres de clase—
viene de `AbstractModel` y no se repite acá.

Entrega una detección por instancia con su **máscara recortada al bbox**, que es lo que
pide el contrato: con cientos de partículas en un frame, una máscara del tamaño de la
imagen por detección no entra en memoria. La máscara sale de rasterizar el polígono que
devuelve ultralytics, que ya viene en coordenadas del frame original.

Sobre los pesos protegidos: ultralytics carga **desde una ruta** y no desde bytes, así que
un contenedor cifrado no se puede abrir por este camino y `load()` corta con el motivo. El
archivo igual pasa por `_read_weights()`, que es lo que valida que exista, deja la metadata
—si la trae— y confronta la tarea. Cerrar esa puerta es implementar el loader nativo de
TensorRT (`runtime.deserialize_cuda_engine(bytes)`), que sí recibe bytes; está anotado en
`docs/model_protection.md`.
"""

import cv2
import numpy as np

from system.logger import logger

from . import encrypted_weights
from .abstract_model import STATUS_UNLOADED, TASK_SEGMENTATION, AbstractModel
from ..result import Detection

# Ultralytics quiere la confianza en 0–1 y el contrato la declara en 0–100.
_PCT_TO_FRACTION = 100.0


class YoloSegModel(AbstractModel):
    """
    Segmentación por instancias con ultralytics, sobre pesos `.pt` o `.engine`.

    Una instancia por slot de `inference.models`. Corre en el hilo del motor y no se
    comparte entre hilos: `load()`, `predict()` y `unload()` se llaman desde el mismo.
    """

    task = TASK_SEGMENTATION

    def __init__(self, config_manager, model_slot: str):
        super().__init__(config_manager, model_slot)
        self._model = None
        self._device = "cpu"

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    def load(self):
        if self.is_loaded:
            return

        path = self._get_model_path()
        if _is_protected_file(path):
            self._set_error(
                f"Los pesos de '{path}' están protegidos y ultralytics carga desde una "
                f"ruta, no desde memoria. Hace falta el loader nativo de TensorRT; ver "
                f"docs/model_protection.md."
            )
            logger.error(f"[Inference] Modelo '{self.model_slot}': {self._error}")
            return

        # Los pesos en claro se leen igual por el contrato: es lo que valida el archivo,
        # deja la metadata que pueda traer y confronta la tarea antes de gastar la carga.
        # El framework después los relee de la ruta, que es lo único que acepta.
        if not self._read_weights(path):
            return

        try:
            from ultralytics import YOLO
        except ImportError as e:
            self._set_error(f"No se pudo importar ultralytics: {e}")
            logger.error(f"[Inference] Modelo '{self.model_slot}': {self._error}")
            return

        self._device = _pick_device()
        try:
            model = YOLO(path, task="segment")
        except Exception as e:
            self._set_error(f"No se pudieron cargar los pesos de '{path}': {e}")
            logger.error(f"[Inference] Modelo '{self.model_slot}': {self._error}")
            return

        # La tarea que informa el runtime, ahora sí: un `.engine` de ultralytics la trae en
        # su metadata, y un desacuerdo tiene que salir acá y no adentro del postproceso.
        if not self._verify_task(str(getattr(model, "task", "") or "")):
            return

        self._model = model
        self._set_loaded()
        logger.info(
            f"[Inference] Modelo '{self.model_slot}': segmentación cargada en "
            f"'{self._device}' desde '{path}'."
        )

    def predict(self, frame_bgr: np.ndarray,
                previous: list[Detection] | None = None) -> list[Detection]:
        """Ignora `previous`: es la única etapa y mira el frame completo."""
        if not self.is_loaded or frame_bgr is None or frame_bgr.size == 0:
            return []

        scaled_bgr, scale = _fit_to_max_side(frame_bgr, self._get_max_side_px())
        try:
            predictions = self._model.predict(
                source=scaled_bgr,
                conf=self.min_confidence_pct / _PCT_TO_FRACTION,
                verbose=False,
                device=self._device,
            )
        except Exception as e:
            logger.error(f"[Inference] Modelo '{self.model_slot}': falló la inferencia: {e}")
            return []

        if not predictions:
            return []
        return self._build_detections(predictions[0], scale, frame_bgr.shape[:2])

    def unload(self):
        self._model = None
        self._status = STATUS_UNLOADED

    # ── Postproceso ──────────────────────────────────────────────────────────

    def _build_detections(self, prediction, scale: float,
                          frame_shape: tuple[int, int]) -> list[Detection]:
        """
        Traduce la salida de ultralytics a detecciones del contrato.

        `scale` deshace la reducción de `max_side_px`: quien llama nunca ve la escala
        interna. Una instancia sin polígono se descarta —un modelo de segmentación que no
        devuelve forma no midió nada— en vez de entrar con el área del bbox, que sería un
        número inventado con la misma pinta que los buenos.
        """
        boxes = getattr(prediction, "boxes", None)
        masks = getattr(prediction, "masks", None)
        if boxes is None or masks is None or len(boxes) == 0:
            return []

        class_indices = boxes.cls.tolist()
        confidences = boxes.conf.tolist()
        detections = []
        for i, polygon in enumerate(masks.xy):
            if i >= len(class_indices) or len(polygon) < 3:
                continue
            bbox_px = _polygon_bbox_px(polygon, scale, frame_shape)
            if bbox_px is None:
                continue
            mask = _rasterize_polygon(polygon, scale, bbox_px)
            area_px = int(mask.sum())
            if area_px == 0:
                continue
            class_index = int(class_indices[i])
            detections.append(Detection(
                class_index=class_index,
                class_name=self._get_class_name(class_index),
                confidence_pct=round(float(confidences[i]) * _PCT_TO_FRACTION, 1),
                bbox_px=bbox_px,
                area_px=area_px,
                mask=mask,
            ))
        return detections


# ── Helpers del módulo ───────────────────────────────────────────────────────

def _is_protected_file(path: str) -> bool:
    """
    Si el archivo arranca con el encabezado de los pesos cifrados.

    Lee sólo el encabezado: el archivo entero puede pesar cientos de MB y esto corre antes
    de decidir si vale la pena leerlo. Un archivo que no se puede abrir devuelve False y el
    motivo lo da `_read_weights()`, que es de quien es.
    """
    if not path:
        return False
    try:
        with open(path, "rb") as weights_file:
            return encrypted_weights.is_encrypted(weights_file.read(
                encrypted_weights.HEADER_SIZE))
    except OSError:
        return False


def _pick_device() -> str:
    """
    'cuda' si hay GPU disponible, 'cpu' si no, avisando por log cuando cae a CPU.

    En la Jetson caer a CPU es una falla de instalación —JetPack, driver, el wheel de torch
    equivocado— y tiene que verse en el log: si no, se deduce recién de que la inferencia
    tarda veinte veces más.
    """
    try:
        import torch
    except ImportError as e:
        logger.warning(f"[Inference] Sin torch ({e}): la inferencia va por CPU.")
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    logger.warning(
        "[Inference] CUDA no disponible: la inferencia va por CPU. En un equipo con GPU "
        "esto es una falla de instalación y no una opción."
    )
    return "cpu"


def _fit_to_max_side(frame_bgr: np.ndarray, max_side_px: int) -> tuple[np.ndarray, float]:
    """
    Reduce el frame si su lado mayor pasa el tope, y devuelve el factor para deshacerlo.

    Con el tope en 0, o con un frame que ya entra, devuelve el mismo array y escala 1.
    """
    if max_side_px <= 0:
        return frame_bgr, 1.0
    height_px, width_px = frame_bgr.shape[:2]
    longest_px = max(height_px, width_px)
    if longest_px <= max_side_px:
        return frame_bgr, 1.0
    ratio = max_side_px / longest_px
    resized = cv2.resize(
        frame_bgr,
        (max(1, int(round(width_px * ratio))), max(1, int(round(height_px * ratio)))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, 1.0 / ratio


def _polygon_bbox_px(polygon: np.ndarray, scale: float,
                     frame_shape: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """
    Caja que encierra al polígono, ya en coordenadas del frame recibido.

    Sale del polígono y no de la caja del modelo porque la máscara se guarda recortada a
    esta caja: una caja más chica que la forma recortaría parte de lo que se va a medir.
    Devuelve None si queda fuera del frame o sin área.
    """
    height_px, width_px = frame_shape
    points = np.asarray(polygon, dtype=np.float64) * scale
    x1 = int(np.floor(points[:, 0].min()))
    y1 = int(np.floor(points[:, 1].min()))
    x2 = int(np.ceil(points[:, 0].max()))
    y2 = int(np.ceil(points[:, 1].max()))
    x1 = max(0, min(x1, width_px))
    y1 = max(0, min(y1, height_px))
    x2 = max(0, min(x2, width_px))
    y2 = max(0, min(y2, height_px))
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def _rasterize_polygon(polygon: np.ndarray, scale: float,
                       bbox_px: tuple[int, int, int, int]) -> np.ndarray:
    """Máscara 0/1 del tamaño del bbox, con el polígono relleno."""
    x1, y1, x2, y2 = bbox_px
    mask = np.zeros((y2 - y1, x2 - x1), dtype=np.uint8)
    points = np.asarray(polygon, dtype=np.float64) * scale
    points[:, 0] -= x1
    points[:, 1] -= y1
    cv2.fillPoly(mask, [np.round(points).astype(np.int32)], 1)
    return mask
