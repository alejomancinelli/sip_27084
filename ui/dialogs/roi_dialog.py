"""
Diálogo de ROI: dibujar con el mouse la región que se analiza de una cámara.

Trabaja sobre el frame en vivo de esa cámara: quien lo abre le conecta la señal de
frames del panel, así que el operador recorta sobre lo que la cámara está viendo y no
sobre una captura vieja.

Las coordenadas que se guardan son del frame de cámara, en píxeles: las mismas que lee
el motor de inferencia. El canvas convierte entre el frame y la pantalla y no deja
salir coordenadas de widget.

Guarda las cuatro claves de geometría bajo el prefijo que se le dé —por defecto
`cameras.<slot>.roi`— y persiste el archivo al aceptar. No toca ninguna otra clave, y el
`enabled` del ROI queda como está: prender el recorte es una decisión aparte de dibujarlo.

**El prefijo es un parámetro** porque un proyecto puede tener más de un rectángulo por
cámara: el que se analiza y el que algún proceso mida aparte.
Los dos se dibujan igual y sobre el mismo frame en vivo, así que lo único que cambia es
dónde se guardan y cómo se titula la ventana. Sin esto, el segundo rectángulo se escribiría
a mano en el `config.yaml`, que es justo lo que este diálogo existe para evitar.
"""

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, Slot
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QVBoxLayout,
)

from system.config_manager import ConfigManager

from ui import theme
from ui.strings import tr
from ui.widgets.form import add_hint_row

_MIN_CANVAS_WIDTH_PX = 640
_MIN_CANVAS_HEIGHT_PX = 400
_MIN_ROI_SIDE_PX = 10        # menos que esto es un click, no un recorte
_MAX_FRAME_SIDE_PX = 8192
_ROI_PEN_WIDTH_PX = 2
_DIALOG_WIDTH_PX = 760
_DIALOG_HEIGHT_PX = 620

# Claves del ROI en el config, en el orden en que se muestran, con su etiqueta.
_ROI_FIELDS = (
    ("x_px", "cam_roi_x"),
    ("y_px", "cam_roi_y"),
    ("width_px", "cam_roi_width"),
    ("height_px", "cam_roi_height"),
)


