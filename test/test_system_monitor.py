"""Tests del monitor de sistema: fuentes de sysfs de la Jetson, fuentes de
Windows, cálculo del throughput de red y estabilidad del contrato de
`get_metrics`.

Las fuentes de la Jetson se montan como archivos en `tmp_path`, así que los dos
caminos —aarch64 con sysfs y Windows con nvidia-smi— se prueban desde cualquier
equipo."""

import os
import subprocess
import sys
import threading
from collections import namedtuple

import psutil
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import system.system_monitor as sm
from system.system_monitor import (
    SystemMonitor,
    _best_temp,
    _is_loopback,
    _parse_nvidia_smi,
    _parse_wmi_zones,
    _to_mbps,
)

# Contadores de psutil: el módulo solo mira estos dos campos.
_Counters = namedtuple("_Counters", "bytes_recv bytes_sent")
_Usage = namedtuple("_Usage", "free")
_Reading = namedtuple("_Reading", "current")

_MEGABIT = 1_000_000 / 8        # bytes que hacen 1 Mbps en un segundo

_EXPECTED_KEYS = {
    "cpu_usage_pct", "gpu_usage_pct", "cpu_temp_c", "gpu_temp_c",
    "ram_used_mb", "ram_total_mb", "disk_free_gb", "power_w",
    "net_mbps", "temps_c",
}


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {}
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self._values[key] = value


