"""
Pestaña de Modbus: la tabla de holding registers que publica el equipo.

Las filas salen de `SCHEMA.descriptions()`, que es el mapa único cargado desde
`register_map.yaml`: agregar o cambiar un registro allá se refleja acá solo, y no hay
una sola dirección escrita en este archivo. Los valores los empuja quien llena los
registros, con `update_values()`.
"""

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from system.config_manager import ConfigManager
from system.modbus.registers import SCHEMA
from system.modbus.schema import to_plc_address

from ui import service_status
from ui.strings import tr
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.widgets.status_chip import StatusChip

_ROW_HEIGHT_PX = 26
_COLUMN_VALUE = 2
_EMPTY_VALUE = "---"


class ModbusTab(AbstractDiagnosticsTab):
    """Estado de los dos transportes y la tabla de registros."""

    TITLE_KEY = "tab_modbus"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._row_by_address: dict[int, int] = {}

        self._chips = {
            service_status.SERVICE_MODBUS_TCP: StatusChip(),
            service_status.SERVICE_MODBUS_RTU: StatusChip(),
        }
        chip_row = QHBoxLayout()
        for service, chip in self._chips.items():
            self.set_service_status(service, "")
            chip_row.addWidget(chip)
        chip_row.addStretch()

        note = QLabel(tr("diag_modbus_table_note"))
        note.setObjectName("hintLabel")

        layout = QVBoxLayout(self)
        layout.addLayout(chip_row)
        layout.addWidget(note)
        layout.addWidget(self._build_table())

    # ── API pública ──────────────────────────────────────────────────────────

    def set_service_status(self, service: str, status: str):
        """Actualiza el chip de uno de los dos transportes. Ignora otro servicio."""
        chip = self._chips.get(service)
        if chip is None:
            return
        text, chip_state = service_status.describe(service, status)
        chip.set_state(chip_state, text)

    @Slot(object)
    def update_values(self, values: dict):
        """Slot para {dirección: valor}. Las direcciones que no están en el mapa se ignoran."""
        for address, value in values.items():
            row = self._row_by_address.get(int(address))
            if row is None:
                continue
            item = self._table.item(row, _COLUMN_VALUE)
            if item is not None:
                item.setText(str(value))

    # ── Internos ─────────────────────────────────────────────────────────────

    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([
            tr("diag_modbus_register"), tr("diag_modbus_desc"), tr("diag_modbus_value"),
        ])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT_PX)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        for address, description in sorted(SCHEMA.descriptions().items()):
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._row_by_address[address] = row
            for column, text in enumerate(
                    [to_plc_address(address), description, _EMPTY_VALUE]):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled)
                self._table.setItem(row, column, item)
        return self._table
