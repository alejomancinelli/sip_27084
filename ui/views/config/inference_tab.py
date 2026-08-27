"""
Pestaña de inferencia: `inference.models`, `inference.pipelines` e `inference.overlay`.

Un grupo por slot de modelo y otro por slot de pipeline, sacados del config: los slots
son fijos por proyecto, igual que las cámaras.

**El orden de las etapas no está acá y no puede estarlo**: se programa en
`system/inference/pipeline.py`, porque encadenar dos modelos es lógica del proceso. De
esta pestaña sale de dónde se carga cada modelo y qué cámaras entran a cada pipeline.

Los tipos de modelo que ofrece el combo salen de `REGISTERED_MODELS`, así que un fork
que registra su modelo en la fábrica lo ve acá sin tocar la UI.

Tres claves de la sección no están en esta pantalla, y no es un olvido:

  - **`device`** no existe más: el modelo resuelve solo si hay CUDA disponible. Elegirlo
    a mano desde la planta sólo servía para dejar la inferencia en CPU sin darse cuenta.
  - **`frames_per_cycle`** no lo lee el motor —emite un resultado por frame y no promedia—:
    lo usa quien orquesta el ciclo de medición. Mostrarlo acá invita a cambiarlo esperando
    un efecto que no va a pasar.
  - **Las cuatro banderas de dibujado que van siempre** (cajas, máscaras, etiquetas y ROI):
    ver la nota del grupo de anotado.

Las claves siguen funcionando si un proyecto las agrega al config.yaml; lo que se saca es
el control de planta, no la capacidad.
"""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from system.inference.model_factory import REGISTERED_MODELS

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    add_check_row, add_form_row, add_hint_row, build_combo_box, build_double_spin_box,
    build_group_box, build_line_edit, build_spin_box, join_list, set_combo_value,
    split_list,
)

_MAX_SIDE_PX = 8192
_MAX_DETECTIONS = 10000
_MAX_INTERVAL_S = 3600.0
_MAX_FONT_SCALE = 4.0
_MAX_THICKNESS_PX = 10


