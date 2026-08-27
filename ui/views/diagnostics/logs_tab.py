"""
Pestaña de logs: las últimas líneas de log con filtro por nivel.

No lee el archivo de log ni engancha un handler: recibe las líneas por `log_event()`.
Quien las publica es el cableado, que es el único que sabe qué handler del logger
alimenta a la GUI.
"""

from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.widgets.log_table import FILTER_ALL, LEVELS, EventLogTable

_MAX_ROWS = 200
_BUTTON_HEIGHT_PX = 24
_FILTER_PROPERTY = "selected"     # lo pinta el QSS: el filtro activo se ve distinto


class LogsTab(AbstractDiagnosticsTab):
    """Tabla de logs con botones de filtro. Ver el contrato en `abstract_tab.py`."""

    TITLE_KEY = "tab_logs"
    IS_CENTERED = False

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._filter_buttons: dict[str, QPushButton] = {}
        self._active_filter = FILTER_ALL

        self._table = EventLogTable(max_rows=_MAX_ROWS, show_module=True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addLayout(self._build_button_row())
        layout.addWidget(self._table)

    # ── API pública ──────────────────────────────────────────────────────────

    def log_event(self, level: str, message: str, module: str = ""):
        self._table.append(level, message, module)

    def apply_theme(self, dark: bool):
        super().apply_theme(dark)
        self._table.set_dark(dark)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        for level, text in [(FILTER_ALL, tr("diag_log_all"))] + [(lvl, lvl) for lvl in LEVELS]:
            button = QPushButton(text)
            button.setObjectName("logFilterButton")
            button.setFixedHeight(_BUTTON_HEIGHT_PX)
            button.clicked.connect(lambda _, chosen=level: self._set_filter(chosen))
            self._filter_buttons[level] = button
            row.addWidget(button)
        row.addStretch()

        clear_button = QPushButton(tr("diag_log_clear"))
        clear_button.setObjectName("clearButton")
        clear_button.setFixedHeight(_BUTTON_HEIGHT_PX)
        clear_button.clicked.connect(self._table.clear_log)
        row.addWidget(clear_button)

        self._refresh_filter_buttons()
        return row

    def _set_filter(self, level: str):
        self._active_filter = level
        self._table.set_filter(level)
        self._refresh_filter_buttons()

    def _refresh_filter_buttons(self):
        for level, button in self._filter_buttons.items():
            button.setProperty(_FILTER_PROPERTY, level == self._active_filter)
            button.style().unpolish(button)
            button.style().polish(button)