class _FakeProbe:
    """
    `subprocess.run` de mentira: devuelve `stdout` y guarda cómo se lo llamó.

    Sirve para las dos sondas externas —nvidia-smi y la consulta WMI— porque las
    dos pasan por el mismo `_run_probe`.
    """

    def __init__(self, stdout: str = "", error: Exception | None = None):
        self.stdout = stdout
        self.error = error
        self.calls = []

    def __call__(self, argv: list, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(argv, 0, self.stdout, "")


class _Clock:
    """Reloj monótono controlado: el intervalo de red se mide, no se asume."""

    def __init__(self, now_s: float = 1_000.0):
        self.now_s = now_s

    def advance(self, seconds: float):
        self.now_s += seconds

    def __call__(self) -> float:
        return self.now_s


def _monitor(**config_overrides) -> SystemMonitor:
    """
    Monitor sin sondas externas: cada test enchufa las fuentes que necesita.

    Ni nvidia-smi ni la consulta WMI salen a buscar nada, así que el equipo donde
    corre el test no decide la métrica ni se paga un proceso por caso.
    """
    _monitored = SystemMonitor(_MockConfig(**config_overrides))
    _monitored._nvidia_smi_path = None
    _monitored._disabled_probes.add(sm._WMI_THERMAL)
    return _monitored


def _linux_monitor(**config_overrides) -> SystemMonitor:
    """Monitor que se cree Linux: las fuentes de la Jetson se prueban desde Windows."""
    _monitored = _monitor(**config_overrides)
    _monitored._is_linux = True
    return _monitored


class _FakeJetson:
    """
    Monta en `tmp_path` los nodos de sysfs que publica una Jetson.

    Ningún nodo existe hasta que el test lo escribe: un módulo sin sensor de
    potencia o sin zona de GPU es un caso real, no un test roto.
    """

    def __init__(self, tmp_path, monkeypatch):
        self.thermal_path = tmp_path / "thermal"
        self.thermal_path.mkdir()
        self.gpu_load_path = tmp_path / "gpu_load"
        self.power_path = tmp_path / "ina3221"
        monkeypatch.setattr(sm, "_THERMAL_BASE", str(self.thermal_path))
        monkeypatch.setattr(sm, "_GPU_LOAD_PATHS", (str(self.gpu_load_path),))
        monkeypatch.setattr(sm, "_POWER_GLOBS",
                            ((str(self.power_path / "*" / "in_power0_input"), 1_000.0),))
        self.monitor = _linux_monitor()

    def set_zones(self, *zones: tuple[str, int]):
        """Escribe una zona térmica por par (nombre, m°C)."""
        for _index, (_name, _milli_c) in enumerate(zones):
            _zone_path = self.thermal_path / f"{sm._ZONE_PREFIX}{_index}"
            _zone_path.mkdir()
            (_zone_path / "type").write_text(_name)
            (_zone_path / "temp").write_text(str(_milli_c))

    def set_gpu_load(self, per_mille: int):
        self.gpu_load_path.write_text(f"{per_mille}\n")

    def set_power(self, milli_w: int, channel: str = "1-0040"):
        _channel_path = self.power_path / channel
        _channel_path.mkdir(parents=True)
        (_channel_path / "in_power0_input").write_text(f"{milli_w}\n")


@pytest.fixture
def jetson(tmp_path, monkeypatch) -> _FakeJetson:
    return _FakeJetson(tmp_path, monkeypatch)


@pytest.fixture
def windows(monkeypatch) -> SystemMonitor:
    """Monitor en un equipo sin sysfs y con nvidia-smi en el PATH."""
    _monitored = _monitor()
    _monitored._is_linux = False
    _monitored._nvidia_smi_path = "nvidia-smi"
    monkeypatch.delattr(sm.psutil, "sensors_temperatures", raising=False)
    return _monitored


def _logged(caplog: pytest.LogCaptureFixture, text: str) -> int:
    return sum(1 for _record in caplog.records if text in _record.getMessage())


# ── Helpers puros ────────────────────────────────────────────────────────────

class TestBestTemp:
    def test_an_exact_name_is_found(self):
        assert _best_temp({"cpu-thermal": 45.5}, ("cpu-thermal",)) == 45.5

    def test_the_candidate_order_decides(self):
        _zones = {"CPU": 40.0, "cpu-thermal": 50.0}
        assert _best_temp(_zones, ("cpu-thermal", "CPU")) == 50.0
        assert _best_temp(_zones, ("CPU", "cpu-thermal")) == 40.0

    def test_an_exact_name_beats_a_partial_one(self):
        _zones = {"CPU-therm-extra": 70.0, "CPU": 40.0}
        assert _best_temp(_zones, ("CPU",)) == 40.0

    def test_a_partial_name_matches_ignoring_case(self):
        assert _best_temp({"thermal-cpu-1": 55.0}, ("CPU",)) == 55.0

    def test_the_partial_match_does_not_depend_on_the_insertion_order(self):
        """os.listdir no garantiza orden: la métrica no puede cambiar de sensor."""
        assert _best_temp({"b-CPU": 70.0, "a-CPU": 40.0}, ("CPU",)) == 40.0
        assert _best_temp({"a-CPU": 40.0, "b-CPU": 70.0}, ("CPU",)) == 40.0

    def test_no_match_is_zero(self):
        assert _best_temp({"gpu-thermal": 60.0}, ("cpu-thermal",)) == 0.0

    def test_no_zones_is_zero(self):
        assert _best_temp({}, sm._CPU_ZONE_NAMES) == 0.0


class TestToMbps:
    def test_bytes_of_one_second_become_mbps(self):
        assert _to_mbps(int(10 * _MEGABIT), 1.0) == 10.0

    def test_the_interval_is_divided_not_assumed(self):
        """El bug clásico: asumir 1 s y reportar el quíntuple si el poll es cada 5 s."""
        assert _to_mbps(int(10 * _MEGABIT), 5.0) == 2.0

    def test_a_slow_link_does_not_collapse_to_zero(self):
        assert _to_mbps(50_000, 1.0) == 0.4

    def test_a_counter_reset_counts_zero(self):
        assert _to_mbps(-1_000_000, 1.0) == 0.0

    def test_no_traffic_is_zero(self):
        assert _to_mbps(0, 1.0) == 0.0

    def test_a_zero_interval_does_not_divide(self):
        assert _to_mbps(1_000, 0.0) == 0.0


class TestParseNvidiaSmi:
    def test_a_full_line_gives_the_three_metrics(self):
        assert _parse_nvidia_smi("37, 54, 22.11") == {
            "gpu_usage_pct": 37, "gpu_temp_c": 54, "power_w": 22,
        }

    def test_a_field_the_gpu_does_not_report_is_left_out(self):
        assert _parse_nvidia_smi("37, 54, [N/A]") == {
            "gpu_usage_pct": 37, "gpu_temp_c": 54,
        }

    def test_extra_spaces_are_tolerated(self):
        assert _parse_nvidia_smi("  37 ,54,  22.11 ")["gpu_usage_pct"] == 37

    def test_a_line_with_other_fields_is_discarded(self):
        assert _parse_nvidia_smi("37, 54") == {}
        assert _parse_nvidia_smi("37, 54, 22.11, 8192") == {}

    def test_an_empty_line_is_discarded(self):
        assert _parse_nvidia_smi("") == {}


class TestIsLoopback:
    @pytest.mark.parametrize("iface", ["lo", "Loopback Pseudo-Interface 1", "loopback0"])
    def test_loopback_names(self, iface):
        assert _is_loopback(iface) is True

    @pytest.mark.parametrize("iface", ["eth0", "eth1", "Ethernet", "Wi-Fi", "local"])
    def test_real_interfaces(self, iface):
        assert _is_loopback(iface) is False


# ── Zonas térmicas y temperaturas ────────────────────────────────────────────

class TestThermalZones:
    def test_the_zones_are_read_by_name_in_celsius(self, jetson):
        jetson.set_zones(("cpu-thermal", 45_500), ("gpu-thermal", 39_000))
        assert jetson.monitor._read_thermal_zones() == {
            "cpu-thermal": 45.5, "gpu-thermal": 39.0,
        }

    def test_entries_that_are_not_zones_are_ignored(self, jetson):
        jetson.set_zones(("cpu-thermal", 45_000))
        (jetson.thermal_path / "cooling_device0").mkdir()
        assert list(jetson.monitor._read_thermal_zones()) == ["cpu-thermal"]

    def test_a_zone_without_temp_is_skipped(self, jetson):
        jetson.set_zones(("cpu-thermal", 45_000))
        (jetson.thermal_path / f"{sm._ZONE_PREFIX}9").mkdir()
        assert jetson.monitor._read_thermal_zones() == {"cpu-thermal": 45.0}

    def test_a_non_numeric_temp_is_skipped(self, jetson):
        jetson.set_zones(("cpu-thermal", 45_000))
        _broken = jetson.thermal_path / f"{sm._ZONE_PREFIX}9"
        _broken.mkdir()
        (_broken / "type").write_text("broken")
        (_broken / "temp").write_text("n/a")
        assert jetson.monitor._read_thermal_zones() == {"cpu-thermal": 45.0}

    def test_a_disabled_sensor_is_discarded(self, jetson):
        """Una zona apagada no desaparece del sysfs: reporta un valor absurdo."""
        jetson.set_zones(("cpu-thermal", 45_000), ("iwlwifi", -256_000),
                         ("bogus", 500_000))
        assert jetson.monitor._read_thermal_zones() == {"cpu-thermal": 45.0}

    def test_windows_does_not_walk_sysfs(self, jetson):
        """En Windows la fuente es WMI: /sys no se recorre ni si existiera."""
        jetson.set_zones(("cpu-thermal", 45_000))
        jetson.monitor._is_linux = False
        assert jetson.monitor._read_thermal_zones() == {}

    def test_no_thermal_directory_is_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sm, "_THERMAL_BASE", str(tmp_path / "missing"))
        assert _linux_monitor()._read_thermal_zones() == {}


