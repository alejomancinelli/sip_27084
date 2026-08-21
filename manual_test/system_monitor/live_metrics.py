"""
Prueba manual del monitor de sistema.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\system_monitor\\live_metrics.py
    Linux:    .venv/bin/python manual_test/system_monitor/live_metrics.py

Lee el config.yaml que tiene al lado (no el de la app), con el ConfigManager real,
e imprime una línea por intervalo con lo que devuelve `get_metrics()`. Antes de la
primera línea lista qué fuentes encontró en este equipo: es lo que hay que mirar al
estrenar el módulo en una máquina nueva, porque los nodos de sysfs cambian entre
Orin, Xavier y NX, y en Windows no existe ninguno.

Qué mirar:
    - El bloque de fuentes. En la Jetson tienen que aparecer las zonas térmicas y
      el nodo de carga de GPU; si están todas ausentes, las rutas que conoce el
      módulo no son las de este kernel y eso es lo que hay que corregir. En
      Windows lo normal es que los nodos de sysfs estén todos ausentes, que la
      zona térmica sea una ACPI (`\\_TZ.TZ00`) y que la GPU la conteste nvidia-smi.
    - Cargar el equipo (compilar algo, correr la inferencia, `dd` sobre el disco) y
      ver subir el uso de CPU, la temperatura y la potencia. En Windows la
      temperatura se refresca cada 10 s, no cada línea: la consulta WMI cuesta un
      proceso y la zona del firmware se mueve despacio.
    - Copiar un archivo grande por la red y ver el rx/tx de esa interfaz.
    - Cambiar `_REFRESH_INTERVAL_S` a 5.0: las tasas de red tienen que dar lo mismo
      que con 1.0, no cinco veces más. El intervalo se mide, no se asume.
    - Editar el config.yaml de al lado con la prueba corriendo: al guardar se relee
      y cambian la partición medida o las interfaces, sin reiniciar.
    - Poner en `net_interfaces` una interfaz que no existe: el aviso sale una sola
      vez y el resto de las métricas sigue saliendo igual.
    - Ctrl+C imprime el mínimo, el promedio y el máximo de la corrida, que es lo
      que sirve para dejarlo un rato largo midiendo.
"""

import glob
import os
import platform
import shutil
import sys
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

import system.system_monitor as sm                          # noqa: E402
from system.config_manager import ConfigManager             # noqa: E402
from system.system_monitor import SystemMonitor             # noqa: E402

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

_REFRESH_INTERVAL_S = 1.0       # cada cuánto se pide una métrica nueva
_HEADER_EVERY_ROWS = 20         # cada cuántas filas se repite la cabecera

# Métricas escalares que entran en el resumen final.
_TRACKED_KEYS = ("cpu_usage_pct", "cpu_temp_c", "gpu_usage_pct", "gpu_temp_c",
                 "ram_used_mb", "disk_free_gb", "power_w")

# Los anchos son los mismos que usa _format_row: la cabecera queda alineada sola.
_HEADER = (f"{'hora':8s}  {'CPU':>4s}  {'temp':>5s}  {'GPU':>4s}  {'temp':>5s}  "
           f"{'RAM (MB)':>13s}  {'disco':>7s}  {'pot.':>7s}  red (Mbps rx/tx)")


class _Stats:
    """Mínimo, promedio y máximo de cada métrica escalar, y el pico de cada interfaz."""

    def __init__(self):
        self._samples = {key: [] for key in _TRACKED_KEYS}
        self._net_peaks = {}

    def update(self, metrics: dict):
        for key in _TRACKED_KEYS:
            self._samples[key].append(metrics[key])
        for iface, rates in metrics["net_mbps"].items():
            peak = self._net_peaks.setdefault(iface, {"rx_mbps": 0.0, "tx_mbps": 0.0})
            peak["rx_mbps"] = max(peak["rx_mbps"], rates["rx_mbps"])
            peak["tx_mbps"] = max(peak["tx_mbps"], rates["tx_mbps"])

    def print_summary(self):
        count = len(self._samples[_TRACKED_KEYS[0]])
        if not count:
            print("Sin muestras.")
            return
        print(f"\nResumen de {count} muestras:")
        for key, values in self._samples.items():
            print(f"  {key:14s}  mín {min(values):8.1f}   "
                  f"prom {sum(values) / count:8.1f}   máx {max(values):8.1f}")
        for iface, peak in self._net_peaks.items():
            print(f"  {iface:14s}  pico rx {peak['rx_mbps']:8.1f} Mbps   "
                  f"pico tx {peak['tx_mbps']:8.1f} Mbps")


