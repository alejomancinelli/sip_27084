"""
Grilla de cámaras: un panel por cámara, con foco y modo de video.

Cuatro decisiones del contrato:

  - **Se muestran todas las cámaras que la grilla tiene a cargo, no las que están
    andando.** Una cámara caída queda en su lugar con "SIN SEÑAL", porque el operador
    tiene que ver que falta y no descubrirlo porque la grilla cambió de forma. La
    cantidad de paneles se fija al construirla y no se mueve en runtime.
  - **Qué cámaras**: por defecto todas las de `cameras:`, y con `camera_slots` sólo
    esas y en ese orden. Es lo que permite armar más de una grilla —tres cámaras en una
    pantalla y tres en otra— sin escribir un widget de cero: ver «composición» abajo.
  - **Foco**: el selector elige entre ver todas o una sola, y la que se enfoca ocupa
    todo el espacio de la grilla. Se puede saltar de una cámara a otra sin volver a
    verlas todas: el selector es un solo control con «ver todas» como primera opción.
  - **Modo de video**: la grilla no elige qué frame entra. Publica el modo elegido por
    `mode_changed` y quien cablea decide qué le empuja a `update_frame()` —el frame de
    captura o el anotado del motor—. Así la grilla no conoce ni al hilo de captura ni
    al de inferencia.

Los frames entran por slot: `update_frame(frame, slot)`. Un slot que esta grilla no
tiene a cargo se descarta en silencio, así que se le puede conectar la señal de todas
las cámaras y cada grilla toma la suya.

**Composición.** Un proyecto que quiera otra disposición no edita este archivo: arma su
widget con varias grillas y lo entra por `MonitorView.set_content()`. Lo único que ese
widget tiene que respetar es la misma API por slot, para que el cableado no cambie:

    class DualBeltView(QWidget):
        def __init__(self, config):
            ...
            self._grids = (CameraGrid(config, camera_slots=("camera_1", "camera_2")),
                           CameraGrid(config, camera_slots=("camera_3", "camera_4")))

        def update_frame(self, frame_bgr, camera_slot):
            for grid in self._grids:      # cada grilla descarta lo que no es suyo
                grid.update_frame(frame_bgr, camera_slot)

`camera_slots` dice qué cámaras quedó atendiendo cada grilla, por si el compositor
prefiere repartir en vez de ofrecer el frame a todas.
"""

import math

import numpy as np
from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager
from system.logger import logger
from system.video.abstract_video_server import MODE_ANNOTATED, MODE_RAW

from ui.strings import tr
from ui.widgets.camera_panel import CameraPanel

_MAX_COLUMNS = 2         # más de dos columnas deja los feeds ilegibles en una pantalla
_NO_FOCUS = ""           # valor de `focused_slot` cuando se ven todas


