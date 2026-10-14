"""
Tests de la pestaña de sistema: lo que identifica al equipo se muestra pero no se edita.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.views.config.system_tab import SystemTab                        # noqa: E402

_DEVICE_ID = "jetson-1"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get` punteado y `set`, sin archivo ni Lock."""

    def __init__(self):
        self._values = {"system.device_id": _DEVICE_ID}
        self.written: dict = {}

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self.written[key] = value


class TestDeviceId:
    def test_it_is_shown_but_not_editable(self):
        tab = SystemTab(_MockConfig())
        assert tab._device_id.text() == _DEVICE_ID
        assert tab._device_id.isReadOnly()

    def test_save_does_not_write_it(self):
        """Es la identidad del equipo en la telemetría: se cambia sólo en el archivo."""
        config = _MockConfig()
        tab = SystemTab(config)
        tab._device_id.setText("otro")
        tab.save()
        assert "system.device_id" not in config.written
