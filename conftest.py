"""
Deja la raíz del repo en sys.path para que los tests importen `system` y `tools`.

pytest carga este archivo antes que cualquier test, así que los módulos de prueba
no necesitan calcular la ruta ellos mismos: sin esto, cada uno tendría que contar
cuántos niveles lo separan de la raíz, y ese número cambia al mover un archivo de
carpeta.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
