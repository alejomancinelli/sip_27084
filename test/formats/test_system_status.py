"""Tests de la palabra de estado del sistema: con cualquier bit en 1 las
mediciones de proceso no son aptas para control."""

import pytest

from system.formats.system_status import (
    BIT_DEAD_THREAD,
    BIT_FALLBACK_CONFIG,
    BIT_INFERENCE_ERROR,
    BIT_MODEL_NOT_LOADED,
    DESCRIPTIONS,
    WORD_MASK,
    describe,
    pack,
)

# Todo en orden: la única combinación que tiene que dar 0.
_ALL_GOOD = {"model_loaded": True, "inference_error": False,
             "fallback_config": False, "dead_thread": False}

_BIT_BY_KEYWORD = {
    "inference_error": BIT_INFERENCE_ERROR,
    "fallback_config": BIT_FALLBACK_CONFIG,
    "dead_thread": BIT_DEAD_THREAD,
}


class TestBitLayout:
    def test_bits_are_distinct(self):
        bits = (BIT_MODEL_NOT_LOADED, BIT_INFERENCE_ERROR,
                BIT_FALLBACK_CONFIG, BIT_DEAD_THREAD)
        assert len(set(bits)) == len(bits)

    def test_word_mask_covers_exactly_the_used_bits(self):
        expected = 0
        for bit in DESCRIPTIONS:
            expected |= 1 << bit
        assert WORD_MASK == expected

    def test_every_bit_has_a_description(self):
        assert set(DESCRIPTIONS) == {BIT_MODEL_NOT_LOADED, BIT_INFERENCE_ERROR,
                                     BIT_FALLBACK_CONFIG, BIT_DEAD_THREAD}


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
        word = pack(model_loaded=False, inference_error=True,
                    fallback_config=True, dead_thread=True)
        assert word == WORD_MASK

    def test_word_stays_inside_the_mask(self):
        word = pack(model_loaded=False, inference_error=True,
                    fallback_config=True, dead_thread=True)
        assert word & ~WORD_MASK == 0

    def test_any_bit_means_the_measurements_are_not_trustworthy(self):
        for keyword in ("inference_error", "fallback_config", "dead_thread"):
            assert pack(**{**_ALL_GOOD, keyword: True}) != 0

    def test_is_keyword_only(self):
        """Son cuatro banderas del mismo tipo: un orden posicional se equivoca callado."""
        with pytest.raises(TypeError):
            pack(True, False, False, False)

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
        assert "0x0010" in describe(1 << 4)

    def test_every_bit_describes_itself(self):
        for bit, text in DESCRIPTIONS.items():
            assert describe(1 << bit) == text
