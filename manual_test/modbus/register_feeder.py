"""
Maquinaria compartida por las dos pruebas manuales de Modbus.

No es una prueba: es lo que las dos hacen igual. `tcp_server.py` y `rtu_server.py`
sólo eligen su transporte y le pasan el timón a `serve()`.

Lo que hace: levanta el `SharedModbusServer` en un hilo aparte —`run()` se queda
con el hilo que lo llama— y desde el hilo principal escribe los registros una vez
por segundo, imprimiendo lo que escribió.

Los valores son inventados y no pretenden significar nada: el heartbeat cuenta y
el resto se mueve al azar dentro de un rango plausible. Lo que sí es real es el
camino que recorren, y es lo que la prueba ejercita de punta a punta:

    valor físico  ->  formats/*.pack()  ->  SCHEMA.encode_batch()  ->  update_block()

Ningún número se escribe en una dirección literal y ningún bit se corre acá: los
registros se piden por nombre y las palabras las arman sus módulos dueños. Si esta
prueba anda, ese contrato anda.

La palabra de comunicaciones es la más interesante de mirar, porque no es inventada:
sale de los `status` reales del servidor, así que el bit del transporte se prende
solo un par de segundos después del arranque, cuando pasa de `starting` a `active`.
"""

from __future__ import annotations

import random
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# La raíz del repo es el primer ancestro que contiene `system/`, no un número fijo de
# niveles: así el script sobrevive a que lo muevan de carpeta.
_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "system").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene system/.")
sys.path.insert(0, str(_REPO_ROOT))

from system.config_manager import ConfigManager                    # noqa: E402
from system.formats import camera_health, com_status, system_status  # noqa: E402
from system.modbus.registers import SCHEMA                         # noqa: E402
from system.modbus.schema import UINT16_MAX                        # noqa: E402
from system.modbus.server import (                                 # noqa: E402
    STATUS_DISABLED,
    SharedModbusServer,
)

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

TRANSPORT_TCP = "tcp"
TRANSPORT_RTU = "rtu"

_REFRESH_INTERVAL_S = 1.0       # cada cuánto se reescriben los registros
_HEADER_EVERY_ROWS = 20         # cada cuántas filas se repite la cabecera
_SERVER_STOP_TIMEOUT_S = 5.0

_HEADER = (f"{'hora':8s}  {'latido':>6s}  {'coms':>8s}  {'cám1':>9s}  "
           f"{'CPU':>4s}  {'temp':>6s}  {'GPU':>4s}  {'RAM':>6s}  {'pot.':>6s}")


class _Walk:
    """
    Valor que se mueve de a poco dentro de un rango.

    Un `random.uniform()` por ciclo salta de un extremo al otro y no se distingue
    de un registro roto; un paseo se ve avanzar, que es lo que hay que comprobar.
    """

    def __init__(self, value: float, minimum: float, maximum: float, step: float):
        self._value = value
        self._minimum = minimum
        self._maximum = maximum
        self._step = step

    def next(self) -> float:
        self._value += random.uniform(-self._step, self._step)
        self._value = max(self._minimum, min(self._maximum, self._value))
        return self._value


def _build_walks() -> dict[str, _Walk]:
    """Un paseo por registro numérico, con el rango que tendría en un equipo real."""
    return {
        "cpu_usage_pct": _Walk(30, 0, 100, 8),
        "gpu_usage_pct": _Walk(20, 0, 100, 12),
        "cpu_temp_c": _Walk(45, 30, 95, 1.5),
        "gpu_temp_c": _Walk(42, 30, 95, 1.5),
        "ram_used_mb": _Walk(4096, 512, 16384, 128),
        "disk_free_gb": _Walk(220, 1, 500, 2),
        "power_w": _Walk(35, 5, 120, 4),
        "camera_1_temperature_c": _Walk(41, 25, 80, 0.8),
        "camera_1_fps": _Walk(15, 0, 30, 0.5),
        "camera_1_illumination_pct": _Walk(60, 0, 100, 5),
    }


def _build_values(server: SharedModbusServer, walks: dict[str, _Walk],
                  tick: int) -> dict[str, object]:
    """
    Valores físicos por nombre de registro, listos para `SCHEMA.encode_batch()`.

    Las tres palabras de estado las arman sus módulos dueños. La de comunicaciones
    va con los `status` de verdad del servidor, así que es la única que no se
    inventa nada.
    """
    values: dict[str, object] = {name: walk.next() for name, walk in walks.items()}

    # El watchdog: cuenta y da la vuelta. Lo que el PLC mira es que cambie.
    values["heartbeat"] = tick % (UINT16_MAX + 1)

    values["system_status_bitfield"] = system_status.pack(
        model_loaded=False,          # no hay system/inference/ todavía
        inference_error=False,
        fallback_config=False,
        dead_thread=False,
    )
    values["com_status_bitfield"] = com_status.pack(
        modbus_tcp_status=server.tcp_status,
        modbus_rtu_status=server.rtu_status,
    )
    values["camera_1_state_bitfield"] = camera_health.pack(
        connected=True,
        config_error=None,
        fps_estimated=values["camera_1_fps"],
        is_synthetic=True,           # los frames son inventados, y la palabra lo dice
        dirty_lens=bool(tick % 10 == 0),
    )

    # `ram_total_mb` no pasea: es el único que en un equipo real no se mueve.
    values["ram_total_mb"] = 16384
    return values


