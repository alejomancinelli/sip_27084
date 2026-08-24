"""Tests de la fábrica de cámaras, el NullDriver y el contrato del driver."""

import os
import sys

import pytest


from tools.camera.abstract_driver import AbstractCameraDriver
from tools.camera.camera_factory import REGISTERED_DRIVERS, create_camera
from tools.camera.mock_driver import MockDriver
from tools.camera.null_driver import NullDriver

_STATUS_KEYS = {"connected", "capture_enabled", "temperature", "fps_estimated"}


class TestCameraFactory:
    def test_mock_driver_created_for_mock(self):
        assert isinstance(create_camera({"driver": "mock"}), MockDriver)

    def test_driver_name_is_normalized(self):
        assert isinstance(create_camera({"driver": "  MOCK  "}), MockDriver)

    def test_driver_is_abstract_subclass(self):
        assert isinstance(create_camera({"driver": "mock"}), AbstractCameraDriver)

    def test_registered_drivers_matches_what_the_factory_builds(self):
        """La lista que se publica para la UI y los mensajes no puede mentir."""
        for driver_type in REGISTERED_DRIVERS:
            driver = create_camera({"driver": driver_type})
            assert not driver.is_config_error, driver_type


class TestCameraFactoryConfigErrors:
    """Un driver inválido NO debe caer a mock: frames sintéticos con estado
    'conectada' ocultarían el error de configuración a la UI y al PLC."""

    @pytest.mark.parametrize("config", [
        {"driver": "driver_inexistente", "address": "0.0.0.0"},   # sin registrar
        {"address": "0.0.0.0"},                                   # campo ausente
        {"driver": None},                                         # driver: null en YAML
        {"driver": "   "},                                        # string vacío
    ])
    def test_invalid_driver_returns_null_driver(self, config):
        driver = create_camera(config)
        assert isinstance(driver, NullDriver)
        assert driver.is_config_error is True

    def test_null_driver_never_connects_nor_yields_frames(self):
        driver = create_camera({"driver": "driver_inexistente"})
        assert driver.connect() is False
        assert driver.is_connected is False
        assert driver.get_frame() is None

    def test_null_driver_status_reports_disconnected_with_reason(self):
        driver = create_camera({"driver": "driver_inexistente"})
        status = driver.get_status()
        assert status["connected"] is False
        assert status["capture_enabled"] is False
        assert status["fps_estimated"] == 0.0
        assert "driver_inexistente" in status["error"]


class TestStatusContract:
    """Todos los drivers tienen que devolver las mismas claves: el consumidor no
    sabe qué driver le tocó."""

    @pytest.mark.parametrize("driver_type", ["mock", "driver_inexistente"])
    def test_status_has_the_stable_keys(self, driver_type):
        status = create_camera({"driver": driver_type}).get_status()
        assert _STATUS_KEYS <= set(status)

    def test_markers_are_declared_on_the_abstraction(self):
        """Sin esto el consumidor necesita getattr() para leerlos."""
        assert AbstractCameraDriver.is_synthetic is False
        assert AbstractCameraDriver.is_config_error is False
        assert MockDriver.is_synthetic is True
        assert NullDriver.is_config_error is True
