"""
Pestaña de inferencia: `inference.models` e `inference.pipelines`.

Dos secciones, una debajo de la otra: «Modelos» y «Pipelines», cada una con una
sub-pestaña por slot, como Cámaras. Los slots salen del config y son fijos por proyecto,
igual que las cámaras. Los pipelines van debajo porque nombran modelos, así que se leen en
ese orden, y las dos barras nunca se mezclan: un slot de modelo y uno de pipeline en la
misma fila de pestañas no se distinguen por el rótulo. Con más slots de los que entran a
lo ancho, la barra muestra flechas en vez de cortar rótulos (`build_slot_tabs()`).

**El orden de las etapas no está acá y no puede estarlo**: se programa en
`system/inference/pipeline.py`, porque encadenar dos modelos es lógica del proceso. De
esta pestaña sale de dónde se carga cada modelo y qué cámaras entran a cada pipeline.

Los tipos de modelo que ofrece el combo salen de `REGISTERED_MODELS`, así que un fork
que registra su modelo en la fábrica lo ve acá sin tocar la UI.

Tres cosas de la sección no están en esta pantalla, y no es un olvido:

  - **`device`** no existe más: el modelo resuelve solo si hay CUDA disponible. Elegirlo
    a mano desde la planta sólo servía para dejar la inferencia en CPU sin darse cuenta.
  - **`inference.overlay`**, el anotado del resultado, se edita sólo en el config.yaml,
    como su paleta `class_colors_bgr`. Varios de sus valores son de la instalación y no
    una preferencia del operador —el tamaño del texto se calibra para el frame de cada
    cámara—, y cambiarlos desde la planta no mide mejor: sólo cambia lo que se ve en el
    stream, en la UI y en el dataset a la vez.
  - **`class_names`** de cada modelo se escribe en el config.yaml al armar el fork, o lo
    traen los pesos, que le ganan. Cambiar el orden no da un error: invierte en silencio
    todos los porcentajes. Un fork que declara sus clases en el código no lo usa, y ahí
    escribirlo sólo podía hacer que el pipeline se negara a arrancar.

Las claves del ciclo de medición —`frames_per_cycle` y las tres que dependen de él— sí se
muestran: no las lee el motor sino el scheduler, y la nota del grupo dice que con una
imagen por medición las otras tres no hacen nada.
"""

from collections.abc import Callable, Iterable

from PySide6.QtWidgets import QVBoxLayout, QWidget

from system.inference.models.model_factory import REGISTERED_MODELS

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    add_check_row, add_form_row, add_hint_row, build_combo_box, build_double_spin_box,
    build_form_page, build_group_box, build_line_edit, build_slot_tabs, build_spin_box,
    join_list, set_combo_value, split_list,
)

_MAX_SIDE_PX = 8192
_MAX_DETECTIONS = 10000
_MAX_INTERVAL_S = 3600.0
_MAX_FRAMES_PER_CYCLE = 100      # N imágenes por medición; más que esto es otro problema
_MAX_CYCLE_INTERVAL_S = 86400.0  # una medición por día es el extremo razonable


