"""
Rutas del proyecto.

Módulo puro de infraestructura: no importa nada del repo. Traduce las rutas del
config.yaml —que se escriben relativas a la raíz del repo— a rutas absolutas, para
que ninguna ruta dependa del directorio desde donde se lanzó el proceso.
"""

import os

# Dueño único del valor: cualquier módulo que necesite anclar una ruta lo importa
# de acá en vez de volver a subir dos niveles desde su propio __file__.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve(path: str | None, default: str) -> str:
    """
    Ruta absoluta de una ruta de config; las relativas se anclan a la raíz del repo.

    Un valor vacío o None cae en `default`, que se resuelve igual.
    """
    candidate = str(path).strip() if path else ""
    if not candidate:
        candidate = default
    if not os.path.isabs(candidate):
        candidate = os.path.join(PROJECT_ROOT, candidate)
    return os.path.normpath(candidate)
