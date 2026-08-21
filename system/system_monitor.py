"""
Métricas de hardware del equipo donde corre la aplicación.

Maquinaria genérica: no conoce cámaras, inferencia ni protocolo. Expone
`get_metrics()`, que devuelve un dict de claves estables con valores físicos ya
convertidos a unidades (%, MiB, GiB, °C, W, Mbps). Ninguna clave falta nunca: lo
que el equipo no expone vale 0, no desaparece del dict.

Las fuentes cambian según la plataforma, el contrato no:

  métrica         Jetson (aarch64)                    Windows (x86_64)
  cpu_usage_pct   psutil                              psutil
  ram_*           psutil                              psutil
  disk_free_gb    psutil                              psutil
  net_mbps        psutil, contadores por interfaz      psutil
  temps_c         /sys/class/thermal                   zonas ACPI por WMI
  cpu_temp_c      zona de CPU, o la del SoC            la zona ACPI más caliente
  gpu_usage_pct   /sys/devices/…/load (por mil)        nvidia-smi
  gpu_temp_c      zona de GPU, o la del SoC            nvidia-smi
  power_w         rails INA3221 del módulo             nvidia-smi (solo la GPU)

La temperatura en Windows es la que publica el firmware en sus zonas ACPI
(`\\_TZ.TZ00`), no el sensor del die del CPU: ese está en
`MSAcpi_ThermalZoneTemperature`, que le contesta 'acceso denegado' a un proceso
sin privilegios de administrador. Puede leer bastante más bajo que lo que muestra
la BIOS, y es la misma zona con la que Windows decide su propio throttling.
`temps_c` dice con qué sensor se midió, en Windows y en la Jetson.

Dos puntos del contrato que no se ven en las firmas:
  - `net_mbps` se calcula por diferencia contra la lectura anterior, así que la
    primera llamada devuelve las interfaces en 0. El intervalo se mide, no se
    asume: llamar cada 5 s da la misma tasa que llamar cada 1 s.
  - Una fuente que falla deja su métrica en el valor neutro y no arrastra a las
    demás. El aviso de cada fuente sale una sola vez.
"""

import glob
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable

import psutil

from system.config_manager import ConfigManager
from system.logger import logger

_MB = 1024 ** 2
_GB = 1024 ** 3
_MILLI = 1000.0                 # sysfs expone las temperaturas en m°C
_KELVIN_OFFSET_C = 273.15       # WMI las expone en décimas de kelvin

# Ruta relativa a la raíz del repo, como el resto de las rutas del config.
_DEFAULT_DISK_PATH = "."
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Rango físico plausible: una zona deshabilitada no desaparece, reporta absurdos.
_MIN_TEMP_C = -40.0
_MAX_TEMP_C = 150.0

# ── Fuentes de sysfs (Jetson) ────────────────────────────────────────────────

_THERMAL_BASE = "/sys/class/thermal"
_ZONE_PREFIX = "thermal_zone"

# Nombres de zona térmica de Jetson Orin / Xavier / NX, en orden de preferencia.
_CPU_ZONE_NAMES = ("cpu-thermal", "CPU-therm", "CPU", "Tdiode")
_GPU_ZONE_NAMES = ("gpu-thermal", "GPU-therm", "GPU")
_SOC_ZONE_NAMES = ("soc0-thermal", "soc0", "soc1", "soc2", "SOC", "AO-therm")

# Sensores de psutil en x86 con Linux, en orden de preferencia.
_PSUTIL_SENSOR_NAMES = ("coretemp", "k10temp", "cpu_thermal", "acpitz")

# Carga de GPU de Tegra. El nodo publica por mil (0–1000): 417 es 41.7 %.
_GPU_LOAD_PATHS = (
    "/sys/devices/platform/gpu.0/load",
    "/sys/devices/gpu.0/load",
    "/sys/kernel/debug/tegra_gpu_powergate/gpu_load",
)
_GPU_LOAD_SCALE = 10            # por mil -> %

# Potencia del módulo por INA3221. Cada patrón trae su divisor hacia W: el nodo
# de iio publica en mW y el de hwmon en µW.
_POWER_GLOBS = (
    ("/sys/bus/i2c/drivers/ina3221x/*/iio:device0/in_power0_input", 1_000.0),
    ("/sys/bus/i2c/drivers/ina3221/*/hwmon/hwmon*/power1_input", 1_000_000.0),
)

# ── Sondas externas ──────────────────────────────────────────────────────────

