"""Tests del controlador de GPIO: lectura del config, modo simulado, comando de salidas y
estado de las entradas.

Sin `gpiod` el módulo degrada a simulado adentro, que es justamente el camino que corre en
un equipo de desarrollo. El camino con hardware se prueba con un doble de `gpiod`, sin
placa: lo que se verifica es que se le pida lo que corresponde, no que el kernel conteste.
"""

import pytest

from system.gpio_control import GpioController, GpioPollingThread, READ_ERROR

_CHIP = "/dev/gpiochip0"
_OUTPUTS = {"DO1": 51, "DO2": 52}
_INPUTS = {"DI1": 105, "DI2": 144}


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            "gpio.enabled": True,
            "gpio.chip": _CHIP,
            "gpio.outputs": dict(_OUTPUTS),
            "gpio.inputs": dict(_INPUTS),
            "gpio.polling_interval_ms": 200,
            "gpio.initial_outputs": {},
        }
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _FakeValue:
    ACTIVE = "active"
    INACTIVE = "inactive"


class _FakeRequest:
    """Una petición de líneas: recuerda lo escrito y contesta lo que se le programe."""

    def __init__(self, values_by_offset: dict | None = None, *, fails: bool = False):
        self.values_by_offset = values_by_offset or {}
        self.released = False
        self._fails = fails

    def set_value(self, offset: int, value: object):
        if self._fails:
            raise OSError("la línea no responde")
        self.values_by_offset[offset] = value

    def get_value(self, offset: int):
        if self._fails:
            raise OSError("la línea no responde")
        return self.values_by_offset.get(offset, _FakeValue.INACTIVE)

    def release(self):
        self.released = True


class _FakeGpiod:
    """Lo mínimo de la API de libgpiod v2 que usa el módulo."""

    def __init__(self, *, raises: bool = False):
        self.requests: list[_FakeRequest] = []
        self._raises = raises

    def request_lines(self, chip_path, consumer, config):
        if self._raises:
            raise PermissionError("sin permisos sobre el chip")
        request = _FakeRequest()
        self.requests.append(request)
        return request

    @staticmethod
    def LineSettings(**kwargs):
        return kwargs


def _install_gpiod(monkeypatch, fake: _FakeGpiod) -> _FakeGpiod:
    """Instala el doble de `gpiod` y su submódulo `line`, como si estuviera instalado."""
    import sys
    import types

    import system.gpio_control as gpio_control

    line_module = types.SimpleNamespace(Direction=types.SimpleNamespace(
        OUTPUT="out", INPUT="in"), Value=_FakeValue)
    monkeypatch.setattr(gpio_control, "gpiod", fake)
    monkeypatch.setattr(gpio_control, "_GPIOD_AVAILABLE", True)
    monkeypatch.setitem(sys.modules, "gpiod.line", line_module)
    return fake


@pytest.fixture
def with_gpiod(monkeypatch):
    return _install_gpiod(monkeypatch, _FakeGpiod())


# ── Modo simulado ────────────────────────────────────────────────────────────

class TestSimulatedMode:
    def test_without_the_library_it_does_not_raise(self, monkeypatch):
        """Un equipo sin GPIO tiene que arrancar igual: la pantalla es la que lo avisa."""
        import system.gpio_control as gpio_control
        monkeypatch.setattr(gpio_control, "_GPIOD_AVAILABLE", False)
        gpio = GpioController(_MockConfig())
        assert gpio.hardware_available is False

    def test_disabled_in_the_config_is_not_an_error(self, monkeypatch):
        import system.gpio_control as gpio_control
        monkeypatch.setattr(gpio_control, "_GPIOD_AVAILABLE", False)
        status = GpioController(_MockConfig(**{"gpio.enabled": False})).get_status()
        assert (status["status"], status["error"]) == ("disabled", None)

    def test_enabled_without_the_library_reports_the_reason(self, monkeypatch):
        import system.gpio_control as gpio_control
        monkeypatch.setattr(gpio_control, "_GPIOD_AVAILABLE", False)
        status = GpioController(_MockConfig()).get_status()
        assert status["status"] == "error" and "gpiod" in status["error"]

    def test_commanding_an_output_still_succeeds(self, monkeypatch):
        """El comando se registra y se publica; que no haya hardware lo dice el status."""
        import system.gpio_control as gpio_control
        monkeypatch.setattr(gpio_control, "_GPIOD_AVAILABLE", False)
        gpio = GpioController(_MockConfig())
        assert gpio.set_output(1, 1) is True
        assert gpio.get_output_state(1) == 1

    def test_a_chip_that_cannot_be_opened_falls_back(self, monkeypatch):
        _install_gpiod(monkeypatch, _FakeGpiod(raises=True))
        gpio = GpioController(_MockConfig())
        assert gpio.hardware_available is False and "permisos" in gpio.get_status()["error"]


# ── Lectura del config ───────────────────────────────────────────────────────

