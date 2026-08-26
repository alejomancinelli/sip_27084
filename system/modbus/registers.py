"""
Mapa de registros Modbus cargado — punto de entrada del paquete al mapa concreto.

Lee `register_map.yaml` una sola vez, al importar, y expone el `RegisterSchema` ya
validado. Que el mapa esté roto se descubre acá, al arrancar la app, y no la
primera vez que el PLC pide un registro.

Quien necesite una dirección la pide por nombre —`SCHEMA.addr("cpu_usage_pct")`—
y quien necesite escribir un valor lo entrega en su unidad física a
`SCHEMA.encode()` o `SCHEMA.encode_batch()`. El mapa concreto vive en el YAML: no
hay direcciones en este archivo.
"""

from __future__ import annotations

from system.modbus.schema import Reg, RegisterSchema, default_map_path, load_registers

MAP_PATH = default_map_path()

REGISTERS: list[Reg] = load_registers(MAP_PATH)

SCHEMA = RegisterSchema(REGISTERS)