class TestTemperatures:
    def test_the_jetson_zones_feed_both_temperatures(self, jetson):
        jetson.set_zones(("cpu-thermal", 45_500), ("gpu-thermal", 39_200))
        _zones = jetson.monitor._read_thermal_zones()
        assert jetson.monitor._read_temperatures(_zones) == (45, 39)

    def test_without_a_gpu_zone_the_gpu_reports_the_cpu(self):
        """En Jetson comparten SoC: la temperatura del SoC es la que hace throttling."""
        assert _linux_monitor()._read_temperatures({"cpu-thermal": 48.0}) == (48, 48)

    def test_without_a_cpu_zone_the_soc_answers(self):
        assert _linux_monitor()._read_temperatures({"soc0-thermal": 51.0}) == (51, 51)

    def test_a_linux_zone_with_no_known_name_is_zero(self):
        """En sysfs el nombre dice qué mide: uno desconocido no es el CPU."""
        assert _linux_monitor()._read_temperatures({"iwlwifi": 40.0}) == (0, 0)

    def test_windows_has_no_temperature_source_without_zones(self, windows):
        assert windows._read_temperatures({}) == (0, 0)

    def test_on_x86_linux_psutil_answers_for_the_cpu(self, monkeypatch):
        monkeypatch.setattr(sm.psutil, "sensors_temperatures",
                            lambda: {"coretemp": [_Reading(62.5)]}, raising=False)
        # La GPU discreta no está en los sensores del CPU: la completa nvidia-smi.
        assert _linux_monitor()._read_temperatures({}) == (62, 0)

    def test_the_sensor_order_of_preference_is_respected(self, monkeypatch):
        monkeypatch.setattr(sm.psutil, "sensors_temperatures",
                            lambda: {"acpitz": [_Reading(40.0)],
                                     "coretemp": [_Reading(62.0)]}, raising=False)
        assert _linux_monitor()._read_temperatures({})[0] == 62

    def test_an_unknown_sensor_is_not_reported(self, monkeypatch):
        monkeypatch.setattr(sm.psutil, "sensors_temperatures",
                            lambda: {"nct6798": [_Reading(35.0)]}, raising=False)
        assert _linux_monitor()._read_temperatures({}) == (0, 0)


class TestWindowsTemperatures:
    """Las zonas ACPI no dicen qué miden, así que la elección es por temperatura."""

    def test_the_hottest_acpi_zone_is_the_cpu_temperature(self, windows):
        assert windows._read_temperatures({"\\_TZ.TZ00": 27.9, "\\_TZ.TZ01": 41.2}) == (41, 0)

    def test_a_single_zone_answers(self, windows):
        assert windows._read_temperatures({"\\_TZ.TZ00": 27.9}) == (27, 0)

    def test_the_acpi_zone_is_not_reported_as_the_gpu(self, windows):
        """La GPU discreta tiene su propio sensor: la zona del firmware no lo suple."""
        assert windows._read_temperatures({"\\_TZ.TZ00": 27.9})[1] == 0

    def test_a_known_name_still_wins(self, windows):
        # Si el firmware llegara a nombrar la zona, el nombre manda sobre el máximo.
        assert windows._read_temperatures({"CPU": 30.0, "\\_TZ.TZ00": 45.0}) == (30, 0)


# ── GPU y potencia por sysfs ─────────────────────────────────────────────────

class TestGpuUsage:
    def test_the_node_publishes_per_mille(self, jetson):
        jetson.set_gpu_load(417)
        assert jetson.monitor._read_gpu_usage_pct() == 41

    def test_a_light_load_is_not_read_as_a_percentage(self, jetson):
        """417 es 41.7 %, así que 50 es 5 % y no 50 %."""
        jetson.set_gpu_load(50)
        assert jetson.monitor._read_gpu_usage_pct() == 5

    def test_an_idle_gpu_is_zero(self, jetson):
        jetson.set_gpu_load(0)
        assert jetson.monitor._read_gpu_usage_pct() == 0

    def test_a_saturated_gpu_is_one_hundred(self, jetson):
        jetson.set_gpu_load(1_000)
        assert jetson.monitor._read_gpu_usage_pct() == 100

    def test_the_value_is_clamped(self, jetson):
        jetson.set_gpu_load(2_500)
        assert jetson.monitor._read_gpu_usage_pct() == 100

    def test_a_negative_value_is_clamped(self, jetson):
        jetson.set_gpu_load(-10)
        assert jetson.monitor._read_gpu_usage_pct() == 0

    def test_no_node_reports_no_source(self, jetson):
        assert jetson.monitor._read_gpu_usage_pct() is None

    def test_the_second_node_answers_when_the_first_is_absent(self, tmp_path, monkeypatch):
        _present = tmp_path / "load"
        _present.write_text("300")
        monkeypatch.setattr(sm, "_GPU_LOAD_PATHS",
                            (str(tmp_path / "missing"), str(_present)))
        assert _linux_monitor()._read_gpu_usage_pct() == 30

    def test_windows_has_no_sysfs_source(self, windows):
        assert windows._read_gpu_usage_pct() is None


