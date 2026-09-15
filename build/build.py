"""
Compila el ejecutable con Nuitka, con las opciones de este proyecto.

    .venv\\Scripts\\python.exe build\\build.py                    compila
    .venv\\Scripts\\python.exe build\\build.py --dry-run          sólo muestra el comando
    .venv\\Scripts\\python.exe build\\build.py -- --lto=yes       agrega flags al final

**Por qué es un script y no un comando en el README.** Dos motivos, y el primero es que
una de las opciones no es una constante: el `.exe` lleva la versión en sus propiedades, y
ese número tiene un solo dueño, `system/version.py`. Escrito a mano en un comando que se
copia y se pega sería la cuarta copia —las otras tres la importan— y la única que nadie
puede notar desactualizada. El docstring de `version.py` ya explica por qué eso importa: el
número que alguien lee para reportar un problema tiene que venir del programa que corre, y
las propiedades del archivo son justo donde lo va a mirar la gente de sistemas del cliente.

El segundo es el directorio de salida. **Nuitka no pisa los `.c` que dejó una corrida
anterior**: aborta con un `AssertionError` que no explica nada, o con un error del backend
de Scons. Acá es una precondición y no una nota al pie.

Lo que **no** hace es armar el entregable: eso es `make_release.py`, y son dos etapas
porque se reempaqueta mucho más seguido de lo que se recompila —cambiar el `config.yaml` de
la planta no necesita cuarenta minutos de Nuitka.

**Este archivo es maquinaria; los valores del bloque de más abajo son del fork.** Se
cross-portea entero y sólo se editan las seis constantes marcadas: identidad del programa,
icono y qué se excluye del build. Un fork que agrega un framework de inferencia pesado
—TensorFlow, PyTorch— y no lo usa en el entregable lo suma a `_EXCLUDED`; uno que no
excluye nada deja la tupla vacía y Nuitka empaqueta todo lo que `main.py` importa.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)

from system.version import APP_VERSION  # noqa: E402

_DEFAULT_OUT = "build/out"
_ENTRY_POINT = "main.py"

# ── Lo que cambia en cada fork ────────────────────────────────────────────────
#
# Identidad del programa en las propiedades del `.exe`. Va acá y no de `config.yaml`
# —que tiene `system.app_name`, el título de la ventana— porque el ejecutable es el
# programa y no la instalación: leerlo del config haría que el mismo binario dijera
# llamarse distinto en cada planta. Y de paso, un `.exe` sin firmar con las propiedades
# vacías es una apuesta peor ante el módulo de reputación de un antivirus que uno sin
# firmar que al menos dice qué es.
_COMPANY = "IEA"
_PRODUCT = "APP_NAME"
_DESCRIPTION = "APP_DESCRIPTION"
_ICON = "ui/icons/iea_100x100.ico"

# Paquetes que no entran al ejecutable, y por qué cada uno. Vacía por defecto: Nuitka
# empaqueta lo que `main.py` importa, así que sólo hace falta excluir lo que el proyecto
# sabe que no necesita en el entregable —un framework de inferencia que el modelo elegido
# no usa, un SDK de cámara que no corresponde a esta instalación—. Ejemplo de un fork que
# corre su modelo en ONNX Runtime y no en TensorFlow:
#
#     _EXCLUDED = (
#         "tensorflow",   # el modelo corre en ONNX Runtime; el config pide el tipo _onnx
#         "keras",
#         "tf2onnx",
#     )
_EXCLUDED: tuple = ()

# Datos que viajan con el programa: los lee `paths.app_file()`, no `DATA_DIR`.
_DATA_DIRS = ("ui/styles", "ui/icons")


def _flags(out_dir: str) -> list:
    """Las opciones de Nuitka, en el orden en que se leen."""
    flags = [
        "--standalone",
        "--enable-plugin=pyside6",
        # Sin esto Nuitka intercepta el import de un módulo excluido y levanta su propio
        # error, en vez de dejar que la importación normal encuentre la copia suelta si
        # el fork la copia a mano en `make_release.py` (ver `_LOOSE_PACKAGES` ahí).
        "--no-deployment-flag=excluded-module-usage",
        # El subsistema del `.exe` es un bit del encabezado PE y se fija al enlazar, así
        # que un solo binario no puede ser con ventana y con consola a la vez. `attach` es
        # el que sirve a los dos usos: la app no arrastra una consola detrás, y una
        # herramienta de línea de comandos que comparta el mismo ejecutable —un
        # subcomando de calibración, por ejemplo— se engancha a la del `cmd` que la lanzó.
        "--windows-console-mode=attach",
        f"--windows-icon-from-ico={_ICON}",
        f"--company-name={_COMPANY}",
        f"--product-name={_PRODUCT}",
        f"--file-description={_DESCRIPTION}",
        f"--file-version={APP_VERSION}",
        f"--product-version={APP_VERSION}",
        "--assume-yes-for-downloads",
        f"--output-dir={out_dir}",
    ]
    flags += [f"--nofollow-import-to={package}" for package in _EXCLUDED]
    flags += [f"--include-data-dir={directory}={directory}" for directory in _DATA_DIRS]
    return flags


def _require_empty(out_dir: str, force: bool):
    """
    El directorio de salida arranca vacío, o no se arranca.

    Reusar uno de una corrida interrumpida no falla en el momento: falla más tarde y con
    un error que no menciona el motivo. Con `--force` se borra, que es lo que uno quiere
    al reintentar; sin él se avisa y se corta, que es lo que uno quiere cuando el
    directorio de al lado tiene el build que hoy anda en la planta.
    """
    if not os.path.isdir(out_dir) or not os.listdir(out_dir):
        return
    if not force:
        raise SystemExit(
            f"{out_dir} no está vacío. Nuitka no pisa los .c de una corrida anterior y "
            f"aborta con un error que no lo dice. Borrarlo, usar --out con otro nombre, "
            f"o --force para que lo borre este script.")
    print(f"  se borra {out_dir} (--force)")
    shutil.rmtree(out_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compila el ejecutable con Nuitka.")
    parser.add_argument("--out", default=_DEFAULT_OUT, help="directorio de salida")
    parser.add_argument("--force", action="store_true",
                        help="borrar el directorio de salida si tiene algo")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="mostrar el comando y no compilar")
    parser.add_argument("extra", nargs=argparse.REMAINDER,
                        help="lo que siga después de -- se agrega al comando")
    args = parser.parse_args()

    out_dir = args.out if os.path.isabs(args.out) else os.path.join(_REPO_ROOT, args.out)
    extra = [flag for flag in args.extra if flag != "--"]
    command = [sys.executable, "-m", "nuitka", *_flags(args.out), *extra, _ENTRY_POINT]

    print(f"  {_PRODUCT} {APP_VERSION}")
    print(f"  {_shown(command)}\n")
    if args.dry_run:
        return 0

    _require_empty(out_dir, args.force)
    started = time.monotonic()
    # `cwd` en la raíz: las rutas de los datos y del icono son relativas a ella.
    done = subprocess.run(command, cwd=_REPO_ROOT)
    elapsed = time.monotonic() - started

    dist = os.path.join(out_dir, "main.dist")
    if done.returncode != 0:
        print(f"\n  FALLÓ en {elapsed / 60:.1f} min (código {done.returncode}).")
        print(f"  Si dice «Failed to add resources to file», es el antivirus tomando el "
              f".exe mientras Nuitka lo escribe: hace falta una exclusión para {out_dir}.")
        return done.returncode

    print(f"\n  {_size_mb(dist):.0f} MB en {_count(dist)} archivos, {elapsed / 60:.1f} min")
    print(f"\n  Ahora el entregable:")
    print(f"    .venv\\Scripts\\python.exe build\\make_release.py --dist {args.out}/main.dist")
    return 0


def _shown(command: list) -> str:
    """
    El comando como habría que escribirlo a mano.

    Se entrecomilla lo que tiene espacios: el proceso se lanza con una lista y no por
    shell, así que las comillas no hacen falta para correrlo —pero esta línea está para
    leerla y para pegarla en una consola, y ahí sí.
    """
    return " ".join(f'"{part}"' if " " in part else part for part in command)


def _size_mb(path: str) -> float:
    return sum(os.path.getsize(os.path.join(root, name))
               for root, _, names in os.walk(path) for name in names) / 1e6


def _count(path: str) -> int:
    return sum(len(names) for _, _, names in os.walk(path))


if __name__ == "__main__":
    sys.exit(main())
