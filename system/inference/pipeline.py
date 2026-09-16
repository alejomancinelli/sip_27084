"""
Pipeline de inferencia del proyecto: qué modelos corren y en qué orden.

**Específico del proyecto — no cross-portar tal cual.** Es uno de los tres archivos que
se reescriben en cada fork, junto con `metrics.py` y las filas `producer: inference` del
mapa de registros. La maquinaria que lo corre —el contrato de las etapas, el hilo, el
overlay y las agregaciones— no se toca.

Este proyecto tiene una sola etapa: el segmentador sobre el frame de la cinta, que separa
pellet de desmenuzado. Lo que queda sin detectar es cinta a la vista, y eso no lo dice el
modelo sino la cuenta de `metrics.py`.

Encadenar etapas sería escribir `_run()` con más de un `_run_stage()`; el contrato está en
`abstract_pipeline.py`.
"""

import numpy as np

from .abstract_pipeline import AbstractPipeline
from .result import Detection


class BeltPipeline(AbstractPipeline):
    """Una etapa: el segmentador de la cinta sobre el frame recibido."""

    model_slots = ("segmenter",)

    def _run(self, frame_bgr: np.ndarray) -> list[Detection]:
        return self._run_stage("segmenter", frame_bgr)