class TestPower:
    def test_the_iio_node_publishes_milliwatts(self, jetson):
        jetson.set_power(15_500)
        assert jetson.monitor._read_power_w() == 15.5

    def test_the_first_channel_is_the_module_total(self, jetson):
        # Los otros canales son parciales (CPU, GPU, SoC): sumarlos contaría de más.
        jetson.set_power(15_500, channel="1-0040")
        jetson.set_power(4_200, channel="1-0041")
        assert jetson.monitor._read_power_w() == 15.5

    def test_the_hwmon_node_publishes_microwatts(self, tmp_path, monkeypatch):
        _hwmon_path = tmp_path / "hwmon3"
        _hwmon_path.mkdir()
        (_hwmon_path / "power1_input").write_text("12300000")
        monkeypatch.setattr(sm, "_POWER_GLOBS",
                            ((str(tmp_path / "*" / "power1_input"), 1_000_000.0),))
        assert _linux_monitor()._read_power_w() == 12.3

    def test_no_rail_reports_no_source(self, jetson):
        assert jetson.monitor._read_power_w() is None

    def test_an_unreadable_rail_reports_no_source(self, jetson):
        _channel_path = jetson.power_path / "1-0040"
        _channel_path.mkdir(parents=True)
        (_channel_path / "in_power0_input").write_text("n/a")
        assert jetson.monitor._read_power_w() is None

    def test_windows_has_no_rails(self, windows):
        assert windows._read_power_w() is None


# ── GPU discreta por nvidia-smi ──────────────────────────────────────────────

class TestNvidiaSmi:
    def test_the_output_becomes_metrics(self, windows, monkeypatch):
        monkeypatch.setattr(sm.subprocess, "run", _FakeProbe("37, 54, 22.11\n"))
        assert windows._read_nvidia_smi() == {
            "gpu_usage_pct": 37, "gpu_temp_c": 54, "power_w": 22,
        }

    def test_the_query_asks_for_parseable_csv(self, windows, monkeypatch):
        _fake = _FakeProbe("37, 54, 22.11")
        monkeypatch.setattr(sm.subprocess, "run", _fake)
        windows._read_nvidia_smi()
        assert _fake.calls[0][0] == "nvidia-smi"
        assert _fake.calls[0][1] == (
            "--query-gpu=utilization.gpu,temperature.gpu,power.draw"
        )
        assert "--format=csv,noheader,nounits" in _fake.calls[0]

    def test_only_the_first_gpu_is_reported(self, windows, monkeypatch):
        monkeypatch.setattr(sm.subprocess, "run",
                            _FakeProbe("37, 54, 22.11\n80, 71, 145.00\n"))
        assert windows._read_nvidia_smi()["gpu_usage_pct"] == 37

    def test_empty_output_gives_no_metrics(self, windows, monkeypatch):
        monkeypatch.setattr(sm.subprocess, "run", _FakeProbe("\n"))
        assert windows._read_nvidia_smi() == {}

    def test_without_the_binary_nothing_is_launched(self, monkeypatch):
        _fake = _FakeProbe("37, 54, 22.11")
        monkeypatch.setattr(sm.subprocess, "run", _fake)
        _monitored = _monitor()                 # _nvidia_smi_path en None
        _monitored._is_linux = False
        assert _monitored._read_nvidia_smi() == {}
        assert _fake.calls == []

    @pytest.mark.parametrize("error", [
        subprocess.TimeoutExpired("nvidia-smi", 2.0),
        subprocess.CalledProcessError(9, "nvidia-smi"),
        OSError("no se pudo lanzar el proceso"),
    ])
    def test_a_failure_gives_no_metrics(self, windows, monkeypatch, error):
        monkeypatch.setattr(sm.subprocess, "run", _FakeProbe(error=error))
        assert windows._read_nvidia_smi() == {}

    def test_it_stops_querying_after_repeated_failures(self, windows, monkeypatch):
        """Cada intento cuesta un proceso y hasta 2 s: insistir sale más caro que
        la métrica que falta."""
        _fake = _FakeProbe(error=OSError("sin driver"))
        monkeypatch.setattr(sm.subprocess, "run", _fake)
        for _ in range(sm._PROBE_MAX_FAILURES + 5):
            assert windows._read_nvidia_smi() == {}
        assert len(_fake.calls) == sm._PROBE_MAX_FAILURES
        assert sm._NVIDIA_SMI in windows._disabled_probes

    def test_a_transient_failure_does_not_disable_the_source(self, windows, monkeypatch):
        _fake = _FakeProbe(error=subprocess.TimeoutExpired("nvidia-smi", 2.0))
        monkeypatch.setattr(sm.subprocess, "run", _fake)
        for _ in range(sm._PROBE_MAX_FAILURES - 1):
            windows._read_nvidia_smi()

        _fake.error = None
        _fake.stdout = "37, 54, 22.11"
        assert windows._read_nvidia_smi()["gpu_usage_pct"] == 37
        # El contador vuelve a cero: los fallos que cuentan son los seguidos.
        _fake.error = OSError("sin driver")
        windows._read_nvidia_smi()
        assert sm._NVIDIA_SMI not in windows._disabled_probes

    def test_disabling_one_probe_does_not_disable_the_other(self, windows, monkeypatch):
        """Las sondas comparten la política de corte, no el contador."""
        _fake = _FakeProbe(error=OSError("sin driver"))
        monkeypatch.setattr(sm.subprocess, "run", _fake)
        windows._disabled_probes.discard(sm._WMI_THERMAL)
        for _ in range(sm._PROBE_MAX_FAILURES):
            windows._read_nvidia_smi()
        assert sm._NVIDIA_SMI in windows._disabled_probes

        _fake.error, _fake.stdout = None, "\\_TZ.TZ00=3010"
        assert windows._read_wmi_zones() == {"\\_TZ.TZ00": 27.9}


