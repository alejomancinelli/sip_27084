"""Tests de la palabra de comunicaciones: un bit por canal, y sólo prende con el
canal andando de verdad."""

import pytest

from system.formats.com_status import (
    ACTIVE_STATES,
    BIT_HTTP_VIDEO,
    BIT_INFLUXDB,
    BIT_MODBUS_RTU,
    BIT_MODBUS_TCP,
    BIT_MQTT,
    BIT_RTSP,
    DESCRIPTIONS,
    WORD_MASK,
    describe,
    is_active,
    pack,
)

# Cada canal con el keyword que lo prende: si se agrega un bit sin su keyword, o
# al revés, los tests de superficie lo cazan.
_CHANNELS = {
    "rtsp_status": BIT_RTSP,
    "http_video_status": BIT_HTTP_VIDEO,
    "influxdb_status": BIT_INFLUXDB,
    "mqtt_status": BIT_MQTT,
    "modbus_tcp_status": BIT_MODBUS_TCP,
    "modbus_rtu_status": BIT_MODBUS_RTU,
}

# Vocabulario que reportan los subsistemas y que NO cuenta como andando.
_INACTIVE_STATES = ("disabled", "starting", "error", "disconnected", "", None)


class TestBitLayout:
    def test_bits_are_distinct(self):
        assert len(set(_CHANNELS.values())) == len(_CHANNELS)

    def test_word_mask_covers_exactly_the_used_bits(self):
        expected = 0
        for bit in _CHANNELS.values():
            expected |= 1 << bit
        assert WORD_MASK == expected

    def test_every_bit_has_a_description(self):
        assert set(DESCRIPTIONS) == set(_CHANNELS.values())


class TestIsActive:
    def test_active_and_connected_are_the_only_true_values(self):
        assert ACTIVE_STATES == frozenset({"active", "connected"})
        assert is_active("active")
        assert is_active("connected")

    def test_everything_else_is_false(self):
        for status in _INACTIVE_STATES:
            assert not is_active(status), f"{status!r} no debería contar como activo"


class TestPack:
    def test_no_channel_reported_is_zero(self):
        assert pack() == 0

    def test_each_channel_sets_only_its_own_bit(self):
        for keyword, bit in _CHANNELS.items():
            assert pack(**{keyword: "active"}) == 1 << bit

    def test_connected_also_sets_the_bit(self):
        """Los backends de telemetría reportan `connected`, no `active`."""
        assert pack(influxdb_status="connected") == 1 << BIT_INFLUXDB

    def test_disabled_reads_the_same_as_broken(self):
        """Un bit no alcanza para cuatro estados: apagado a propósito se lee igual."""
        assert pack(rtsp_status="disabled") == pack(rtsp_status="error") == 0

    def test_starting_is_not_yet_active(self):
        assert pack(modbus_tcp_status="starting") == 0

    def test_every_channel_at_once_fills_the_mask(self):
        word = pack(**{keyword: "active" for keyword in _CHANNELS})
        assert word == WORD_MASK

    def test_word_stays_inside_the_mask(self):
        word = pack(**{keyword: "active" for keyword in _CHANNELS})
        assert word & ~WORD_MASK == 0

    def test_is_keyword_only(self):
        """Son seis estados del mismo tipo: un orden posicional se equivoca callado."""
        with pytest.raises(TypeError):
            pack("active", "active")


class TestDescribe:
    def test_zero_says_there_is_nothing_up(self):
        assert describe(0) == "sin canales activos"

    def test_lists_the_active_channels(self):
        text = describe(pack(rtsp_status="active", modbus_tcp_status="active"))
        assert DESCRIPTIONS[BIT_RTSP] in text
        assert DESCRIPTIONS[BIT_MODBUS_TCP] in text
        assert DESCRIPTIONS[BIT_MQTT] not in text

    def test_unknown_bit_reports_the_raw_word(self):
        assert "0x0080" in describe(1 << 7)

    def test_every_bit_describes_itself(self):
        for bit, text in DESCRIPTIONS.items():
            assert describe(1 << bit) == text