def _format_row(values: dict[str, object]) -> str:
    """Una línea con lo escrito en el ciclo, alineada con `_HEADER`."""
    return (
        f"{datetime.now():%H:%M:%S}  "
        f"{values['heartbeat']:6d}  "
        f"{values['com_status_bitfield']:#08b}  "
        f"{values['camera_1_state_bitfield']:#09b}  "
        f"{values['cpu_usage_pct']:3.0f}%  {values['cpu_temp_c']:5.1f}°  "
        f"{values['gpu_usage_pct']:3.0f}%  {values['ram_used_mb']:5.0f}M  "
        f"{values['power_w']:5.1f}W"
    )


def _print_register_table():
    """El mapa que el cliente va a leer, para no tener que abrir el YAML al lado."""
    print("Registros que se escriben:")
    for reg in sorted(SCHEMA.registers, key=lambda r: r.addr):
        scale = f" x{reg.scale}" if reg.scale != 1 else ""
        unit = f" {reg.unit}" if reg.unit else ""
        print(f"  {reg.addr:3d}  4{reg.addr:04d}  {reg.name:26s}{unit}{scale}")


def _load_config(transport: str) -> ConfigManager | None:
    """
    Carga el config de al lado y apaga en memoria el transporte que no se prueba.

    `run()` levanta los dos transportes, así que sin esto la prueba de TCP abriría
    también el puerto serie y al revés. Se apaga en memoria: el archivo no se toca,
    y es lo que permite que las dos pruebas compartan un config.
    """
    if not CONFIG_PATH.exists():
        print(f"Falta {CONFIG_PATH}.")
        return None

    config = ConfigManager(str(CONFIG_PATH))
    if not config.get(f"modbus.{transport}.enabled", False):
        print(f"`modbus.{transport}.enabled` está en false en {CONFIG_PATH.name}: "
              f"no hay nada que probar.")
        return None

    other = TRANSPORT_RTU if transport == TRANSPORT_TCP else TRANSPORT_TCP
    config.set(f"modbus.{other}.enabled", False)
    return config


def _describe_endpoint(config: ConfigManager, transport: str) -> str:
    """Dónde tiene que apuntar el cliente."""
    slave_id = config.get("modbus.slave_id", 1)
    if transport == TRANSPORT_TCP:
        host = config.get("modbus.tcp.host", "0.0.0.0")
        port = config.get("modbus.tcp.port", 502)
        return f"tcp {host}:{port}, unit id {slave_id}"
    return (f"rtu {config.get('modbus.rtu.port')} @ "
            f"{config.get('modbus.rtu.baudrate')} bps "
            f"{config.get('modbus.rtu.parity')}-8-{config.get('modbus.rtu.stop_bits')}, "
            f"unit id {slave_id}")


def _status_of(server: SharedModbusServer, transport: str) -> str:
    return server.tcp_status if transport == TRANSPORT_TCP else server.rtu_status


def serve(transport: str) -> int:
    """
    Levanta el servidor por `transport` y alimenta los registros hasta Ctrl+C.

    Devuelve 2 si no hay con qué correr la prueba (falta el config, o el transporte
    está apagado) y 1 si el transporte no llegó a levantar.
    """
    config = _load_config(transport)
    if config is None:
        return 2

    server = SharedModbusServer(config)
    print(f"Config: {CONFIG_PATH}")
    print(f"Escuchando: {_describe_endpoint(config, transport)}")
    print(f"Mapa: {len(SCHEMA.registers)} registros, "
          f"datastore de {server.register_count}\n")
    _print_register_table()

    worker = threading.Thread(target=server.run, name=f"modbus-{transport}",
                              daemon=True)
    worker.start()

    print(f"\nUn ciclo cada {_REFRESH_INTERVAL_S:.0f} s. Ctrl+C para terminar.\n")

    walks = _build_walks()
    tick = 0
    rows = 0
    try:
        # Mientras el hilo viva hay algo sirviendo. Si el transporte no levantó,
        # `run()` vuelve solo y seguir escribiendo registros no le llega a nadie:
        # la prueba corta y lo dice, en vez de girar en vacío pareciendo que anda.
        while worker.is_alive():
            values = _build_values(server, walks, tick)
            server.update_block(SCHEMA.encode_batch(values))
            if rows % _HEADER_EVERY_ROWS == 0:
                print(_HEADER)
            print(_format_row(values))
            tick += 1
            rows += 1
            time.sleep(_REFRESH_INTERVAL_S)
        print(f"\nEl servidor cerró solo: {transport} no quedó sirviendo. "
              f"El motivo está en el log de arriba.")
    except KeyboardInterrupt:
        print()

    status = _status_of(server, transport)
    print(f"Cerrando ({transport} quedó en '{status}')...")
    server.stop()
    worker.join(timeout=_SERVER_STOP_TIMEOUT_S)
    if worker.is_alive():
        print("El hilo del servidor no cerró en "
              f"{_SERVER_STOP_TIMEOUT_S:.0f} s: `stop()` no lo desarmó.")
        return 1
    print(f"Cerrado. {tick} ciclos escritos.")

    # Un transporte que nunca levantó ya se explicó por el log; acá sólo se refleja
    # en el código de salida, para que la prueba no parezca exitosa si no sirvió nada.
    return 0 if status not in (STATUS_DISABLED, "error") else 1
