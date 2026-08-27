"""
Panel de una cámara: el feed en vivo, sus chips de telemetría y el recuadro del ROI.

Un panel por slot de `cameras:`. Recibe frames y estado por sus slots
—`update_frame()` y `update_status()`—, que es lo que un CaptureThread emite, y no
sabe de dónde salieron: la grilla o la vista de monitor los conecta.

El recuadro del ROI se dibuja acá porque es una referencia de la configuración de la
cámara, no del resultado: lo que dibuja la inferencia ya viene pintado en el frame
anotado que emite el motor.
"""

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
)

from system.config_manager import ConfigManager

from ui import theme
from ui.strings import tr
from ui.widgets.status_chip import CHIP_ERROR, CHIP_INFO, CHIP_OK, CHIP_WARNING, StatusChip

_MIN_VIDEO_WIDTH_PX = 320
_MIN_VIDEO_HEIGHT_PX = 200
_ROI_PEN_WIDTH_PX = 2


class CameraPanel(QGroupBox):
    """
    Feed de una cámara con su telemetría.

    El título es el `name` de la cámara en el config; el slot es la identidad y no se
    muestra. Guarda el último frame recibido y lo publica por `frame_updated`, que es
    de donde lo toma el diálogo de ROI para trabajar en vivo.
    """

    frame_updated = Signal(object)   # np.ndarray BGR — uno por frame recibido

    def __init__(self, camera_slot: str, config_manager: ConfigManager, parent=None):
        self._config = config_manager
        self._camera_slot = camera_slot
        title = str(self._config.get(f"cameras.{camera_slot}.name", camera_slot))
        super().__init__(title, parent)
        self._last_frame: np.ndarray | None = None
        self._is_dark = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 16, 6, 6)
        layout.setSpacing(4)

        self._video_label = QLabel(tr("camera_no_signal"))
        self._video_label.setObjectName("videoFeed")
        self._video_label.setMinimumSize(_MIN_VIDEO_WIDTH_PX, _MIN_VIDEO_HEIGHT_PX)
        self._video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._video_label.setAlignment(Qt.AlignCenter)

        self._chip_state = StatusChip(tr("camera_starting"))
        self._chip_fps = StatusChip("0.0 FPS")
        self._chip_temperature = StatusChip("0.0 °C")
        self._chip_illumination = StatusChip("--- %")
        for chip in (self._chip_fps, self._chip_temperature, self._chip_illumination):
            chip.set_state(CHIP_INFO)

        chip_row = QHBoxLayout()
        for chip in (self._chip_state, self._chip_fps,
                     self._chip_temperature, self._chip_illumination):
            chip_row.addWidget(chip)
        chip_row.addStretch()

        layout.addWidget(self._video_label, stretch=1)
        layout.addLayout(chip_row)

    # ── API pública ──────────────────────────────────────────────────────────

    @property
    def camera_slot(self) -> str:
        return self._camera_slot

    def get_last_frame(self) -> np.ndarray | None:
        """Último frame recibido, o None si todavía no llegó ninguno."""
        return self._last_frame

    def apply_theme(self, dark: bool):
        self._is_dark = dark

    @Slot(object)
    def update_frame(self, frame_bgr: np.ndarray | None):
        """Slot de `CaptureThread.frame_ready` (o del frame anotado del motor)."""
        if frame_bgr is None or frame_bgr.size == 0:
            return
        frame_bgr = _to_bgr(frame_bgr)
        self._last_frame = frame_bgr
        self.frame_updated.emit(frame_bgr)

        height, width = frame_bgr.shape[:2]
        image = QImage(frame_bgr.data, width, height, 3 * width, QImage.Format_BGR888)
        scaled = QPixmap.fromImage(image).scaled(
            self._video_label.size(), Qt.KeepAspectRatio, Qt.FastTransformation
        )
        self._draw_roi(scaled, width, height)
        self._video_label.setPixmap(scaled)

    @Slot(object)
    def update_status(self, status: dict):
        """
        Slot de `CaptureThread.status_updated`. Claves de `AbstractCameraDriver.get_status()`.

        Los cuatro estados se distinguen porque piden cosas distintas: una cámara mal
        configurada necesita que alguien corrija el config y reinicie, una desconectada
        se recupera sola, y una deshabilitada está así porque alguien lo pidió. El orden
        de los `if` es el de la acción más urgente primero.
        """
        error = status.get("error")
        if error:
            self._chip_state.set_state(CHIP_ERROR, tr("camera_misconfigured"))
            self._chip_state.setToolTip(str(error))
            self._show_placeholder()
        elif not status.get("connected"):
            self._chip_state.set_state(CHIP_ERROR, tr("camera_disconnected"))
            self._chip_state.setToolTip("")
            self._show_placeholder()
        elif not status.get("capture_enabled", True):
            self._chip_state.set_state(CHIP_WARNING, tr("camera_disabled"))
            self._chip_state.setToolTip("")
        else:
            self._chip_state.set_state(CHIP_OK, tr("camera_connected"))
            self._chip_state.setToolTip("")
        self._chip_fps.setText(f"{status.get('fps_estimated', 0.0):.1f} FPS")
        self._chip_temperature.setText(f"{status.get('temperature', 0.0):.1f} °C")

    def set_illumination_pct(self, illumination_pct: int):
        """Brillo medio del ROI que midió el motor de inferencia, 0–100."""
        self._chip_illumination.setText(f"{int(illumination_pct)} %")

    # ── Internos ─────────────────────────────────────────────────────────────

    def _show_placeholder(self):
        self._video_label.clear()
        self._video_label.setText(tr("camera_no_signal"))

    def _draw_roi(self, pixmap: QPixmap, frame_width_px: int, frame_height_px: int):
        roi = self._config.get(f"cameras.{self._camera_slot}.roi", {}) or {}
        width_px = int(roi.get("width_px", 0))
        height_px = int(roi.get("height_px", 0))
        if not roi.get("enabled") or width_px <= 0 or height_px <= 0:
            return
        scale_x = pixmap.width() / frame_width_px
        scale_y = pixmap.height() / frame_height_px
        painter = QPainter(pixmap)
        painter.setPen(QPen(theme.get_color("roi_outline", self._is_dark), _ROI_PEN_WIDTH_PX))
        painter.drawRect(
            int(int(roi.get("x_px", 0)) * scale_x), int(int(roi.get("y_px", 0)) * scale_y),
            int(width_px * scale_x), int(height_px * scale_y),
        )
        painter.end()


def _to_bgr(frame: np.ndarray) -> np.ndarray:
    """Lleva un frame gris o de un canal a BGR contiguo, que es lo que espera QImage."""
    if frame.ndim == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    elif frame.ndim == 3 and frame.shape[2] == 1:
        frame = cv2.cvtColor(frame[:, :, 0], cv2.COLOR_GRAY2BGR)
    return np.ascontiguousarray(frame)
