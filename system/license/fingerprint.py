"""
Huella de la máquina: un hash por componente de hardware.

Módulo de infraestructura: sólo importa el logger. Sin Qt, sin ConfigManager, sin red.

**Devuelve un hash por fuente y no uno solo de todo junto**, y esa decisión manda en el
resto del archivo. Un hash único no sobrevive a un disco reemplazado en garantía:
cualquier cambio da otro número y no hay forma de saber cuánto cambió. Con un hash por
componente, la licencia declara cuántos tienen que seguir dando y un equipo al que le
cambiaron la placa de red sigue andando sin reemitir nada.

**Nunca levanta una excepción.** Una fuente que no se puede leer —el equipo no tiene DMI,
el `/sys` pide root, WMI no contesta— simplemente no aparece en el dict. Una huella
incompleta es un dato y no un error: es al comparar donde se decide si alcanza.

Los valores que los fabricantes dejan de relleno —«To be filled by O.E.M.», un UUID de
ceros— se descartan como si la fuente no existiera. Son idénticos en miles de equipos, y
tomarlos por identidad haría que una licencia validara en cualquier máquina con el mismo
BIOS de relleno.

Qué se lee en cada plataforma:

    board_uuid      Windows: Win32_ComputerSystemProduct.UUID
                    Linux:   /sys/class/dmi/id/product_uuid   (suele pedir root)
    board_serial    Windows: Win32_BaseBoard.SerialNumber
                    Linux:   /sys/class/dmi/id/board_serial    (suele pedir root)
    module_serial   Linux:   /proc/device-tree/serial-number — el serial del módulo de la
                             Jetson, y la fuente buena en aarch64, donde no hay DMI
    disk_serial     Windows: Win32_DiskDrive del disco 0
                    Linux:   /sys/block/<dev>/device/{serial,cid}
    mac_<iface>     las dos: una fuente por placa de red física, con el nombre de la
                             interfaz en la clave

`/etc/machine-id` y el `MachineGuid` del registro **no** se usan: cambian al reinstalar el
sistema, que es justo lo que pasa después de la falla que uno quiere sobrevivir.

El hash de cada fuente lleva su propio nombre adentro (`sha256("board_uuid=ABC…")`), así
el mismo valor bajo dos fuentes distintas no da el mismo número. El firmante repite este
mismo cálculo del otro lado, así que la fórmula es parte del formato y no un detalle.
"""

import hashlib
import json
import os
import re
import subprocess
import sys

from system.logger import logger

SOURCE_BOARD_UUID = "board_uuid"
SOURCE_BOARD_SERIAL = "board_serial"
SOURCE_MODULE_SERIAL = "module_serial"
SOURCE_DISK_SERIAL = "disk_serial"

# Las fuentes de red no son una sola: hay una por placa y el sufijo es el nombre de la
# interfaz, que es estable dentro de un mismo equipo aunque no lo sea entre equipos.
MAC_SOURCE_PREFIX = "mac_"

# Fuentes que no cambian al reemplazar una pieza periférica. Son las que conviene exigir
# cuando se emite una licencia; las MAC y el disco entran como refuerzo.
STABLE_SOURCES = (SOURCE_BOARD_UUID, SOURCE_BOARD_SERIAL, SOURCE_MODULE_SERIAL)

# TODO: decisión pendiente — cuántas fuentes tienen que seguir coincidiendo para aceptar
# la licencia. Se define con la huella real de una Jetson y de un equipo de planta a la
# vista; hasta entonces, dos de las que haya.
DEFAULT_MIN_MATCHES = 2

_MIN_VALUE_LENGTH = 4             # menos que esto no identifica nada
_WINDOWS_PROBE_TIMEOUT_S = 20.0   # WMI en frío tarda varios segundos la primera vez

# Sin esto, cada consulta a WMI abre una ventana de consola en una app con GUI.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