class TestConfig:
    def test_the_channel_is_the_number_at_the_end_of_the_key(self, monkeypatch):
        import system.gpio_control as gpio_control
        monkeypatch.setattr(gpio_control, "_GPIOD_AVAILABLE", False)
        gpio = GpioController(_MockConfig())
        assert (gpio.output_count, gpio.input_count) == (2, 2)

    def test_a_key_without_a_number_is_ignored(self, monkeypatch):
        """Silenciarla dejaría una salida sin comandar y nadie sabría por qué."""
        import system.gpio_control as gpio_control
        monkeypatch.setattr(gpio_control, "_GPIOD_AVAILABLE", False)
        gpio = GpioController(_MockConfig(**{"gpio.outputs": {"rele": 51, "DO2": 52}}))
        assert gpio.output_count == 1

    def test_an_unknown_output_is_refused(self, monkeypatch):
        import system.gpio_control as gpio_control
        monkeypatch.setattr(gpio_control, "_GPIOD_AVAILABLE", False)
        assert GpioController(_MockConfig()).set_output(9, 1) is False

    def test_an_unknown_input_reads_as_an_error(self, monkeypatch):
        import system.gpio_control as gpio_control
        monkeypatch.setattr(gpio_control, "_GPIOD_AVAILABLE", False)
        assert GpioController(_MockConfig()).read_input(9) == READ_ERROR


# ── Con hardware ─────────────────────────────────────────────────────────────

class TestWithHardware:
    def test_it_opens_one_request_for_outputs_and_one_for_inputs(self, with_gpiod):
        GpioController(_MockConfig())
        assert len(with_gpiod.requests) == 2

    def test_commanding_an_output_writes_its_offset(self, with_gpiod):
        gpio = GpioController(_MockConfig())
        gpio.set_output(2, 1)
        assert with_gpiod.requests[0].values_by_offset[52] == _FakeValue.ACTIVE

    def test_the_initial_outputs_are_applied_on_open(self, with_gpiod):
        """Una salida en reposo no siempre es la posición segura del proceso."""
        GpioController(_MockConfig(**{"gpio.initial_outputs": {"DO1": 1}}))
        assert with_gpiod.requests[0].values_by_offset[51] == _FakeValue.ACTIVE

    def test_reading_an_input_maps_the_active_value_to_one(self, with_gpiod):
        gpio = GpioController(_MockConfig())
        with_gpiod.requests[1].values_by_offset[105] = _FakeValue.ACTIVE
        assert gpio.read_input(1) == 1

    def test_reading_every_input_returns_one_entry_per_channel(self, with_gpiod):
        assert set(GpioController(_MockConfig()).read_all_inputs()) == {1, 2}

    def test_a_write_that_fails_reports_false(self, with_gpiod):
        gpio = GpioController(_MockConfig())
        with_gpiod.requests[0]._fails = True
        assert gpio.set_output(1, 1) is False

    def test_a_read_that_fails_reports_the_error_value(self, with_gpiod):
        """Un canal que no responde no es un canal en cero."""
        gpio = GpioController(_MockConfig())
        with_gpiod.requests[1]._fails = True
        assert gpio.read_input(1) == READ_ERROR

    def test_closing_puts_the_outputs_at_rest_and_releases(self, with_gpiod):
        gpio = GpioController(_MockConfig())
        gpio.set_output(1, 1)
        gpio.close()
        outputs = with_gpiod.requests[0]
        assert outputs.values_by_offset[51] == _FakeValue.INACTIVE and outputs.released

    def test_closing_twice_is_harmless(self, with_gpiod):
        gpio = GpioController(_MockConfig())
        gpio.close()
        gpio.close()

    def test_it_reports_itself_as_active(self, with_gpiod):
        assert GpioController(_MockConfig()).get_status()["status"] == "active"


# ── Hilo de polling ──────────────────────────────────────────────────────────

class TestPollingThread:
    def test_it_emits_the_state_of_every_input(self, with_gpiod):
        """Se conecta en directo: en la app la entrega el event loop, que acá no corre."""
        from PySide6.QtCore import Qt

        gpio = GpioController(_MockConfig())
        thread = GpioPollingThread(gpio, _MockConfig())
        seen = []
        thread.inputs_updated.connect(seen.append, Qt.ConnectionType.DirectConnection)
        thread.start()
        try:
            deadline = 2000
            while not seen and deadline > 0:
                thread.msleep(20)
                deadline -= 20
        finally:
            thread.requestInterruption()
            assert thread.wait(2000)
        assert seen and set(seen[0]) == {1, 2}

    def test_the_interval_has_a_floor(self, with_gpiod):
        """Un intervalo de 0 en el config dejaría el hilo girando sin ceder el CPU."""
        thread = GpioPollingThread(GpioController(_MockConfig()),
                                   _MockConfig(**{"gpio.polling_interval_ms": 0}))
        assert thread._interval_ms > 0
