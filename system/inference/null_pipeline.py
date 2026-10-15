"""
Pipeline de falla de configuración: lo devuelve la fábrica cuando el `type` de un pipeline
no está registrado.

Maquinaria genérica: reutilizable entre proyectos. Existe por lo mismo que `NullModel`:
caer en el pipeline por defecto correría un proceso que nadie eligió, con números
creíbles, y le escondería el error a la UI y al PLC.
"""

import numpy as np

from .abstract_pipeline import AbstractPipeline
from .result import Detection


class NullPipeline(AbstractPipeline):
    """
    Sin etapas: nunca queda cargado y nunca detecta nada.

    El motor marca cada resultado como no confiable con el motivo `no_model`, y la app
    arranca igual. El motivo lo dejó la fábrica en el log.
    """

    def _run(self, frame_bgr: np.ndarray, camera_slot: str) -> list[Detection]:
        return []
