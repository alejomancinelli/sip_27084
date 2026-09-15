"""Tests de la tabla de claves: cuál gana, qué se descarta y qué pasa sin módulo generado.

El `_public_key.py` del build entregado no existe en un checkout de fuentes —está en el
`.gitignore`— así que acá se arma uno de mentira con la misma API, que es lo único que
este archivo le pide.
"""

import pytest

from system.license import public_key, schema

BUILD_KEY_ID = "iea-1"
SOURCE_KEY_ID = "test-key"


class FakeGeneratedKeys:
    """Lo que genera el repositorio de firma, reducido a lo que se le llama."""

    def __init__(self, keys: dict):
        self._keys = keys

    def get_public_key(self, key_id: str) -> bytes | None:
        return self._keys.get(key_id)

    def has_any_key(self) -> bool:
        return bool(self._keys)


@pytest.fixture
def build_keys(monkeypatch):
    """Devuelve `install(**keys)` para poner una tabla generada mientras dura el test."""
    def install(**keys):
        monkeypatch.setattr(public_key, "_generated_keys", FakeGeneratedKeys(keys))

    return install


@pytest.fixture
def source_key(monkeypatch):
    """Clave puesta a mano en la tabla en código, como hace el conftest de la suite."""
    raw = bytes(range(32))
    monkeypatch.setitem(public_key.PUBLIC_KEYS, SOURCE_KEY_ID, schema.encode_b64url(raw))
    return raw


class TestWithoutGeneratedModule:
    """Un checkout de fuentes: no hay build y la tabla en código es todo lo que hay."""

    def test_the_template_carries_no_key(self):
        assert not public_key.PUBLIC_KEYS
        assert public_key.get_public_key("iea-1") is None

    def test_a_key_put_in_by_hand_is_found(self, source_key):
        assert public_key.get_public_key(SOURCE_KEY_ID) == source_key
        assert public_key.has_any_key()

    def test_an_unknown_key_id_is_absent(self, source_key):
        assert public_key.get_public_key("otra") is None

    def test_a_key_of_the_wrong_length_is_treated_as_absent(self, monkeypatch):
        # Es un error de tipeo al pegarla: dejarla pasar terminaría en «la firma no
        # cierra», que manda a buscar el problema al lado equivocado.
        monkeypatch.setitem(public_key.PUBLIC_KEYS, "corta", schema.encode_b64url(b"corta"))
        assert public_key.get_public_key("corta") is None
        assert not public_key.has_any_key()

    def test_something_that_is_not_base64url_is_treated_as_absent(self, monkeypatch):
        monkeypatch.setitem(public_key.PUBLIC_KEYS, "rota", "no es base64 %%%")
        assert public_key.get_public_key("rota") is None


class TestWithGeneratedModule:
    """El build entregado: la tabla la escribió el repositorio de firma."""

    def test_the_generated_key_is_used(self, build_keys):
        raw = bytes(range(32))
        build_keys(**{BUILD_KEY_ID: raw})
        assert public_key.get_public_key(BUILD_KEY_ID) == raw
        assert public_key.has_any_key()

    def test_a_generated_key_of_the_wrong_length_is_absent(self, build_keys):
        build_keys(**{BUILD_KEY_ID: b"no son 32 bytes"})
        assert public_key.get_public_key(BUILD_KEY_ID) is None

    def test_the_source_table_still_works_next_to_it(self, build_keys, source_key):
        # Los tests inyectan su clave sin tocar el archivo generado, que no se edita.
        build_keys(**{BUILD_KEY_ID: bytes(range(32))})
        assert public_key.get_public_key(SOURCE_KEY_ID) == source_key

    def test_an_empty_generated_table_is_a_build_without_keys(self, build_keys):
        build_keys()
        assert not public_key.has_any_key()
        assert public_key.get_public_key(BUILD_KEY_ID) is None
