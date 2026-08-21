"""
Fábrica de drivers de cámara: traduce el campo `driver` de config.yaml en una
implementación concreta de AbstractCameraDriver.

Único archivo del repo que conoce las clases concretas. Agregar un driver es
implementarlo y sumar una línea a _DRIVER_CLASSES; ningún otro módulo se toca.
Maquinaria genérica: reutilizable entre proyectos.
"""

from system.logger import logger

from .abstract_driver import AbstractCameraDriver
from .basler_driver import BaslerDriver
from .mock_driver import MockDriver
from .null_driver import NullDriver
from .st_driver import StDriver

# Registro único: la clave es el valor que lleva `driver` en config.yaml.
# NullDriver no figura acá — no se elige por config, es la salida ante un fallo.
_DRIVER_CLASSES: dict[str, type[AbstractCameraDriver]] = {
    "basler_gige": BaslerDriver,
    "st_gige": StDriver,
    "mock": MockDriver,
}

# Valores válidos de `driver`, para mensajes de error y validación de config.
REGISTERED_DRIVERS = tuple(_DRIVER_CLASSES)


def create_camera(config_cam: dict) -> AbstractCameraDriver:
    """
    Devuelve el driver que corresponde al sub-diccionario de una cámara.

    Si `driver` falta o no está registrado devuelve un NullDriver
    """
    raw_driver = config_cam.get("driver")
    driver_type = str(raw_driver or "").strip().lower()
    address = config_cam.get("address")

    if not driver_type:
        reason = "Config de cámara sin campo 'driver'"
        logger.error(f"{reason}. Cámara marcada como no configurada.")
        return NullDriver(config_cam, reason)

    driver_class = _DRIVER_CLASSES.get(driver_type)
    if driver_class is None:
        reason = f"Driver '{raw_driver}' no registrado en el core"
        logger.error(
            f"{reason}. Cámara marcada como no configurada. "
            f"Valores válidos: {', '.join(REGISTERED_DRIVERS)}."
        )
        return NullDriver(config_cam, reason)

    logger.info(f"Fábrica: driver '{driver_type}' para la cámara {address or '(sin dirección)'}")
    return driver_class(config_cam)
