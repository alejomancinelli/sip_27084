"""Tests del MockDriver: es el único driver que corre sin hardware, así que es
donde se verifica el contrato que la base impone a todos."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.camera.mock_driver import MockDriver

# fps alto para que los frames no cuesten tiempo de test real.
_FAST = {"acquisition": {"fps_limit": 200}}


def _connected(**extra) -> MockDriver:
    driver = MockDriver({**_FAST, **extra})
    driver.connect()
    return driver


class TestCaptureEnabled:
    def test_capture_is_enabled_by_default(self):
        assert _connected().is_capture_enabled is True

    def test_config_can_start_with_capture_disabled(self):
        driver = _connected(enabled=False)
        assert driver.is_capture_enabled is False
        assert driver.get_frame() is None

    def test_disabled_capture_yields_no_frames_while_still_connected(self):
        """Deshabilitada no es lo mismo que desconectada: el consumidor las separa."""
        driver = _connected()
        assert driver.get_frame() is not None

        driver.set_capture_enabled(False)
        assert driver.get_frame() is None
        assert driver.is_connected is True
        assert driver.get_status()["connected"] is True
        assert driver.get_status()["capture_enabled"] is False

    def test_capture_can_be_re_enabled(self):
        driver = _connected(enabled=False)
        driver.set_capture_enabled(True)
        assert driver.get_frame() is not None
        assert driver.get_status()["capture_enabled"] is True

    def test_setting_the_same_value_is_a_no_op(self):
        driver = _connected()
        driver.set_capture_enabled(True)
        assert driver.get_frame() is not None


class TestMeasuredFps:
    def test_fps_is_zero_before_two_frames(self):
        driver = _connected()
        assert driver.get_status()["fps_estimated"] == 0.0
        driver.get_frame()
        assert driver.get_status()["fps_estimated"] == 0.0

    def test_fps_is_measured_from_delivered_frames(self):
        driver = _connected()
        for _ in range(10):
            driver.get_frame()
        assert driver.get_status()["fps_estimated"] > 0.0

    def test_disabled_capture_does_not_add_frames_to_the_measurement(self):
        driver = _connected()
        driver.set_capture_enabled(False)
        for _ in range(5):
            driver.get_frame()
        assert driver.get_status()["fps_estimated"] == 0.0


class TestRotation:
    def test_rotation_comes_from_the_base_class(self):
        straight = _connected().get_frame()
        rotated = _connected(rotation="90cw").get_frame()
        assert rotated.shape[:2] == straight.shape[:2][::-1]

    def test_unknown_rotation_is_ignored(self):
        assert _connected(rotation="45").get_frame().shape == _connected().get_frame().shape
