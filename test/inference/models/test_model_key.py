"""
Tests de la derivación de la clave de los pesos.

El módulo generado por el build no existe en el repo —está en el `.gitignore`—, así que
acá se lo reemplaza por un objeto con los mismos atributos. Eso también prueba lo que
tiene que pasar sin él: que no haya clave y que nada se caiga.
"""

from types import SimpleNamespace

import pytest

from system.inference.models import model_key
from system.inference.models.encrypted_weights import KEY_SIZE

_SALT = b"salt-de-prueba01"
_FRAGMENTS = (b"primer-fragmento", b"segundo-fragmento", b"tercer-fragmento")


@pytest.fixture
def fork_key(monkeypatch):
    """Devuelve `install(**overrides)`, que deja puesto un módulo de clave de mentira."""

    def install(**overrides) -> SimpleNamespace:
        values = {"KEY_LABEL": "test-1", "SALT": _SALT, "FRAGMENTS": _FRAGMENTS}
        values.update(overrides)
        generated = SimpleNamespace(**values)
        monkeypatch.setattr(model_key, "_fork_key", generated)
        return generated

    return install


class TestWithoutGeneratedModule:
    def test_there_is_no_key(self, monkeypatch):
        monkeypatch.setattr(model_key, "_fork_key", None)
        assert model_key.get_model_key() is None
        assert not model_key.has_key()

    def test_the_label_says_there_is_no_key(self, monkeypatch):
        monkeypatch.setattr(model_key, "_fork_key", None)
        assert model_key.get_key_label() == model_key._NO_KEY_LABEL


class TestDerivation:
    def test_the_key_has_the_size_aes_256_needs(self, fork_key):
        fork_key()
        assert len(model_key.get_model_key()) == KEY_SIZE

    def test_the_same_material_always_derives_the_same_key(self, fork_key):
        fork_key()
        assert model_key.get_model_key() == model_key.get_model_key()

    def test_another_salt_derives_another_key(self, fork_key):
        fork_key()
        first = model_key.get_model_key()
        fork_key(SALT=b"otro-salt-000001")
        assert model_key.get_model_key() != first

    def test_another_fragment_derives_another_key(self, fork_key):
        fork_key()
        first = model_key.get_model_key()
        fork_key(FRAGMENTS=(_FRAGMENTS[0], _FRAGMENTS[1], b"otro-tercero"))
        assert model_key.get_model_key() != first

    def test_the_order_of_the_fragments_matters(self, fork_key):
        fork_key()
        first = model_key.get_model_key()
        fork_key(FRAGMENTS=tuple(reversed(_FRAGMENTS)))
        assert model_key.get_model_key() != first

    def test_the_label_comes_from_the_generated_module(self, fork_key):
        fork_key(KEY_LABEL="acme-linea3-1")
        assert model_key.get_key_label() == "acme-linea3-1"
        assert model_key.has_key()


class TestMalformedGeneratedModule:
    """Un módulo generado a medias se trata como ausente: derivar igual daría una clave
    que no abre nada, y el motivo que se leería mandaría a buscar el problema al lado
    equivocado."""

    @pytest.mark.parametrize("overrides", [
        {"SALT": None},
        {"SALT": b""},
        {"SALT": "no-son-bytes"},
        {"FRAGMENTS": ()},
        {"FRAGMENTS": None},
        {"FRAGMENTS": ("no", "son", "bytes")},
        {"FRAGMENTS": (b"vale", b"")},
    ])
    def test_there_is_no_key(self, fork_key, overrides: dict):
        fork_key(**overrides)
        assert model_key.get_model_key() is None
