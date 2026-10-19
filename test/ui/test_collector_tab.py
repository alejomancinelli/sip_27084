"""
Tests de la pestaña de dataset: qué modos ofrece según haya ciclo de medición, y que el
modo y la lista de cámaras que se guardan vayan y vuelvan del config sin deformarse.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.views.config.collector_tab import CollectorTab                  # noqa: E402

_SLOTS = ("camera_1", "camera_2", "camera_3")


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get` punteado y `set`, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            "cameras": {slot: {} for slot in _SLOTS},
            "image_collector.enabled": True,
        }
        self._values.update(overrides)
        self.written: dict = {}

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self.written[key] = value


def _tab(**overrides) -> tuple[CollectorTab, _MockConfig]:
    config = _MockConfig(**overrides)
    return CollectorTab(config), config


def _checked(tab: CollectorTab) -> list[str]:
    return [slot for slot, check in tab._camera_checks.items() if check.isChecked()]


class TestCameras:
    def test_one_checkbox_per_configured_camera(self):
        tab, _ = _tab()
        assert tuple(tab._camera_checks) == _SLOTS

    def test_an_empty_list_checks_every_camera_and_saves_empty(self):
        tab, config = _tab(**{"image_collector.cameras": []})
        assert _checked(tab) == list(_SLOTS)
        tab.save()
        assert config.written["image_collector.cameras"] == []

    def test_a_missing_key_checks_every_camera(self):
        tab, _ = _tab()
        assert _checked(tab) == list(_SLOTS)

    def test_a_list_round_trips(self):
        tab, config = _tab(**{"image_collector.cameras": ["camera_2"]})
        assert _checked(tab) == ["camera_2"]
        tab.save()
        assert config.written["image_collector.cameras"] == ["camera_2"]

    def test_unchecking_one_saves_the_others(self):
        tab, config = _tab()
        tab._camera_checks["camera_1"].setChecked(False)
        tab.save()
        assert config.written["image_collector.cameras"] == ["camera_2", "camera_3"]

    def test_checking_them_all_saves_empty(self):
        """Todas marcadas es [] y no la lista: así entra también la cámara que se agregue."""
        tab, config = _tab(**{"image_collector.cameras": ["camera_1"]})
        for check in tab._camera_checks.values():
            check.setChecked(True)
        tab.save()
        assert config.written["image_collector.cameras"] == []

    def test_the_last_checked_camera_cannot_be_unchecked(self):
        """Ninguna marcada se guardaría como [], que es todas."""
        tab, config = _tab(**{"image_collector.cameras": ["camera_3"]})
        tab._camera_checks["camera_3"].setChecked(False)
        assert _checked(tab) == ["camera_3"]
        tab.save()
        assert config.written["image_collector.cameras"] == ["camera_3"]

    def test_a_slot_that_is_not_configured_goes_back_to_the_file(self):
        tab, config = _tab(**{"image_collector.cameras": ["camera_1", "camera_9"]})
        assert _checked(tab) == ["camera_1"]
        tab.save()
        assert config.written["image_collector.cameras"] == ["camera_1", "camera_9"]

    def test_turning_collection_off_does_not_uncheck_them(self):
        """El toggle de recolección atenúa las cámaras, pero no las desmarca."""
        tab, config = _tab(**{"image_collector.cameras": ["camera_2"]})
        tab._enabled.setChecked(False)
        assert not tab._camera_checks["camera_2"].isEnabled()
        tab.save()
        assert config.written["image_collector.cameras"] == ["camera_2"]


def _with_cycle(frames_per_cycle: int, mode: str) -> dict:
    return {
        "inference.pipelines": {"pipeline_1": {}},
        "inference.pipelines.pipeline_1.frames_per_cycle": frames_per_cycle,
        "image_collector.mode": mode,
    }


def _modes(tab: CollectorTab) -> list[str]:
    return [tab._mode_combo.itemData(i) for i in range(tab._mode_combo.count())]


class TestMode:
    def test_with_a_cycle_both_modes_are_offered(self):
        tab, _ = _tab(**_with_cycle(5, "interval"))
        assert _modes(tab) == ["on_demand", "interval"]
        assert tab._no_cycle_warning.isHidden()

    def test_without_a_cycle_only_interval_is_offered(self):
        tab, _ = _tab(**_with_cycle(1, "interval"))
        assert _modes(tab) == ["interval"]
        assert tab._no_cycle_warning.isHidden()

    def test_on_demand_without_a_cycle_is_kept_with_a_warning(self):
        """Ocultarla y guardar cambiaría el modo en silencio."""
        tab, _ = _tab(**_with_cycle(1, "on_demand"))
        assert _modes(tab) == ["on_demand", "interval"]
        assert tab._mode_combo.currentData() == "on_demand"
        assert not tab._no_cycle_warning.isHidden()

    def test_on_demand_without_a_cycle_goes_back_to_the_file(self):
        tab, config = _tab(**_with_cycle(1, "on_demand"))
        tab.save()
        assert config.written["image_collector.mode"] == "on_demand"

    def test_choosing_interval_hides_the_warning(self):
        tab, _ = _tab(**_with_cycle(1, "on_demand"))
        tab._mode_combo.setCurrentIndex(_modes(tab).index("interval"))
        assert tab._no_cycle_warning.isHidden()

    def test_the_rule_is_re_evaluated_on_load(self):
        tab, config = _tab(**_with_cycle(1, "interval"))
        config._values["inference.pipelines.pipeline_1.frames_per_cycle"] = 5
        tab.load()
        assert _modes(tab) == ["on_demand", "interval"]
        assert tab._mode_combo.currentData() == "interval"
