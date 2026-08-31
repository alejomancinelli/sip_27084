"""
Modelo de falla de configuración: lo devuelve la fábrica cuando la sección del modelo
no sirve.

Maquinaria genérica: reutilizable entre proyectos. Existe para que una config mal
escrita no caiga en el mock: detecciones sintéticas con estado 'cargado' le esconderían
el error a la UI y al PLC.
"""

import numpy as np

from system.config_manager import ConfigManager

from .abstract_model import AbstractModel
from ..result import Detection


class NullModel(AbstractModel):
    """
    Nunca carga y nunca detecta nada. Su `error` dice por qué.

    El pipeline que lo tiene queda sin cargar, y el motor marca cada resultado como no
    confiable con el motivo `no_model`.
    """

    # Marca de fallo permanente: reintentar load() no cambia nada.
    is_config_error = True

    def __init__(self, config_manager: ConfigManager, model_slot: str,
                 reason: str = "Modelo sin configurar"):
        super().__init__(config_manager, model_slot)
        self._set_error(reason)

    def load(self):
        """No hay nada que cargar: deja el estado de error como estaba."""

    def predict(self, frame_bgr: np.ndarray,
                previous: list[Detection] | None = None) -> list[Detection]:
        return []

    def unload(self):
        """El estado de error sobrevive a la descarga: sigue siendo el motivo real."""
