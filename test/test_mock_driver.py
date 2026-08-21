"""Tests del MockDriver: es el único driver que corre sin hardware, así que es
donde se verifica el contrato que la base impone a todos."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.camera.mock_driver import MockDriver

# fps alto para que los frames no cuesten tiempo de test real.
_FAST = {"acquisition": {"fps_limit": 200}}


def _connected(**extra) -> MockDriver:
    _driver = MockDriver({**_FAST, **extra})
    _driver.connect()
    return _driver


class TestCaptureEnabled:
    def test_capture_is_enabled_by_default(self):
        assert _connected().is_capture_enabled is True

    def test_config_can_start_with_capture_disabled(self):
        _driver = _connected(enabled=False)
        assert _driver.is_capture_enabled is False
        assert _driver.get_frame() is None

    def test_disabled_capture_yields_no_frames_while_still_connected(self):
        """Deshabilitada no es lo mismo que desconectada: el consumidor las separa."""
        _driver = _connected()
        assert _driver.get_frame() is not None

        _driver.set_capture_enabled(False)
        assert _driver.get_frame() is None
        assert _driver.is_connected is True
        assert _driver.get_status()["connected"] is True
        assert _driver.get_status()["capture_enabled"] is False

    def test_capture_can_be_re_enabled(self):
        _driver = _connected(enabled=False)
        _driver.set_capture_enabled(True)
        assert _driver.get_frame() is not None
        assert _driver.get_status()["capture_enabled"] is True

    def test_setting_the_same_value_is_a_no_op(self):
        _driver = _connected()
        _driver.set_capture_enabled(True)
        assert _driver.get_frame() is not None


class TestMeasuredFps:
    def test_fps_is_zero_before_two_frames(self):
        _driver = _connected()
        assert _driver.get_status()["fps_estimated"] == 0.0
        _driver.get_frame()
        assert _driver.get_status()["fps_estimated"] == 0.0

    def test_fps_is_measured_from_delivered_frames(self):
        _driver = _connected()
        for _ in range(10):
            _driver.get_frame()
        assert _driver.get_status()["fps_estimated"] > 0.0

    def test_disabled_capture_does_not_add_frames_to_the_measurement(self):
        _driver = _connected()
        _driver.set_capture_enabled(False)
        for _ in range(5):
            _driver.get_frame()
        assert _driver.get_status()["fps_estimated"] == 0.0


class TestRotation:
    def test_rotation_comes_from_the_base_class(self):
        _straight = _connected().get_frame()
        _rotated = _connected(rotation="90cw").get_frame()
        assert _rotated.shape[:2] == _straight.shape[:2][::-1]

    def test_unknown_rotation_is_ignored(self):
        assert _connected(rotation="45").get_frame().shape == _connected().get_frame().shape