class CameraGrid(QWidget):
    """
    Grilla de paneles de cámara con selector de modo y de foco.

    `camera_slots` vacío toma todas las cámaras de `cameras:`, en el orden del config;
    con slots toma sólo esas y en el orden pedido. Sin ninguna cámara que mostrar deja
    el aviso y no arma paneles.
    """

    mode_changed = Signal(str)          # MODE_RAW o MODE_ANNOTATED, al cambiar el combo
    focus_changed = Signal(str)         # slot enfocado, o '' cuando se ven todas

    def __init__(self, config_manager: ConfigManager, *,
                 camera_slots: tuple = (), parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._requested_slots = tuple(camera_slots)
        self._panels: dict[str, CameraPanel] = {}
        self._cells: dict[str, tuple[int, int]] = {}   # slot -> (fila, columna)
        self._rows = 0
        self._columns = 0
        self._focused_slot = _NO_FOCUS

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._mode_combo = QComboBox()
        self._mode_combo.addItem(tr("camera_mode_raw"), MODE_RAW)
        self._mode_combo.addItem(tr("camera_mode_annotated"), MODE_ANNOTATED)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_index_changed)

        # Un solo control para el foco: «ver todas» es la primera opción, así se salta
        # de una cámara a otra sin pasar por la grilla completa.
        self._focus_combo = QComboBox()
        self._focus_combo.setToolTip(tr("camera_focus_tooltip"))
        self._focus_combo.currentIndexChanged.connect(self._on_focus_index_changed)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self._mode_combo)
        toolbar.addWidget(self._focus_combo)
        toolbar.addStretch()

        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)

        layout.addLayout(toolbar)
        layout.addLayout(self._grid, stretch=1)

        self._build_panels()

    # ── API pública ──────────────────────────────────────────────────────────

    @property
    def camera_slots(self) -> tuple[str, ...]:
        """Cámaras que esta grilla tiene a cargo, en el orden en que las muestra."""
        return tuple(self._panels)

    @property
    def mode(self) -> str:
        """Modo de video elegido: MODE_RAW o MODE_ANNOTATED."""
        return str(self._mode_combo.currentData())

    @property
    def focused_slot(self) -> str:
        """Slot enfocado, o '' si se están viendo todas."""
        return self._focused_slot

    def get_panel(self, camera_slot: str) -> CameraPanel | None:
        """Panel de esa cámara, o None si el slot no está configurado."""
        return self._panels.get(camera_slot)

    @Slot(object, str)
    def update_frame(self, frame_bgr: np.ndarray | None, camera_slot: str):
        """
        Slot con la firma de `CaptureThread.frame_ready`: (frame, slot).

        Quien cablea decide si el frame es el crudo o el anotado, según `mode`.
        """
        panel = self._panels.get(camera_slot)
        if panel is not None:
            panel.update_frame(frame_bgr)

    @Slot(object, str)
    def update_raw_frame(self, frame_bgr: np.ndarray | None, camera_slot: str):
        """
        El frame de cámara, con la firma de `frame_ready`, para el que lo necesite crudo.

        No dibuja: guarda. Es lo que alimenta al diálogo de ROI, que tiene que trabajar
        sobre el frame del sensor aunque la grilla esté mostrando el anotado.
        """
        panel = self._panels.get(camera_slot)
        if panel is not None:
            panel.update_raw_frame(frame_bgr)

    @Slot(str)
    def clear_frame(self, camera_slot: str):
        """
        Saca la imagen de ese panel y deja dicho que todavía no hay inferencia.

        Es lo que el cableado usa al pasar a anotado sin una medición hecha: sin esto
        queda a la vista el último frame crudo, que se lee como si fuera el resultado.
        """
        panel = self._panels.get(camera_slot)
        if panel is not None:
            panel.clear_frame(tr("camera_waiting_inference"))

    @Slot(object, str)
    def update_status(self, status: dict, camera_slot: str):
        """Slot con la firma de `CaptureThread.status_updated`: (status, slot)."""
        panel = self._panels.get(camera_slot)
        if panel is not None:
            panel.update_status(status)

    def set_illumination_pct(self, camera_slot: str, illumination_pct: int):
        panel = self._panels.get(camera_slot)
        if panel is not None:
            panel.set_illumination_pct(illumination_pct)

    def set_mode(self, mode: str):
        """Fija el modo de video. Uno desconocido se ignora."""
        index = self._mode_combo.findData(mode)
        if index >= 0:
            self._mode_combo.setCurrentIndex(index)

    def set_focus(self, camera_slot: str):
        """
        Enfoca una cámara, que pasa a ocupar toda la grilla.

        Con '' —o un slot que no existe— vuelve a mostrar todas. Deja el selector en
        sincronía sin volver a disparar la señal, así que da igual si el foco vino del
        combo o de una llamada del cableado.
        """
        self._focused_slot = camera_slot if camera_slot in self._panels else _NO_FOCUS
        for slot, panel in self._panels.items():
            panel.setVisible(not self._focused_slot or slot == self._focused_slot)
        self._apply_stretches()

        index = self._focus_combo.findData(self._focused_slot)
        if index >= 0 and index != self._focus_combo.currentIndex():
            self._focus_combo.blockSignals(True)
            self._focus_combo.setCurrentIndex(index)
            self._focus_combo.blockSignals(False)

        self.focus_changed.emit(self._focused_slot)

    def apply_theme(self, dark: bool):
        for panel in self._panels.values():
            panel.apply_theme(dark)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _resolve_slots(self) -> list[str]:
        """
        Qué cámaras arma esta grilla: las pedidas, o todas las del config.

        El orden es el pedido y no el del config: quien compone la pantalla lo eligió.
        Un slot pedido que no está en `cameras:` se descarta y se avisa —armarle un panel
        mostraría una cámara que nadie configuró, a la que además nunca le van a llegar
        frames—, pero no frena la grilla: las demás se muestran igual.
        """
        configured = list((self._config.get("cameras", {}) or {}).keys())
        if not self._requested_slots:
            return configured

        unknown = [slot for slot in self._requested_slots if slot not in configured]
        if unknown:
            logger.warning(
                f"[UI] La grilla no muestra {', '.join(unknown)}: no están en "
                f"'cameras:' del config.yaml."
            )
        return [slot for slot in self._requested_slots if slot in configured]

    def _build_panels(self):
        slots = self._resolve_slots()
        self._focus_combo.addItem(tr("camera_show_all"), _NO_FOCUS)
        if not slots:
            self._grid.addWidget(QLabel(tr("camera_no_cameras")), 0, 0)
            self._mode_combo.setEnabled(False)
            self._focus_combo.setEnabled(False)
            return

        self._columns = min(_MAX_COLUMNS, len(slots))
        self._rows = math.ceil(len(slots) / self._columns)
        for index, slot in enumerate(slots):
            panel = CameraPanel(slot, self._config)
            cell = (index // self._columns, index % self._columns)
            self._panels[slot] = panel
            self._cells[slot] = cell
            self._grid.addWidget(panel, cell[0], cell[1])
            self._focus_combo.addItem(panel.title(), slot)
        self._apply_stretches()

    def _apply_stretches(self):
        """
        Reparte el espacio sólo entre las filas y columnas que tienen un panel visible.

        Es lo que hace que la cámara enfocada ocupe toda la grilla: una fila sin nada
        visible y con stretch 0 se colapsa, en vez de seguir reservando su porción.
        """
        visible_cells = [cell for slot, cell in self._cells.items()
                         if self._panels[slot].isVisible()]
        visible_rows = {row for row, _ in visible_cells}
        visible_columns = {column for _, column in visible_cells}
        for row in range(self._rows):
            self._grid.setRowStretch(row, 1 if row in visible_rows else 0)
        for column in range(self._columns):
            self._grid.setColumnStretch(column, 1 if column in visible_columns else 0)

    def _on_mode_index_changed(self, index: int):
        self.mode_changed.emit(str(self._mode_combo.itemData(index)))

    def _on_focus_index_changed(self, index: int):
        self.set_focus(str(self._focus_combo.itemData(index) or _NO_FOCUS))
