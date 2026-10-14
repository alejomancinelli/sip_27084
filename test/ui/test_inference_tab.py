"""
Tests de la pestaña de inferencia: qué secciones edita y que `load()`/`save()` no deformen
lo que hay en el config.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.views.config.inference_tab import InferenceTab                  # noqa: E402

_MODEL = "segmenter"
_PIPELINE = "pipeline_1"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get` punteado y `set`, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            "inference.models": {_MODEL: {}},
            f"inference.models.{_MODEL}.type": "mock",
            "inference.pipelines": {_PIPELINE: {}},
            f"inference.pipelines.{_PIPELINE}.cameras": ["camera_1"],
            f"inference.pipelines.{_PIPELINE}.frames_per_cycle": 3,
            "inference.overlay.font_scale": 1.6,
        }
        self._values.update(overrides)
        self.written: dict = {}

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self.written[key] = value


def _tab(**overrides) -> tuple[InferenceTab, _MockConfig]:
    config = _MockConfig(**overrides)
    return InferenceTab(config), config


class TestOverlay:
    def test_save_does_not_touch_the_overlay(self):
        """El anotado se edita sólo en el config.yaml: la pestaña no escribe ninguna clave."""
        tab, config = _tab()
        tab.save()
        assert not [key for key in config.written if key.startswith("inference.overlay")]


class TestRoundTrip:
    def test_the_model_and_the_pipeline_go_back_as_they_were(self):
        tab, config = _tab()
        tab.save()
        assert config.written[f"inference.models.{_MODEL}.type"] == "mock"
        assert config.written[f"inference.pipelines.{_PIPELINE}.cameras"] == ["camera_1"]
        assert config.written[f"inference.pipelines.{_PIPELINE}.frames_per_cycle"] == 3
