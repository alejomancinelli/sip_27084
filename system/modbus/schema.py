"""
Esquema de registros Modbus — maquinaria genérica, reutilizable entre proyectos.

Define el tipo `Reg` (una fila del mapa) y `RegisterSchema`, que deriva de una
lista de `Reg` todo lo que el resto del sistema necesita: la codificación de cada
valor físico a su holding register, las descripciones para la UI y la
documentación, la lista de registros a releer del servidor, y el espejo
lectura/escritura del bloque de configuración dinámica.

Este módulo no conoce ningún registro puntual: la lista concreta se carga de un
archivo YAML con `load_registers()` y vive en `system/modbus/register_map.yaml`.
Así la maquinaria se comparte entre forks mientras el mapa, que es lo que cambia
en cada instalación, queda en un archivo de datos que se edita sin tocar código.

**Este módulo es el dueño de las escalas.** Quien produce un valor entrega el
valor físico (%, °C, ms, fps) y pide `encode()`; nadie multiplica por 10 ni satura
a uint16 por su cuenta.

Módulo puro: sin Qt, sin pymodbus, sin ConfigManager.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

import yaml

from system.paths import DATA_DIR

_MAP_FILENAME = "register_map.yaml"

# Rango de un holding register.
UINT16_MAX = 0xFFFF

# Quién escribe cada fila; determina cómo la trata el sistema.
PRODUCER_INFERENCE = "inference"   # lo escribe el productor de resultados de inferencia
PRODUCER_HEALTH = "health"         # lo escribe el ciclo de métricas de hardware y cámara
PRODUCER_CONFIG = "config"         # espejo R/W de config.yaml; lo maneja este esquema
PRODUCERS = (PRODUCER_INFERENCE, PRODUCER_HEALTH, PRODUCER_CONFIG)


def to_plc_address(addr: int) -> str:
    """
    La dirección como la nombra el PLC: el registro 1 es el 40001.

    El `4` adelante es la convención con la que se documentan los holding registers, y no
    algo que viaje por el cable —en el protocolo la petición lleva un offset de base 0, así
    que el registro 1 se pide como offset 0—. Son tres numeraciones para lo mismo, y por eso
    la conversión vive acá y no en cada pantalla: adentro del repo la dirección es siempre
    base-1, y esto es lo único que la traduce para mostrarla.
    """
    return f"4{int(addr):04d}"


def to_uint16(value: object, scale: int = 1) -> int:
    """Codifica un valor (o None) a un holding register, saturando en el rango."""
    if value is None:
        return 0
    return max(0, min(UINT16_MAX, int(round(float(value) * scale))))


@dataclass(frozen=True)
class Reg:
    """
    Una fila del mapa de registros Modbus.

    `producer` indica quién escribe el registro; los valores válidos son los
    `PRODUCER_*` de este módulo. Las filas `config` son las únicas que este
    esquema escribe por su cuenta (`mirror_batch` / `detect_plc_writes` /
    `apply_plc_writes`); las demás las rellena su productor.

    Los campos `config_key`, `default`, `apply` y `mirror_from_config` sólo aplican
    a filas con `producer: config`.
    """
    addr: int                          # dirección Modbus 1-based
    name: str                          # id corto y único: "cpu_usage_pct"
    desc: str                          # texto humano → UI y documentación
    producer: str                      # uno de PRODUCERS
    scale: int = 1                     # registro = round(valor*scale); valor = registro/scale
    is_bool: bool = False              # codifica/decodifica como 0/1
    unit: str = ""                     # unidad física del valor sin escalar: "%", "°C", "ms"
    # ── Sólo filas producer="config" ──
    config_key: str | None = None      # ruta punteada en config.yaml
    default: object = 0                # default para cfg.get al espejar
    apply: str | None = None           # hook especial de escritura; si es None → cfg.set
    mirror_from_config: bool = True    # False = el valor viene de afuera del config


# Campos que `load_registers` acepta en cada entrada del YAML.
_REG_FIELDS = frozenset(f.name for f in fields(Reg))
_REQUIRED_FIELDS = ("addr", "name", "desc", "producer")


def load_registers(map_path: str) -> list[Reg]:
    """
    Lee el mapa de registros de un YAML y lo devuelve como lista de `Reg`.

    El archivo es una lista de mapas, uno por registro, con las claves del
    dataclass; las que no están toman el default del campo. Un archivo vacío o sin
    entradas devuelve una lista vacía, que es un mapa válido: hay proyectos que
    todavía no publican nada.

    Levanta ValueError con el archivo y la fila si una entrada está incompleta,
    tiene una clave desconocida o un `producer` fuera del vocabulario. Preferimos
    romper al arrancar antes que servirle al PLC un mapa a medias.
    """
    with open(map_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{map_path}: el mapa tiene que ser una lista de registros.")

    registers = []
    for index, entry in enumerate(raw):
        location = f"{map_path}, entrada {index + 1}"
        if not isinstance(entry, dict):
            raise ValueError(f"{location}: cada registro es un mapa de claves, no {type(entry).__name__}.")

        unknown = set(entry) - _REG_FIELDS
        if unknown:
            raise ValueError(f"{location}: clave(s) desconocida(s) {sorted(unknown)}.")

        missing = [key for key in _REQUIRED_FIELDS if entry.get(key) in (None, "")]
        if missing:
            raise ValueError(f"{location}: falta(n) {missing}.")

        if entry["producer"] not in PRODUCERS:
            raise ValueError(
                f"{location}: producer {entry['producer']!r} no es uno de {list(PRODUCERS)}."
            )
        if not isinstance(entry["addr"], int) or entry["addr"] < 1:
            raise ValueError(f"{location}: addr tiene que ser un entero >= 1 (base-1).")

        registers.append(Reg(**entry))

    return registers


class RegisterSchema:
    """
    Índices y operaciones derivadas de una lista de `Reg`.

    Valida al construirse: dos filas no pueden compartir dirección ni nombre. Es
    inmutable después, así que se instancia una vez a nivel de módulo y se comparte
    entre hilos sin lock.
    """

    def __init__(self, registers: list[Reg]):
        self.registers = list(registers)
        self._by_addr: dict[int, Reg] = {}
        self._by_name: dict[str, Reg] = {}
        for r in self.registers:
            if r.addr in self._by_addr:
                raise ValueError(f"Dirección Modbus duplicada: {r.addr}")
            if r.name in self._by_name:
                raise ValueError(f"Nombre de registro duplicado: {r.name!r}")
            self._by_addr[r.addr] = r
            self._by_name[r.name] = r

    # ── Índices de sólo lectura ──────────────────────────────────────────────

    def descriptions(self) -> dict[int, str]:
        """{addr: desc} — fuente única para la tabla de registros de la UI y los docs."""
        return {r.addr: r.desc for r in self.registers}

    def addr(self, name: str) -> int:
        """Dirección de un registro por su nombre corto. Ninguna dirección se escribe
        como literal fuera de este paquete: se pide por nombre."""
        return self._by_name[name].addr

    def max_addr(self) -> int:
        """Dirección más alta del mapa; 0 si el mapa está vacío."""
        return max(self._by_addr, default=0)

    def readback_addrs(self) -> list[int]:
        """
        Direcciones cuyo `producer` es `inference`.

        Son las que no viajan en el batch de salud, así que quien quiera mostrar el
        mapa completo tiene que releerlas del servidor en vez de esperarlas por señal.
        """
        return [r.addr for r in self.registers if r.producer == PRODUCER_INFERENCE]

    def config_regs(self) -> list[Reg]:
        """Filas del bloque de configuración dinámica (espejo R/W)."""
        return [r for r in self.registers if r.producer == PRODUCER_CONFIG]

    # ── Codificación escalar ─────────────────────────────────────────────────

    def encode(self, name: str, value: object) -> int:
        """Codifica un valor físico al holding register de `name`, con su escala."""
        return self._encode(self._by_name[name], value)

    def encode_batch(self, values: dict[str, object]) -> dict[int, int]:
        """{nombre: valor físico} → {addr: valor crudo}, listo para `update_block`."""
        return {self._by_name[name].addr: self._encode(self._by_name[name], value)
                for name, value in values.items()}

    def _encode(self, reg: Reg, value: object) -> int:
        if reg.is_bool:
            return 1 if value else 0
        return to_uint16(value, reg.scale)

    def _decode(self, reg: Reg, raw: int) -> object:
        if reg.is_bool:
            return bool(raw)
        if reg.scale != 1:
            return raw / reg.scale
        return raw

    # ── Espejo de configuración (config.yaml → registros) ────────────────────

    def mirror_batch(self, config_manager) -> dict[int, int]:
        """
        Valores a escribir para las filas `config` respaldadas por config.yaml.

        Excluye las de origen externo (`mirror_from_config: false`), que su sitio
        productor rellena por su cuenta.
        """
        out: dict[int, int] = {}
        for r in self.config_regs():
            if r.mirror_from_config and r.config_key:
                out[r.addr] = self._encode(r, config_manager.get(r.config_key, r.default))
        return out

    # ── Detección y aplicación de escrituras del PLC ─────────────────────────

    def detect_plc_writes(self, server, last_pushed: dict[int, int]) -> dict[int, int]:
        """
        Detecta escrituras del PLC comparando el valor ACTUAL del servidor contra lo
        que la app escribió por ÚLTIMA vez (`last_pushed`) — NO contra el espejo
        recién calculado. La distinción es clave:

          - PLC escribió el registro → server != last_pushed → sí es escritura del
            PLC; se aplica a config.
          - La app cambió config → server == last_pushed (el PLC no lo tocó), aunque
            el espejo nuevo difiera → NO es del PLC; sólo se re-espeja.

        Comparar contra el espejo confundiría un cambio hecho desde la app con una
        escritura del PLC, y el valor viejo del registro terminaría pisando el valor
        nuevo de config.

        En el primer ciclo `last_pushed` está vacío → no detecta nada (prime natural;
        el datastore arranca en 0 y no hay con qué comparar todavía).
        Devuelve {addr: valor crudo del PLC}.
        """
        writes: dict[int, int] = {}
        for r in self.config_regs():
            if r.addr not in last_pushed:
                continue
            current = server.get_register(r.addr)
            if current != last_pushed[r.addr]:
                writes[r.addr] = current
        return writes

    def apply_plc_writes(self, config_manager, writes: dict[int, int],
                         hooks: dict, batch: dict[int, int]) -> bool:
        """
        Aplica las escrituras del PLC: al hook que la fila declare en `apply`, o a
        config.yaml (`cfg.set`) en el caso normal.

        También hace ECO del valor crudo del PLC en `batch`, para que el
        `update_block` posterior conserve ese valor en vez de reescribir el que la
        app había calculado antes de detectar el cambio; si no, el ciclo siguiente
        lo volvería a confundir con una escritura nueva del PLC.

        Devuelve True si alguna fila respaldada por config cambió → conviene `save()`.
        Las filas con hook suelen persistir por su cuenta.
        """
        needs_save = False
        for addr, raw in writes.items():
            reg = self._by_addr[addr]
            if reg.apply:
                hook = hooks.get(reg.apply)
                if hook is not None:
                    hook(self._decode(reg, raw))
            elif reg.config_key:
                config_manager.set(reg.config_key, self._decode(reg, raw))
                needs_save = True
            batch[addr] = raw
        return needs_save


def default_map_path() -> str:
    """
    Dónde está el mapa: primero en la raíz de la instalación, si no al lado de este módulo.

    Son dos lugares porque el archivo cumple dos papeles. En el repo vive junto al esquema
    que lo valida, que es donde se lo edita y se lo lee. En un entregable compilado es un
    archivo de la instalación —lo acuerda el integrador y cambia en cada planta—, así que
    va al lado del ejecutable, en `DATA_DIR`, y no adentro de la distribución, que se
    reemplaza entera al actualizar.

    Gana `DATA_DIR`: si alguien puso un mapa ahí, es el de esa planta y no el de fábrica.
    """
    installed = os.path.join(DATA_DIR, _MAP_FILENAME)
    if os.path.isfile(installed):
        return installed
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _MAP_FILENAME)
