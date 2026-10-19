"""
Genera `docs/tests.md`: el índice de la suite, para ubicar un test sin abrir los archivos.

Lee `test/` con `ast` y no importa nada, así que no levanta Qt ni toca el hardware y
tarda menos de un segundo. Cuenta funciones de test, no casos: un test parametrizado
cuenta una vez, y por eso el total queda por debajo del que informa pytest.

El índice es **generado**, como el mapa Modbus: no se edita a mano. No lleva números de
línea a propósito, para que cambie sólo cuando se agrega, se renombra o se borra un test
y no cada vez que se toca uno.

    .venv\\Scripts\\python.exe ci/suite_index.py            # regenera
    .venv\\Scripts\\python.exe ci/suite_index.py --check    # sólo verifica que esté al día
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = PROJECT_ROOT / "test"
INDEX_PATH = PROJECT_ROOT / "docs" / "tests.md"

# Los tests sueltos, fuera de una clase, se agrupan bajo este nombre.
_MODULE_LEVEL = "(sin clase)"

_GENERATED_NOTE = (
    "Generado por `python ci/suite_index.py` desde `test/`. No editar a mano: se "
    "regenera, y el hook de commit avisa si quedó desactualizado."
)


@dataclass
class IndexedGroup:
    """Una clase `Test*` del archivo, o los tests sueltos del módulo."""

    name: str
    summary: str
    tests: list[str] = field(default_factory=list)


@dataclass
class IndexedFile:
    """Un archivo de test: su ruta relativa a la raíz, su resumen y sus grupos."""

    path: str
    summary: str
    groups: list[IndexedGroup]

    @property
    def test_count(self) -> int:
        return sum(len(group.tests) for group in self.groups)


def _first_paragraph(docstring: str | None) -> str:
    """El primer párrafo de un docstring, en una sola línea."""
    if not docstring:
        return ""
    paragraph = docstring.strip().split("\n\n", 1)[0]
    return " ".join(line.strip() for line in paragraph.splitlines())


def _is_test_function(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
        "test"
    )


def parse_test_file(path: Path) -> IndexedFile:
    """Lee un archivo de test y devuelve sus clases y tests, en el orden del archivo."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_group = IndexedGroup(_MODULE_LEVEL, "")
    groups = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            tests = [child.name for child in node.body if _is_test_function(child)]
            if tests:
                groups.append(
                    IndexedGroup(node.name, _first_paragraph(ast.get_docstring(node)), tests)
                )
        elif _is_test_function(node):
            module_group.tests.append(node.name)
    if module_group.tests:
        groups.insert(0, module_group)
    return IndexedFile(
        path=path.relative_to(PROJECT_ROOT).as_posix(),
        summary=_first_paragraph(ast.get_docstring(tree)),
        groups=groups,
    )


def collect() -> list[IndexedFile]:
    """Todos los `test_*.py` de la suite, ordenados por ruta."""
    return [parse_test_file(path) for path in sorted(TEST_DIR.rglob("test_*.py"))]


def _folder_of(test_file: IndexedFile) -> str:
    """La carpeta del archivo dentro de `test/`; los de la raíz quedan en `test/`."""
    return test_file.path.rsplit("/", 1)[0] + "/"


def _escape_cell(text: str) -> str:
    """Texto apto para una celda de tabla Markdown."""
    return text.replace("|", "\\|")


def build_markdown(test_files: list[IndexedFile]) -> str:
    """Arma el índice completo en Markdown."""
    total_tests = sum(test_file.test_count for test_file in test_files)
    total_groups = sum(len(test_file.groups) for test_file in test_files)

    # Por carpeta y no por ruta: así `test/` va primero y no entre `telemetry/` y `tools/`.
    folders: dict[str, list[IndexedFile]] = {}
    for test_file in test_files:
        folders.setdefault(_folder_of(test_file), []).append(test_file)
    folders = dict(sorted(folders.items()))

    lines = [
        "# Índice de tests",
        "",
        _GENERATED_NOTE,
        "",
        f"**{len(test_files)} archivos, {total_groups} grupos, {total_tests} funciones de "
        "test.** pytest informa más casos porque cuenta cada parametrización por separado.",
        "",
        "Cómo se corre (desde la raíz, con el intérprete del venv):",
        "",
        "    .venv\\Scripts\\python.exe ci/check.py test                                  # toda la suite",
        "    .venv\\Scripts\\python.exe ci/check.py test -- test/video/test_http_server.py  # un archivo",
        '    .venv\\Scripts\\python.exe ci/check.py test -- -k "TestLifecycle"              # por nombre',
        "",
        "## Por carpeta",
        "",
        "| Carpeta | Archivos | Tests |",
        "|---|---:|---:|",
    ]
    for folder, folder_files in folders.items():
        anchor = folder.rstrip("/").replace("/", "").lower()
        count = sum(test_file.test_count for test_file in folder_files)
        lines.append(f"| [{folder}](#{anchor}) | {len(folder_files)} | {count} |")
    lines.append("")

    for folder, folder_files in folders.items():
        lines += [f"## {folder}", ""]
        for test_file in folder_files:
            lines += _file_section(test_file)
    return "\n".join(lines).rstrip() + "\n"


def _file_section(test_file: IndexedFile) -> list[str]:
    """La sección de un archivo: resumen, tabla de grupos y la lista plegada de tests."""
    name = test_file.path.rsplit("/", 1)[-1]
    lines = [
        f"### [{name}](../{test_file.path}) — {test_file.test_count} tests",
        "",
    ]
    if test_file.summary:
        lines += [test_file.summary, ""]
    # La columna del resumen sólo aparece si alguna clase del archivo tiene docstring:
    # la mayoría no lo tiene, y una columna vacía no dice nada.
    if any(group.summary for group in test_file.groups):
        lines += ["| Grupo | Qué prueba | Tests |", "|---|---|---:|"]
        for group in test_file.groups:
            lines.append(
                f"| `{group.name}` | {_escape_cell(group.summary)} | {len(group.tests)} |"
            )
    else:
        lines += ["| Grupo | Tests |", "|---|---:|"]
        lines += [f"| `{group.name}` | {len(group.tests)} |" for group in test_file.groups]
    lines += ["", "<details><summary>Tests</summary>", ""]
    for group in test_file.groups:
        lines.append(f"- `{group.name}`")
        lines += [f"  - `{test}`" for test in group.tests]
    lines += ["", "</details>", ""]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera docs/tests.md desde test/.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="no escribe: sale con 1 si el índice no coincide con la suite",
    )
    args = parser.parse_args(argv)

    content = build_markdown(collect())
    current = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else None
    if args.check:
        if content != current:
            print(
                f"{INDEX_PATH.relative_to(PROJECT_ROOT).as_posix()} está desactualizado: "
                "regenerarlo con `python ci/suite_index.py` y sumarlo al commit.",
                file=sys.stderr,
            )
            return 1
        return 0

    INDEX_PATH.write_text(content, encoding="utf-8", newline="\n")
    print(f"{INDEX_PATH.relative_to(PROJECT_ROOT).as_posix()}: {len(content.splitlines())} líneas")
    return 0


if __name__ == "__main__":
    sys.exit(main())