# ── Zonas ACPI de Windows por WMI ────────────────────────────────────────────

class TestParseWmiZones:
    def test_deci_kelvin_becomes_celsius(self):
        assert _parse_wmi_zones("\\_TZ.TZ00=3010") == {"\\_TZ.TZ00": 27.9}

    def test_every_zone_is_reported(self):
        assert _parse_wmi_zones("\\_TZ.TZ00=3010\r\n\\_TZ.TZ01=3145\r\n") == {
            "\\_TZ.TZ00": 27.9, "\\_TZ.TZ01": 41.4,
        }

    def test_a_zone_that_reports_nothing_is_discarded(self):
        """Sin sensor detrás, el contador vale 0: eso serían -273 °C."""
        assert _parse_wmi_zones("\\_TZ.TZ00=0") == {}

    def test_a_non_numeric_value_is_skipped(self):
        assert _parse_wmi_zones("\\_TZ.TZ00=n/a\n\\_TZ.TZ01=3450") == {"\\_TZ.TZ01": 71.9}

    @pytest.mark.parametrize("stdout", ["", "\n", "sin zonas", "=3010", "\\_TZ.TZ00="])
    def test_output_without_pairs_gives_no_zones(self, stdout):
        assert _parse_wmi_zones(stdout) == {}


class TestWmiZones:
    """La temperatura de Windows sale de las zonas ACPI del firmware."""

    def _enabled(self, monitor: SystemMonitor) -> SystemMonitor:
        monitor._disabled_probes.discard(sm._WMI_THERMAL)
        return monitor

    def test_the_zones_come_from_the_query(self, windows, monkeypatch):
        monkeypatch.setattr(sm.subprocess, "run", _FakeProbe("\\_TZ.TZ00=3010"))
        assert self._enabled(windows)._read_wmi_zones() == {"\\_TZ.TZ00": 27.9}

    def test_the_query_uses_the_wmi_class_and_not_a_perfmon_counter(self, windows, monkeypatch):
        """El nombre de la clase WMI no está traducido; el del contador de perfmon sí,
        y este equipo tiene Windows en español."""
        _fake = _FakeProbe("\\_TZ.TZ00=3010")
        monkeypatch.setattr(sm.subprocess, "run", _fake)
        self._enabled(windows)._read_wmi_zones()
        _argv = _fake.calls[0]
        assert _argv[0] == sm._POWERSHELL
        assert "-NoProfile" in _argv and "-NonInteractive" in _argv
        assert sm._WMI_THERMAL_CLASS in _argv[-1]
        # Comillas dobles en el comando: el parser de la consola se las come.
        assert '"' not in _argv[-1]

    def test_the_result_is_cached(self, windows, monkeypatch):
        """La consulta cuesta ~300 ms y la zona ACPI se mueve despacio."""
        _fake = _FakeProbe("\\_TZ.TZ00=3010")
        monkeypatch.setattr(sm.subprocess, "run", _fake)
        for _ in range(10):
            assert self._enabled(windows)._read_wmi_zones() == {"\\_TZ.TZ00": 27.9}
        assert len(_fake.calls) == 1

    def test_the_cache_expires(self, windows, monkeypatch):
        _fake = _FakeProbe("\\_TZ.TZ00=3010")
        monkeypatch.setattr(sm.subprocess, "run", _fake)
        _clock = _Clock()
        monkeypatch.setattr(sm.time, "monotonic", _clock)
        self._enabled(windows)._read_wmi_zones()

        _clock.advance(sm._WMI_THERMAL_TTL_S + 0.1)
        _fake.stdout = "\\_TZ.TZ00=3145"
        assert windows._read_wmi_zones() == {"\\_TZ.TZ00": 41.4}
        assert len(_fake.calls) == 2

    def test_a_failure_does_not_retry_before_the_ttl(self, windows, monkeypatch):
        _fake = _FakeProbe(error=subprocess.TimeoutExpired("powershell", 5.0))
        monkeypatch.setattr(sm.subprocess, "run", _fake)
        for _ in range(5):
            assert self._enabled(windows)._read_wmi_zones() == {}
        assert len(_fake.calls) == 1

    def test_linux_never_launches_powershell(self, monkeypatch):
        _fake = _FakeProbe("\\_TZ.TZ00=3010")
        monkeypatch.setattr(sm.subprocess, "run", _fake)
        _monitored = self._enabled(_linux_monitor())
        monkeypatch.setattr(sm, "_THERMAL_BASE", "/sys/class/thermal")
        _monitored._read_thermal_zones()
        assert _fake.calls == []


