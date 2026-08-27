"""
Pestaña de dataset: la sección `image_collector:`.

`enabled` se prende y se apaga en caliente, pero el modo no: cambiarlo exige reiniciar
el recolector, y la nota al pie lo dice donde se lo elige.

Con la recolección apagada no se edita nada más: los campos se atenúan como en las otras
pestañas, porque un valor que se puede escribir mientras el subsistema está apagado
promete algo que no va a pasar.

Los dos intervalos tienen una condición más: sólo hacen algo en modo `interval`, así que
están habilitados únicamente cuando la recolección está prendida **y** el modo es ese.
"""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    add_check_row, add_form_row, add_hint_row, build_combo_box, build_group_box,
    build_spin_box, set_combo_value, wire_enable_toggle,
)

# Texto que ve el operador -> valor de `image_collector.mode`.
_MODES = {
    "col_mode_on_demand": "on_demand",
    "col_mode_interval":  "interval",
}
_MODE_INTERVAL = "interval"

_MAX_INTERVAL_S = 86400
_MAX_IMAGES = 1000000
_MAX_FREE_SPACE_GB = 1000
_MAX_HISTORY_LEN = 50
_MAX_HAMMING = 64


class CollectorTab(AbstractConfigTab):
    """Sección `image_collector:` del config. Ver el contrato en `abstract_tab.py`."""

    TITLE_KEY = "tab_collector"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._build_general_box())
        layout.addWidget(self._build_dedup_box())
        layout.addStretch()
        self._wire_enabled()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_general_box(self) -> QWidget:
        box, form = build_group_box(tr("col_box_general"))
        self._enabled = add_check_row(form, tr("field_enabled"), False)
        self._mode_combo = add_form_row(
            form, tr("col_mode"),
            build_combo_box([tr(key) for key in _MODES], tr("col_mode_on_demand")),
        )
        add_hint_row(form, tr("col_mode_note"))
        self._interval_min = add_form_row(
            form, tr("col_interval_min"), build_spin_box(1, _MAX_INTERVAL_S, 60)
        )
        self._interval_max = add_form_row(
            form, tr("col_interval_max"), build_spin_box(1, _MAX_INTERVAL_S, 600)
        )
        self._max_images = add_form_row(
            form, tr("col_max_images"), build_spin_box(1, _MAX_IMAGES, 10000)
        )
        self._min_free_space = add_form_row(
            form, tr("col_min_free_space"), build_spin_box(0, _MAX_FREE_SPACE_GB, 5)
        )
        self._save_original = add_check_row(form, tr("col_save_original"), True)
        self._save_annotated = add_check_row(form, tr("col_save_annotated"), False)
        self._save_json = add_check_row(form, tr("col_save_json"), True)
        return box

    def _build_dedup_box(self) -> QWidget:
        box, form = build_group_box(tr("col_box_dedup"))
        self._dedup_enabled = add_check_row(form, tr("field_enabled"), True)
        self._history_len = add_form_row(
            form, tr("col_history_len"), build_spin_box(1, _MAX_HISTORY_LEN, 5)
        )
        self._hamming = add_form_row(
            form, tr("col_hamming"), build_spin_box(0, _MAX_HAMMING, 5)
        )
        wire_enable_toggle(self._dedup_enabled, [self._history_len, self._hamming])
        return box

    def _wire_enabled(self):
        """
        Ata todo el bloque al checkbox de recolección activa.

        Se llama después de armar los dos grupos porque la cadena llega hasta los campos
        de dedup: `wire_enable_toggle` re-emite el `toggled` de un checkbox dependiente,
        así que apagar la recolección apaga dedup y, en cascada, sus dos campos.
        """
        wire_enable_toggle(self._enabled, [
            self._mode_combo, self._interval_min, self._interval_max,
            self._max_images, self._min_free_space,
            self._save_original, self._save_annotated, self._save_json,
            self._dedup_enabled,
        ])
        # Los intervalos dependen además del modo, y eso se resuelve después del toggle,
        # que es el que los deja habilitados sin mirar el modo.
        self._enabled.toggled.connect(self._refresh_interval_fields)
        self._mode_combo.currentTextChanged.connect(self._refresh_interval_fields)

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        self._enabled.setChecked(bool(self._config.get("image_collector.enabled", False)))
        mode = str(self._config.get("image_collector.mode", "on_demand"))
        set_combo_value(self._mode_combo, _get_mode_label(mode))
        self._interval_min.setValue(int(self._config.get("image_collector.interval_min_s", 60)))
        self._interval_max.setValue(int(self._config.get("image_collector.interval_max_s", 600)))
        self._max_images.setValue(int(self._config.get("image_collector.max_images", 10000)))
        self._min_free_space.setValue(
            int(self._config.get("image_collector.min_free_space_gb", 5))
        )
        self._save_original.setChecked(
            bool(self._config.get("image_collector.save_original", True))
        )
        self._save_annotated.setChecked(
            bool(self._config.get("image_collector.save_annotated", False))
        )
        self._save_json.setChecked(
            bool(self._config.get("image_collector.save_inference_json", True))
        )
        self._dedup_enabled.setChecked(
            bool(self._config.get("image_collector.dedup.enabled", True))
        )
        self._history_len.setValue(int(self._config.get("image_collector.dedup.history_len", 5)))
        self._hamming.setValue(
            int(self._config.get("image_collector.dedup.hamming_threshold", 5))
        )
        # `toggled` no se emite si el valor no cambió, así que la cascada se fuerza acá:
        # `load()` corre también al descartar y el estado tiene que quedar consistente.
        self._enabled.toggled.emit(self._enabled.isChecked())
        self._refresh_interval_fields()

    def save(self):
        self._config.set("image_collector.enabled", self._enabled.isChecked())
        self._config.set("image_collector.mode", _get_mode_value(self._mode_combo.currentText()))
        self._config.set("image_collector.interval_min_s", self._interval_min.value())
        self._config.set("image_collector.interval_max_s", self._interval_max.value())
        self._config.set("image_collector.max_images", self._max_images.value())
        self._config.set("image_collector.min_free_space_gb", self._min_free_space.value())
        self._config.set("image_collector.save_original", self._save_original.isChecked())
        self._config.set("image_collector.save_annotated", self._save_annotated.isChecked())
        self._config.set("image_collector.save_inference_json", self._save_json.isChecked())
        self._config.set("image_collector.dedup.enabled", self._dedup_enabled.isChecked())
        self._config.set("image_collector.dedup.history_len", self._history_len.value())
        self._config.set("image_collector.dedup.hamming_threshold", self._hamming.value())

    # ── Internos ─────────────────────────────────────────────────────────────

    def _refresh_interval_fields(self):
        """Los intervalos: sólo con la recolección prendida y el modo por intervalo."""
        is_usable = (self._enabled.isChecked()
                     and _get_mode_value(self._mode_combo.currentText()) == _MODE_INTERVAL)
        for spin in (self._interval_min, self._interval_max):
            spin.setEnabled(is_usable)


def _get_mode_label(mode: str) -> str:
    """Valor de `mode` -> texto que se muestra. Uno desconocido se muestra tal cual."""
    for key, value in _MODES.items():
        if value == mode:
            return tr(key)
    return mode


def _get_mode_value(mode_label: str) -> str:
    """Texto que se muestra -> valor de `mode`. Uno desconocido vuelve tal cual."""
    for key, value in _MODES.items():
        if tr(key) == mode_label:
            return value
    return mode_label
