"""Tests de la palabra de estado del sistema: con cualquier bit en 1 las
mediciones de proceso no son aptas para control."""

import pytest

from system.formats.system_status import (
    BIT_DEAD_THREAD,
    BIT_FALLBACK_CONFIG,
    BIT_INFERENCE_ERROR,
    BIT_LICENSE_INVALID,
    BIT_MODEL_NOT_LOADED,
    DESCRIPTIONS,
    WORD_MASK,
    describe,
    pack,
)

# Cada bandera con el bit que prende. `model_loaded` no está acá porque va negada y se
# prueba aparte. Agregar un bit al módulo es agregar una línea a este mapa: los tests de
# superficie cazan el que se agregue en un lado y no en el otro.
_BIT_BY_KEYWORD = {
    "inference_error": BIT_INFERENCE_ERROR,
    "fallback_config": BIT_FALLBACK_CONFIG,
    "dead_thread": BIT_DEAD_THREAD,
    "license_invalid": BIT_LICENSE_INVALID,
}

# Todo en orden: la única combinación que tiene que dar 0.
_ALL_GOOD = {"model_loaded": True, **{keyword: False for keyword in _BIT_BY_KEYWORD}}

# Todo mal: la que tiene que dar la máscara entera.
_ALL_BAD = {"model_loaded": False, **{keyword: True for keyword in _BIT_BY_KEYWORD}}

# El bit libre más bajo, para probar qué hace `describe` con algo que no conoce.
_UNUSED_BIT = max(DESCRIPTIONS) + 1


class TestBitLayout:
    def test_bits_are_distinct(self):
        bits = (BIT_MODEL_NOT_LOADED, *_BIT_BY_KEYWORD.values())
        assert len(set(bits)) == len(bits)

    def test_word_mask_covers_exactly_the_used_bits(self):
        expected = 0
        for bit in DESCRIPTIONS:
            expected |= 1 << bit
        assert WORD_MASK == expected

    def test_every_bit_has_a_description(self):
        assert set(DESCRIPTIONS) == {BIT_MODEL_NOT_LOADED, *_BIT_BY_KEYWORD.values()}


class TestPack:
    def test_everything_fine_is_zero(self):
        assert pack(**_ALL_GOOD) == 0

    def test_model_not_loaded_is_negated(self):
        """La entrada dice `model_loaded`; el bit dice lo contrario."""
        assert pack(**{**_ALL_GOOD, "model_loaded": False}) == 1 << BIT_MODEL_NOT_LOADED

    def test_each_flag_sets_only_its_own_bit(self):
        for keyword, bit in _BIT_BY_KEYWORD.items():
            assert pack(**{**_ALL_GOOD, keyword: True}) == 1 << bit

    def test_flags_accumulate(self):
        assert pack(**_ALL_BAD) == WORD_MASK

    def test_word_stays_inside_the_mask(self):
        assert pack(**_ALL_BAD) & ~WORD_MASK == 0

    def test_any_bit_means_the_measurements_are_not_trustworthy(self):
        for keyword in _BIT_BY_KEYWORD:
            assert pack(**{**_ALL_GOOD, keyword: True}) != 0

    def test_a_bad_licence_is_not_trustworthy_either(self):
        # No es un bit comercial metido en una palabra de proceso: con la licencia caída
        # el equipo puede seguir publicando números que no se distinguen de los buenos,
        # que es el criterio que comparten todos los bits de esta palabra.
        assert pack(**{**_ALL_GOOD, "license_invalid": True}) == 1 << BIT_LICENSE_INVALID

    def test_is_keyword_only(self):
        """Son banderas del mismo tipo: un orden posicional se equivoca callado."""
        with pytest.raises(TypeError):
            pack(True, False, False, False, False)

    def test_every_flag_is_required(self):
        """Un default silencioso taparía un estado que nadie midió."""
        with pytest.raises(TypeError):
            pack(model_loaded=True)


class TestDescribe:
    def test_zero_is_ok(self):
        assert describe(0) == "OK"

    def test_lists_the_active_bits(self):
        text = describe(pack(**{**_ALL_GOOD, "dead_thread": True}))
        assert DESCRIPTIONS[BIT_DEAD_THREAD] in text
        assert DESCRIPTIONS[BIT_INFERENCE_ERROR] not in text

    def test_unknown_bit_reports_the_raw_word(self):
        assert f"0x{1 << _UNUSED_BIT:04X}" in describe(1 << _UNUSED_BIT)

    def test_every_bit_describes_itself(self):
        for bit, text in DESCRIPTIONS.items():
            assert describe(1 << bit) == text
