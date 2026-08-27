"""
Pestaña de proceso: la sección `process:` del config — **vacía en el template**.

Es la única pestaña que llega sin campos, y es a propósito: `process:` es la única
sección cuya *forma* cambia entre proyectos. Escalas de píxel, límites de carga, anchos
de cinta, umbrales de aceptación —lo que el cliente mide— no tienen nada en común entre
una instalación y la siguiente, así que el template no puede declararlos.

La pestaña existe igual porque **el punto de extensión tiene que verse**: un fork que
necesita que el operador ajuste un umbral encuentra el archivo, agrega sus campos y ya
está en la pantalla, sin tocar el ConfigView ni descubrir de casualidad que se podía.

Cómo se llena. Dos patrones cubren casi todo:

    def _build_box(self) -> QWidget:
        box, form = build_group_box(tr("mi_grupo"))

        # 1. Escalar: un valor para todo el proceso.
        self._min_load = add_form_row(form, tr("mi_min_load"), build_spin_box(0, 100, 60))

        # 2. Mapa por cámara: una fila por slot de `cameras:`, como hace CamerasTab.
        self._scales = {}
        for camera_slot in (self._config.get("cameras", {}) or {}):
            name = self._config.get(f"cameras.{camera_slot}.name", "") or camera_slot
            self._scales[camera_slot] = add_form_row(
                form, f"{tr('mi_escala')} — {name}",
                build_double_spin_box(0.01, 100.0, 1.0, decimals=2),
            )
        return box

    def load(self):
        prefix = "process.belt_1"
        self._min_load.setValue(int(self._config.get(f"{prefix}.min_load_pct", 60)))
        scales = self._config.get(f"{prefix}.scale_px_per_mm", {}) or {}
        for camera_slot, field in self._scales.items():
            field.setValue(float(scales.get(camera_slot, 1.0)))

    def save(self):
        prefix = "process.belt_1"
        self._config.set(f"{prefix}.min_load_pct", self._min_load.value())
        for camera_slot, field in self._scales.items():
            self._config.set(f"{prefix}.scale_px_per_mm.{camera_slot}", field.value())

Dos cosas que **no** van acá:

  - **La validación de lo obligatorio.** Un valor cuya ausencia corrompe una medición
    —una escala de píxel— tiene que frenar el arranque, y eso lo chequea `main.py`, que
    es quien lee la sección y la inyecta. Un valor cuya ausencia es un no-op documentado
    —un mínimo en 0— sí puede tener default.
  - **Qué cámaras usa cada proceso.** Va en el config, como `process.<nombre>.cameras`,
    con la misma forma que `inference.pipelines.<slot>.cameras`. No se deduce de las
    claves de los mapas por cámara: un parámetro uniforme no tiene mapa, y un error de
    tipeo en una clave movería una cámara de proceso en silencio.

Un proyecto con varios procesos idénticos abre una sub-sección por proceso y esta
pestaña arma un grupo por cada uno, con el mismo bucle de arriba sobre
`self._config.get("process", {})`.
"""

from PySide6.QtWidgets import QLabel, QVBoxLayout

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab


class ProcessTab(AbstractConfigTab):
    """Sección `process:` del config. Sin campos hasta que el fork los agregue."""

    TITLE_KEY = "tab_process"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)

        note = QLabel(tr("process_empty_note"))
        note.setObjectName("hintLabel")
        note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(note)
        layout.addStretch()

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        """Sin campos que cargar. El fork la escribe junto con los suyos."""

    def save(self):
        """
        Sin campos que guardar.

        Vacío y no `NotImplementedError`: el ConfigView recorre todas las pestañas al
        guardar, y una que levante excepción abortaría el guardado de las demás.
        """
