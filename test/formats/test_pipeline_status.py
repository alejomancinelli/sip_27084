"""Tests de la palabra de estado de un pipeline: con todos los bits en 0 sus números son
una medición, y cualquier bit dice por qué no."""

import pytest

from system.formats.pipeline_status import (
    BIT_DISABLED,
    BIT_GATED,
    BIT_INVALID_RESULT,
    BIT_NOT_LICENSED,
    BIT_NOT_LOADED,
    BIT_NOT_RUNNING,
    BIT_SYNTHETIC,
    DESCRIPTIONS,
    WORD_MASK,
    describe,
    pack,
)

# Las entradas afirmativas, con el bit que prenden cuando son False. Agregar un bit al
# módulo es agregar una línea acá o en `_BIT_BY_TRUE_KEYWORD`: los tests de superficie
# cazan el que se agregue en un lado y no en el otro.
_BIT_BY_FALSE_KEYWORD = {
    "licensed": BIT_NOT_LICENSED,
    "enabled": BIT_DISABLED,
    "loaded": BIT_NOT_LOADED,
    "running": BIT_NOT_RUNNING,
    "last_result_valid": BIT_INVALID_RESULT,
}
# Las que prenden su bit cuando son True.
_BIT_BY_TRUE_KEYWORD = {"synthetic": BIT_SYNTHETIC, "gated": BIT_GATED}

_ALL_GOOD = {**{keyword: True for keyword in _BIT_BY_FALSE_KEYWORD},
             **{keyword: False for keyword in _BIT_BY_TRUE_KEYWORD}}
_ALL_BAD = {**{keyword: False for keyword in _BIT_BY_FALSE_KEYWORD},
            **{keyword: True for keyword in _BIT_BY_TRUE_KEYWORD}}

_UNUSED_BIT = max(DESCRIPTIONS) + 1


class TestBitLayout:
    def test_bits_are_distinct(self):
        bits = (*_BIT_BY_FALSE_KEYWORD.values(), *_BIT_BY_TRUE_KEYWORD.values())
        assert len(set(bits)) == len(bits)

    def test_word_mask_covers_exactly_the_used_bits(self):
        expected = 0
        for bit in DESCRIPTIONS:
            expected |= 1 << bit
        assert WORD_MASK == expected

    def test_every_bit_has_a_description(self):
        assert set(DESCRIPTIONS) == {*_BIT_BY_FALSE_KEYWORD.values(),
                                     *_BIT_BY_TRUE_KEYWORD.values()}


class TestPack:
    def test_everything_fine_is_zero(self):
        assert pack(**_ALL_GOOD) == 0

    def test_each_affirmative_input_is_negated(self):
        """La entrada dice `licensed`, `enabled`…; el bit dice lo contrario."""
        for keyword, bit in _BIT_BY_FALSE_KEYWORD.items():
            assert pack(**{**_ALL_GOOD, keyword: False}) == 1 << bit

    def test_synthetic_sets_its_bit(self):
        assert pack(**{**_ALL_GOOD, "synthetic": True}) == 1 << BIT_SYNTHETIC

    def test_a_closed_gate_sets_its_bit(self):
        """No es una falla, pero sin el bit un pipeline en pausa se lee como uno sano."""
        assert pack(**{**_ALL_GOOD, "gated": True}) == 1 << BIT_GATED

    def test_flags_accumulate(self):
        assert pack(**_ALL_BAD) == WORD_MASK

    def test_word_stays_inside_the_mask(self):
        assert pack(**_ALL_BAD) & ~WORD_MASK == 0

    def test_is_keyword_only(self):
        """Son banderas del mismo tipo: un orden posicional se equivoca callado."""
        with pytest.raises(TypeError):
            pack(True, True, True, True, False, True)

    def test_every_flag_is_required(self):
        """Un default silencioso taparía un estado que nadie midió."""
        with pytest.raises(TypeError):
            pack(licensed=True)


class TestDescribe:
    def test_zero_is_ok(self):
        assert describe(0) == "OK"

    def test_lists_the_active_bits(self):
        text = describe(pack(**{**_ALL_GOOD, "running": False}))
        assert DESCRIPTIONS[BIT_NOT_RUNNING] in text
        assert DESCRIPTIONS[BIT_NOT_LOADED] not in text

    def test_unknown_bit_reports_the_raw_word(self):
        assert f"0x{1 << _UNUSED_BIT:04X}" in describe(1 << _UNUSED_BIT)

    def test_every_bit_describes_itself(self):
        for bit, text in DESCRIPTIONS.items():
            assert describe(1 << bit) == text
