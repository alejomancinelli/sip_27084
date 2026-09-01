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
from PySide6.QtCore import QEvent, Qt, Signal, Slot
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
# Ancho mínimo del panel, fijo a propósito: ver `__init__`.
_MIN_PANEL_WIDTH_PX = 360
_ROI_PEN_WIDTH_PX = 2


class CameraPanel(QGroupBox):
    """
    Feed de una cámara con su telemetría.

    El título es el `name` de la cámara en el config; el slot es la identidad y no se
    muestra.

    **Lo que se muestra y lo que se recorta no son el mismo frame.** `update_frame()`
    pinta lo que el cableado mande según el modo —crudo o anotado—, y `update_raw_frame()`
    guarda el de cámara, que es el único con el que tiene sentido definir un ROI: el
    anotado puede venir con máscaras encima y, si el overlay recorta al ROI, ni siquiera
    tiene el tamaño del sensor. `frame_updated` publica el crudo por eso mismo.
    """

    frame_updated = Signal(object)   # np.ndarray BGR crudo — uno por frame de cámara

    def __init__(self, camera_slot: str, config_manager: ConfigManager, parent=None):
        self._config = config_manager
        self._camera_slot = camera_slot
        title = str(self._config.get(f"cameras.{camera_slot}.name", camera_slot))
        super().__init__(title, parent)
        self._last_frame: np.ndarray | None = None
        self._last_raw_frame: np.ndarray | None = None
        self._is_dark = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 16, 6, 6)
        layout.setSpacing(4)

        self._video_label = QLabel(tr("camera_no_signal"))
        self._video_label.setObjectName("videoFeed")
        self._video_label.setMinimumSize(_MIN_VIDEO_WIDTH_PX, _MIN_VIDEO_HEIGHT_PX)
        self._video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._video_label.setAlignment(Qt.AlignCenter)
        self._video_label.installEventFilter(self)

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

        # El ancho que el panel pide es fijo y no sale de su contenido. Sin esto lo fija el
        # texto más largo que tenga adentro —el nombre de la cámara en el título, o el chip
        # de estado, que pasa de «Conectada» a «Mal configurada» en caliente— y dos paneles
        # con el mismo tamaño de imagen terminan con anchos distintos en la grilla: la
        # cámara con problemas se lleva 70 px de la que anda. Se nota cuando el espacio
        # aprieta, que es justo con la barra lateral abierta. Si el texto no entra, se
        # recorta; el estado también se lee por el color del chip.
        #
        # `Ignored` es la mitad que hace falta para que sean iguales de verdad: el mínimo
        # de arriba empareja el piso, pero la grilla reparte el espacio sobrante mirando
        # también el tamaño preferido, que el título vuelve a inflar. Ignorándolo, lo único
        # que decide el ancho es el stretch de la columna, que es 1 para todas.
        self.setMinimumWidth(_MIN_PANEL_WIDTH_PX)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

    # ── API pública ──────────────────────────────────────────────────────────

    @property
    def camera_slot(self) -> str:
        return self._camera_slot

    def get_last_frame(self) -> np.ndarray | None:
        """
        Último frame CRUDO de la cámara, o None si todavía no llegó ninguno.

        Crudo y no el que se está mostrando: es el que usa el diálogo de ROI, y un ROI
        definido sobre un frame anotado o recortado sale con coordenadas que no son las
        del sensor.
        """
        return self._last_raw_frame

    @Slot(object)
    def update_raw_frame(self, frame_bgr: np.ndarray | None):
        """Guarda el frame de cámara y lo publica. No dibuja: de eso se ocupa
        `update_frame()`, que puede estar mostrando el anotado."""
        if frame_bgr is None or frame_bgr.size == 0:
            return
        self._last_raw_frame = _to_bgr(frame_bgr)
        self.frame_updated.emit(self._last_raw_frame)

    def apply_theme(self, dark: bool):
        self._is_dark = dark

    @Slot(object)
    def update_frame(self, frame_bgr: np.ndarray | None):
        """Slot de `CaptureThread.frame_ready` (o del frame anotado del motor)."""
        if frame_bgr is None or frame_bgr.size == 0:
            return
        self._last_frame = _to_bgr(frame_bgr)
        self._repaint()

    def clear_frame(self, message: str = ""):
        """
        Deja el panel sin imagen, con un texto en el lugar del video.

        La hace falta porque el panel retiene el último pixmap que le dieron: mostrando el
        anotado antes de la primera medición quedaría el último frame crudo, que parece la
        inferencia y no lo es.
        """
        self._last_frame = None
        self._video_label.clear()
        self._video_label.setText(message or tr("camera_no_signal"))

    def eventFilter(self, watched, event):
        """
        Reescala la imagen cuando cambia el tamaño del video.

        El pixmap se escala al tamaño que tiene el label en el momento de pintarlo, así
        que sin esto una imagen quieta se queda del tamaño viejo. En crudo no se nota
        —llegan cinco frames por segundo y cada uno se pinta al tamaño nuevo— pero el
        anotado es uno por ciclo: plegar la barra lateral o enfocar una cámara dejaba la
        imagen chica hasta la medición siguiente.
        """
        if (watched is self._video_label and event.type() == QEvent.Resize
                and event.size() != event.oldSize()):
            self._repaint()
        return super().eventFilter(watched, event)

    def _repaint(self):
        """Pinta `_last_frame` al tamaño que tiene ahora el label."""
        frame_bgr = self._last_frame
        if frame_bgr is None:
            return
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
