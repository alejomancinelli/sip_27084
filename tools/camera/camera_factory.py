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
    _raw_driver = config_cam.get("driver")
    _driver_type = str(_raw_driver or "").strip().lower()
    _address = config_cam.get("address")

    if not _driver_type:
        _reason = "Config de cámara sin campo 'driver'"
        logger.error(f"{_reason}. Cámara marcada como no configurada.")
        return NullDriver(config_cam, _reason)

    _driver_class = _DRIVER_CLASSES.get(_driver_type)
    if _driver_class is None:
        _reason = f"Driver '{_raw_driver}' no registrado en el core"
        logger.error(
            f"{_reason}. Cámara marcada como no configurada. "
            f"Valores válidos: {', '.join(REGISTERED_DRIVERS)}."
        )
        return NullDriver(config_cam, _reason)

    logger.info(f"Fábrica: driver '{_driver_type}' para la cámara {_address or '(sin dirección)'}")
    return _driver_class(config_cam)
