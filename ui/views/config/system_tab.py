"""
Pestaña de sistema: las secciones `project:`, `system:`, `ui:` y `system_monitor:`.

`project:` y el nombre de la aplicación se muestran de sólo lectura: identifican la
instalación y se escriben una vez, al armar el fork. Cambiarlos desde la UI de planta
rompería la correspondencia entre el equipo y sus datos históricos —y el nombre, además,
es el que aparece en el título de la ventana y en el header, que se arman al iniciar—.

El idioma y el tema son las dos únicas claves que la UI aplica sin reiniciar la app:
las emite el ConfigView y las toma la ventana principal.
"""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from system.config_manager import ConfigManager

from ui.strings import LANGUAGES, tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    add_check_row, add_form_row, build_combo_box, build_group_box, build_line_edit,
    build_readonly_line_edit, join_list, set_combo_value, split_list,
)

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class SystemTab(AbstractConfigTab):
    """Secciones `project:`, `system:`, `ui:` y `system_monitor:` del config."""

    TITLE_KEY = "tab_system"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._build_project_box())
        layout.addWidget(self._build_general_box())
        layout.addWidget(self._build_interface_box())
        layout.addWidget(self._build_monitor_box())
        layout.addStretch()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_project_box(self) -> QWidget:
        box, form = build_group_box(tr("sys_box_project"))
        self._client = add_form_row(form, tr("sys_client"), build_readonly_line_edit(""))
        self._project_id = add_form_row(form, tr("sys_project_id"), build_readonly_line_edit(""))
        return box

    def _build_general_box(self) -> QWidget:
        box, form = build_group_box(tr("sys_box_general"))
        self._app_name = add_form_row(form, tr("sys_app_name"), build_readonly_line_edit(""))
        self._device_id = add_form_row(form, tr("sys_device_id"), build_line_edit(""))
        self._log_level = add_form_row(
            form, tr("sys_log_level"), build_combo_box(list(_LOG_LEVELS), "INFO")
        )
        self._logs_path = add_form_row(form, tr("sys_logs_path"), build_line_edit(""))
        self._dataset_path = add_form_row(form, tr("sys_dataset_path"), build_line_edit(""))
        return box

    def _build_interface_box(self) -> QWidget:
        box, form = build_group_box(tr("sys_box_interface"))
        self._language = add_form_row(
            form, tr("sys_language"), build_combo_box(list(LANGUAGES), "es")
        )
        self._dark_mode = add_check_row(form, tr("sys_dark_mode"), True)
        return box

    def _build_monitor_box(self) -> QWidget:
        box, form = build_group_box(tr("sys_box_monitor"))
        self._disk_path = add_form_row(form, tr("sys_disk_path"), build_line_edit("", "."))
        self._net_interfaces = add_form_row(
            form, tr("sys_net_interfaces"), build_line_edit("", "eth0, eth1")
        )
        return box

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        self._client.setText(str(self._config.get("project.client", "") or ""))
        self._project_id.setText(str(self._config.get("project.project_id", "") or ""))

        self._app_name.setText(str(self._config.get("system.app_name", "") or ""))
        self._device_id.setText(str(self._config.get("system.device_id", "") or ""))
        set_combo_value(self._log_level, self._config.get("system.log_level", "INFO"))
        self._logs_path.setText(str(self._config.get("system.paths.logs", "") or ""))
        self._dataset_path.setText(str(self._config.get("system.paths.dataset", "") or ""))

        set_combo_value(self._language, self._config.get("ui.language", "es"))
        self._dark_mode.setChecked(bool(self._config.get("ui.dark_mode", True)))

        self._disk_path.setText(str(self._config.get("system_monitor.disk_path", ".") or "."))
        self._net_interfaces.setText(
            join_list(self._config.get("system_monitor.net_interfaces", []))
        )

    def save(self):
        # `app_name` no se guarda: es de sólo lectura.
        self._config.set("system.device_id", self._device_id.text())
        self._config.set("system.log_level", self._log_level.currentText())
        self._config.set("system.paths.logs", self._logs_path.text())
        self._config.set("system.paths.dataset", self._dataset_path.text())

        self._config.set("ui.language", self._language.currentText())
        self._config.set("ui.dark_mode", self._dark_mode.isChecked())

        self._config.set("system_monitor.disk_path", self._disk_path.text())
        self._config.set("system_monitor.net_interfaces",
                         split_list(self._net_interfaces.text()))
