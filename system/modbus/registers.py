"""
Mapa de registros Modbus cargado — punto de entrada del paquete al mapa concreto.

Lee `register_map.yaml` una sola vez, al importar, y expone el `RegisterSchema` ya
validado. Que el mapa esté roto se descubre acá, al arrancar la app, y no la
primera vez que el PLC pide un registro.

Quien necesite una dirección la pide por nombre —`SCHEMA.addr("cpu_usage_pct")`—
y quien necesite escribir un valor lo entrega en su unidad física a
`SCHEMA.encode()` o `SCHEMA.encode_batch()`. El mapa concreto vive en el YAML: no
hay direcciones en este archivo.

**Un mapa que falta o que está roto no mata el import.** Queda el motivo en
`LOAD_ERROR` y un mapa vacío, así que la app arranca y lo único que se cae es la
publicación al PLC: sigue mirando, midiendo, guardando el dataset y publicando a
la telemetría. Levantar acá sería matar el proceso **durante un import**, antes de
que exista un logger con archivo y antes de que haya una ventana — y compilado,
lanzado sin consola, eso es un programa que no arranca y no deja nada en pantalla.
Que el mapa esté mal se sigue descubriendo al arrancar y no en la primera consulta
del PLC; lo que cambia es que se cuenta en vez de abortar.
"""

from __future__ import annotations

from system.modbus.schema import Reg, RegisterSchema, default_map_path, load_registers

MAP_PATH = default_map_path()

# El motivo, o vacío si el mapa cargó. Lo lee el servidor Modbus para dejarlo en su
# `status`, que es el mismo vocabulario que usa cuando no puede tomar el puerto.
LOAD_ERROR = ""

try:
    REGISTERS: list[Reg] = load_registers(MAP_PATH)
except (OSError, ValueError) as error:
    REGISTERS = []
    LOAD_ERROR = f"No se pudo cargar {MAP_PATH}: {error}"

SCHEMA = RegisterSchema(REGISTERS)
