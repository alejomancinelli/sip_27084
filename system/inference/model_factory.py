"""
Fábrica de modelos de inferencia: traduce el campo `type` de una sección
`inference.models.<slot>` en una implementación concreta de AbstractModel.

Único archivo del repo que conoce las clases concretas. Agregar un modelo es
implementarlo y sumar una línea a _MODEL_CLASSES; ningún otro módulo se toca, ni el
pipeline que lo pide por slot ni el motor que lo corre.

Maquinaria genérica: reutilizable entre proyectos. Lo que cambia en cada fork son las
implementaciones registradas, no este mecanismo.
"""

from system.config_manager import ConfigManager
from system.logger import logger

from .abstract_model import AbstractModel
from .mock_model import MockModel
from .null_model import NullModel

# Registro único: la clave es el valor que lleva `type` en la sección del modelo.
# NullModel no figura acá — no se elige por config, es la salida ante un fallo.
_MODEL_CLASSES: dict[str, type[AbstractModel]] = {
    "mock": MockModel,
}

# Valores válidos de `type`, para mensajes de error y validación de config.
REGISTERED_MODELS = tuple(_MODEL_CLASSES)


def create_model(config_manager: ConfigManager, model_slot: str) -> AbstractModel:
    """
    Devuelve el modelo del slot pedido, sin cargarlo todavía.

    Si la sección falta, no declara `type` o el tipo no está registrado devuelve un
    NullModel con el motivo: la app arranca igual y el error viaja en el status.
    """
    section = config_manager.get(f"inference.models.{model_slot}", {})
    if not isinstance(section, dict) or not section:
        reason = f"Sin sección 'inference.models.{model_slot}' en el config"
        logger.error(f"{reason}. Modelo marcado como no configurado.")
        return NullModel(config_manager, model_slot, reason)

    raw_type = section.get("type")
    model_type = str(raw_type or "").strip().lower()
    if not model_type:
        reason = f"'inference.models.{model_slot}' no declara 'type'"
        logger.error(f"{reason}. Modelo marcado como no configurado.")
        return NullModel(config_manager, model_slot, reason)

    model_class = _MODEL_CLASSES.get(model_type)
    if model_class is None:
        valid = ", ".join(REGISTERED_MODELS)
        reason = f"Modelo '{raw_type}' no registrado en el core"
        logger.error(
            f"{reason}. Slot '{model_slot}' marcado como no configurado. "
            f"Valores válidos: {valid}."
        )
        return NullModel(config_manager, model_slot, reason)

    logger.info(f"Fábrica: modelo '{model_type}' para el slot '{model_slot}'")
    return model_class(config_manager, model_slot)
