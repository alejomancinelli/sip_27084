"""
Tabla de eventos: las últimas N líneas de log, la más nueva arriba.

Buffer FIFO propio: la tabla se reconstruye desde él, así que cambiar de tema o de
filtro no pierde historia. Los colores por nivel salen de `ui/theme.py`, porque el
QSS no llega a los items de una QTableWidget.
"""

from collections import deque
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem,
)

from ui import theme
from ui.strings import tr

# Niveles que la tabla sabe colorear. Uno que no esté se muestra con el estilo de INFO.
LEVELS = ("INFO", "WARNING", "ERROR")
FILTER_ALL = "ALL"

_ROW_HEIGHT_PX = 26
_TIMESTAMP_FORMAT = "%d/%m %H:%M:%S"


class EventLogTable(QTableWidget):
    """
    Tabla de eventos FIFO de hasta `max_rows` filas.

    Con `show_module=True` agrega la columna del módulo entre la hora y el nivel, que
    es lo que quiere la pestaña de logs; la barra lateral del monitor la deja afuera
    porque no tiene ancho.
    """

    def __init__(self, parent=None, *, max_rows: int = 20, show_module: bool = False):
        super().__init__(0, 4 if show_module else 3, parent)
        self._entries: deque = deque(maxlen=max_rows)
        self._show_module = show_module
        self._filter = ""
        self._level_colors = theme.get_log_level_colors(True)

        self._build_headers()
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT_PX)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(False)
        self.setShowGrid(False)
        self.setWordWrap(False)

    # ── API pública ──────────────────────────────────────────────────────────

    def append(self, level: str, message: str, module: str = ""):
        """Agrega una entrada. `level` es uno de LEVELS; otro se pinta como INFO."""
        timestamp = datetime.now().strftime(_TIMESTAMP_FORMAT)
        self._entries.append((timestamp, level.upper(), message, module))
        self._rebuild()

    def set_filter(self, level: str):
        """Muestra sólo ese nivel. FILTER_ALL o vacío muestra todo."""
        self._filter = "" if not level or level == FILTER_ALL else level.upper()
        self._rebuild()

    def clear_log(self):
        self._entries.clear()
        self.setRowCount(0)

    def set_dark(self, dark: bool):
        self._level_colors = theme.get_log_level_colors(dark)
        self._rebuild()

    # ── Internos ─────────────────────────────────────────────────────────────

    def _build_headers(self):
        headers = [tr("diag_log_time")]
        if self._show_module:
            headers.append(tr("diag_log_module"))
        headers += [tr("diag_log_level"), tr("diag_log_message")]
        self.setHorizontalHeaderLabels(headers)
        header = self.horizontalHeader()
        for column in range(len(headers) - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)

    def _rebuild(self):
        self.setRowCount(0)
        for timestamp, level, message, module in reversed(self._entries):
            if self._filter and level != self._filter:
                continue
            row = self.rowCount()
            self.insertRow(row)
            foreground, background = self._level_colors.get(level, self._level_colors["INFO"])
            columns = [timestamp, module, level, message] if self._show_module else \
                      [timestamp, level, message]
            for column, text in enumerate(columns):
                item = QTableWidgetItem(text)
                item.setForeground(foreground)
                item.setBackground(background)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.setItem(row, column, item)