class RoiDialog(QDialog):
    """
    Diálogo de definición de ROI de una cámara.

    Se abre con `exec()`. Aceptar escribe el ROI en el config y lo persiste; cancelar
    no deja nada. Los frames entran por `update_frame()`, que es el slot que hay que
    conectar a la señal de frames de esa cámara.

    `config_prefix` dice bajo qué clave se guardan las cuatro coordenadas; vacío significa
    el ROI de análisis de esa cámara. `title_key` es la clave de idiomas del título, para
    que la ventana diga qué rectángulo se está dibujando.
    """

    def __init__(self, config_manager: ConfigManager, camera_slot: str, parent=None, *,
                 config_prefix: str = "", title_key: str = "roi_title"):
        super().__init__(parent)
        self._config = config_manager
        self._camera_slot = camera_slot
        self._prefix = config_prefix or f"cameras.{camera_slot}.roi"
        self._last_frame: np.ndarray | None = None

        camera_name = str(self._config.get(f"cameras.{camera_slot}.name", "") or camera_slot)
        self.setWindowTitle(f"{tr(title_key)} — {camera_name}")
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.resize(_DIALOG_WIDTH_PX, _DIALOG_HEIGHT_PX)

        roi = self._config.get(self._prefix, {}) or {}
        self._canvas = _RoiCanvas()
        self._canvas.roi_changed = self._on_roi_drawn
        self._canvas.set_roi_px(
            int(roi.get("x_px", 0)), int(roi.get("y_px", 0)),
            int(roi.get("width_px", 0)), int(roi.get("height_px", 0)),
        )

        layout = QVBoxLayout(self)
        layout.addWidget(self._canvas, stretch=1)
        layout.addWidget(self._build_coords_box(roi))
        layout.addLayout(self._build_button_row())

    # ── API pública ──────────────────────────────────────────────────────────

    @Slot(object)
    def update_frame(self, frame_bgr: np.ndarray | None):
        """Slot para la señal de frames de la cámara. Ignora un frame vacío."""
        if frame_bgr is None or frame_bgr.size == 0:
            return
        self._last_frame = frame_bgr
        self._canvas.set_frame(frame_bgr)

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_coords_box(self, roi: dict) -> QGroupBox:
        box = QGroupBox(tr("roi_box_coords"))
        form = QFormLayout(box)
        self._spins: dict[str, QSpinBox] = {}
        for key, label_key in _ROI_FIELDS:
            spin = QSpinBox()
            spin.setRange(0, _MAX_FRAME_SIDE_PX)
            spin.setValue(int(roi.get(key, 0)))
            spin.valueChanged.connect(self._on_spin_changed)
            self._spins[key] = spin
            form.addRow(QLabel(tr(label_key)), spin)
        add_hint_row(form, tr("roi_hint"))
        return box

    def _build_button_row(self) -> QHBoxLayout:
        reset_button = QPushButton(tr("roi_reset"))
        reset_button.setObjectName("resetButton")
        reset_button.clicked.connect(self._on_reset_clicked)

        cancel_button = QPushButton(tr("dialog_cancel"))
        cancel_button.setObjectName("cancelButton")
        cancel_button.clicked.connect(self.reject)

        save_button = QPushButton(tr("roi_save"))
        save_button.setObjectName("saveButton")
        save_button.clicked.connect(self._on_save_clicked)

        row = QHBoxLayout()
        row.addWidget(reset_button)
        row.addStretch()
        row.addWidget(cancel_button)
        row.addWidget(save_button)
        return row

    # ── Internos ─────────────────────────────────────────────────────────────

    def _on_roi_drawn(self, x_px: int, y_px: int, width_px: int, height_px: int):
        for key, value in (("x_px", x_px), ("y_px", y_px),
                           ("width_px", width_px), ("height_px", height_px)):
            spin = self._spins[key]
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _on_spin_changed(self):
        """Un valor tipeado a mano también mueve el recuadro del canvas."""
        self._canvas.set_roi_px(*(self._spins[key].value() for key, _ in _ROI_FIELDS))
        if self._last_frame is not None:
            self._canvas.set_frame(self._last_frame)

    def _on_reset_clicked(self):
        self._on_roi_drawn(0, 0, 0, 0)
        self._canvas.set_roi_px(0, 0, 0, 0)
        if self._last_frame is not None:
            self._canvas.set_frame(self._last_frame)

    def _on_save_clicked(self):
        for key, _ in _ROI_FIELDS:
            self._config.set(f"{self._prefix}.{key}", self._spins[key].value())
        self._config.save()
        self.accept()