# Relleno de fábrica: aparece idéntico en miles de equipos, así que no es identidad.
_PLACEHOLDER_VALUES = frozenset({
    "NONE", "NULL", "N/A", "NA", "UNKNOWN", "DEFAULT STRING", "SYSTEM SERIAL NUMBER",
    "TO BE FILLED BY O.E.M.", "TO BE FILLED BY OEM", "BASE BOARD SERIAL NUMBER",
    "NOT SPECIFIED", "NOT APPLICABLE", "SERIAL NUMBER", "CHASSIS SERIAL NUMBER",
    "0123456789", "INVALID", "EMPTY", "FILLED BY OEM", "PRODUCT SERIAL NUMBER",
})

# Interfaces que aparecen y desaparecen con un contenedor, una VPN o un hipervisor.
_VIRTUAL_INTERFACE_PATTERN = re.compile(
    r"loopback|virtual|vethernet|veth|vmware|vbox|virbr|docker|hyper-v|bluetooth|"
    r"tap-|tun|wsl|npcap|vpn|teredo|isatap|pseudo|zerotier|tailscale",
    re.IGNORECASE,
)

_EMPTY_MACS = frozenset({"", "00:00:00:00:00:00", "00-00-00-00-00-00"})

# Bloques que no son un disco de sistema y cuyo serial no identifica al equipo.
_IGNORED_BLOCK_PREFIXES = ("loop", "ram", "zram", "sr", "dm-", "md", "fd")

_LINUX_FILES = {
    SOURCE_BOARD_UUID: "/sys/class/dmi/id/product_uuid",
    SOURCE_BOARD_SERIAL: "/sys/class/dmi/id/board_serial",
    SOURCE_MODULE_SERIAL: "/proc/device-tree/serial-number",
}

# Una sola consulta para las tres fuentes de Windows: WMI cuesta un proceso y casi un
# segundo, y tres llamadas serían tres.
_WINDOWS_PROBE = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$product = Get-CimInstance Win32_ComputerSystemProduct;"
    "$board = Get-CimInstance Win32_BaseBoard;"
    "$disk = Get-CimInstance Win32_DiskDrive | Where-Object { $_.Index -eq 0 };"
    "[pscustomobject]@{"
    "board_uuid = [string]$product.UUID;"
    "board_serial = [string]$board.SerialNumber;"
    "disk_serial = [string]$disk.SerialNumber"
    "} | ConvertTo-Json -Compress"
)

# Leer la huella cuesta un proceso de PowerShell, y el hardware no cambia mientras la app
# corre: se lee una vez por proceso y el re-chequeo periódico reusa lo leído.
_cached_components: dict | None = None


# ── API pública ──────────────────────────────────────────────────────────────

def read_components(*, refresh: bool = False) -> dict[str, str]:
    """
    Devuelve `{fuente: sha256}` con lo que se pudo leer en este equipo.

    El resultado queda cacheado por proceso: `refresh=True` vuelve a consultar el
    hardware, que es lo que hace falta después de un cambio de pieza en caliente y en
    ningún otro lado.
    """
    global _cached_components
    if _cached_components is not None and not refresh:
        return dict(_cached_components)

    raw = _read_windows_values() if sys.platform == "win32" else _read_linux_values()
    raw.update(_read_mac_values())

    components = {}
    for source, value in raw.items():
        digest = hash_value(source, value)
        if digest:
            components[source] = digest

    _cached_components = components
    logger.info(
        f"Huella de la máquina: {len(components)} fuentes "
        f"({', '.join(sorted(components)) or 'ninguna'})."
    )
    return dict(components)


def hash_value(source: str, value: str) -> str:
    """
    Hash de una fuente, con su nombre adentro.

    Devuelve cadena vacía si el valor es relleno de fábrica, demasiado corto o un solo
    carácter repetido: en ese caso la fuente se trata como si no existiera.
    """
    normalized = _normalize(value)
    if not normalized:
        return ""
    return hashlib.sha256(f"{source}={normalized}".encode("utf-8")).hexdigest()


def count_matches(components: dict[str, str], expected: dict[str, str]) -> int:
    """Cuántas fuentes de la licencia siguen dando el mismo hash en este equipo."""
    return sum(1 for source, digest in expected.items() if components.get(source) == digest)