# ── Disco ────────────────────────────────────────────────────────────────────

class TestDisk:
    def _recorded_path(self, monitor: SystemMonitor, monkeypatch) -> str:
        _paths = []

        def _fake_disk_usage(path: str) -> _Usage:
            _paths.append(path)
            return _Usage(free=100 * 1024 ** 3)

        monkeypatch.setattr(sm.psutil, "disk_usage", _fake_disk_usage)
        monitor._read_disk_free_gb()
        return os.path.normpath(_paths[0])

    def test_the_free_space_is_reported_in_gib(self, monkeypatch):
        monkeypatch.setattr(sm.psutil, "disk_usage",
                            lambda path: _Usage(free=53 * 1024 ** 3 + 500))
        assert _monitor()._read_disk_free_gb() == 53

    def test_a_relative_path_hangs_off_the_repo_root(self, monkeypatch):
        """Relativa al CWD, la métrica cambiaría según desde dónde se lanzó la app."""
        _monitored = _monitor(**{"system_monitor.disk_path": "data/dataset"})
        assert self._recorded_path(_monitored, monkeypatch) == os.path.normpath(
            os.path.join(sm._PROJECT_ROOT, "data", "dataset")
        )

    def test_the_default_is_the_repo_root(self, monkeypatch):
        assert self._recorded_path(_monitor(), monkeypatch) == sm._PROJECT_ROOT

    def test_an_absolute_path_is_used_as_is(self, monkeypatch, tmp_path):
        _monitored = _monitor(**{"system_monitor.disk_path": str(tmp_path)})
        assert self._recorded_path(_monitored, monkeypatch) == os.path.normpath(str(tmp_path))

    def test_the_real_partition_answers(self):
        """Sin mocks: la ruta por defecto existe en Windows y en la Jetson."""
        assert _monitor()._read_disk_free_gb() >= 0


# ── Red ──────────────────────────────────────────────────────────────────────

def _net_source(monkeypatch, *snapshots: dict) -> _Clock:
    """Encadena las lecturas de psutil.net_io_counters, una por llamada."""
    _pending = list(snapshots)
    monkeypatch.setattr(sm.psutil, "net_io_counters",
                        lambda pernic: _pending.pop(0) if _pending else {})
    _clock = _Clock()
    monkeypatch.setattr(sm.time, "monotonic", _clock)
    return _clock


class TestNetThroughput:
    def test_the_first_call_has_no_interval_to_divide(self, monkeypatch):
        _net_source(monkeypatch, {"eth0": _Counters(1_000, 2_000)})
        assert _monitor()._read_net_mbps() == {"eth0": {"rx_mbps": 0.0, "tx_mbps": 0.0}}

    def test_the_second_call_reports_the_difference(self, monkeypatch):
        _clock = _net_source(
            monkeypatch,
            {"eth0": _Counters(0, 0)},
            {"eth0": _Counters(int(10 * _MEGABIT), int(2 * _MEGABIT))},
        )
        _monitored = _monitor()
        _monitored._read_net_mbps()
        _clock.advance(1.0)
        assert _monitored._read_net_mbps() == {"eth0": {"rx_mbps": 10.0, "tx_mbps": 2.0}}

    def test_the_interval_is_measured(self, monkeypatch):
        """Los mismos bytes en 4 s son la cuarta parte de la tasa."""
        _clock = _net_source(
            monkeypatch,
            {"eth0": _Counters(0, 0)},
            {"eth0": _Counters(int(10 * _MEGABIT), 0)},
        )
        _monitored = _monitor()
        _monitored._read_net_mbps()
        _clock.advance(4.0)
        assert _monitored._read_net_mbps()["eth0"]["rx_mbps"] == 2.5

    def test_a_counter_reset_does_not_report_a_negative_rate(self, monkeypatch):
        _clock = _net_source(
            monkeypatch,
            {"eth0": _Counters(10_000_000, 10_000_000)},
            {"eth0": _Counters(1_000, 1_000)},      # la interfaz se reinició
        )
        _monitored = _monitor()
        _monitored._read_net_mbps()
        _clock.advance(1.0)
        assert _monitored._read_net_mbps() == {"eth0": {"rx_mbps": 0.0, "tx_mbps": 0.0}}

    def test_each_interface_is_independent(self, monkeypatch):
        _clock = _net_source(
            monkeypatch,
            {"eth0": _Counters(0, 0), "eth1": _Counters(0, 0)},
            {"eth0": _Counters(int(8 * _MEGABIT), 0),
             "eth1": _Counters(0, int(3 * _MEGABIT))},
        )
        _monitored = _monitor()
        _monitored._read_net_mbps()
        _clock.advance(1.0)
        assert _monitored._read_net_mbps() == {
            "eth0": {"rx_mbps": 8.0, "tx_mbps": 0.0},
            "eth1": {"rx_mbps": 0.0, "tx_mbps": 3.0},
        }

    def test_loopback_is_left_out_by_default(self, monkeypatch):
        _net_source(monkeypatch, {"lo": _Counters(0, 0), "eth0": _Counters(0, 0),
                                  "Loopback Pseudo-Interface 1": _Counters(0, 0)})
        assert list(_monitor()._read_net_mbps()) == ["eth0"]

    def test_the_config_selects_the_interfaces(self, monkeypatch):
        _net_source(monkeypatch, {"eth0": _Counters(0, 0), "eth1": _Counters(0, 0),
                                  "wlan0": _Counters(0, 0)})
        _monitored = _monitor(**{"system_monitor.net_interfaces": ["eth1"]})
        assert list(_monitored._read_net_mbps()) == ["eth1"]

    def test_an_interface_of_another_machine_is_skipped(self, monkeypatch, caplog):
        """'eth0' no existe en Windows y 'Ethernet' no existe en la Jetson."""
        _net_source(monkeypatch,
                    {"Ethernet": _Counters(0, 0)}, {"Ethernet": _Counters(0, 0)})
        _monitored = _monitor(**{"system_monitor.net_interfaces": ["eth0", "Ethernet"]})
        assert list(_monitored._read_net_mbps()) == ["Ethernet"]
        assert _logged(caplog, "'eth0' no existe") == 1

        _monitored._read_net_mbps()          # el aviso no se repite en cada vuelta
        assert _logged(caplog, "'eth0' no existe") == 1

    def test_an_interface_that_appears_starts_from_zero(self, monkeypatch):
        _clock = _net_source(
            monkeypatch,
            {"eth0": _Counters(0, 0)},
            {"eth0": _Counters(int(_MEGABIT), 0), "eth1": _Counters(999_999, 999_999)},
        )
        _monitored = _monitor()
        _monitored._read_net_mbps()
        _clock.advance(1.0)
        # eth1 recién apareció: su contador no es tráfico de este intervalo.
        assert _monitored._read_net_mbps()["eth1"] == {"rx_mbps": 0.0, "tx_mbps": 0.0}

    def test_an_interface_that_disappears_stops_being_reported(self, monkeypatch):
        _clock = _net_source(
            monkeypatch,
            {"eth0": _Counters(0, 0), "eth1": _Counters(0, 0)},
            {"eth0": _Counters(0, 0)},
        )
        _monitored = _monitor()
        _monitored._read_net_mbps()
        _clock.advance(1.0)
        assert list(_monitored._read_net_mbps()) == ["eth0"]

    def test_the_baseline_advances_on_every_call(self, monkeypatch):
        """La tasa es del último intervalo, no acumulada desde el arranque."""
        _clock = _net_source(
            monkeypatch,
            {"eth0": _Counters(0, 0)},
            {"eth0": _Counters(int(5 * _MEGABIT), 0)},
            {"eth0": _Counters(int(6 * _MEGABIT), 0)},
        )
        _monitored = _monitor()
        _monitored._read_net_mbps()
        _clock.advance(1.0)
        _monitored._read_net_mbps()
        _clock.advance(1.0)
        assert _monitored._read_net_mbps()["eth0"]["rx_mbps"] == 1.0


