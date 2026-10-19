"""
Punto de entrada único de las verificaciones: lo llaman los hooks de git, Claude y la persona.

    .venv\\Scripts\\python.exe ci/check.py              # lint + tests: lo que corre el pre-push
    .venv\\Scripts\\python.exe ci/check.py lint         # Ruff + índice de tests al día: el pre-commit
    .venv\\Scripts\\python.exe ci/check.py test         # la suite, en paralelo si está pytest-xdist
    .venv\\Scripts\\python.exe ci/check.py test -- -k lifecycle   # lo que va después de -- es de pytest

Cambiar cómo se corre algo se hace acá, una sola vez, y lo toman los hooks y quien lo
llame a mano. Corre siempre con el intérprete del venv: si se lo invoca con otro, se
relanza con el del proyecto, porque el `python` del PATH no tiene las dependencias.

Sale con 0 si todo pasó y con el código de la primera verificación que falló.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

CI_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CI_DIR.parent

# Windows y Linux ponen el intérprete del venv en lugares distintos.
_VENV_PYTHONS = (
    PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
    PROJECT_ROOT / ".venv" / "bin" / "python",
)


def _venv_python() -> Path | None:
    """El intérprete del venv del proyecto, o None si no hay venv."""
    for candidate in _VENV_PYTHONS:
        if candidate.is_file():
            return candidate
    return None


def _is_running_in(python: Path) -> bool:
    """¿Este proceso corre con ese intérprete? Se compara el prefix: el exe puede ser un shim."""
    return Path(sys.prefix).resolve() == python.parent.parent.resolve()


def _run(command: list[str]) -> int:
    """Corre un comando desde la raíz y devuelve su código de salida, mostrándolo antes."""
    shown = [Path(part).name if part == sys.executable else part for part in command]
    print(f"$ {' '.join(shown)}", flush=True)
    return subprocess.call(command, cwd=PROJECT_ROOT)


def lint() -> int:
    """Ruff sobre todo el árbol, y el índice de tests coincidiendo con la suite."""
    if importlib.util.find_spec("ruff") is None:
        print("Falta ruff: instalar requirements-dev.txt en el venv.", file=sys.stderr)
        return 1
    code = _run([sys.executable, "-m", "ruff", "check", "."])
    if code != 0:
        return code
    # Relativa a la raíz, que es el cwd del comando: se lee mejor en la salida del hook.
    index_script = (CI_DIR / "suite_index.py").relative_to(PROJECT_ROOT).as_posix()
    return _run([sys.executable, index_script, "--check"])


def test(pytest_args: list[str]) -> int:
    """La suite completa, o lo que se le pida a pytest.

    Sin argumentos corre en paralelo con pytest-xdist si está instalado: la suite baja de
    un minuto a unos 15 s. Con argumentos no, porque levantar los workers cuesta más que
    correr un archivo suelto.
    """
    command = [sys.executable, "-m", "pytest", "-q"]
    if pytest_args:
        command += pytest_args
    elif importlib.util.find_spec("xdist") is not None:
        command += ["-n", "auto"]
    return _run(command)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv

    python = _venv_python()
    if python is not None and not _is_running_in(python):
        return subprocess.call([str(python), __file__, *argv], cwd=PROJECT_ROOT)

    parser = argparse.ArgumentParser(description="Lint y tests del proyecto.")
    parser.add_argument(
        "stage",
        nargs="?",
        choices=("all", "lint", "test"),
        default="all",
        help="qué correr; sin nada, lint y después tests",
    )
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="argumentos para pytest")
    args = parser.parse_args(argv)
    pytest_args = [arg for arg in args.pytest_args if arg != "--"]

    if args.stage == "lint":
        return lint()
    if args.stage == "test":
        return test(pytest_args)
    return lint() or test(pytest_args)


if __name__ == "__main__":
    sys.exit(main())
