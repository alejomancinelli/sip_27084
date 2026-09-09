"""
Carga del archivo `.env` en el entorno del proceso.

Módulo de infraestructura: solo importa el logger y las rutas. Existe porque las
credenciales no van al config.yaml —que se versiona, se copia en la entrega y se ve
desde la pantalla de configuración— sino al entorno, y en un equipo de planta el
entorno se escribe una vez, en un archivo al lado del proyecto.

Quién lo lee: nadie. Los consumidores de un secreto siguen leyendo `os.environ` y no
saben si alguien cargó un archivo antes: `system/telemetry/backends/influxdb_backend.py`,
`mqtt_backend.py` y `tools/camera/rtsp_driver.py` no cambian. Este módulo solo llena el
entorno antes de que arranquen, así que `load_env_file()` va al principio del arranque
—`main.py`— y de cualquier script que necesite un secreto sin pasar por `main.py`.

Dos puntos del contrato que no se ven en la firma:
  - **Una variable que ya existe en el entorno gana.** El archivo llena huecos, no
    pisa: así un servicio, un contenedor o un `$env:VAR` puesto a mano para una prueba
    mandan sobre el archivo sin editarlo.
  - **Que el archivo falte no es un error.** Una instalación sin telemetría y con
    cámaras abiertas no tiene ningún secreto que cargar.

El formato es el mínimo: `CLAVE=valor` por línea, `#` para comentario, líneas vacías
ignoradas. Nada de interpolación ni de valores en varias líneas — si algún día hacen
falta, esto se reemplaza por `python-dotenv` y el llamador no se entera.
"""

import os

from system import paths
from system.logger import logger

_ENV_FILENAME = ".env"

# Prefijo que traen los archivos pensados también para `source` desde una shell.
_EXPORT_PREFIX = "export "


def load_env_file(path: str | None = None) -> tuple[str, ...]:
    """
    Carga el `.env` en `os.environ` y devuelve los nombres que quedaron definidos.

    Sin `path` usa el `.env` de la raíz del repo, que es la única ubicación fija: el
    directorio desde el que se lanzó el proceso no sirve como ancla —un servicio arranca
    en `C:\\Windows\\system32`— y el config puede ser otro archivo (`main.py otro.yaml`)
    sin que las credenciales del equipo cambien.

    Las variables que ya existían en el entorno no se tocan y no salen en la lista.
    """
    env_path = path or os.path.join(paths.PROJECT_ROOT, _ENV_FILENAME)
    if not os.path.isfile(env_path):
        logger.debug(f"Sin archivo de entorno en {env_path}: no hay nada que cargar.")
        return ()

    try:
        lines = _read_lines(env_path)
    except OSError as e:
        logger.error(f"No se pudo leer {env_path}: {e}. El proceso sigue con su entorno.")
        return ()

    loaded = []
    for line_number, line in enumerate(lines, start=1):
        parsed = _parse_line(line)
        if parsed is None:
            continue
        name, value = parsed
        if not name:
            logger.warning(f"{_ENV_FILENAME}:{line_number} sin nombre de variable. Se ignora.")
            continue
        # Lo que ya está en el entorno manda: el archivo llena huecos.
        if name in os.environ:
            continue
        os.environ[name] = value
        loaded.append(name)

    # Los nombres sí, los valores nunca: este log va al archivo y a la consola.
    logger.info(
        f"Entorno: {len(loaded)} variables cargadas de {_ENV_FILENAME}"
        f"{' (' + ', '.join(loaded) + ')' if loaded else ''}."
    )
    return tuple(loaded)


def _read_lines(env_path: str) -> list[str]:
    # utf-8-sig: un archivo guardado desde el Notepad de Windows arranca con BOM, y sin
    # esto la primera variable queda con basura adelante del nombre.
    with open(env_path, "r", encoding="utf-8-sig") as env_file:
        return env_file.readlines()


def _parse_line(line: str) -> tuple[str, str] | None:
    """
    Devuelve el par de una línea, o None si la línea no declara nada.

    El corte es en el PRIMER `=`: un token de InfluxDB termina en `==` y partirlo por
    todos dejaría la mitad del secreto afuera.
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if text.startswith(_EXPORT_PREFIX):
        text = text[len(_EXPORT_PREFIX):].lstrip()
    if "=" not in text:
        return None

    name, _, value = text.partition("=")
    return name.strip(), _strip_quotes(value.strip())


def _strip_quotes(value: str) -> str:
    """Saca las comillas que envuelven el valor; las de adentro quedan como están."""
    for quote in ('"', "'"):
        if len(value) >= 2 and value.startswith(quote) and value.endswith(quote):
            return value[1:-1]
    return value
