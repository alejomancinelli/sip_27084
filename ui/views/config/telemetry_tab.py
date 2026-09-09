"""
Pestaña de telemetría: la sección `telemetry:` — InfluxDB y MQTT.

Las credenciales no están en el formulario porque no están en el config: el token de
InfluxDB sale de `INFLUXDB_TOKEN` y la password de MQTT de `MQTT_PASSWORD`. Las dos
notas al pie lo dicen en pantalla, que es donde alguien va a buscar el campo que falta.

El muestreo de la telemetría no está en esta pantalla: es un punto por segundo, fijo en
`main.py`, porque no hay instalación que quiera otro. La estructura de las series está en
`docs/influxdb.md`, y cómo sale esa misma estructura por MQTT en `docs/mqtt.md`.
"""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    add_check_row, add_form_row, add_hint_row, build_group_box, build_line_edit,
    build_spin_box, wire_enable_toggle,
)

_MAX_PORT = 65535


class TelemetryTab(AbstractConfigTab):
    """Sección `telemetry:` del config. Ver el contrato en `abstract_tab.py`."""

    TITLE_KEY = "tab_telemetry"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._build_influxdb_box())
        layout.addWidget(self._build_mqtt_box())
        layout.addStretch()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_influxdb_box(self) -> QWidget:
        box, form = build_group_box(tr("tel_box_influxdb"))
        self._influx_enabled = add_check_row(form, tr("field_enabled"), False)
        self._influx_url = add_form_row(
            form, tr("field_url"), build_line_edit("", "http://localhost:8086")
        )
        self._influx_org = add_form_row(form, tr("tel_org"), build_line_edit(""))
        self._influx_bucket = add_form_row(form, tr("tel_bucket"), build_line_edit(""))
        add_hint_row(form, tr("tel_influx_token_note"))
        wire_enable_toggle(self._influx_enabled, [
            self._influx_url, self._influx_org, self._influx_bucket,
        ])
        return box

    def _build_mqtt_box(self) -> QWidget:
        box, form = build_group_box(tr("tel_box_mqtt"))
        self._mqtt_enabled = add_check_row(form, tr("field_enabled"), False)
        self._mqtt_host = add_form_row(form, tr("field_host"), build_line_edit("", "192.168.0.20"))
        # 0 no es un puerto: es "el que corresponda según tls", y lo resuelve el backend.
        self._mqtt_port = add_form_row(form, tr("field_port"), build_spin_box(0, _MAX_PORT, 0))
        self._mqtt_tls = add_check_row(form, tr("tel_tls"), False)
        self._mqtt_user = add_form_row(form, tr("field_user"), build_line_edit(""))
        self._mqtt_topic_base = add_form_row(form, tr("tel_topic_base"), build_line_edit(""))
        add_hint_row(form, tr("tel_mqtt_password_note"))
        wire_enable_toggle(self._mqtt_enabled, [
            self._mqtt_host, self._mqtt_port, self._mqtt_tls,
            self._mqtt_user, self._mqtt_topic_base,
        ])
        return box

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        self._influx_enabled.setChecked(
            bool(self._config.get("telemetry.influxdb.enabled", False))
        )
        self._influx_url.setText(str(self._config.get("telemetry.influxdb.url", "") or ""))
        self._influx_org.setText(str(self._config.get("telemetry.influxdb.org", "") or ""))
        self._influx_bucket.setText(str(self._config.get("telemetry.influxdb.bucket", "") or ""))

        self._mqtt_enabled.setChecked(bool(self._config.get("telemetry.mqtt.enabled", False)))
        self._mqtt_host.setText(str(self._config.get("telemetry.mqtt.host", "") or ""))
        self._mqtt_port.setValue(int(self._config.get("telemetry.mqtt.port", 0)))
        self._mqtt_tls.setChecked(bool(self._config.get("telemetry.mqtt.tls", False)))
        self._mqtt_user.setText(str(self._config.get("telemetry.mqtt.user", "") or ""))
        self._mqtt_topic_base.setText(str(self._config.get("telemetry.mqtt.topic_base", "") or ""))

    def save(self):
        self._config.set("telemetry.influxdb.enabled", self._influx_enabled.isChecked())
        self._config.set("telemetry.influxdb.url", self._influx_url.text())
        self._config.set("telemetry.influxdb.org", self._influx_org.text())
        self._config.set("telemetry.influxdb.bucket", self._influx_bucket.text())

        self._config.set("telemetry.mqtt.enabled", self._mqtt_enabled.isChecked())
        self._config.set("telemetry.mqtt.host", self._mqtt_host.text())
        self._config.set("telemetry.mqtt.port", self._mqtt_port.value())
        self._config.set("telemetry.mqtt.tls", self._mqtt_tls.isChecked())
        self._config.set("telemetry.mqtt.user", self._mqtt_user.text())
        self._config.set("telemetry.mqtt.topic_base", self._mqtt_topic_base.text())
