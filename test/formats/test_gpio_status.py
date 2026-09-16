"""Tests de las dos palabras de GPIO: estado por canal, bits de falla de lectura y la
marca de GPIO ausente.

Modulo puro: se arma un dict y se compara un entero.
"""

from system.formats import gpio_status

_ALL_ON = 0b1111


class TestInputs:
    def test_an_active_channel_sets_its_state_bit(self):
        assert gpio_status.pack_inputs({1: 1}) == 0b0001

    def test_the_channels_are_numbered_from_one(self):
        assert gpio_status.pack_inputs({4: 1}) == 0b1000

    def test_every_channel_can_be_active_at_once(self):
        assert gpio_status.pack_inputs({1: 1, 2: 1, 3: 1, 4: 1}) == _ALL_ON

    def test_a_failed_read_sets_the_error_bit_and_not_the_state(self):
        """Un canal que no responde no es un canal en cero: sin este bit, una placa
        muerta se lee igual que una entrada apagada."""
        word = gpio_status.pack_inputs({1: gpio_status.READ_ERROR})
        assert word == 1 << gpio_status.ERROR_SHIFT

    def test_an_unwired_channel_is_zero_without_an_error_bit(self):
        assert gpio_status.pack_inputs({2: 1}) == 0b0010

    def test_a_channel_out_of_range_is_ignored(self):
        assert gpio_status.pack_inputs({9: 1}) == 0

    def test_without_hardware_it_marks_the_word(self):
        assert gpio_status.pack_inputs({}, hardware_available=False) == \
            1 << gpio_status.BIT_UNAVAILABLE

    def test_an_empty_read_is_an_empty_word(self):
        assert gpio_status.pack_inputs({}) == 0


class TestOutputs:
    def test_a_commanded_output_sets_its_bit(self):
        assert gpio_status.pack_outputs({3: 1}) == 0b0100

    def test_outputs_have_no_error_bits(self):
        """Se publica el eco de lo comandado, no una relectura: no hay lectura que falle."""
        word = gpio_status.pack_outputs({1: gpio_status.READ_ERROR})
        assert word == 0

    def test_without_hardware_it_marks_the_word(self):
        assert gpio_status.pack_outputs({1: 1}, hardware_available=False) == \
            (1 << gpio_status.BIT_UNAVAILABLE) | 0b0001


class TestWordMask:
    def test_every_bit_it_can_set_is_inside_the_mask(self):
        word = gpio_status.pack_inputs(
            {n: gpio_status.READ_ERROR for n in range(1, gpio_status.MAX_CHANNELS + 1)},
            hardware_available=False)
        word |= gpio_status.pack_inputs({n: 1 for n in range(1, gpio_status.MAX_CHANNELS + 1)})
        assert word & ~gpio_status.WORD_MASK == 0

    def test_the_word_fits_in_a_register(self):
        assert 0 <= gpio_status.WORD_MASK <= 0xFFFF