# Cada sonda cuesta un proceso: tras esta cantidad de fallos seguidos se
# abandona, porque insistir sale más caro que la métrica que falta.
_PROBE_MAX_FAILURES = 3

_NVIDIA_SMI = "nvidia-smi"
_NVIDIA_SMI_FIELDS = ("utilization.gpu", "temperature.gpu", "power.draw")
_NVIDIA_SMI_METRICS = ("gpu_usage_pct", "gpu_temp_c", "power_w")
_NVIDIA_SMI_TIMEOUT_S = 2.0

# Zonas térmicas de Windows. La clase de perfmon la lee cualquier usuario; el
# nombre de la clase WMI no está traducido, el del contador de perfmon sí.
_WMI_THERMAL = "WMI"
_WMI_THERMAL_CLASS = "Win32_PerfFormattedData_Counters_ThermalZoneInformation"
_WMI_THERMAL_TIMEOUT_S = 5.0
# Una consulta cuesta ~300 ms y la zona ACPI se mueve despacio: no hay por qué
# pagarla en cada vuelta del hilo de telemetría.
_WMI_THERMAL_TTL_S = 10.0
_POWERSHELL = "powershell.exe"

# Sin esto, una app sin consola (pythonw) parpadea una ventana por consulta.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


# ── Helpers de lectura y de escala ───────────────────────────────────────────

def _read_int_file(path: str) -> int | None:
    """Entero de un nodo de sysfs. None si no existe, no se puede leer o no es número."""
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _read_text_file(path: str) -> str | None:
    """Contenido de texto de un nodo de sysfs, sin espacios al borde. None si falla."""
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _is_plausible_temp(temp_c: float) -> bool:
    """Descarta los valores que reporta un sensor deshabilitado o inexistente."""
    return _MIN_TEMP_C <= temp_c <= _MAX_TEMP_C


def _best_temp(zones: dict, names: tuple[str, ...]) -> float:
    """
    Temperatura de la primera zona que coincide con `names`, o 0.0.

    Busca el nombre exacto en el orden dado y después una coincidencia parcial
    sobre las zonas ordenadas: los nombres cambian entre kernels y la métrica no
    puede saltar de sensor de una lectura a la otra.
    """
    for name in names:
        if name in zones:
            return zones[name]
    for zone in sorted(zones):
        if any(name.lower() in zone.lower() for name in names):
            return zones[zone]
    return 0.0


def _to_mbps(delta_bytes: int, elapsed_s: float) -> float:
    """Bytes de un intervalo a Mbps. Un contador que retrocedió cuenta 0, no negativo."""
    if delta_bytes <= 0 or elapsed_s <= 0:
        return 0.0
    return round(delta_bytes * 8 / 1_000_000 / elapsed_s, 1)


def _parse_nvidia_smi(csv_line: str) -> dict:
    """
    Traduce una línea de `nvidia-smi --format=csv,noheader,nounits`.

    Devuelve las claves de `_NVIDIA_SMI_METRICS` que la GPU reportó; las que
    llegan como '[N/A]' quedan afuera. {} si la línea no tiene la forma esperada.
    """
    values = [value.strip() for value in csv_line.split(",")]
    if len(values) != len(_NVIDIA_SMI_METRICS):
        return {}
    metrics = {}
    for key, value in zip(_NVIDIA_SMI_METRICS, values):
        try:
            metrics[key] = int(float(value))
        except ValueError:
            continue
    return metrics


def _parse_wmi_zones(stdout: str) -> dict:
    """Traduce las líneas 'nombre=décimas de kelvin' de la consulta WMI a °C."""
    zones = {}
    for line in stdout.splitlines():
        name, _, raw = line.strip().partition("=")
        if not name or not raw:
            continue
        try:
            temp_c = round(int(raw) / 10.0 - _KELVIN_OFFSET_C, 1)
        except ValueError:
            continue
        if _is_plausible_temp(temp_c):
            zones[name] = temp_c
    return zones


def _is_loopback(iface: str) -> bool:
    """True si la interfaz es de loopback ('lo' en Linux, 'Loopback…' en Windows)."""
    return iface == "lo" or iface.lower().startswith("loopback")


