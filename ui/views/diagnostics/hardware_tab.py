"""
Pestaña de hardware: las métricas del equipo y los FPS de cada cámara.

Consume el dict de `SystemMonitor.get_metrics()` tal como sale, con sus claves
estables. Nada se calcula acá: si hace falta un número nuevo lo agrega el monitor, que
es el dueño de la medición.

Dos cosas se arman desde los datos y no a mano: los chips de red salen de las
interfaces que el monitor informa en `net_mbps` —el equipo de planta no siempre tiene
eth0 y eth1—, y los de FPS salen de los slots de `cameras:`.
"""

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QVBoxLayout, QWidget

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.widgets.realtime_chart import RealtimeChart
from ui.widgets.status_chip import CHIP_INFO, StatusChip

_CHART_POINTS = 60
_DEFAULT_RAM_MAX_MB = 8192
_MAX_TEMP_C = 110


class HardwareTab(AbstractDiagnosticsTab):
    """Métricas de hardware en vivo. Ver el contrato en `abstract_tab.py`."""

    TITLE_KEY = "tab_hardware"
    IS_CENTERED = False
    IS_SCROLLABLE = True      # cuatro gráficas y dos grupos apilados no entran

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)

        self._metric_chips = {
            "cpu_usage_pct": StatusChip("CPU: ---"),
            "gpu_usage_pct": StatusChip("GPU: ---"),
            "ram_used_mb":   StatusChip("RAM: ---"),
            "disk_free_gb":  StatusChip("Disco: ---"),
            "cpu_temp_c":    StatusChip("T.CPU: ---"),
            "gpu_temp_c":    StatusChip("T.GPU: ---"),
            "power_w":       StatusChip("Potencia: ---"),
        }
        for chip in self._metric_chips.values():
            chip.set_state(CHIP_INFO)

        self._charts = {
            "cpu_usage_pct": RealtimeChart(tr("diag_chart_cpu"), max_points=_CHART_POINTS,
                                           y_max=100, series_role="cpu"),
            "gpu_usage_pct": RealtimeChart(tr("diag_chart_gpu"), max_points=_CHART_POINTS,
                                           y_max=100, series_role="gpu"),
            "ram_used_mb":   RealtimeChart(tr("diag_chart_ram"), max_points=_CHART_POINTS,
                                           y_max=_DEFAULT_RAM_MAX_MB, series_role="ram"),
            "cpu_temp_c":    RealtimeChart(tr("diag_chart_cpu_temp"), max_points=_CHART_POINTS,
                                           y_max=_MAX_TEMP_C, series_role="cpu_temp"),
        }

        self._camera_chips: dict[str, StatusChip] = {}
        self._net_chips: dict[str, StatusChip] = {}

        chip_row = QHBoxLayout()
        for chip in self._metric_chips.values():
            chip_row.addWidget(chip)
        chip_row.addStretch()

        chart_row = QHBoxLayout()
        chart_row.addWidget(self._charts["ram_used_mb"])
        chart_row.addWidget(self._charts["cpu_temp_c"])

        layout = QVBoxLayout(self)
        layout.addLayout(chip_row)
        layout.addWidget(self._charts["cpu_usage_pct"])
        layout.addWidget(self._charts["gpu_usage_pct"])
        layout.addLayout(chart_row)
        layout.addWidget(self._build_cameras_box())
        layout.addWidget(self._build_network_box())

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_cameras_box(self) -> QWidget:
        box = QGroupBox(tr("diag_box_cameras"))
        inner = QHBoxLayout(box)
        for camera_slot in (self._config.get("cameras", {}) or {}):
            name = str(self._config.get(f"cameras.{camera_slot}.name", "") or camera_slot)
            chip = StatusChip(f"{name}: --- FPS")
            chip.set_state(CHIP_INFO)
            self._camera_chips[camera_slot] = chip
            inner.addWidget(chip)
        inner.addStretch()
        return box

    def _build_network_box(self) -> QWidget:
        self._network_box = QGroupBox(tr("diag_box_network"))
        self._network_layout = QHBoxLayout(self._network_box)
        self._network_layout.addStretch()
        return self._network_box

    # ── Slots de actualización ───────────────────────────────────────────────

    @Slot(object)
    def update_metrics(self, metrics: dict):
        """Slot para el dict de `SystemMonitor.get_metrics()`."""
        self._metric_chips["cpu_usage_pct"].setText(f"CPU: {metrics.get('cpu_usage_pct', 0)} %")
        self._metric_chips["gpu_usage_pct"].setText(f"GPU: {metrics.get('gpu_usage_pct', 0)} %")
        self._metric_chips["disk_free_gb"].setText(f"Disco: {metrics.get('disk_free_gb', 0)} GB")
        self._metric_chips["cpu_temp_c"].setText(f"T.CPU: {metrics.get('cpu_temp_c', 0)} °C")
        self._metric_chips["gpu_temp_c"].setText(f"T.GPU: {metrics.get('gpu_temp_c', 0)} °C")
        self._metric_chips["power_w"].setText(f"Potencia: {metrics.get('power_w', 0)} W")

        ram_used_mb = metrics.get("ram_used_mb", 0)
        ram_total_mb = metrics.get("ram_total_mb", 0)
        self._metric_chips["ram_used_mb"].setText(f"RAM: {ram_used_mb} / {ram_total_mb} MB")
        if ram_total_mb:
            self._charts["ram_used_mb"].set_y_max(ram_total_mb)

        for key, chart in self._charts.items():
            chart.push(metrics.get(key, 0))

        self._update_network_chips(metrics.get("net_mbps", {}) or {})

    @Slot(object, str)
    def update_camera_status(self, status: dict, camera_slot: str):
        """Slot con la firma de `CaptureThread.status_updated`: (status, slot)."""
        chip = self._camera_chips.get(camera_slot)
        if chip is None:
            return
        name = str(self._config.get(f"cameras.{camera_slot}.name", "") or camera_slot)
        chip.setText(f"{name}: {status.get('fps_estimated', 0.0):.1f} FPS")

    def apply_theme(self, dark: bool):
        super().apply_theme(dark)
        for chart in self._charts.values():
            chart.set_dark(dark)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _update_network_chips(self, net_mbps: dict):
        """
        Un chip por interfaz que informa el monitor, creado la primera vez que aparece.

        Se crean acá y no en el constructor porque cuáles hay depende del equipo y de
        `system_monitor.net_interfaces`, y eso se sabe con la primera medición.
        """
        for iface, throughput in sorted(net_mbps.items()):
            chip = self._net_chips.get(iface)
            if chip is None:
                chip = StatusChip()
                chip.set_state(CHIP_INFO)
                self._net_chips[iface] = chip
                self._network_layout.insertWidget(self._network_layout.count() - 1, chip)
            chip.setText(
                f"{iface}: {throughput.get('rx_mbps', 0.0):.1f} rx / "
                f"{throughput.get('tx_mbps', 0.0):.1f} tx"
            )
