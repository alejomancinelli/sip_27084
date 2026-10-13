"""
Tests del panel de cámara: lo que cuesta pintar un frame en el hilo de la GUI.

Construye widgets, así que necesita un `QApplication` sin pantalla —plataforma
`offscreen`—, igual que `test_process_tab.py`.
"""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.widgets.camera_panel import CameraPanel                         # noqa: E402

_SLOT = "camera_1"
_FRAME_SHAPE = (1536, 2048, 3)      # un 3 MP, como el de la cinta


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    def get(self, key: str, default: object = None) -> object:
        return {f"cameras.{_SLOT}.name": "Cinta"}.get(key, default)


def _frame() -> np.ndarray:
    return np.full(_FRAME_SHAPE, 90, np.uint8)


def _shown_panel() -> CameraPanel:
    panel = CameraPanel(_SLOT, _MockConfig())
    panel.resize(660, 520)
    panel.show()
    QApplication.processEvents()
    return panel


class TestPainting:
    def test_a_hidden_panel_keeps_the_frame_without_painting_it(self):
        """Nadie lo ve: la conversión de 3 MP por frame era gasto puro en la GUI."""
        panel = CameraPanel(_SLOT, _MockConfig())
        panel.update_frame(_frame())
        assert panel._video_label.pixmap().isNull()

    def test_it_paints_the_kept_frame_when_shown(self):
        panel = CameraPanel(_SLOT, _MockConfig())
        panel.update_frame(_frame())
        panel.resize(660, 520)
        panel.show()
        QApplication.processEvents()
        assert not panel._video_label.pixmap().isNull()

    def test_the_pixmap_fits_the_label_and_keeps_the_aspect(self):
        panel = _shown_panel()
        panel.update_frame(_frame())
        pixmap = panel._video_label.pixmap()
        label = panel._video_label.size()
        assert pixmap.width() <= label.width() and pixmap.height() <= label.height()
        assert abs(pixmap.width() / pixmap.height() - 2048 / 1536) < 0.02

    def test_the_frame_is_shrunk_before_it_reaches_qt(self, monkeypatch):
        """Qt recibe una imagen del tamaño del label, no los 3 MP del sensor."""
        import ui.widgets.camera_panel as camera_panel
        sizes = []

        class _SpyImage(camera_panel.QImage):
            def __init__(self, data, width, height, *args):
                sizes.append((width, height))
                super().__init__(data, width, height, *args)

        panel = _shown_panel()
        monkeypatch.setattr(camera_panel, "QImage", _SpyImage)
        panel.update_frame(_frame())
        label = panel._video_label.size()
        assert sizes and all(w <= label.width() and h <= label.height() for w, h in sizes)