class SystemMonitor:
    """
    Recolecta las métricas de hardware del equipo.

    Pensado para un único hilo de telemetría que llame `get_metrics()` cada
    ~1 s: el uso de CPU y el throughput de red se miden contra la lectura
    anterior, así que dos llamadores se reparten los intervalos y ninguno mide lo
    que cree. El Lock protege el estado interno, no el sentido de la métrica.

    Lee su sección `system_monitor` del config en el momento de usarla, así que
    cambiar el disco o las interfaces a medir no exige reiniciar.
    """

    def __init__(self, config_manager: ConfigManager):
        self._config = config_manager
        self._is_linux = platform.system() == "Linux"
        self._lock = threading.Lock()
        self._last_net_io: dict = {}
        self._last_net_s = 0.0
        # Se busca una sola vez: es un acceso al PATH y no cambia en caliente.
        self._nvidia_smi_path = shutil.which(_NVIDIA_SMI)
        # Las zonas de Windows salen de un proceso: se cachean por `_WMI_THERMAL_TTL_S`.
        self._wmi_zones: dict = {}
        self._wmi_zones_s: float | None = None
        self._probe_failures: dict[str, int] = {}
        self._disabled_probes: set[str] = set()
        # Fuentes ya avisadas: get_metrics corre en loop y una fuente ausente
        # llenaría el log con la misma línea.
        self._warned: set[str] = set()
        # psutil mide el uso de CPU contra la llamada anterior: sin esta primera
        # lectura, la primera métrica sería el promedio desde que arrancó el
        # proceso. Va guardada como el resto: construir el monitor no puede tumbar
        # el arranque de la app por una métrica.
        self._read_or(lambda: psutil.cpu_percent(interval=None), 0, "el uso de CPU")

    # ── API pública ──────────────────────────────────────────────────────────

    def get_metrics(self) -> dict:
        """
        Métricas del equipo en unidades físicas. Claves estables:
          cpu_usage_pct, gpu_usage_pct   uso 0–100
          cpu_temp_c, gpu_temp_c         °C; 0 si el equipo no lo expone
          ram_used_mb, ram_total_mb      MiB
          disk_free_gb                   GiB libres en `system_monitor.disk_path`
          power_w                        W del módulo, o de la GPU discreta; 0 sin sensor
          net_mbps                       {interfaz: {"rx_mbps": float, "tx_mbps": float}}
          temps_c                        {zona: °C} con el nombre que le da la plataforma

        No propaga excepciones: la métrica de una fuente que falla queda en su
        valor neutro y el resto se reporta igual.
        """
        with self._lock:
            zones = self._read_or(self._read_thermal_zones, {}, "las zonas térmicas")
            cpu_temp_c, gpu_temp_c = self._read_or(
                lambda: self._read_temperatures(zones), (0, 0), "la temperatura")
            gpu_usage_pct = self._read_or(self._read_gpu_usage_pct, None, "el uso de GPU")
            power_w = self._read_or(self._read_power_w, None, "la potencia")

            # nvidia-smi cuesta un proceso por llamada, así que solo se consulta si
            # el equipo no publica la GPU en sysfs. En Jetson sysfs alcanza y
            # nvidia-smi no reporta utilización.
            gpu = ({} if gpu_usage_pct is not None
                   else self._read_or(self._read_nvidia_smi, {}, "nvidia-smi"))
            memory = self._read_or(psutil.virtual_memory, None, "la memoria")

            return {
                "cpu_usage_pct": self._read_or(
                    lambda: int(psutil.cpu_percent(interval=None)), 0, "el uso de CPU"),
                "gpu_usage_pct": (gpu_usage_pct if gpu_usage_pct is not None
                                  else gpu.get("gpu_usage_pct", 0)),
                "cpu_temp_c": cpu_temp_c,
                "gpu_temp_c": gpu_temp_c or gpu.get("gpu_temp_c", 0),
                "ram_used_mb": 0 if memory is None else int(memory.used / _MB),
                "ram_total_mb": 0 if memory is None else int(memory.total / _MB),
                "disk_free_gb": self._read_or(self._read_disk_free_gb, 0, "el disco"),
                "power_w": power_w if power_w is not None else gpu.get("power_w", 0),
                "net_mbps": self._read_or(self._read_net_mbps, {}, "la red"),
                "temps_c": zones,
            }

    # ── Temperaturas ─────────────────────────────────────────────────────────

    def _read_thermal_zones(self) -> dict:
        """Zonas térmicas del equipo por nombre, en °C. {} si no publica ninguna."""
        return self._read_sysfs_zones() if self._is_linux else self._read_wmi_zones()

    def _read_sysfs_zones(self) -> dict:
        """
        Temperaturas de /sys/class/thermal por nombre de zona, en °C.

        Descarta las lecturas fuera de rango físico: una zona deshabilitada no
        desaparece del sysfs, devuelve un valor absurdo.
        """
        if not os.path.isdir(_THERMAL_BASE):
            return {}
        zones = {}
        for entry in sorted(os.listdir(_THERMAL_BASE)):
            if not entry.startswith(_ZONE_PREFIX):
                continue
            name = _read_text_file(os.path.join(_THERMAL_BASE, entry, "type"))
            milli_c = _read_int_file(os.path.join(_THERMAL_BASE, entry, "temp"))
            if not name or milli_c is None:
                continue
            temp_c = round(milli_c / _MILLI, 1)
            if _is_plausible_temp(temp_c):
                zones[name] = temp_c
        return zones

    def _read_wmi_zones(self) -> dict:
        """
        Zonas térmicas ACPI de Windows, en °C. {} si el firmware no publica ninguna.

        Es la única temperatura que Windows entrega sin driver ni privilegios de
        administrador. El resultado se cachea `_WMI_THERMAL_TTL_S` porque la
        consulta cuesta un proceso de ~300 ms.
        """
        now_s = time.monotonic()
        if self._wmi_zones_s is not None and now_s - self._wmi_zones_s < _WMI_THERMAL_TTL_S:
            return self._wmi_zones

        # Todo con comillas simples: el argumento pasa por el parser de la consola
        # de Windows antes de llegar a PowerShell.
        stdout = self._run_probe(_WMI_THERMAL, [
            _POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
            f"Get-CimInstance -ClassName {_WMI_THERMAL_CLASS} | "
            f"ForEach-Object {{ $_.Name + '=' + $_.HighPrecisionTemperature }}",
        ], _WMI_THERMAL_TIMEOUT_S)

        self._wmi_zones_s = now_s
        self._wmi_zones = {} if stdout is None else _parse_wmi_zones(stdout)
        return self._wmi_zones

    def _read_temperatures(self, zones: dict) -> tuple[int, int]:
        """
        (temperatura de CPU, temperatura de GPU) en °C, 0 la que no tenga fuente.

        En Jetson el CPU y la GPU comparten SoC: si el kernel no expone la zona de
        GPU se reporta la del SoC, que es la que gobierna el throttling. En x86 la
        GPU es discreta y no está en las zonas térmicas, así que queda en 0 y la
        completa nvidia-smi.
        """
        if zones:
            cpu_temp_c = (_best_temp(zones, _CPU_ZONE_NAMES)
                          or _best_temp(zones, _SOC_ZONE_NAMES))
            gpu_temp_c = _best_temp(zones, _GPU_ZONE_NAMES)
            if self._is_linux:
                gpu_temp_c = gpu_temp_c or cpu_temp_c
            elif not cpu_temp_c:
                # Las zonas ACPI de Windows se llaman '\_TZ.TZ00': el nombre no dice
                # qué miden, así que se reporta la más caliente, que es con la que el
                # firmware decide el throttling.
                cpu_temp_c = max(zones.values())
            return int(cpu_temp_c), int(gpu_temp_c)

        # psutil no expone sensores en Windows: el atributo directamente no existe.
        read_sensors = getattr(psutil, "sensors_temperatures", None)
        if read_sensors is None:
            return 0, 0
        sensors = read_sensors()
        for name in _PSUTIL_SENSOR_NAMES:
            readings = sensors.get(name)
            if readings:
                return int(readings[0].current), 0
        return 0, 0

    # ── GPU y potencia ───────────────────────────────────────────────────────

    def _read_gpu_usage_pct(self) -> int | None:
        """Uso de GPU de sysfs en %, o None si el equipo no lo publica."""
        if not self._is_linux:
            return None
        for path in _GPU_LOAD_PATHS:
            per_mille = _read_int_file(path)
            if per_mille is not None:
                return max(0, min(100, per_mille // _GPU_LOAD_SCALE))
        return None

    def _read_power_w(self) -> float | None:
        """
        Potencia del módulo en W desde los rails INA3221, o None si no hay sensor.

        El primer canal que expone el kernel es el total del módulo; los otros son
        parciales (CPU, GPU, SoC) y sumarlos contaría dos veces lo mismo.
        """
        if not self._is_linux:
            return None
        for pattern, scale in _POWER_GLOBS:
            for path in sorted(glob.glob(pattern)):
                raw = _read_int_file(path)
                if raw is not None:
                    return round(raw / scale, 1)
        return None

    def _read_nvidia_smi(self) -> dict:
        """
        Uso, temperatura y potencia de la GPU discreta. {} si no hay nada que leer.

        Con varias GPU se reporta la primera: la métrica es una sola.
        """
        if self._nvidia_smi_path is None:
            return {}
        stdout = self._run_probe(_NVIDIA_SMI, [
            self._nvidia_smi_path,
            f"--query-gpu={','.join(_NVIDIA_SMI_FIELDS)}",
            "--format=csv,noheader,nounits",
        ], _NVIDIA_SMI_TIMEOUT_S)
        if stdout is None:
            return {}
        lines = stdout.strip().splitlines()
        return _parse_nvidia_smi(lines[0]) if lines else {}

    # ── Disco y red ──────────────────────────────────────────────────────────

    def _read_disk_free_gb(self) -> int:
        """GiB libres en la partición de `system_monitor.disk_path`."""
        path = self._config.get("system_monitor.disk_path", _DEFAULT_DISK_PATH)
        if not os.path.isabs(path):
            # Relativa a la raíz del repo, no al CWD: la métrica no puede depender
            # de desde dónde se lanzó el proceso.
            path = os.path.join(_PROJECT_ROOT, path)
        return int(psutil.disk_usage(path).free / _GB)

    def _read_net_mbps(self) -> dict:
        """
        Throughput por interfaz desde la lectura anterior, en Mbps.

        La primera llamada devuelve las interfaces en 0: no hay intervalo contra
        el que dividir.
        """
        now_s = time.monotonic()
        counters = psutil.net_io_counters(pernic=True)
        previous, previous_s = self._last_net_io, self._last_net_s
        self._last_net_io, self._last_net_s = counters, now_s
        elapsed_s = now_s - previous_s

        throughput = {}
        for iface in self._select_interfaces(counters):
            before = previous.get(iface)
            now = counters[iface]
            if before is None:
                throughput[iface] = {"rx_mbps": 0.0, "tx_mbps": 0.0}
                continue
            throughput[iface] = {
                "rx_mbps": _to_mbps(now.bytes_recv - before.bytes_recv, elapsed_s),
                "tx_mbps": _to_mbps(now.bytes_sent - before.bytes_sent, elapsed_s),
            }
        return throughput

    def _select_interfaces(self, counters: dict) -> list[str]:
        """
        Interfaces a reportar: las de `system_monitor.net_interfaces`, o todas las
        que no sean loopback si la lista está vacía.

        Una interfaz configurada que el equipo no tiene se avisa una vez y se
        saltea: los nombres no se cross-portean ('eth0' en Jetson, 'Ethernet' en
        Windows).
        """
        configured = self._config.get("system_monitor.net_interfaces", None) or []
        if not configured:
            return [iface for iface in counters if not _is_loopback(iface)]

        selected = []
        for iface in configured:
            if iface in counters:
                selected.append(iface)
            else:
                self._warn_once(f"la interfaz '{iface}'",
                                f"La interfaz '{iface}' no existe en este equipo.")
        return selected

    # ── Internos ─────────────────────────────────────────────────────────────

    def _run_probe(self, probe: str, argv: list, timeout_s: float) -> str | None:
        """
        Corre un programa externo y devuelve su stdout, o None si falló.

        Tras `_PROBE_MAX_FAILURES` fallos seguidos la sonda queda deshabilitada:
        cada intento cuesta un proceso y hasta `timeout_s`, y eso es más caro que
        la métrica que falta.
        """
        if probe in self._disabled_probes:
            return None
        try:
            completed = subprocess.run(argv, capture_output=True, text=True,
                                       check=True, timeout=timeout_s, **_NO_WINDOW)
        except (OSError, subprocess.SubprocessError) as e:
            self._probe_failures[probe] = self._probe_failures.get(probe, 0) + 1
            logger.debug(f"[SystemMonitor] {probe} falló: {e}")
            if self._probe_failures[probe] >= _PROBE_MAX_FAILURES:
                self._disabled_probes.add(probe)
                logger.warning(f"[SystemMonitor] {probe} no responde; se deja de consultar.")
            return None

        self._probe_failures[probe] = 0
        return completed.stdout

    def _read_or(self, reader: Callable[[], object], fallback: object, what: str) -> object:
        """Ejecuta un lector y devuelve `fallback` si falla, sin propagar."""
        try:
            return reader()
        except Exception as e:
            self._warn_once(what, f"No se pudo leer {what}: {e}")
            return fallback

    def _warn_once(self, key: str, message: str):
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning(f"[SystemMonitor] {message}")