class _RoiCanvas(QLabel):
    """
    Lienzo del frame con el recuadro del ROI dibujable con el mouse.

    Guarda el ROI en coordenadas del frame y lo reproyecta en cada frame recibido, así
    sobrevive a que la ventana cambie de tamaño. `roi_changed` es un callback
    `(x_px, y_px, width_px, height_px)` que el diálogo asigna.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("videoFeed")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(_MIN_CANVAS_WIDTH_PX, _MIN_CANVAS_HEIGHT_PX)

        self.roi_changed = None
        self._roi_px: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._start: QPoint | None = None
        self._end: QPoint | None = None
        self._is_drawing = False
        self._scale_x = 1.0
        self._scale_y = 1.0
        self._offset_x_px = 0
        self._offset_y_px = 0
        self._frame_width_px = 0
        self._frame_height_px = 0

    def set_roi_px(self, x_px: int, y_px: int, width_px: int, height_px: int):
        """Fija el ROI en coordenadas del frame; se dibuja con el próximo frame."""
        self._roi_px = (x_px, y_px, width_px, height_px)
        self._start = None
        self._end = None

    def set_frame(self, frame_bgr: np.ndarray):
        """Escala el frame al lienzo, calcula la conversión y dibuja el recuadro."""
        if frame_bgr.ndim == 2:
            frame_bgr = cv2.cvtColor(frame_bgr, cv2.COLOR_GRAY2BGR)
        frame_bgr = np.ascontiguousarray(frame_bgr)
        self._frame_height_px, self._frame_width_px = frame_bgr.shape[:2]

        image = QImage(frame_bgr.data, self._frame_width_px, self._frame_height_px,
                       3 * self._frame_width_px, QImage.Format_BGR888)
        scaled = QPixmap.fromImage(image).scaled(
            self.size(), Qt.KeepAspectRatio, Qt.FastTransformation
        )
        if scaled.width() <= 0 or scaled.height() <= 0:
            return

        self._scale_x = self._frame_width_px / scaled.width()
        self._scale_y = self._frame_height_px / scaled.height()
        self._offset_x_px = (self.width() - scaled.width()) // 2
        self._offset_y_px = (self.height() - scaled.height()) // 2

        canvas = QPixmap(self.size())
        canvas.fill(Qt.black)
        painter = QPainter(canvas)
        painter.drawPixmap(self._offset_x_px, self._offset_y_px, scaled)
        rect = self._get_roi_rect()
        if not rect.isNull():
            painter.setPen(QPen(theme.get_color("roi_outline", True), _ROI_PEN_WIDTH_PX))
            painter.drawRect(rect)
        painter.end()
        self.setPixmap(canvas)

    # ── Mouse ────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.LeftButton:
            return
        self._start = event.position().toPoint()
        self._end = self._start
        self._is_drawing = True

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_drawing:
            self._end = event.position().toPoint()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.LeftButton or not self._is_drawing:
            return
        self._end = event.position().toPoint()
        self._is_drawing = False
        self._emit_roi()

    # ── Internos ─────────────────────────────────────────────────────────────

    def _get_roi_rect(self) -> QRect:
        """Recuadro a dibujar, en coordenadas del widget."""
        if self._start is not None and self._end is not None:
            return QRect(self._start, self._end).normalized()
        x_px, y_px, width_px, height_px = self._roi_px
        if width_px <= 0 or height_px <= 0 or not self._frame_width_px:
            return QRect()
        return QRect(
            int(x_px / self._scale_x) + self._offset_x_px,
            int(y_px / self._scale_y) + self._offset_y_px,
            int(width_px / self._scale_x), int(height_px / self._scale_y),
        )

    def _emit_roi(self):
        """
        Pasa el recuadro dibujado a coordenadas del frame y lo publica.

        Un recuadro más chico que `_MIN_ROI_SIDE_PX` no se publica: es un click, y
        aceptarlo dejaría un ROI de dos píxeles sin que el operador lo pida.
        """
        if self.roi_changed is None or not self._frame_width_px:
            return
        rect = QRect(self._start, self._end).normalized()
        x1_px = _clamp(int((rect.left() - self._offset_x_px) * self._scale_x),
                       self._frame_width_px)
        y1_px = _clamp(int((rect.top() - self._offset_y_px) * self._scale_y),
                       self._frame_height_px)
        x2_px = _clamp(int((rect.right() - self._offset_x_px) * self._scale_x),
                       self._frame_width_px)
        y2_px = _clamp(int((rect.bottom() - self._offset_y_px) * self._scale_y),
                       self._frame_height_px)
        width_px = x2_px - x1_px
        height_px = y2_px - y1_px
        if width_px < _MIN_ROI_SIDE_PX or height_px < _MIN_ROI_SIDE_PX:
            return
        self._roi_px = (x1_px, y1_px, width_px, height_px)
        self.roi_changed(x1_px, y1_px, width_px, height_px)


def _clamp(value: int, maximum: int) -> int:
    return max(0, min(value, maximum))
