"""
Pestaña de video: estado de los dos servidores y las URLs que hay que abrir.

**Las URLs no se arman acá.** La UI no sabe la forma de una ruta de stream ni qué
interfaces tiene el equipo: las recibe hechas con `set_stream_urls()` desde el
cableado, que las pide a cada servidor. Si la UI las compusiera, el formato de la ruta
quedaría definido en dos lugares y un cambio en el servidor dejaría a la pantalla
mostrando direcciones que no existen.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager

from ui import service_status, theme
from ui.strings import tr
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.widgets.status_chip import CHIP_INFO, CHIP_OK, StatusChip

# Servicio -> clave del título de su grupo de URLs.
_STREAM_BOX_KEYS = {
    service_status.SERVICE_VIDEO_HTTP: "diag_streams_http",
    service_status.SERVICE_VIDEO_RTSP: "diag_streams_rtsp",
}


class VideoTab(AbstractDiagnosticsTab):
    """Estado, clientes y URLs de los servidores de video."""

    TITLE_KEY = "tab_video"
    IS_SCROLLABLE = True      # la lista de URLs crece con las cámaras

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._status_chips: dict[str, StatusChip] = {}
        self._client_chips: dict[str, StatusChip] = {}
        self._url_layouts: dict[str, QVBoxLayout] = {}
        self._urls: dict[str, list] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        for service in _STREAM_BOX_KEYS:
            layout.addLayout(self._build_chip_row(service))
            layout.addWidget(self._build_streams_box(service))
        layout.addStretch()

    # ── API pública ──────────────────────────────────────────────────────────

    def set_service_status(self, service: str, status: str):
        """Actualiza el chip de estado de un servidor. Ignora un servicio que no sea de video."""
        chip = self._status_chips.get(service)
        if chip is None:
            return
        text, chip_state = service_status.describe(service, status)
        chip.set_state(chip_state, text)

    def set_client_count(self, service: str, count: int):
        """Clientes conectados a ese servidor: en verde con uno o más, en gris con ninguno."""
        chip = self._client_chips.get(service)
        if chip is None:
            return
        chip.set_state(CHIP_OK if count > 0 else CHIP_INFO,
                       f"{tr('diag_clients')}: {count}")

    def set_stream_urls(self, service: str, urls: list):
        """URLs de los streams de ese servidor, ya armadas por quien las publica."""
        if service not in self._url_layouts:
            return
        self._urls[service] = list(urls)
        self._rebuild_urls(service)

    def apply_theme(self, dark: bool):
        super().apply_theme(dark)
        for service in self._url_layouts:
            self._rebuild_urls(service)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _build_chip_row(self, service: str) -> QHBoxLayout:
        status_chip = StatusChip()
        self._status_chips[service] = status_chip
        self.set_service_status(service, "")

        client_chip = StatusChip()
        self._client_chips[service] = client_chip
        self.set_client_count(service, 0)

        row = QHBoxLayout()
        row.addWidget(status_chip)
        row.addWidget(client_chip)
        row.addStretch()
        return row

    def _build_streams_box(self, service: str) -> QWidget:
        box = QGroupBox(tr(_STREAM_BOX_KEYS[service]))
        inner = QVBoxLayout(box)
        inner.setSpacing(6)
        self._url_layouts[service] = inner
        self._urls[service] = []
        self._rebuild_urls(service)
        return box

    def _rebuild_urls(self, service: str):
        inner = self._url_layouts[service]
        while inner.count():
            item = inner.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        urls = self._urls.get(service, [])
        if not urls:
            inner.addWidget(QLabel(tr("diag_streams_empty")))
            return

        palette = theme.get_palette(self._is_dark)
        url_style = (
            "font-family: monospace; padding: 4px 8px; border-radius: 4px; "
            f"background: {palette['url_background']}; color: {palette['url_text']};"
        )
        for url in urls:
            label = QLabel(str(url))
            # Estilo inline: el color de una URL depende del tema y hay un solo widget
            # que lo usa, así que no gana nada con un selector propio en el QSS.
            label.setStyleSheet(url_style)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            inner.addWidget(label)
