"""
Fábrica de pipelines: traduce el campo `type` de una sección `inference.pipelines.<slot>`
en una implementación concreta de AbstractPipeline.

Único archivo del repo que conoce las clases concretas de pipeline. Agregar uno es
escribirlo y sumar una línea a _PIPELINE_CLASSES; ni la app, ni la corrida de imágenes
sueltas, ni el motor que lo corre se tocan.

Sin `type` el slot corre `default`, que es el `Pipeline` de `pipeline.py`: un config de un
solo tipo de pipeline no tiene por qué nombrarlo. Dos slots con el mismo `type` son dos
instancias, con sus propios pesos y en dos hilos: así se reparten cámaras entre hilos.

Maquinaria genérica: reutilizable entre proyectos. Lo que cambia en cada fork son las
implementaciones registradas, no este mecanismo.
"""

from system.config_manager import ConfigManager
from system.logger import logger

from .abstract_pipeline import AbstractPipeline
from .null_pipeline import NullPipeline
from .pipeline import Pipeline

DEFAULT_PIPELINE = "default"

# Registro único: la clave es el valor que lleva `type` en la sección del pipeline.
# NullPipeline no figura acá — no se elige por config, es la salida ante un fallo.
_PIPELINE_CLASSES: dict[str, type[AbstractPipeline]] = {
    DEFAULT_PIPELINE: Pipeline,
}

# Valores válidos de `type`, para mensajes de error y para el combo de la UI.
REGISTERED_PIPELINES = tuple(_PIPELINE_CLASSES)


def create_pipeline(config_manager: ConfigManager, pipeline_slot: str) -> AbstractPipeline:
    """
    Devuelve el pipeline del slot pedido, sin cargar sus modelos.

    Sin `type` devuelve el `default`. Un tipo que no está registrado devuelve un
    NullPipeline y deja el motivo en el log: la app arranca igual, y el pipeline queda sin
    cargar en vez de correr otro proceso en su lugar.
    """
    raw_type = config_manager.get(f"inference.pipelines.{pipeline_slot}.type")
    pipeline_type = str(raw_type or "").strip().lower() or DEFAULT_PIPELINE

    pipeline_class = _PIPELINE_CLASSES.get(pipeline_type)
    if pipeline_class is None:
        valid = ", ".join(_PIPELINE_CLASSES)
        logger.error(
            f"Pipeline '{raw_type}' no registrado en el core. Slot '{pipeline_slot}' sin "
            f"etapas: sus resultados salen como no confiables. Valores válidos: {valid}."
        )
        return NullPipeline(config_manager, pipeline_slot)

    logger.info(f"Fábrica: pipeline '{pipeline_type}' para el slot '{pipeline_slot}'")
    return pipeline_class(config_manager, pipeline_slot)
