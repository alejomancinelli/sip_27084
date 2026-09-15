"""Tests de la solicitud: qué lleva y cuándo el equipo directamente no se puede licenciar."""

import json

import pytest

from system.license import fingerprint, request, schema

from .conftest import TEST_COMPONENTS, FakeConfig


@pytest.fixture
def machine_sources(monkeypatch):
    """Devuelve `set(count)` para simular cuántas fuentes de huella expone el equipo."""
    def set_sources(count: int):
        components = dict(list(TEST_COMPONENTS.items())[:count])
        monkeypatch.setattr(fingerprint, "read_components", lambda **kwargs: components)
        return components

    return set_sources


class TestBuildRequest:
    def test_a_normal_machine_asks_for_the_floor(self, machine_sources):
        components = machine_sources(3)
        built = request.build_request(FakeConfig())

        assert built["fingerprint"]["components"] == components
        assert built["fingerprint"]["suggested_min_matches"] == schema.FINGERPRINT_MIN_FLOOR

    def test_a_machine_below_the_floor_cannot_be_licensed(self, machine_sources):
        # El que firma se va a negar igual: enterarse acá es enterarse con el equipo
        # delante, y no por mail tres días después.
        machine_sources(1)
        with pytest.raises(request.WeakFingerprintError):
            request.build_request(FakeConfig())

    def test_a_machine_with_no_sources_at_all_says_so(self, machine_sources):
        machine_sources(0)
        with pytest.raises(request.WeakFingerprintError, match="ninguna"):
            request.build_request(FakeConfig())

    def test_the_reason_names_the_sources_that_were_read(self, machine_sources):
        machine_sources(1)
        with pytest.raises(request.WeakFingerprintError, match=fingerprint.SOURCE_BOARD_UUID):
            request.build_request(FakeConfig())


class TestSaveRequest:
    def test_the_file_is_written_and_is_readable_json(self, machine_sources, tmp_path):
        machine_sources(3)
        path = request.save_request(FakeConfig(), str(tmp_path / "solicitud.json"))

        with open(path, "r", encoding="utf-8") as handle:
            assert json.load(handle)["schema"] == request.REQUEST_SCHEMA_VERSION

    def test_nothing_is_written_when_the_machine_cannot_be_licensed(self, machine_sources,
                                                                    tmp_path):
        machine_sources(1)
        path = tmp_path / "solicitud.json"

        with pytest.raises(request.WeakFingerprintError):
            request.save_request(FakeConfig(), str(path))
        assert not path.exists()
