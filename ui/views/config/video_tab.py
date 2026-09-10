"""
Pestaña de video: la sección `video:` — HTTP, RTSP y el ajuste de visualización.

Los dos servidores son independientes y así se editan: cada uno con su checkbox de
habilitado, que apaga y atenúa sus propios campos.

El ajuste de visualización está acá y no en la pestaña de inferencia a propósito: la
gamma y el contraste local no mueven un píxel de lugar, así que van sólo al camino de
visualización y el modelo y el dataset siguen viendo el frame crudo. La nota al pie lo
dice en pantalla, que es donde hace falta.
"""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    add_check_row, add_form_row, add_hint_row, build_double_spin_box, build_group_box,
    build_line_edit, build_spin_box, build_value_combo_box, join_list, set_combo_data,
    split_list, wire_enable_toggle,
)

# Texto que ve el operador -> valor de `video.rtsp.codec`. Los `_hw` usan el encoder
# de la Jetson y no existen en un x86; el texto lo dice para que no se elijan a ciegas.
# El valor viaja en el item del combo y no se reconstruye desde el texto.
_CODECS = {
    "H.264 software":            "h264_sw",
    "H.264 hardware (Jetson)":   "h264_hw",
    "H.265 software":            "h265_sw",
    "H.265 hardware (Jetson)":   "h265_hw",
    "MJPEG":                     "mjpeg",
}
_DEFAULT_CODEC = "h264_sw"

_MAX_PORT = 65535
_MAX_FRAME_WIDTH_PX = 8192
_MAX_FPS = 240
_MAX_CLAHE_CLIP = 8.0
_MAX_GAMMA = 4.0


class VideoTab(AbstractConfigTab):
    """Sección `video:` del config. Ver el contrato en `abstract_tab.py`."""

    TITLE_KEY = "tab_video"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._build_http_box())
        layout.addWidget(self._build_rtsp_box())
        layout.addWidget(self._build_display_box())
        layout.addStretch()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_http_box(self) -> QWidget:
        box, form = build_group_box(tr("video_box_http"))
        self._http_enabled = add_check_row(form, tr("field_enabled"), True)
        self._http_port = add_form_row(form, tr("field_port"), build_spin_box(1, _MAX_PORT, 8091))
        self._http_quality = add_form_row(
            form, tr("video_jpeg_quality"), build_spin_box(1, 100, 80)
        )
        self._http_width = add_form_row(
            form, tr("video_frame_width"), build_spin_box(0, _MAX_FRAME_WIDTH_PX, 960)
        )
        self._http_log = add_check_row(form, tr("video_log_connections"), True)
        wire_enable_toggle(self._http_enabled, [
            self._http_port, self._http_quality, self._http_width, self._http_log,
        ])
        return box

    def _build_rtsp_box(self) -> QWidget:
        box, form = build_group_box(tr("video_box_rtsp"))
        self._rtsp_enabled = add_check_row(form, tr("field_enabled"), False)
        self._rtsp_port = add_form_row(form, tr("field_port"), build_spin_box(1, _MAX_PORT, 8554))
        self._rtsp_codec = add_form_row(
            form, tr("video_codec"), build_value_combo_box(_CODECS, _DEFAULT_CODEC)
        )
        self._rtsp_fps = add_form_row(form, tr("field_fps"), build_spin_box(1, _MAX_FPS, 15))
        self._rtsp_width = add_form_row(
            form, tr("video_frame_width"), build_spin_box(0, _MAX_FRAME_WIDTH_PX, 0)
        )
        self._rtsp_interfaces = add_form_row(
            form, tr("video_net_interfaces"), build_line_edit("", "eth0, eth1")
        )
        self._rtsp_log = add_check_row(form, tr("video_log_connections"), True)
        wire_enable_toggle(self._rtsp_enabled, [
            self._rtsp_port, self._rtsp_codec, self._rtsp_fps, self._rtsp_width,
            self._rtsp_interfaces, self._rtsp_log,
        ])
        return box

    def _build_display_box(self) -> QWidget:
        box, form = build_group_box(tr("video_box_display"))
        self._gamma = add_form_row(
            form, tr("video_gamma"),
            build_double_spin_box(0.1, _MAX_GAMMA, 1.0, decimals=2, step=0.05),
        )
        self._clahe_clip = add_form_row(
            form, tr("video_clahe_clip"),
            build_double_spin_box(0.0, _MAX_CLAHE_CLIP, 0.0, decimals=1, step=0.5),
        )
        add_hint_row(form, tr("video_display_note"))
        return box

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        self._http_enabled.setChecked(bool(self._config.get("video.http.enabled", True)))
        self._http_port.setValue(int(self._config.get("video.http.port", 8091)))
        self._http_quality.setValue(int(self._config.get("video.http.jpeg_quality", 80)))
        self._http_width.setValue(int(self._config.get("video.http.frame_width_px", 960)))
        self._http_log.setChecked(bool(self._config.get("video.http.log_connections", True)))

        self._rtsp_enabled.setChecked(bool(self._config.get("video.rtsp.enabled", False)))
        self._rtsp_port.setValue(int(self._config.get("video.rtsp.port", 8554)))
        set_combo_data(self._rtsp_codec, self._config.get("video.rtsp.codec", _DEFAULT_CODEC))
        self._rtsp_fps.setValue(int(self._config.get("video.rtsp.fps", 15)))
        self._rtsp_width.setValue(int(self._config.get("video.rtsp.frame_width_px", 0)))
        self._rtsp_interfaces.setText(join_list(self._config.get("video.rtsp.net_interfaces", [])))
        self._rtsp_log.setChecked(bool(self._config.get("video.rtsp.log_connections", True)))

        self._gamma.setValue(float(self._config.get("video.display.gamma", 1.0)))
        self._clahe_clip.setValue(float(self._config.get("video.display.clahe_clip", 0.0)))

    def save(self):
        self._config.set("video.http.enabled", self._http_enabled.isChecked())
        self._config.set("video.http.port", self._http_port.value())
        self._config.set("video.http.jpeg_quality", self._http_quality.value())
        self._config.set("video.http.frame_width_px", self._http_width.value())
        self._config.set("video.http.log_connections", self._http_log.isChecked())

        self._config.set("video.rtsp.enabled", self._rtsp_enabled.isChecked())
        self._config.set("video.rtsp.port", self._rtsp_port.value())
        self._config.set("video.rtsp.codec", self._rtsp_codec.currentData())
        self._config.set("video.rtsp.fps", self._rtsp_fps.value())
        self._config.set("video.rtsp.frame_width_px", self._rtsp_width.value())
        self._config.set("video.rtsp.net_interfaces", split_list(self._rtsp_interfaces.text()))
        self._config.set("video.rtsp.log_connections", self._rtsp_log.isChecked())

        self._config.set("video.display.gamma", self._gamma.value())
        self._config.set("video.display.clahe_clip", self._clahe_clip.value())
