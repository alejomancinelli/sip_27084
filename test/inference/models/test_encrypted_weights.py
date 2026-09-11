"""
Tests del contenedor de pesos cifrados: el formato y lo que pasa cuando algo no cierra.

Sin hardware, sin GPU y sin framework: la clave se genera en el propio test, como en
`test/license`, así que no hay ninguna clave de prueba versionada que alguien pueda
confundir con una de verdad.
"""

import os

import pytest

from system.inference.models import encrypted_weights as ew

_WEIGHTS = b"\x00\x01\x02pesos-de-mentira" * 64
_METADATA = {"task": "detection", "class_names": ["tornillo", "tuerca"]}


def _key() -> bytes:
    return os.urandom(ew.KEY_SIZE)


class TestRoundTrip:
    def test_weights_survive_the_round_trip(self):
        key = _key()
        bundle = ew.unpack(ew.pack(_WEIGHTS, key), key)
        assert bundle.weights == _WEIGHTS

    def test_metadata_survives_the_round_trip(self):
        key = _key()
        bundle = ew.unpack(ew.pack(_WEIGHTS, key, _METADATA), key)
        assert bundle.metadata == _METADATA

    def test_bundle_is_marked_as_protected(self):
        key = _key()
        assert ew.unpack(ew.pack(_WEIGHTS, key), key).is_protected

    def test_metadata_is_empty_when_none_was_packed(self):
        key = _key()
        assert ew.unpack(ew.pack(_WEIGHTS, key), key).metadata == {}

    def test_container_declares_the_magic_and_the_version(self):
        raw = ew.pack(_WEIGHTS, _key())
        assert raw[:len(ew.MAGIC)] == ew.MAGIC
        assert raw[len(ew.MAGIC)] == ew.FORMAT_VERSION
        assert ew.is_encrypted(raw)


class TestConfidentiality:
    def test_the_weights_are_not_in_the_container_in_the_clear(self):
        raw = ew.pack(_WEIGHTS, _key())
        assert _WEIGHTS not in raw

    def test_the_metadata_is_not_in_the_container_in_the_clear(self):
        raw = ew.pack(_WEIGHTS, _key(), _METADATA)
        assert b"tornillo" not in raw

    def test_two_packs_of_the_same_weights_use_a_different_nonce(self):
        key = _key()
        first = ew.pack(_WEIGHTS, key)
        second = ew.pack(_WEIGHTS, key)
        nonce_slice = slice(len(ew.MAGIC) + 1, ew.HEADER_SIZE)
        assert first[nonce_slice] != second[nonce_slice]
        assert first != second


class TestFailures:
    def test_the_wrong_key_fails_with_a_readable_reason(self):
        raw = ew.pack(_WEIGHTS, _key())
        with pytest.raises(ew.WeightsDecryptError) as error:
            ew.unpack(raw, _key())
        assert "clave" in str(error.value).lower()

    def test_no_key_at_all_fails_with_its_own_reason(self):
        raw = ew.pack(_WEIGHTS, _key())
        with pytest.raises(ew.WeightsDecryptError) as error:
            ew.unpack(raw, None)
        assert "clave" in str(error.value).lower()

    def test_a_key_of_the_wrong_size_is_rejected(self):
        with pytest.raises(ew.WeightsDecryptError):
            ew.pack(_WEIGHTS, b"corta")

    @pytest.mark.parametrize("offset", [ew.HEADER_SIZE, ew.HEADER_SIZE + 40, -1])
    def test_a_single_flipped_bit_does_not_open(self, offset: int):
        key = _key()
        raw = bytearray(ew.pack(_WEIGHTS, key, _METADATA))
        raw[offset] ^= 0x01
        with pytest.raises(ew.WeightsDecryptError):
            ew.unpack(bytes(raw), key)

    def test_a_flipped_bit_in_the_header_does_not_open_either(self):
        # El encabezado va como dato autenticado del propio GCM: editar el nonce rompe
        # el tag igual que editar el texto cifrado.
        key = _key()
        raw = bytearray(ew.pack(_WEIGHTS, key))
        raw[len(ew.MAGIC) + 1] ^= 0x01
        with pytest.raises(ew.WeightsDecryptError):
            ew.unpack(bytes(raw), key)

    def test_a_truncated_container_fails_as_a_format_error(self):
        key = _key()
        raw = ew.pack(_WEIGHTS, key)
        with pytest.raises(ew.WeightsFormatError):
            ew.unpack(raw[:ew.HEADER_SIZE + 4], key)

    def test_an_unknown_format_version_is_rejected_instead_of_guessed(self):
        key = _key()
        raw = bytearray(ew.pack(_WEIGHTS, key))
        raw[len(ew.MAGIC)] = ew.FORMAT_VERSION + 1
        with pytest.raises(ew.WeightsFormatError) as error:
            ew.unpack(bytes(raw), key)
        assert "versión" in str(error.value).lower()

    def test_metadata_that_is_not_serializable_is_refused_when_packing(self):
        with pytest.raises(ew.WeightsFormatError):
            ew.pack(_WEIGHTS, _key(), {"model": object()})


class TestPlainWeights:
    def test_plain_weights_come_back_untouched_and_without_a_key(self):
        bundle = ew.unpack(_WEIGHTS)
        assert bundle.weights == _WEIGHTS
        assert bundle.metadata == {}
        assert not bundle.is_protected

    def test_plain_weights_are_not_reported_as_encrypted(self):
        assert not ew.is_encrypted(_WEIGHTS)
        assert not ew.is_encrypted(b"")

    def test_an_empty_file_is_not_a_container(self):
        assert ew.unpack(b"").weights == b""