def is_match(components: dict[str, str], expected: dict[str, str], min_matches: int) -> bool:
    """
    True si la huella de la licencia corresponde a este equipo.

    Una licencia sin componentes y con `min_matches` en 0 no está atada a ninguna máquina
    y da True en todas: es un caso deliberado —una demo— y se ve como tal en el estado.
    """
    if min_matches <= 0:
        return not expected
    return count_matches(components, expected) >= min_matches


def build_machine_key(components: dict[str, str]) -> bytes:
    """
    Clave derivada de la huella, para firmar el estado que queda en disco.

    Con la huella vacía devuelve igual una clave utilizable: sirve para que el archivo de
    estado no se pueda editar a mano en el propio equipo, no para identificarlo.
    """
    material = "|".join(f"{source}={components[source]}" for source in sorted(components))
    return hashlib.sha256(f"machine-key:{material}".encode("utf-8")).digest()


# ── Lectura por plataforma ───────────────────────────────────────────────────

def _read_windows_values() -> dict[str, str]:
    output = _run_probe(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_PROBE]
    )
    if not output:
        return {}

    try:
        parsed = json.loads(output)
    except ValueError:
        logger.warning("La consulta de hardware no devolvió JSON. La huella va sin DMI.")
        return {}

    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items() if value is not None}


def _read_linux_values() -> dict[str, str]:
    values = {}
    for source, path in _LINUX_FILES.items():
        text = _read_text_file(path)
        if text:
            values[source] = text

    disk_serial = _read_linux_disk_serial()
    if disk_serial:
        values[SOURCE_DISK_SERIAL] = disk_serial
    return values


def _read_linux_disk_serial() -> str:
    block_root = "/sys/block"
    if not os.path.isdir(block_root):
        return ""

    try:
        names = sorted(os.listdir(block_root))
    except OSError:
        return ""

    for name in names:
        if name.startswith(_IGNORED_BLOCK_PREFIXES):
            continue
        # El eMMC de la Jetson no expone `serial` pero sí el `cid`, que es único por chip.
        for filename in ("serial", "cid", "wwid"):
            serial = _read_text_file(os.path.join(block_root, name, "device", filename))
            if serial:
                return f"{name}:{serial}"
    return ""


def _read_mac_values() -> dict[str, str]:
    try:
        import psutil
    except ImportError:
        logger.warning("Sin psutil: la huella va sin las placas de red.")
        return {}

    try:
        interfaces = psutil.net_if_addrs()
    except OSError as e:
        logger.warning(f"No se pudieron listar las interfaces de red: {e}.")
        return {}

    values = {}
    for name, entries in interfaces.items():
        if _VIRTUAL_INTERFACE_PATTERN.search(name):
            continue
        for entry in entries:
            if entry.family != psutil.AF_LINK:
                continue
            mac = str(entry.address or "").strip().lower()
            if mac in _EMPTY_MACS or len(mac) < 12:
                continue
            values[MAC_SOURCE_PREFIX + _slugify(name)] = mac
    return values


# ── Helpers ──────────────────────────────────────────────────────────────────

def _run_probe(argv: list) -> str:
    """Corre una consulta al sistema y devuelve su salida; cadena vacía si falla."""
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, check=True,
                                   timeout=_WINDOWS_PROBE_TIMEOUT_S, **_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"No se pudo consultar el hardware ({argv[0]}): {e}.")
        return ""
    return (completed.stdout or "").strip()


def _read_text_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            return handle.read()
    except OSError:
        return ""


def _normalize(value: str) -> str:
    # El device-tree de la Jetson termina sus strings en NUL y eso viaja adentro del valor.
    text = str(value or "").replace("\x00", "").strip().upper()
    text = re.sub(r"\s+", " ", text)

    if len(text) < _MIN_VALUE_LENGTH or text in _PLACEHOLDER_VALUES:
        return ""
    if len(set(text.replace("-", "").replace(" ", ""))) <= 1:
        return ""
    return text


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "iface"
