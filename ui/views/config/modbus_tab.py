"""
Pestaña de Modbus: la sección `modbus:` — transporte y datastore, no el mapa.

El mapa de registros no se edita desde acá y la nota al pie lo dice: vive en
`system/modbus/register_map.yaml`, que es fuente única, y la tabla del integrador se
genera con `python -m system.modbus.export_map`. Un mapa editable desde la UI serían
dos verdades sobre la misma dirección.

`register_count` se muestra pero no se edita: cuántos registros expone el datastore lo
decide el tamaño del mapa, no una preferencia de planta. Al lado se informa hasta qué
registro llega el mapa cargado, y si el mapa se pasa de la cuenta expuesta el aviso lo
dice: esos registros no se publicarían y el PLC leería ceros sin que nada falle.
"""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from system.modbus.registers import SCHEMA

from ui.widgets.form import (
    add_check_row, add_form_row, add_hint_row, build_combo_box, build_group_box,
    build_line_edit, build_readonly_line_edit, build_spin_box, set_combo_value,
    wire_enable_toggle,
)

_PARITIES = ("N", "E", "O")
_STOP_BITS = ("1", "2")

_MAX_PORT = 65535
_MAX_SLAVE_ID = 247
_MIN_BAUDRATE = 1200
_MAX_BAUDRATE = 921600


class ModbusTab(AbstractConfigTab):
    """Sección `modbus:` del config. Ver el contrato en `abstract_tab.py`."""

    TITLE_KEY = "tab_modbus"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._build_general_box())
        layout.addWidget(self._build_tcp_box())
        layout.addWidget(self._build_rtu_box())
        layout.addStretch()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_general_box(self) -> QWidget:
        box, form = build_group_box(tr("mb_box_general"))
        self._slave_id = add_form_row(
            form, tr("mb_slave_id"), build_spin_box(1, _MAX_SLAVE_ID, 1)
        )
        self._register_count = add_form_row(
            form, tr("mb_register_count"), build_readonly_line_edit("")
        )
        self._map_extent = add_hint_row(form, "")
        add_hint_row(form, tr("mb_map_note"))
        return box

    def _build_tcp_box(self) -> QWidget:
        box, form = build_group_box(tr("mb_box_tcp"))
        self._tcp_enabled = add_check_row(form, tr("field_enabled"), True)
        self._tcp_host = add_form_row(form, tr("field_host"), build_line_edit("", "0.0.0.0"))
        self._tcp_port = add_form_row(form, tr("field_port"), build_spin_box(1, _MAX_PORT, 502))
        wire_enable_toggle(self._tcp_enabled, [self._tcp_host, self._tcp_port])
        return box

    def _build_rtu_box(self) -> QWidget:
        box, form = build_group_box(tr("mb_box_rtu"))
        self._rtu_enabled = add_check_row(form, tr("field_enabled"), False)
        self._rtu_port = add_form_row(
            form, tr("mb_serial_port"), build_line_edit("", "COM1 / /dev/ttyS0")
        )
        self._rtu_baudrate = add_form_row(
            form, tr("mb_baudrate"), build_spin_box(_MIN_BAUDRATE, _MAX_BAUDRATE, 115200)
        )
        self._rtu_parity = add_form_row(
            form, tr("mb_parity"), build_combo_box(list(_PARITIES), "N")
        )
        self._rtu_stop_bits = add_form_row(
            form, tr("mb_stop_bits"), build_combo_box(list(_STOP_BITS), "1")
        )
        wire_enable_toggle(self._rtu_enabled, [
            self._rtu_port, self._rtu_baudrate, self._rtu_parity, self._rtu_stop_bits,
        ])
        return box

    def _refresh_register_count(self):
        """Muestra los registros expuestos y hasta dónde llega el mapa cargado."""
        count = int(self._config.get("modbus.register_count", 100))
        max_addr = SCHEMA.max_addr()
        self._register_count.setText(str(count))
        is_overflow = max_addr > count
        self._map_extent.setText(
            tr("mb_map_overflow" if is_overflow else "mb_map_extent").format(
                max_addr=max_addr, count=count)
        )
        # El aviso de desborde se pinta como advertencia; el informativo, como nota.
        self._map_extent.setObjectName("warningBanner" if is_overflow else "hintLabel")
        self._map_extent.style().unpolish(self._map_extent)
        self._map_extent.style().polish(self._map_extent)

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        self._slave_id.setValue(int(self._config.get("modbus.slave_id", 1)))
        self._refresh_register_count()

        self._tcp_enabled.setChecked(bool(self._config.get("modbus.tcp.enabled", True)))
        self._tcp_host.setText(str(self._config.get("modbus.tcp.host", "0.0.0.0")))
        self._tcp_port.setValue(int(self._config.get("modbus.tcp.port", 502)))

        self._rtu_enabled.setChecked(bool(self._config.get("modbus.rtu.enabled", False)))
        self._rtu_port.setText(str(self._config.get("modbus.rtu.port", "") or ""))
        self._rtu_baudrate.setValue(int(self._config.get("modbus.rtu.baudrate", 115200)))
        set_combo_value(self._rtu_parity, self._config.get("modbus.rtu.parity", "N"))
        set_combo_value(self._rtu_stop_bits, self._config.get("modbus.rtu.stop_bits", 1))

    def save(self):
        self._config.set("modbus.slave_id", self._slave_id.value())
        # `register_count` no se guarda: es de sólo lectura y su dueño es el mapa.

        self._config.set("modbus.tcp.enabled", self._tcp_enabled.isChecked())
        self._config.set("modbus.tcp.host", self._tcp_host.text())
        self._config.set("modbus.tcp.port", self._tcp_port.value())

        self._config.set("modbus.rtu.enabled", self._rtu_enabled.isChecked())
        self._config.set("modbus.rtu.port", self._rtu_port.text())
        self._config.set("modbus.rtu.baudrate", self._rtu_baudrate.value())
        self._config.set("modbus.rtu.parity", self._rtu_parity.currentText())
        self._config.set("modbus.rtu.stop_bits", int(self._rtu_stop_bits.currentText()))
