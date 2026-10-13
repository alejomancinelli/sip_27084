"""
Pipeline de inferencia del proyecto: qué modelos corren, en qué orden y qué se hace con
lo que devuelven.

**Específico del proyecto — no cross-portar tal cual.** Es uno de los tres archivos que
se reescriben en cada fork, junto con `metrics.py` y las filas `producer: inference` del
mapa de registros. La maquinaria que lo corre —el contrato de las etapas, el hilo, el
overlay y las agregaciones— no se toca.

Este proyecto tiene una sola etapa —el segmentador de la cinta, que separa pellet de
desmenuzado— y un postproceso sobre lo que devuelve: **sacar de cada máscara el fondo
oscuro**. El contorno del segmentador es aproximadamente convexo y se traga la cinta que se
ve *entre* partículas sueltas, así que sin esto una pila dispersa mide como una compacta.

Por qué el postproceso va acá y no en el modelo ni en el analyzer:

  - **En el modelo no**, porque el umbral depende de la iluminación y la iluminación es de
    cada cámara y su montaje. Un modelo es uno solo para todas las cámaras del pipeline y
    `predict()` ni siquiera sabe de cuál viene el frame. Además el modelo se reusa entre
    proyectos y esto no.
  - **En el analyzer tampoco**, porque el analyzer no modifica el resultado: corregiría los
    números pero el overlay seguiría pintando los píxeles que descartó, y el umbral quedaría
    invisible justo mientras se lo calibra.
  - **Acá sí**: corre antes del analyzer y antes del overlay, así que la máscara que se mide
    y la que se dibuja son la misma; recibe el `camera_slot`, así que el umbral es por
    cámara; y es código del fork, que es donde vive la lógica del proceso.

Lo que quede sin un solo píxel se descarta: una instancia que ya no cubre nada no es una
instancia, y contarla inflaría `detection_count` y el promedio de confianza.

**Las detecciones salen de menor a mayor confianza.** El conteo de `metrics.py` y el relleno
del overlay le dan un píxel disputado a la última máscara que lo reclama, y ultralytics las
devuelve de mayor a menor: sin reordenar, donde pellet y desmenuzado se pisan —justo la
frontera que decide la composición— ganaría la menos confiable. Se ordena una sola vez acá
para que los dos consumidores, que comparten el orden, sigan diciendo lo mismo.

Los umbrales llegan **leídos**, como los del analyzer y los annotators: los lee `main.py`
de la sección `process` y esta clase se testea con tres números y sin archivo.

**Las clases de esta cinta están en el código, no en el config.** De cada nombre sale el de
su métrica —`pct_pellet_norm`— y con él el registro que lee el PLC, así que no es algo que
se edite en planta: `CLASS_NAMES` es la única declaración, y de acá la leen el cableado, el
analyzer, el panel y la pestaña de proceso. El pipeline nombra cada detección por su índice
con esta tupla, y al cargar la confronta con los nombres que traen los pesos: un modelo con
las clases en otro orden no da un error, da todos los porcentajes invertidos, y por eso el
pipeline no arranca y deja el motivo en el log. Un modelo que no declara nombres —el mock,
o un `.engine` sin la cabecera de ultralytics— no tiene contra qué confrontarse y se nombra
igual: los nombres de los pesos sólo confirman el orden, nunca son los que se usan.
"""

import cv2
import numpy as np

from system.logger import logger

from .abstract_pipeline import AbstractPipeline
from .result import Detection

_SEGMENTER_SLOT = "segmenter"

# Por índice del modelo: 0 = desmenuzado, 1 = pellet, confirmado contra el `.engine`.
CLASS_NAMES = ("desmenuzado", "pellet")


class BeltPipeline(AbstractPipeline):
    """
    Una etapa —el segmentador de la cinta— más el descarte de fondo oscuro por cámara.

    `dark_background_threshold` es `{camera_slot: {clase: luma_mínima}}`. Una cámara que no
    figura, o una clase con umbral 0, no se refina.
    """

    model_slots = (_SEGMENTER_SLOT,)

    def __init__(self, config_manager, pipeline_slot: str, *,
                 dark_background_threshold: dict | None = None):
        super().__init__(config_manager, pipeline_slot)
        self._dark_background_threshold = dark_background_threshold or {}
        self._class_mismatch = ""

    @property
    def is_loaded(self) -> bool:
        """Cargado, y con un modelo cuyas clases son las de `CLASS_NAMES`."""
        return super().is_loaded and not self._class_mismatch

    def get_status(self) -> dict:
        """El de la base, más `class_error`: por qué no arranca aunque el modelo cargó."""
        status = super().get_status()
        status["class_error"] = self._class_mismatch or None
        return status

    def load(self):
        super().load()
        model = self._models.get(_SEGMENTER_SLOT)
        declared = model.class_names if model is not None else ()
        self._class_mismatch = ""
        if declared and tuple(declared) != CLASS_NAMES:
            self._class_mismatch = (
                f"los pesos declaran las clases {list(declared)} y el pipeline espera "
                f"{list(CLASS_NAMES)}, en ese orden")
            logger.error(f"[Inference] Pipeline '{self.pipeline_slot}' no arranca: "
                         f"{self._class_mismatch}. Medir así invertiría los porcentajes.")

    def _run(self, frame_bgr: np.ndarray, camera_slot: str) -> list[Detection]:
        detections = self._run_stage(_SEGMENTER_SLOT, frame_bgr)
        for detection in detections:
            if 0 <= detection.class_index < len(CLASS_NAMES):
                detection.class_name = CLASS_NAMES[detection.class_index]
        detections = self._drop_dark_background(detections, frame_bgr, camera_slot)
        return sorted(detections, key=lambda detection: detection.confidence_pct)

    def _drop_dark_background(self, detections: list, frame_bgr: np.ndarray,
                              camera_slot: str) -> list[Detection]:
        """
        Saca de cada máscara los píxeles más oscuros que el umbral de su clase.

        Con el umbral mal puesto esto se come la clase entera, y tiene que verse así en el
        anotado: es lo único que permite darse cuenta mirando la pantalla en vez de
        deduciéndolo de un número en cero.
        """
        threshold_by_class = self._dark_background_threshold.get(camera_slot) or {}
        if not threshold_by_class or not detections:
            return detections

        luma = (cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                if frame_bgr.ndim == 3 else frame_bgr)
        kept = []
        for detection in detections:
            threshold = int(threshold_by_class.get(detection.class_name, 0) or 0)
            if threshold <= 0 or detection.mask is None:
                kept.append(detection)
                continue
            x1, y1, x2, y2 = detection.bbox_px
            detection.mask = (detection.mask
                              & (luma[y1:y2, x1:x2] >= threshold)).astype(np.uint8)
            detection.area_px = int(detection.mask.sum())
            if detection.area_px > 0:
                kept.append(detection)
        return kept
