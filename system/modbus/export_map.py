"""
Exporta el mapa de registros a los formatos que lee una persona.

El mapa se edita en `register_map.yaml` y se lee de ahí; esto genera las copias
para quien no abre el repo:

  - `docs/modbus_map.md`  — tabla para leer y para pegar en una puesta en marcha
  - `docs/modbus_map.csv` — la misma tabla para abrir en Excel y mandarla

Las dos son **generadas**: se regeneran cuando cambia el mapa y no se editan a
mano, así que no hay dos versiones del mapa que puedan discrepar.

    .venv\\Scripts\\python.exe -m system.modbus.export_map
"""

from __future__ import annotations

import csv
import os

from system.modbus.registers import MAP_PATH, SCHEMA
from system.modbus.schema import Reg
from system.paths import PROJECT_ROOT

DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")
MARKDOWN_PATH = os.path.join(DOCS_DIR, "modbus_map.md")
CSV_PATH = os.path.join(DOCS_DIR, "modbus_map.csv")

_COLUMNS = ("Registro", "PLC", "Nombre", "Descripción", "Unidad", "Escala", "Origen")

_GENERATED_NOTE = (
    "Generado por `python -m system.modbus.export_map` desde "
    "`system/modbus/register_map.yaml`. No editar a mano: los cambios se hacen en "
    "el YAML y se vuelve a generar."
)


def _row(reg: Reg) -> tuple[str, ...]:
    """Una fila de la tabla, con todo ya como texto."""
    return (
        str(reg.addr),
        f"4{reg.addr:04d}",              # el número con el que el PLC lo direcciona
        reg.name,
        reg.desc,
        reg.unit or "-",
        f"x{reg.scale}" if reg.scale != 1 else "-",
        reg.producer,
    )


def build_markdown() -> str:
    """Arma el documento en Markdown, ordenado por dirección."""
    lines = [
        "# Mapa de registros Modbus",
        "",
        _GENERATED_NOTE,
        "",
        "Holding registers de sólo lectura (FC03), direccionamiento base-1: el "
        "registro N es el que el PLC lee como 4000N.",
        "",
        "Los valores con escala viajan multiplicados por ella: un registro con "
        "escala x10 y valor 415 son 41,5 en su unidad.",
        "",
        "| " + " | ".join(_COLUMNS) + " |",
        "|" + "|".join("---" for _ in _COLUMNS) + "|",
    ]
    for reg in sorted(SCHEMA.registers, key=lambda r: r.addr):
        lines.append("| " + " | ".join(_row(reg)) + " |")
    lines.append("")
    return "\n".join(lines)


def write_exports() -> list[str]:
    """Escribe las dos copias y devuelve las rutas generadas."""
    os.makedirs(DOCS_DIR, exist_ok=True)

    with open(MARKDOWN_PATH, "w", encoding="utf-8") as f:
        f.write(build_markdown())

    # utf-8-sig: sin BOM, Excel abre las tildes como mojibake.
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(_COLUMNS)
        for reg in sorted(SCHEMA.registers, key=lambda r: r.addr):
            writer.writerow(_row(reg))

    return [MARKDOWN_PATH, CSV_PATH]


if __name__ == "__main__":
    print(f"Mapa: {MAP_PATH} ({len(SCHEMA.registers)} registros)")
    for path in write_exports():
        print(f"  -> {os.path.relpath(path, PROJECT_ROOT)}")
