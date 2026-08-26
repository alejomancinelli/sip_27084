"""Tests de la palabra de estado de cámara: exclusividad de los bits de
adquisición, orden de severidad y el bit de lente sucio, que es independiente."""

import pytest

from system.formats import camera_health
from system.formats.camera_health import (
    ACQUISITION_MASK,
    BIT_DIRTY_LENS,
    BIT_DISABLED,
    BIT_DISCONNECTED,
    BIT_MISCONFIGURED,
    BIT_MOCK,
    BIT_NO_FRAMES,
    BIT_OK,
    DESCRIPTIONS,
    WORD_MASK,
    describe,
    get_acquisition_bit,
    pack,
    set_dirty_lens,
)

_ACQUISITION_BITS = (BIT_OK, BIT_MISCONFIGURED, BIT_DISABLED, BIT_DISCONNECTED,
                     BIT_NO_FRAMES, BIT_MOCK)

# Una cámara sana: la base sobre la que cada test cambia una sola entrada.
_HEALTHY = {"connected": True, "config_error": None, "fps_estimated": 15.0}


class TestBitLayout:
    def test_acquisition_bits_are_distinct(self):
        assert len(set(_ACQUISITION_BITS)) == len(_ACQUISITION_BITS)

    def test_dirty_lens_is_outside_the_acquisition_group(self):
        """Si cayera adentro de la máscara, `set_dirty_lens` pisaría un estado."""
        assert not ACQUISITION_MASK & (1 << BIT_DIRTY_LENS)

    def test_acquisition_mask_covers_exactly_its_bits(self):
        expected = 0
        for bit in _ACQUISITION_BITS:
            expected |= 1 << bit
        assert ACQUISITION_MASK == expected

    def test_word_mask_covers_every_described_bit(self):
        for bit in DESCRIPTIONS:
            assert WORD_MASK & (1 << bit), f"el bit {bit} quedó afuera de WORD_MASK"

    def test_every_bit_has_a_description(self):
        """Un bit sin descripción sale como 'desconocido' en el log y en la UI."""
        assert set(DESCRIPTIONS) == set(_ACQUISITION_BITS) | {BIT_DIRTY_LENS}


class TestAcquisitionBit:
    def test_healthy_camera_is_ok(self):
        assert get_acquisition_bit(**_HEALTHY) == BIT_OK

    def test_synthetic_driver_reports_mock(self):
        assert get_acquisition_bit(**_HEALTHY, is_synthetic=True) == BIT_MOCK

    def test_connected_without_frames(self):
        assert get_acquisition_bit(connected=True, config_error=None,
                                   fps_estimated=0) == BIT_NO_FRAMES

    def test_fps_none_counts_as_no_frames(self):
        """`fps_estimated` llega en None cuando la ventana de medición está vacía."""
        assert get_acquisition_bit(connected=True, config_error=None,
                                   fps_estimated=None) == BIT_NO_FRAMES

    def test_disconnected_camera(self):
        assert get_acquisition_bit(connected=False, config_error=None,
                                   fps_estimated=0) == BIT_DISCONNECTED

    def test_config_error_wins_over_everything(self):
        assert get_acquisition_bit(connected=False, config_error="driver desconocido",
                                   fps_estimated=0, capture_enabled=False,
                                   is_synthetic=True) == BIT_MISCONFIGURED

    def test_disabled_wins_over_disconnected_and_no_frames(self):
        """
        Una cámara apagada a propósito no está caída ni se quedó sin frames: los
        dos síntomas son consecuencia de la decisión, no fallas que reportar.
        """
        assert get_acquisition_bit(connected=False, config_error=None,
                                   fps_estimated=0,
                                   capture_enabled=False) == BIT_DISABLED

    def test_disconnected_wins_over_no_frames(self):
        assert get_acquisition_bit(connected=False, config_error=None,
                                   fps_estimated=12.0) == BIT_DISCONNECTED

    def test_capture_enabled_defaults_to_true(self):
        """Un driver que no reporta `capture_enabled` no se lee como deshabilitado."""
        assert get_acquisition_bit(connected=True, config_error=None,
                                   fps_estimated=15.0) == BIT_OK


class TestPack:
    def test_exactly_one_acquisition_bit_is_always_set(self):
        cases = (
            _HEALTHY,
            {**_HEALTHY, "fps_estimated": 0},
            {**_HEALTHY, "connected": False},
            {**_HEALTHY, "config_error": "sin driver"},
            {**_HEALTHY, "capture_enabled": False},
            {**_HEALTHY, "is_synthetic": True},
        )
        for case in cases:
            word = pack(**case, dirty_lens=True)
            acquisition = word & ACQUISITION_MASK
            assert acquisition, f"ningún bit de adquisición en {case}"
            assert acquisition & (acquisition - 1) == 0, f"más de un bit en {case}"

    def test_word_is_never_zero(self):
        """La palabra en 0 es la señal de 'todavía no escribió nadie'."""
        assert pack(**_HEALTHY) != 0
        assert pack(connected=False, config_error=None, fps_estimated=0) != 0

    def test_word_stays_inside_the_mask(self):
        word = pack(**_HEALTHY, is_synthetic=True, dirty_lens=True)
        assert word & ~WORD_MASK == 0

    def test_dirty_lens_coexists_with_a_healthy_camera(self):
        word = pack(**_HEALTHY, dirty_lens=True)
        assert word & (1 << BIT_DIRTY_LENS)
        assert word & ACQUISITION_MASK == 1 << BIT_OK

    def test_dirty_lens_coexists_with_a_dead_camera(self):
        word = pack(connected=False, config_error=None, fps_estimated=0, dirty_lens=True)
        assert word & (1 << BIT_DIRTY_LENS)
        assert word & ACQUISITION_MASK == 1 << BIT_DISCONNECTED

    def test_clean_lens_leaves_the_bit_down(self):
        assert not pack(**_HEALTHY) & (1 << BIT_DIRTY_LENS)

    def test_is_keyword_only(self):
        """Son cinco banderas del mismo tipo: un orden posicional se equivoca callado."""
        with pytest.raises(TypeError):
            pack(True, None, 15.0)


class TestSetDirtyLens:
    def test_sets_the_bit_without_touching_acquisition(self):
        word = pack(**_HEALTHY)
        assert set_dirty_lens(word, True) == word | (1 << BIT_DIRTY_LENS)

    def test_is_idempotent(self):
        word = set_dirty_lens(pack(**_HEALTHY), True)
        assert set_dirty_lens(word, True) == word

    def test_false_leaves_the_word_untouched(self):
        """No limpia: quien mide la nitidez sostiene el veredicto anterior."""
        dirty = set_dirty_lens(pack(**_HEALTHY), True)
        assert set_dirty_lens(dirty, False) == dirty


class TestDescribe:
    def test_zero_is_reported_as_missing_data(self):
        assert describe(0) == "sin datos"

    def test_lists_both_groups(self):
        text = describe(pack(**_HEALTHY, dirty_lens=True))
        assert DESCRIPTIONS[BIT_OK] in text
        assert DESCRIPTIONS[BIT_DIRTY_LENS] in text

    def test_unknown_bit_reports_the_raw_word(self):
        assert "0x0100" in describe(1 << 8)

    def test_every_bit_describes_itself(self):
        for bit, text in DESCRIPTIONS.items():
            assert describe(1 << bit) == text


def test_module_is_pure():
    """Nivel 1: sin Qt, sin Modbus, sin ConfigManager."""
    imported = set(vars(camera_health))
    assert not {"QThread", "Signal", "ConfigManager", "SCHEMA"} & imported
