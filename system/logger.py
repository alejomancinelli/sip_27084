"""
Logger único de la aplicación.

Módulo puro de infraestructura: no importa nada del repo. Expone `logger` ya
configurado con salida a consola; todos los módulos hacen
`from system.logger import logger` y nadie crea loggers propios ni llama a
basicConfig. El nivel definitivo y los handlers a archivo los fija main.py, que es
quien puede leer config.yaml.
"""

import logging
import sys

_LOGGER_NAME = "app"
_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(message)s"
_DATE_FORMAT = "%H:%M:%S"


def _build_logger() -> logging.Logger:
    app_logger = logging.getLogger(_LOGGER_NAME)
    # Reimportar el módulo no debe duplicar la salida.
    if app_logger.handlers:
        return app_logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)
    return app_logger


logger = _build_logger()