def _present(exists: bool) -> str:
    return "presente" if exists else "ausente"


def _format_zones(zones: dict) -> str:
    return ", ".join(f"{name} {temp_c:.1f}°C" for name, temp_c in zones.items())


def _format_row(metrics: dict) -> str:
    """Una línea con las métricas del intervalo, alineada con `_HEADER`."""
    net = "   ".join(
        f"{iface} {rates['rx_mbps']:.1f}/{rates['tx_mbps']:.1f}"
        for iface, rates in metrics["net_mbps"].items()
    ) or "sin interfaces"
    return (
        f"{datetime.now():%H:%M:%S}  "
        f"{metrics['cpu_usage_pct']:3d}%  {metrics['cpu_temp_c']:3d}°C  "
        f"{metrics['gpu_usage_pct']:3d}%  {metrics['gpu_temp_c']:3d}°C  "
        f"{metrics['ram_used_mb']:6d}/{metrics['ram_total_mb']:<6d}  "
        f"{metrics['disk_free_gb']:4d} GB  {metrics['power_w']:5.1f} W  {net}"
    )


def _print_sources(config: ConfigManager, metrics: dict):
    """
    Qué fuentes encontró el módulo en este equipo.

    Las rutas candidatas salen del módulo y las lecturas de `get_metrics()`: lo que
    se ve acá es lo que va a reportar, no lo que debería reportar.
    """
    print(f"Equipo: {platform.system()} {platform.machine()}, "
          f"Python {platform.python_version()}")
    print(f"Config: {_CONFIG_PATH}\n")
    print("Fuentes en este equipo:")

    # La fuente de las zonas no es la misma en los dos equipos, así que se nombra
    # la que se usó y no la de sysfs siempre.
    zone_source = sm._THERMAL_BASE if platform.system() == "Linux" else sm._WMI_THERMAL_CLASS
    zones = metrics["temps_c"]
    print(f"  zonas térmicas  {zone_source}: {len(zones)} zonas"
          f"{' -> ' + _format_zones(zones) if zones else ''}")
    print(f"  temperaturas    CPU {metrics['cpu_temp_c']}°C, "
          f"GPU {metrics['gpu_temp_c']}°C")

    for path in sm._GPU_LOAD_PATHS:
        print(f"  carga de GPU    {path}: {_present(os.path.exists(path))}")
    for pattern, _ in sm._POWER_GLOBS:
        print(f"  potencia        {pattern}: {len(glob.glob(pattern))} canales")

    print(f"  nvidia-smi      {shutil.which(sm._NVIDIA_SMI) or 'ausente'}")
    print(f"  disco           '{config.get('system_monitor.disk_path')}', "
          f"relativo a {sm._PROJECT_ROOT}")
    print(f"  interfaces      {', '.join(metrics['net_mbps']) or 'ninguna'}")


def _reload_if_changed(config: ConfigManager, last_mtime_s: float) -> float:
    """Relee el config si el archivo cambió: el módulo lo toma en la vuelta siguiente."""
    try:
        mtime_s = _CONFIG_PATH.stat().st_mtime
    except OSError:
        return last_mtime_s
    if mtime_s != last_mtime_s:
        config.load()
        print(f"-- {_CONFIG_PATH.name} recargado --")
    return mtime_s


def main() -> int:
    if not _CONFIG_PATH.exists():
        print(f"Falta {_CONFIG_PATH}.")
        return 2

    config = ConfigManager(str(_CONFIG_PATH))
    monitor = SystemMonitor(config)
    config_mtime_s = _CONFIG_PATH.stat().st_mtime

    # La primera llamada no tiene lectura anterior contra la que medir: la red sale
    # en 0 y se usa para listar las fuentes, no para mostrar tasas.
    _print_sources(config, monitor.get_metrics())
    print(f"\nUna línea cada {_REFRESH_INTERVAL_S:.0f} s. Ctrl+C para terminar.\n")

    stats = _Stats()
    rows = 0
    try:
        while True:
            time.sleep(_REFRESH_INTERVAL_S)
            config_mtime_s = _reload_if_changed(config, config_mtime_s)
            metrics = monitor.get_metrics()
            stats.update(metrics)
            if rows % _HEADER_EVERY_ROWS == 0:
                print(_HEADER)
            print(_format_row(metrics))
            rows += 1
    except KeyboardInterrupt:
        print()

    stats.print_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
