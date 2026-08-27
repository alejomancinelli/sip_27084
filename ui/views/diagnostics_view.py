"""
Panel de diagnóstico: el cascarón que junta las pestañas y reparte lo que le llega.

No muestra nada por sí mismo. Recibe métricas, estados y logs y los reenvía a la
pestaña que corresponda; una pestaña que no conoce un servicio lo ignora, así que el
cableado publica todo en un solo lugar y no tiene que saber quién lo dibuja.

Agregar una pestaña de diagnóstico es un archivo en `ui/views/diagnostics/` y una
línea en `_TAB_CLASSES`.
"""

from PySide6.QtCore import Slot
from PySide6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.diagnostics.hardware_tab import HardwareTab
from ui.views.diagnostics.logs_tab import LogsTab
from ui.views.diagnostics.modbus_tab import ModbusTab
from ui.views.diagnostics.video_tab import VideoTab
from ui.widgets.form import wrap_in_card

# Pestañas en el orden en que se muestran.
_TAB_CLASSES = (HardwareTab, ModbusTab, VideoTab, LogsTab)


class DiagnosticsView(QWidget):
    """Panel de diagnóstico. Reparte a las pestañas lo que le empuja el cableado."""

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._tabs: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel(tr("diag_title"))
        title.setObjectName("viewTitle")
        layout.addWidget(title)

        tab_widget = QTabWidget()
        for tab_class in _TAB_CLASSES:
            tab = tab_class(self._config)
            self._tabs.append(tab)
            tab_widget.addTab(
                wrap_in_card(tab, center=tab.IS_CENTERED, scroll=tab.IS_SCROLLABLE),
                tr(tab.TITLE_KEY),
            )
        layout.addWidget(tab_widget, stretch=1)

    # ── API pública ──────────────────────────────────────────────────────────

    def set_service_status(self, service: str, status: str):
        """Reparte el estado de un servicio a las pestañas que lo muestran."""
        for tab in self._tabs:
            setter = getattr(tab, "set_service_status", None)
            if setter is not None:
                setter(service, status)

    def set_client_count(self, service: str, count: int):
        self._get_tab(VideoTab).set_client_count(service, count)

    def set_stream_urls(self, service: str, urls: list):
        self._get_tab(VideoTab).set_stream_urls(service, urls)

    def log_event(self, level: str, message: str, module: str = ""):
        self._get_tab(LogsTab).log_event(level, message, module)

    def apply_theme(self, dark: bool):
        for tab in self._tabs:
            tab.apply_theme(dark)

    # ── Slots de actualización ───────────────────────────────────────────────

    @Slot(object)
    def update_hardware_metrics(self, metrics: dict):
        """Slot para el dict de `SystemMonitor.get_metrics()`."""
        self._get_tab(HardwareTab).update_metrics(metrics)

    @Slot(object, str)
    def update_camera_status(self, status: dict, camera_slot: str):
        """Slot con la firma de `CaptureThread.status_updated`: (status, slot)."""
        self._get_tab(HardwareTab).update_camera_status(status, camera_slot)

    @Slot(object)
    def update_modbus_values(self, values: dict):
        """Slot para {dirección: valor} de los registros que se están publicando."""
        self._get_tab(ModbusTab).update_values(values)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _get_tab(self, tab_class: type):
        for tab in self._tabs:
            if isinstance(tab, tab_class):
                return tab
        raise KeyError(f"La pestaña {tab_class.__name__} no está en _TAB_CLASSES.")