# ── Contrato de get_metrics ──────────────────────────────────────────────────

class TestGetMetrics:
    def test_the_real_machine_answers_every_key(self):
        """
        Sin mocks ni sondas desactivadas: el mismo contrato en Windows y en la
        Jetson, con las fuentes reales del equipo que corre el test.
        """
        _metrics = SystemMonitor(_MockConfig()).get_metrics()
        assert set(_metrics) == _EXPECTED_KEYS
        assert isinstance(_metrics["cpu_usage_pct"], int)
        assert isinstance(_metrics["net_mbps"], dict)
        assert isinstance(_metrics["temps_c"], dict)
        assert _metrics["ram_total_mb"] > 0
        assert 0 <= _metrics["cpu_usage_pct"] <= 100
        assert 0 <= _metrics["gpu_usage_pct"] <= 100
        # Lo que el equipo reporte tiene que ser una temperatura, no un valor crudo.
        for _temp_c in _metrics["temps_c"].values():
            assert sm._MIN_TEMP_C <= _temp_c <= sm._MAX_TEMP_C

    def test_the_keys_are_there_even_without_any_source(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sm, "_THERMAL_BASE", str(tmp_path / "missing"))
        monkeypatch.setattr(sm, "_GPU_LOAD_PATHS", ())
        monkeypatch.setattr(sm, "_POWER_GLOBS", ())
        _metrics = _monitor().get_metrics()
        assert set(_metrics) == _EXPECTED_KEYS
        assert _metrics["gpu_usage_pct"] == 0
        assert _metrics["power_w"] == 0
        assert _metrics["temps_c"] == {}

    def test_the_jetson_sources_feed_the_metrics(self, jetson, monkeypatch):
        _fake = _FakeProbe("37, 54, 22.11")
        monkeypatch.setattr(sm.subprocess, "run", _fake)
        jetson.monitor._nvidia_smi_path = "nvidia-smi"
        jetson.set_zones(("cpu-thermal", 45_500), ("gpu-thermal", 39_000))
        jetson.set_gpu_load(417)
        jetson.set_power(15_500)

        _metrics = jetson.monitor.get_metrics()
        assert _metrics["cpu_temp_c"] == 45
        assert _metrics["gpu_temp_c"] == 39
        assert _metrics["gpu_usage_pct"] == 41
        assert _metrics["power_w"] == 15.5
        assert _metrics["temps_c"] == {"cpu-thermal": 45.5, "gpu-thermal": 39.0}
        # Con sysfs alcanzando, no se paga un proceso por vuelta.
        assert _fake.calls == []

    def test_windows_falls_back_to_nvidia_smi_for_the_gpu(self, windows, monkeypatch):
        monkeypatch.setattr(sm.subprocess, "run", _FakeProbe("37, 54, 22.11"))
        _metrics = windows.get_metrics()
        assert _metrics["gpu_usage_pct"] == 37
        assert _metrics["gpu_temp_c"] == 54
        assert _metrics["power_w"] == 22

    def test_a_firmware_without_zones_reports_no_temperature(self, windows, monkeypatch):
        monkeypatch.setattr(sm.subprocess, "run", _FakeProbe(""))
        windows._disabled_probes.discard(sm._WMI_THERMAL)
        _metrics = windows.get_metrics()
        assert _metrics["cpu_temp_c"] == 0
        assert _metrics["temps_c"] == {}

    def test_the_windows_acpi_zone_feeds_the_cpu_temperature(self, windows, monkeypatch):
        """La única temperatura que Windows da sin permisos de administrador."""
        monkeypatch.setattr(sm.subprocess, "run",
                            _FakeProbe("\\_TZ.TZ00=3010\n\\_TZ.TZ01=3145"))
        windows._disabled_probes.discard(sm._WMI_THERMAL)
        windows._nvidia_smi_path = None         # equipo sin GPU NVIDIA, como el de dev
        _metrics = windows.get_metrics()
        assert _metrics["cpu_temp_c"] == 41
        # `temps_c` dice con qué sensor se midió: la zona del firmware, no el die.
        assert _metrics["temps_c"] == {"\\_TZ.TZ00": 27.9, "\\_TZ.TZ01": 41.4}
        assert _metrics["gpu_temp_c"] == 0

    def test_windows_without_an_nvidia_gpu_reports_zeros(self, monkeypatch):
        _monitored = _monitor()
        _monitored._is_linux = False
        _metrics = _monitored.get_metrics()
        assert _metrics["gpu_usage_pct"] == 0
        assert _metrics["gpu_temp_c"] == 0
        assert _metrics["power_w"] == 0

    def test_the_zones_are_read_once_per_call(self, jetson, monkeypatch):
        """La temperatura y `temps_c` salen de la misma lectura: sysfs se recorre una vez."""
        jetson.set_zones(("cpu-thermal", 45_000))
        _calls = []
        _real_listdir = sm.os.listdir

        def _counted_listdir(path: str) -> list:
            _calls.append(path)
            return _real_listdir(path)

        monkeypatch.setattr(sm.os, "listdir", _counted_listdir)
        jetson.monitor.get_metrics()
        assert _calls.count(str(jetson.thermal_path)) == 1

    def test_a_broken_source_does_not_drag_the_others(self, monkeypatch):
        def _fail() -> None:
            raise RuntimeError("psutil no pudo leer la memoria")

        monkeypatch.setattr(sm.psutil, "virtual_memory", _fail)
        _metrics = _monitor().get_metrics()
        assert _metrics["ram_used_mb"] == 0
        assert _metrics["ram_total_mb"] == 0
        assert _metrics["disk_free_gb"] >= 0
        assert isinstance(_metrics["cpu_usage_pct"], int)

    def test_nothing_propagates_when_every_source_fails(self, monkeypatch):
        """Ni al construirlo: el arranque de la app no se cae por una métrica."""
        def _fail(*args, **kwargs) -> None:
            raise OSError("el equipo no responde")

        for _name in ("cpu_percent", "virtual_memory", "disk_usage", "net_io_counters"):
            monkeypatch.setattr(sm.psutil, _name, _fail)
        _metrics = _monitor().get_metrics()
        assert set(_metrics) == _EXPECTED_KEYS
        assert _metrics["cpu_usage_pct"] == 0
        assert _metrics["net_mbps"] == {}

    def test_a_source_that_keeps_failing_warns_once(self, monkeypatch, caplog):
        """get_metrics corre en loop: el mismo aviso cada segundo tapa el log."""
        def _fail() -> None:
            raise RuntimeError("sin memoria")

        monkeypatch.setattr(sm.psutil, "virtual_memory", _fail)
        _monitored = _monitor()
        for _ in range(5):
            _monitored.get_metrics()
        assert _logged(caplog, "No se pudo leer la memoria") == 1

    def test_the_disk_path_is_read_at_each_call(self, monkeypatch, tmp_path):
        """El config cambia en caliente: cambiar de partición no exige reiniciar."""
        _paths = []
        monkeypatch.setattr(sm.psutil, "disk_usage",
                            lambda path: (_paths.append(path), _Usage(free=0))[1])
        _monitored = _monitor()
        _monitored.get_metrics()
        _monitored._config.set("system_monitor.disk_path", str(tmp_path))
        _monitored.get_metrics()
        assert _paths[0] != _paths[1]
        assert _paths[1] == str(tmp_path)

    def test_concurrent_calls_do_not_corrupt_the_state(self):
        """El contrato es un solo hilo de telemetría; el Lock cuida el estado igual."""
        _monitored = _monitor()
        _failures = []

        def _poll():
            try:
                for _ in range(10):
                    _monitored.get_metrics()
            except Exception as e:      # noqa: BLE001 - el test reporta cualquiera
                _failures.append(e)

        _threads = [threading.Thread(target=_poll) for _ in range(4)]
        for _thread in _threads:
            _thread.start()
        for _thread in _threads:
            _thread.join(timeout=30)
        assert _failures == []
