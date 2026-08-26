"""
Servidor Modbus esclavo: TCP y RTU sobre un mismo datastore en RAM.

Expone los holding registers del mapa como **sólo lectura**: el PLC lee (FC03) y
la app escribe desde adentro con `update_register` / `update_block`. Cualquier
otra función —FC01, FC02, FC04 y todas las de escritura— se rechaza con la
excepción 0x02 en vez de contestar un valor que parezca una medición.

Los dos transportes comparten el mismo contexto, así que el PLC ve los mismos
valores por el cable que sea, y son independientes: se pueden habilitar los dos,
uno o ninguno desde `modbus` en el config.yaml. Cada uno reporta su propio
`status` con el vocabulario `STATUS_*` de este módulo, que es lo que la palabra de
comunicaciones espera leer.

`run()` toma el hilo que lo llama y arma ahí su propio loop de asyncio; `stop()`
lo desarma desde afuera. La forma de uso es un QThread cuyo `run()` llame a este.

El mapa de registros no se conoce acá: este módulo sirve un rango de direcciones,
y qué significa cada una es de `system/modbus/registers.py`.

    server = SharedModbusServer(ConfigManager())
    server.update_block(SCHEMA.encode_batch({"cpu_usage_pct": 37}))
    server.run()                              # bloquea hasta stop()
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading

from pymodbus.datastore import ModbusServerContext
from pymodbus.datastore.simulator import ModbusSimulatorContext
from pymodbus.server import StartAsyncSerialServer, StartAsyncTcpServer

from system.config_manager import ConfigManager
from system.logger import logger

# pymodbus 3.x emite sus advertencias de obsolescencia por su propio logger, no por
# los warnings de Python. Se suprimen acá, en la fuente; migrar a SimData/SimDevice
# al actualizar a la v4, donde ModbusSimulatorContext ya no existe.
logging.getLogger("pymodbus").setLevel(logging.ERROR)

STATUS_DISABLED = "disabled"    # apagado en el config; no es una falla
STATUS_STARTING = "starting"    # levantando, todavía no acepta consultas
STATUS_ACTIVE = "active"        # sirviendo
STATUS_ERROR = "error"          # quiso levantar y no pudo

# Tamaño del mapa expuesto cuando el config no dice otra cosa: registros 1-100
# (40001-40100). El datastore no expone nada más allá: una lectura que se pase del
# final se rechaza en vez de contestar un 0 que parezca medición.
DEFAULT_REGISTER_COUNT = 100

DEFAULT_SLAVE_ID = 1

# Única función Modbus servida: FC03, lectura de holding registers.
_FC_READ_HOLDING_REGISTERS = 3

_SLAVE_ID_MIN = 1
_SLAVE_ID_MAX = 247

# Margen entre el bind y el "está andando": si en ese tiempo no falló, levantó.
_STARTUP_GRACE_S = 2.0


def _build_simulator_config(register_count: int) -> dict:
    """Config del datastore de pymodbus: sólo holding registers, todos uint16 en 0."""
    return {
        "setup": {
            "di size": 0,
            "co size": 0,
            "ir size": 0,
            "hr size": register_count,
            "shared blocks": False,
            "type exception": False,
            "defaults": {
                "value": {"bits": 0, "uint16": 0, "uint32": 0, "float32": 0, "string": " "},
                "action": {"bits": None, "uint16": None, "uint32": None,
                           "float32": None, "string": None},
            },
        },
        "invalid": [],
        "write": [],
        "bits": [],
        "uint16": [[0, register_count - 1]],
        "uint32": [],
        "float32": [],
        "string": [],
        "repeat": [],
    }


class _ReadOnlyHoldingRegisters(ModbusSimulatorContext):
    """
    Contexto que sólo sirve FC03 dentro del mapa.

    Rechaza toda escritura y las demás funciones de lectura con 0x02. También
    arregla el rango: `validate()` de pymodbus chequea sólo la dirección de ARRANQUE
    (y con `>` en vez de `>=`), así que una lectura que empieza adentro y se pasa del
    final revienta con IndexError → 0x04 más un traceback por cada consulta.
    """

    def validate(self, func_code, address, count=1):
        if func_code != _FC_READ_HOLDING_REGISTERS:
            return False
        real_address = self.fc_offset[func_code] + address
        if real_address < 0 or count < 1:
            return False
        if real_address + count > self.register_count:
            return False
        return super().validate(func_code, address, count)


class SharedModbusServer:
    """
    Servidor Modbus TCP + RTU compartiendo un datastore en RAM.

    Los métodos de escritura y lectura de registros son thread-safe y se pueden
    llamar desde cualquier hilo, esté el servidor corriendo o no. `run()` bloquea
    el hilo que lo llama; todo lo demás se llama desde afuera.
    """

    def __init__(self, config_manager: ConfigManager):
        self._config = config_manager
        self._lock = threading.Lock()
        self._tcp_status = STATUS_DISABLED
        self._rtu_status = STATUS_DISABLED
        self._loop: asyncio.AbstractEventLoop | None = None

        self._register_count = self._read_register_count()
        self._simulator = _ReadOnlyHoldingRegisters(
            _build_simulator_config(self._register_count), None
        )
        self._hr_offset = self._simulator.fc_offset[_FC_READ_HOLDING_REGISTERS]

        # Un solo esclavo registrado: el servidor contesta ÚNICAMENTE a su id. Con
        # `single=True` respondía a los 247, que en un RS-485 compartido pisa a los
        # demás dispositivos del bus.
        self._slave_id = self._read_slave_id()
        self._context = ModbusServerContext(devices={self._slave_id: self._simulator})

    # ── Estado ───────────────────────────────────────────────────────────────

    @property
    def tcp_status(self) -> str:
        return self._tcp_status

    @property
    def rtu_status(self) -> str:
        return self._rtu_status

    @property
    def slave_id(self) -> int:
        return self._slave_id

    @property
    def register_count(self) -> int:
        return self._register_count

    # ── Acceso a los registros (thread-safe) ─────────────────────────────────

    def update_register(self, address: int, value: int):
        """
        Escribe un holding register, con direccionamiento base-1: el registro 1 es
        el primero, el que el PLC lee como 40001.

        Una dirección fuera del mapa se descarta con un warning: es un error del
        mapa, no del PLC, y no vale la pena tirar abajo el ciclo que la escribió.
        """
        index = self._index_of(address)
        if index is None:
            logger.warning(
                f"[Modbus] Dirección fuera del mapa: {address} "
                f"(el mapa expone 1-{self._register_count})."
            )
            return
        with self._lock:
            self._simulator.registers[index].value = int(value) & 0xFFFF

    def get_register(self, address: int) -> int:
        """Lee un holding register (base-1). Devuelve 0 fuera del mapa."""
        index = self._index_of(address)
        if index is None:
            return 0
        with self._lock:
            return self._simulator.registers[index].value

    def update_block(self, items: dict[int, int]):
        """Escritura en batch: {addr: valor crudo}, tal como lo devuelve
        `RegisterSchema.encode_batch()`."""
        for address, value in items.items():
            self.update_register(address, value)

    def _index_of(self, address: int) -> int | None:
        """Índice en el datastore de una dirección base-1, o None si está fuera."""
        pdu = address - 1
        if not (0 <= pdu < self._register_count):
            return None
        return self._hr_offset + pdu

    # ── Lectura de configuración ─────────────────────────────────────────────

    def _read_slave_id(self) -> int:
        """Lee `modbus.slave_id` y lo valida contra el rango de Modbus."""
        raw = self._config.get("modbus.slave_id", DEFAULT_SLAVE_ID)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.error(f"[Modbus] slave_id inválido ({raw!r}). Usando {DEFAULT_SLAVE_ID}.")
            return DEFAULT_SLAVE_ID
        if not (_SLAVE_ID_MIN <= value <= _SLAVE_ID_MAX):
            logger.error(
                f"[Modbus] slave_id {value} fuera de rango "
                f"[{_SLAVE_ID_MIN}-{_SLAVE_ID_MAX}]. Usando {DEFAULT_SLAVE_ID}."
            )
            return DEFAULT_SLAVE_ID
        return value

    def _read_register_count(self) -> int:
        """Lee `modbus.register_count`: cuántos registros expone el datastore."""
        raw = self._config.get("modbus.register_count", DEFAULT_REGISTER_COUNT)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        if value < 1:
            logger.error(
                f"[Modbus] register_count inválido ({raw!r}). "
                f"Usando {DEFAULT_REGISTER_COUNT}."
            )
            return DEFAULT_REGISTER_COUNT
        return value

    # ── Servidores async ─────────────────────────────────────────────────────

    async def run_tcp_server(self):
        """Levanta el servidor TCP y no vuelve hasta que se lo cancela."""
        tcp_config = self._config.get("modbus.tcp", {})
        if not tcp_config.get("enabled", True):
            self._tcp_status = STATUS_DISABLED
            logger.info("[Modbus] TCP deshabilitado en config.yaml")
            return

        self._tcp_status = STATUS_STARTING
        host = tcp_config.get("host", "0.0.0.0")
        port = tcp_config.get("port", 502)
        logger.info(
            f"[Modbus] TCP arrancando en {host}:{port} "
            f"({self._register_count} registros, unit id {self._slave_id})"
        )

        grace = asyncio.ensure_future(self._mark_active("_tcp_status"))
        try:
            # ignore_missing_devices=False: una consulta a otro unit id recibe la
            # excepción 0x0B en vez de un timeout opaco. Es punto a punto, no hay bus
            # que pisar, así que conviene avisarle al integrador que el id está mal.
            await StartAsyncTcpServer(
                context=self._context, address=(host, port),
                ignore_missing_devices=False, broadcast_enable=False,
            )
        except asyncio.CancelledError:
            grace.cancel()
            raise
        except PermissionError:
            grace.cancel()
            self._tcp_status = STATUS_ERROR
            self._log_bind_denied(port)
        except (OSError, RuntimeError) as e:
            grace.cancel()
            self._tcp_status = STATUS_ERROR
            logger.error(f"[Modbus] Error del servidor TCP: {e}")

    async def run_rtu_server(self):
        """Levanta el servidor RTU y no vuelve hasta que se lo cancela."""
        rtu_config = self._config.get("modbus.rtu", {})
        if not rtu_config.get("enabled", False):
            self._rtu_status = STATUS_DISABLED
            logger.info("[Modbus] RTU deshabilitado en config.yaml")
            return

        self._rtu_status = STATUS_STARTING
        port = rtu_config.get("port", "/dev/ttyS0")
        baudrate = rtu_config.get("baudrate", 115200)
        logger.info(
            f"[Modbus] RTU arrancando en {port} @ {baudrate} bps "
            f"(unit id {self._slave_id})"
        )

        grace = asyncio.ensure_future(self._mark_active("_rtu_status"))
        try:
            # ignore_missing_devices=True: en un bus RS-485 compartido, contestar una
            # consulta dirigida a otro esclavo pisaría su respuesta y rompería la
            # comunicación de los dos. El maestro ve el timeout del esclavo ausente.
            await StartAsyncSerialServer(
                context=self._context,
                port=port,
                baudrate=baudrate,
                parity=rtu_config.get("parity", "N"),
                stopbits=rtu_config.get("stop_bits", 1),
                bytesize=8,
                ignore_missing_devices=True,
                broadcast_enable=False,
            )
        except asyncio.CancelledError:
            grace.cancel()
            raise
        except Exception as e:
            grace.cancel()
            self._rtu_status = STATUS_ERROR
            logger.error(f"[Modbus] Falla del RTU en {port}: {e}. Continúa sólo el TCP.")

    async def _mark_active(self, status_attr: str):
        """Pasa un transporte a `active` si sobrevivió el arranque sin fallar."""
        await asyncio.sleep(_STARTUP_GRACE_S)
        if getattr(self, status_attr) == STATUS_STARTING:
            setattr(self, status_attr, STATUS_ACTIVE)

    def _log_bind_denied(self, port: int):
        if sys.platform == "win32":
            # En Windows no hay puertos privilegiados: un WSAEACCES (10013) es el
            # puerto ya tomado en exclusiva, el firewall, o un rango reservado por
            # Hyper-V/WSL2 (netsh interface ipv4 show excludedportrange protocol=tcp).
            logger.error(
                f"[Modbus] Sin permiso para el bind en el puerto {port}. Puede estar "
                f"ocupado, bloqueado por el firewall o dentro de un rango reservado "
                f"por Hyper-V/WSL2."
            )
        else:
            logger.error(
                f"[Modbus] Sin permiso para el bind en el puerto {port}. "
                f"Ejecutar con sudo o usar un puerto > 1024."
            )

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    def run(self):
        """
        Toma el hilo actual, arma su propio loop de asyncio y corre TCP + RTU.

        Bloquea hasta que `stop()` lo desarme desde otro hilo. Un transporte que no
        levanta no tumba al otro: cada uno deja el motivo en su `status`.
        """
        if sys.platform == "win32":
            # En Windows el default es ProactorEventLoop, que no implementa
            # add_reader/add_writer — justo lo que pyserial-asyncio necesita para el
            # puerto COM, así que el servidor RTU es imposible sobre Proactor. El loop
            # se crea en el hilo que llama, por eso la política se fija acá y no en el
            # arranque de la app. El TCP funciona igual con las dos.
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        tasks = [
            loop.create_task(self.run_tcp_server()),
            loop.create_task(self.run_rtu_server()),
        ]

        try:
            loop.run_until_complete(asyncio.gather(*tasks))
        except asyncio.CancelledError:
            logger.info("[Modbus] Tareas canceladas durante el cierre.")
        except RuntimeError as e:
            logger.info(f"[Modbus] Loop detenido durante el cierre: {e}")
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self._cancel_pending(loop)
            # Ya no hay nada sirviendo, así que ningún transporte puede quedar en
            # `active`; pero un `error` se conserva, porque es el diagnóstico de por
            # qué esta corrida no sirvió y se perdería para siempre. Las dos cosas
            # apagan igual el bit de la palabra de comunicaciones.
            self._tcp_status = self._settled_status(self._tcp_status)
            self._rtu_status = self._settled_status(self._rtu_status)
            self._loop = None
            logger.info("[Modbus] Servidor cerrado correctamente.")
            loop.close()

    @staticmethod
    def _settled_status(status: str) -> str:
        return status if status == STATUS_ERROR else STATUS_DISABLED

    def stop(self):
        """
        Pide el cierre desde otro hilo; `run()` vuelve poco después.

        Sin servidor corriendo no hace nada, así que se puede llamar dos veces.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._cancel_all_tasks, loop)

    @staticmethod
    def _cancel_all_tasks(loop: asyncio.AbstractEventLoop):
        """Cancela todo lo que quede vivo en el loop, desde el propio loop.

        Sólo cancelar tareas; NO llamar a `loop.stop()` — eso deja el
        `run_until_complete` con un "Event loop stopped before Future completed".
        """
        for task in asyncio.all_tasks(loop):
            task.cancel()

    @staticmethod
    def _cancel_pending(loop: asyncio.AbstractEventLoop):
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
