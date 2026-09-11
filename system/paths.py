"""
Rutas del proyecto.

Módulo puro de infraestructura: no importa nada del repo. Traduce las rutas del
config.yaml —que se escriben relativas a la raíz de la instalación— a rutas absolutas,
para que ninguna ruta dependa del directorio desde donde se lanzó el proceso.

**Hay dos raíces y no son la misma.** Corriendo del repo coinciden, y por eso durante
mucho tiempo alcanzó con una sola; compiladas dejan de coincidir y confundirlas rompe cosas
que no fallan de entrada:

  - `APP_DIR` — dónde está el **programa**. De ahí salen los QSS y los iconos, que son
    parte del ejecutable y se reemplazan enteros en cada actualización.
  - `DATA_DIR` — dónde está la **instalación**. De ahí salen `config.yaml`,
    `register_map.yaml`, los pesos del modelo y `data/`. Es lo que cambia entre plantas y
    lo que **la app escribe**: el panel de configuración reescribe el config y deja su
    `.bak` al lado.

Compilado, el programa vive en una carpeta de sólo lectura que se pisa al actualizar. Si
los datos de la instalación viven ahí adentro, actualizar borra la calibración de la
planta. Por eso `SIP_DATA_DIR` puede apuntarlos a otro lado, y por eso el default es «al
lado del ejecutable»: funciona sin configurar nada, y separarlos es una decisión de quien
instala, no un requisito para arrancar.
"""

import os
import sys

# Nuitka define `__compiled__` en cada módulo que compila; CPython no.
_IS_COMPILED = "__compiled__" in globals()

# Variable de entorno con la que se separan los datos del programa.
DATA_DIR_ENV = "SIP_DATA_DIR"


def _app_dir() -> str:
    """Dónde está el programa: la carpeta del ejecutable, o la raíz del repo."""
    if _IS_COMPILED:
        # `sys.executable` es el propio .exe en un standalone de Nuitka. `argv[0]` es el
        # respaldo para la compilación acelerada, donde el ejecutable no es el intérprete.
        launcher = sys.executable if sys.executable else sys.argv[0]
        return os.path.dirname(os.path.abspath(launcher))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _data_dir(app_dir: str) -> str:
    """Dónde está la instalación: lo que diga el entorno, o al lado del programa."""
    declared = (os.environ.get(DATA_DIR_ENV) or "").strip()
    return os.path.abspath(declared) if declared else app_dir


APP_DIR = _app_dir()
DATA_DIR = _data_dir(APP_DIR)

# Se mantiene el nombre viejo porque corriendo del repo son lo mismo y hay código —y
# scripts de `calibration/`— que lo importan. Lo nuevo pide `DATA_DIR` o `APP_DIR`.
PROJECT_ROOT = DATA_DIR


def resolve(path: str | None, default: str) -> str:
    """
    Ruta absoluta de una ruta de config; las relativas se anclan a `DATA_DIR`.

    Un valor vacío o None cae en `default`, que se resuelve igual.
    """
    candidate = str(path).strip() if path else ""
    if not candidate:
        candidate = default
    if not os.path.isabs(candidate):
        candidate = os.path.join(DATA_DIR, candidate)
    return os.path.normpath(candidate)


def app_file(*parts: str) -> str:
    """
    Ruta de un archivo que viaja **con el programa**: un QSS, un icono.

    No pasa por `DATA_DIR` a propósito. Son parte del ejecutable y no algo que la
    instalación edite; buscarlos donde vive el config los haría faltar en cuanto alguien
    separe los datos del programa.
    """
    return os.path.join(APP_DIR, *parts)