class InferenceTab(AbstractConfigTab):
    """Sección `inference:` del config, menos el orden de las etapas y el anotado."""

    TITLE_KEY = "tab_inference"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._model_forms: dict[str, dict] = {}
        self._pipeline_forms: dict[str, dict] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._build_section(
            tr("inf_box_models"), self._build_model_page,
            self._config.get("inference.models", {}) or {}))
        layout.addWidget(self._build_section(
            tr("inf_box_pipelines"), self._build_pipeline_page,
            self._config.get("inference.pipelines", {}) or {}))
        layout.addStretch()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_section(self, title: str, build_page: Callable[[str], QWidget],
                       slots: Iterable[str]) -> QWidget:
        """Un grupo con una sub-pestaña por slot; el rótulo de cada una es el slot."""
        box, form = build_group_box(title)
        tabs = build_slot_tabs()
        for slot in slots:
            tabs.addTab(build_page(slot), str(slot))
        form.addRow(tabs)
        return box

    def _build_model_page(self, model_slot: str) -> QWidget:
        page, form = build_form_page()
        fields = {
            "type": add_form_row(form, tr("inf_model_type"),
                                 build_combo_box(list(REGISTERED_MODELS), "")),
            "path": add_form_row(form, tr("inf_model_path"),
                                 build_line_edit("", "models/model.pt")),
            "min_confidence_pct": add_form_row(form, tr("inf_min_confidence"),
                                               build_spin_box(0, 100, 50)),
            "max_side_px": add_form_row(form, tr("inf_max_side"),
                                        build_spin_box(0, _MAX_SIDE_PX, 0)),
        }
        self._model_forms[model_slot] = fields
        return page

    def _build_pipeline_page(self, pipeline_slot: str) -> QWidget:
        page, form = build_form_page()
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
            # El ciclo de medición: los lee el scheduler, no el motor. Con
            # `frames_per_cycle` en 1 los otros tres no hacen nada.
            "frames_per_cycle": add_form_row(form, tr("inf_frames_per_cycle"),
                                             build_spin_box(1, _MAX_FRAMES_PER_CYCLE, 1)),
            "cycle_interval_s": add_form_row(
                form, tr("inf_cycle_interval"),
                build_double_spin_box(0.0, _MAX_CYCLE_INTERVAL_S, 0.0, decimals=1, step=10.0),
            ),
            "capture_timeout_s": add_form_row(
                form, tr("inf_capture_timeout"),
                build_double_spin_box(0.0, _MAX_CYCLE_INTERVAL_S, 30.0, decimals=1, step=5.0),
            ),
            "idle_cameras_between_cycles": add_check_row(
                form, tr("inf_idle_cameras"), False),
        }
        add_hint_row(form, tr("inf_cycle_note"))
        self._pipeline_forms[pipeline_slot] = fields
        return page

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
            fields["frames_per_cycle"].setValue(
                int(self._config.get(f"{prefix}.frames_per_cycle", 1)))
            fields["cycle_interval_s"].setValue(
                float(self._config.get(f"{prefix}.cycle_interval_s", 0.0)))
            fields["capture_timeout_s"].setValue(
                float(self._config.get(f"{prefix}.capture_timeout_s", 30.0)))
            fields["idle_cameras_between_cycles"].setChecked(
                bool(self._config.get(f"{prefix}.idle_cameras_between_cycles", False)))

    def save(self):
        for model_slot, fields in self._model_forms.items():
            prefix = f"inference.models.{model_slot}"
            self._config.set(f"{prefix}.type", fields["type"].currentText())
            self._config.set(f"{prefix}.path", fields["path"].text())
            self._config.set(f"{prefix}.min_confidence_pct",
                             fields["min_confidence_pct"].value())
            self._config.set(f"{prefix}.max_side_px", fields["max_side_px"].value())

        for pipeline_slot, fields in self._pipeline_forms.items():
            prefix = f"inference.pipelines.{pipeline_slot}"
            self._config.set(f"{prefix}.enabled", fields["enabled"].isChecked())
            self._config.set(f"{prefix}.cameras", split_list(fields["cameras"].text()))
            self._config.set(f"{prefix}.interval_s", fields["interval_s"].value())
            self._config.set(f"{prefix}.min_detections", fields["min_detections"].value())
            self._config.set(f"{prefix}.min_result_confidence_pct",
                             fields["min_result_confidence_pct"].value())
            self._config.set(f"{prefix}.frames_per_cycle", fields["frames_per_cycle"].value())
            self._config.set(f"{prefix}.cycle_interval_s", fields["cycle_interval_s"].value())
            self._config.set(f"{prefix}.capture_timeout_s",
                             fields["capture_timeout_s"].value())
            self._config.set(f"{prefix}.idle_cameras_between_cycles",
                             fields["idle_cameras_between_cycles"].isChecked())
