"""Tests del contrato de los backends de telemetría: qué tiene que implementar un
destino nuevo y qué garantiza la base a quien lo consume.

Los backends concretos se prueban en su propio archivo; acá se verifica lo que
todos comparten, que es lo que PersistenceThread y la UI dan por cierto."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from system.backends import (
    STATUS_CONNECTED,
    STATUS_CONNECTING,
    STATUS_DISABLED,
    STATUS_ERROR,
    AbstractTelemetryBackend,
    InfluxDBBackend,
    MqttBackend,
)

_IMPLEMENTATIONS = (InfluxDBBackend, MqttBackend)


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def get(self, key: str, default: object = None) -> object:
        return default


# Implementaciones vacías con las que se arman backends a los que les falta uno de
# los métodos del contrato.
_NO_OPS = {
    "setup": lambda self: None,
    "write": lambda self, batch: None,
    "close": lambda self: None,
}


class _MinimalBackend(AbstractTelemetryBackend):
    """Lo mínimo que hay que escribir para sumar un destino nuevo."""

    service_name = "minimal"

    def setup(self):
        self._status = STATUS_CONNECTED

    def write(self, batch: list):
        pass

    def close(self):
        self._status = STATUS_DISABLED


class TestContract:
    @pytest.mark.parametrize("backend_class", _IMPLEMENTATIONS)
    def test_every_backend_implements_the_abstraction(self, backend_class):
        assert issubclass(backend_class, AbstractTelemetryBackend)

    @pytest.mark.parametrize("backend_class", _IMPLEMENTATIONS)
    def test_every_backend_names_its_service(self, backend_class):
        """El nombre identifica al backend y nombra su sección de config."""
        assert backend_class.service_name

    def test_the_service_names_are_unique(self):
        """backend_status() los busca por nombre: repetirlos taparía a uno."""
        names = [backend_class.service_name for backend_class in _IMPLEMENTATIONS]
        assert len(set(names)) == len(names)

    def test_the_abstraction_does_not_name_a_service(self):
        assert AbstractTelemetryBackend.service_name == ""

    @pytest.mark.parametrize("missing", ["setup", "write", "close"])
    def test_each_method_of_the_contract_is_mandatory(self, missing):
        """Un destino a medio implementar falla al construirse, no al escribir."""
        implemented = {name: no_op for name, no_op in _NO_OPS.items() if name != missing}
        incomplete = type("_Incomplete", (AbstractTelemetryBackend,),
                          {"service_name": "incompleto", **implemented})
        with pytest.raises(TypeError):
            incomplete(_MockConfig())

    def test_the_abstraction_itself_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            AbstractTelemetryBackend(_MockConfig())


class TestStatus:
    @pytest.mark.parametrize("backend_class", _IMPLEMENTATIONS + (_MinimalBackend,))
    def test_a_backend_starts_disabled(self, backend_class):
        """Antes del setup no hay nada abierto: la UI no puede mostrarlo conectado."""
        assert backend_class(_MockConfig()).status == STATUS_DISABLED

    def test_the_status_is_read_only_from_outside(self):
        """El estado lo mueve el backend; nadie se lo escribe desde afuera."""
        backend = _MinimalBackend(_MockConfig())
        with pytest.raises(AttributeError):
            backend.status = STATUS_CONNECTED

    def test_the_implementation_moves_its_own_status(self):
        backend = _MinimalBackend(_MockConfig())
        backend.setup()
        assert backend.status == STATUS_CONNECTED
        backend.close()
        assert backend.status == STATUS_DISABLED

    def test_the_vocabulary_is_the_one_the_ui_expects(self):
        """Los literales viven acá: la UI y los tests no los repiten."""
        assert (STATUS_DISABLED, STATUS_CONNECTING, STATUS_CONNECTED, STATUS_ERROR) == (
            "disabled", "connecting", "connected", "error")


class TestConfigAccess:
    @pytest.mark.parametrize("backend_class", _IMPLEMENTATIONS)
    def test_a_disabled_backend_does_nothing(self, backend_class):
        """Sin su `enabled`, setup/write/close no tocan nada y no levantan."""
        backend = backend_class(_MockConfig())
        backend.setup()
        backend.write([{"measurement": "camera_1", "fields": {"x": 1}, "time": 1.0}])
        backend.close()
        assert backend.status == STATUS_DISABLED
