"""
Pipeline de inferencia del proyecto: qué modelos corren y en qué orden.

**Específico del proyecto — no cross-portar tal cual.** Es uno de los tres archivos que
se reescriben en cada fork, junto con `metrics.py` y las filas `producer: inference` del
mapa de registros. La maquinaria que lo corre —el contrato de las etapas, el hilo, el
overlay y las agregaciones— no se toca.

El template trae una sola etapa: el modelo `model_1` sobre el frame que le llega, que es
el caso de la enorme mayoría de las instalaciones. `main.py` lo instancia y se lo pasa al
motor:

    pipeline = Pipeline(config, "pipeline_1")
    engine = InferenceThread(config, pipeline, analyzer=compute_metrics)

Encadenar etapas es escribir `_run()`. Dos formas, y las dos salen del mismo contrato
—cada etapa recibe las detecciones de la anterior y decide qué hacer con ellas—:

    # Cascada: la segunda etapa recorta por lo que encontró la primera.
    model_slots = ("detector", "classifier")

    def _run(self, frame_bgr):
        detections = self._run_stage("detector", frame_bgr)
        return self._run_stage("classifier", frame_bgr, detections)

    # Independientes: la segunda ignora lo que le llega y mira el frame completo.
    model_slots = ("detector_a", "detector_b")

    def _run(self, frame_bgr):
        return self._run_stage("detector_a", frame_bgr) + \
               self._run_stage("detector_b", frame_bgr)

Cada slot que aparezca en `model_slots` necesita su sección en `inference.models` del
config.yaml; sin ella la fábrica devuelve un NullModel y esa etapa no detecta nada.
"""

import numpy as np

from .abstract_pipeline import AbstractPipeline
from .result import Detection


class Pipeline(AbstractPipeline):
    """
    Pipeline de una sola etapa: el modelo `model_1` sobre el frame recibido.

    En el fork se renombra a lo que hace —`GrainPipeline`, `SurfacePipeline`— y se le
    agregan las etapas que necesite.
    """

    model_slots = ("model_1",)

    def _run(self, frame_bgr: np.ndarray) -> list[Detection]:
        return self._run_stage("model_1", frame_bgr)