class InferenceTab(AbstractConfigTab):
    """Sección `inference:` del config, menos el orden de las etapas."""

    TITLE_KEY = "tab_inference"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._model_forms: dict[str, dict] = {}
        self._pipeline_forms: dict[str, dict] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        for model_slot in (self._config.get("inference.models", {}) or {}):
            layout.addWidget(self._build_model_box(model_slot))
        for pipeline_slot in (self._config.get("inference.pipelines", {}) or {}):
            layout.addWidget(self._build_pipeline_box(pipeline_slot))
        layout.addWidget(self._build_overlay_box())
        layout.addStretch()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_model_box(self, model_slot: str) -> QWidget:
        box, form = build_group_box(f"{tr('inf_box_model')} — {model_slot}")
        fields = {
            "type": add_form_row(form, tr("inf_model_type"),
                                 build_combo_box(list(REGISTERED_MODELS), "")),
            "path": add_form_row(form, tr("inf_model_path"),
                                 build_line_edit("", "models/model.pt")),
            "min_confidence_pct": add_form_row(form, tr("inf_min_confidence"),
                                               build_spin_box(0, 100, 50)),
            "max_side_px": add_form_row(form, tr("inf_max_side"),
                                        build_spin_box(0, _MAX_SIDE_PX, 0)),
            "class_names": add_form_row(form, tr("inf_class_names"),
                                        build_line_edit("", "ok, nok")),
        }
        self._model_forms[model_slot] = fields
        return box

    def _build_pipeline_box(self, pipeline_slot: str) -> QWidget:
        box, form = build_group_box(f"{tr('inf_box_pipeline')} — {pipeline_slot}")
        fields = {
            "enabled": add_check_row(form, tr("field_enabled"), True),
            "cameras": add_form_row(form, tr("inf_pipeline_cameras"),
                                    build_line_edit("", "camera_1, camera_2")),
            "interval_s": add_form_row(
                form, tr("inf_pipeline_interval"),
                build_double_spin_box(0.0, _MAX_INTERVAL_S, 0.0, decimals=2, step=0.5),
            ),
            "min_detections": add_form_row(form, tr("inf_min_detections"),
                                           build_spin_box(0, _MAX_DETECTIONS, 0)),
            "min_result_confidence_pct": add_form_row(form, tr("inf_min_result_conf"),
                                                      build_spin_box(0, 100, 0)),
        }
        self._pipeline_forms[pipeline_slot] = fields
        return box

    def _build_overlay_box(self) -> QWidget:
        box, form = build_group_box(tr("inf_box_overlay"))
        self._overlay_enabled = add_check_row(form, tr("field_enabled"), True)
        # Sólo las dos que un operador realmente apaga. Las otras cuatro van siempre:
        # el motor las sigue leyendo del config, así que un proyecto que necesite
        # apagarlas puede, pero no desde la pantalla de planta.
        self._overlay_checks = {
            "draw_summary":   add_check_row(form, tr("inf_draw_summary"), True),
            "draw_timestamp": add_check_row(form, tr("inf_draw_timestamp"), True),
        }
        self._font_scale = add_form_row(
            form, tr("inf_font_scale"),
            build_double_spin_box(0.1, _MAX_FONT_SCALE, 0.6, decimals=2, step=0.1),
        )
        self._thickness = add_form_row(
            form, tr("inf_thickness"), build_spin_box(1, _MAX_THICKNESS_PX, 2)
        )
        add_hint_row(form, tr("inf_overlay_note"))
        return box

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        for model_slot, fields in self._model_forms.items():
            prefix = f"inference.models.{model_slot}"
            set_combo_value(fields["type"], self._config.get(f"{prefix}.type", "mock"))
            fields["path"].setText(str(self._config.get(f"{prefix}.path", "") or ""))
            fields["min_confidence_pct"].setValue(
                int(self._config.get(f"{prefix}.min_confidence_pct", 50))
            )
            fields["max_side_px"].setValue(int(self._config.get(f"{prefix}.max_side_px", 0)))
            fields["class_names"].setText(join_list(self._config.get(f"{prefix}.class_names", [])))

        for pipeline_slot, fields in self._pipeline_forms.items():
            prefix = f"inference.pipelines.{pipeline_slot}"
            fields["enabled"].setChecked(bool(self._config.get(f"{prefix}.enabled", True)))
            fields["cameras"].setText(join_list(self._config.get(f"{prefix}.cameras", [])))
            fields["interval_s"].setValue(float(self._config.get(f"{prefix}.interval_s", 0.0)))
            fields["min_detections"].setValue(
                int(self._config.get(f"{prefix}.min_detections", 0))
            )
            fields["min_result_confidence_pct"].setValue(
                int(self._config.get(f"{prefix}.min_result_confidence_pct", 0))
            )

        self._overlay_enabled.setChecked(bool(self._config.get("inference.overlay.enabled", True)))
        for key, check in self._overlay_checks.items():
            check.setChecked(bool(self._config.get(f"inference.overlay.{key}", True)))
        self._font_scale.setValue(float(self._config.get("inference.overlay.font_scale", 0.6)))
        self._thickness.setValue(int(self._config.get("inference.overlay.thickness", 2)))

    def save(self):
        for model_slot, fields in self._model_forms.items():
            prefix = f"inference.models.{model_slot}"
            self._config.set(f"{prefix}.type", fields["type"].currentText())
            self._config.set(f"{prefix}.path", fields["path"].text())
            self._config.set(f"{prefix}.min_confidence_pct",
                             fields["min_confidence_pct"].value())
            self._config.set(f"{prefix}.max_side_px", fields["max_side_px"].value())
            self._config.set(f"{prefix}.class_names", split_list(fields["class_names"].text()))

        for pipeline_slot, fields in self._pipeline_forms.items():
            prefix = f"inference.pipelines.{pipeline_slot}"
            self._config.set(f"{prefix}.enabled", fields["enabled"].isChecked())
            self._config.set(f"{prefix}.cameras", split_list(fields["cameras"].text()))
            self._config.set(f"{prefix}.interval_s", fields["interval_s"].value())
            self._config.set(f"{prefix}.min_detections", fields["min_detections"].value())
            self._config.set(f"{prefix}.min_result_confidence_pct",
                             fields["min_result_confidence_pct"].value())

        self._config.set("inference.overlay.enabled", self._overlay_enabled.isChecked())
        for key, check in self._overlay_checks.items():
            self._config.set(f"inference.overlay.{key}", check.isChecked())
        self._config.set("inference.overlay.font_scale", self._font_scale.value())
        self._config.set("inference.overlay.thickness", self._thickness.value())
