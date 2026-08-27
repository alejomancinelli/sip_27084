"""
Vista de operador: la barra lateral genérica y un área central **vacía**.

El área central es lo más específico de cada instalación —qué se mira mientras la
línea trabaja— y por eso el template no la llena: el fork le pasa su widget con
`set_content()` y esta vista no lo conoce. Para el caso normal ya está armado
`ui/widgets/camera_grid.py`, que muestra las cámaras configuradas y no hace falta
escribir.

La barra lateral sí es genérica y viene puesta: el estado de los seis canales de
salida y el log de eventos. Se colapsa con la tira de la izquierda, que queda visible
siempre —afuera de la barra— para poder volver a abrirla.

Vista de sólo lectura: no edita config y no comanda nada. Lo que muestra se lo empujan
por sus slots.
"""

from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager

from ui import service_status
from ui.strings import tr
from ui.widgets.log_table import EventLogTable
from ui.widgets.status_chip import StatusChip

_SIDEBAR_MAX_WIDTH_PX = 480
_TOGGLE_WIDTH_PX = 18

# La flecha apunta a dónde va a ir la barra, no a dónde está: con la barra abierta
# señala hacia afuera —el borde derecho, donde se va a guardar— y con la barra cerrada
# señala hacia adentro, de donde va a volver.
_TOGGLE_CLOSE = "▶"
_TOGGLE_OPEN = "◀"


class MonitorView(QWidget):
    """
    Vista de operador. El área central la llena el fork con `set_content()`.

    La barra lateral se actualiza con `set_service_status()` y `log_event()`.
    """

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._service_chips: dict[str, StatusChip] = {}
        self._content: QWidget | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Área central: vacía a propósito. La llena el fork.
        self._content_area = QWidget()
        self._content_layout = QVBoxLayout(self._content_area)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._content_area, stretch=3)

        layout.addWidget(self._build_sidebar_toggle())
        layout.addWidget(self._build_sidebar(), stretch=1)

    # ── API pública ──────────────────────────────────────────────────────────

    def set_content(self, widget: QWidget):
        """
        Pone el widget del proyecto en el área central, en lugar del que hubiera.

        Es el punto de extensión de esta vista: la grilla de cámaras, los paneles de
        proceso del fork, o los dos dentro de un layout propio.
        """
        if self._content is not None:
            self._content_layout.removeWidget(self._content)
            self._content.deleteLater()
        self._content = widget
        self._content_layout.addWidget(widget)

    def set_service_status(self, service: str, status: str):
        """Actualiza el chip de un servicio. Un servicio desconocido se ignora."""
        chip = self._service_chips.get(service)
        if chip is None:
            return
        text, chip_state = service_status.describe(service, status)
        chip.set_state(chip_state, text)

    def log_event(self, level: str, message: str):
        """Agrega una línea al log que ve el operador."""
        self._event_log.append(level, message)

    def apply_theme(self, dark: bool):
        self._event_log.set_dark(dark)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _build_sidebar_toggle(self) -> QPushButton:
        self._sidebar_toggle = QPushButton(_TOGGLE_CLOSE)
        self._sidebar_toggle.setObjectName("sidebarToggle")
        self._sidebar_toggle.setFixedWidth(_TOGGLE_WIDTH_PX)
        self._sidebar_toggle.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._sidebar_toggle.setToolTip(tr("sidebar_tooltip"))
        self._sidebar_toggle.clicked.connect(self._on_toggle_clicked)
        return self._sidebar_toggle

    def _build_sidebar(self) -> QWidget:
        self._sidebar = QWidget()
        self._sidebar.setMaximumWidth(_SIDEBAR_MAX_WIDTH_PX)
        layout = QVBoxLayout(self._sidebar)
        layout.setContentsMargins(4, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._build_services_box())
        layout.addWidget(self._build_log_box(), stretch=1)
        return self._sidebar

    def _build_services_box(self) -> QWidget:
        box = QGroupBox(tr("monitor_services"))
        inner = QVBoxLayout(box)
        # Los seis canales, en el orden en que los declara `com_status`: es el mismo
        # orden en el que el PLC los lee.
        for service in service_status.SERVICES:
            chip = StatusChip()
            self._service_chips[service] = chip
            self.set_service_status(service, "")
            inner.addWidget(chip)
        inner.addStretch()
        return box

    def _build_log_box(self) -> QWidget:
        box = QGroupBox(tr("monitor_event_log"))
        inner = QVBoxLayout(box)
        inner.setContentsMargins(2, 8, 2, 2)
        self._event_log = EventLogTable()
        inner.addWidget(self._event_log)
        return box

    def _on_toggle_clicked(self):
        is_visible = not self._sidebar.isVisible()
        self._sidebar.setVisible(is_visible)
        self._sidebar_toggle.setText(_TOGGLE_CLOSE if is_visible else _TOGGLE_OPEN)
