"""
Entradas y salidas digitales del equipo, sobre libgpiod v2.

Dos piezas: `GpioController`, que abre las líneas y las mantiene abiertas mientras el
objeto viva —una línea que se suelta vuelve a su estado de reposo, así que cerrarla entre
comandos apagaría la salida—, y `GpioPollingThread`, que relee las entradas y avisa por
señal.

**Sin `gpiod`, sin permisos o sin chip, el módulo degrada a modo simulado adentro y expone
la misma API.** Quien lo usa no pregunta si está disponible ni escribe un `try: import`
ajeno: comanda igual, y que lo que salió no llegó a ningún borne lo dice
`hardware_available`, que es lo que termina en el bit 15 de las palabras de GPIO. Un
equipo sin GPIO tiene que arrancar igual, y la pantalla lo avisa.

**El controlador no arma bitfields.** Entrega estados por canal —`1`, `0`, o `READ_ERROR`
cuando la lectura falló— y quien publica los traduce con `system/formats/gpio_status.py`,
que es el dueño de esas dos palabras.

Las salidas se reportan desde la caché de lo comandado y no releyendo el hardware: una
línea configurada como salida no se relee, así que lo que se publica es la intención. Que
el contacto haya cerrado de verdad es algo que este módulo no puede afirmar.

Los canales se numeran desde 1, como en el borne: las claves del config pueden venir como
`DO1` o como `1` y se lee el número final.
"""

import threading

from PySide6.QtCore import QThread, Signal

from system.config_manager import ConfigManager
from system.logger import logger

#: Lo que devuelve una lectura que no se pudo hacer. Mismo valor que en `formats`.
READ_ERROR = -1

_DEFAULT_POLLING_INTERVAL_MS = 200
_MIN_POLLING_INTERVAL_MS = 20
_CONSUMER_OUTPUTS = "sip_gpio_outputs"
_CONSUMER_INPUTS = "sip_gpio_inputs"

try:
    import gpiod
    _GPIOD_AVAILABLE = True
except ImportError:
    gpiod = None
    _GPIOD_AVAILABLE = False


class GpioController:
    """
    Abre las líneas declaradas en `gpio:` y comanda las salidas.

    Thread-safe: el hilo de polling lee las entradas mientras la interfaz comanda las
    salidas, así que todo acceso a las líneas va bajo el mismo Lock.

    Dueño único de las líneas del chip: nadie más las abre. `close()` deja las salidas en
    reposo y suelta las líneas, y hay que llamarlo al apagar.
    """

    def __init__(self, config_manager: ConfigManager):
        self._config = config_manager
        self._enabled = bool(config_manager.get("gpio.enabled", False))
        self._chip_path = _resolve_chip_path(config_manager.get("gpio.chip", ""))
        self._offset_by_output = _parse_channels(config_manager.get("gpio.outputs", {}))
        self._offset_by_input = _parse_channels(config_manager.get("gpio.inputs", {}))

        self._output_states = {channel: 0 for channel in self._offset_by_output}
        self._input_states = {channel: 0 for channel in self._offset_by_input}
        self._lock = threading.Lock()
        self._error: str | None = None

        self._output_request = None
        self._input_request = None
        self._value = None          # gpiod.line.Value, guardado para no reimportar

        if self._enabled:
            self._open()
            self._apply_initial_outputs()
        else:
            logger.info("[GPIO] Deshabilitado en el config: modo simulado.")

    # ── Estado ───────────────────────────────────────────────────────────────

    @property
    def hardware_available(self) -> bool:
        """True si las líneas están abiertas de verdad; False en modo simulado."""
        return self._output_request is not None or self._input_request is not None

    @property
    def output_count(self) -> int:
        return len(self._offset_by_output)

    @property
    def input_count(self) -> int:
        return len(self._offset_by_input)

    def get_status(self) -> dict:
        """
        Estado del subsistema con claves estables:

            status             : str — disabled | active | error
            hardware_available : bool
            output_count       : int
            input_count        : int
            error              : str | None

        El GPIO no ocupa bit en la palabra de comunicaciones: su disponibilidad ya viaja
        en el bit 15 de las dos palabras de GPIO, que es donde la mira el PLC.
        """
        if not self._enabled:
            status = "disabled"
        elif self.hardware_available:
            status = "active"
        else:
            status = "error"
        return {
            "status": status,
            "hardware_available": self.hardware_available,
            "output_count": self.output_count,
            "input_count": self.input_count,
            "error": self._error,
        }

    # ── Salidas ──────────────────────────────────────────────────────────────

    def set_output(self, channel: int, state: int) -> bool:
        """
        Comanda una salida. Devuelve False si el canal no existe o si la escritura falló.

        En modo simulado devuelve True: el comando se registró y se publica, y que no haya
        hardware detrás lo dice `hardware_available`. Un False acá significa que algo salió
        mal, no que el equipo no tenga GPIO.
        """
        if channel not in self._offset_by_output:
            logger.error(f"[GPIO] La salida {channel} no está declarada en 'gpio.outputs'.")
            return False

        value = int(bool(state))
        with self._lock:
            if self._output_request is not None:
                try:
                    self._output_request.set_value(
                        self._offset_by_output[channel],
                        self._value.ACTIVE if value else self._value.INACTIVE)
                except Exception as e:
                    logger.error(f"[GPIO] No se pudo escribir la salida {channel}: {e}")
                    return False
            self._output_states[channel] = value
        return True

    def get_output_state(self, channel: int) -> int:
        """Último valor comandado a esa salida, o 0 si el canal no existe."""
        with self._lock:
            return self._output_states.get(channel, 0)

    def get_all_outputs(self) -> dict[int, int]:
        """Lo comandado a cada salida, sin tocar el hardware."""
        with self._lock:
            return dict(self._output_states)

    # ── Entradas ─────────────────────────────────────────────────────────────

    def read_input(self, channel: int) -> int:
        """Lee una entrada: 1, 0, o READ_ERROR si el canal no existe o falló la lectura."""
        if channel not in self._offset_by_input:
            logger.error(f"[GPIO] La entrada {channel} no está declarada en 'gpio.inputs'.")
            return READ_ERROR

        with self._lock:
            if self._input_request is None:
                return self._input_states.get(channel, 0)
            try:
                raw = self._input_request.get_value(self._offset_by_input[channel])
            except Exception as e:
                logger.error(f"[GPIO] No se pudo leer la entrada {channel}: {e}")
                return READ_ERROR
            value = 1 if raw == self._value.ACTIVE else 0
            self._input_states[channel] = value
            return value

    def read_all_inputs(self) -> dict[int, int]:
        """Lee todas las entradas declaradas: `{canal: 1 | 0 | READ_ERROR}`."""
        return {channel: self.read_input(channel) for channel in self._offset_by_input}

    def get_all_inputs(self) -> dict[int, int]:
        """Última lectura de cada entrada, sin volver a tocar el hardware."""
        with self._lock:
            return dict(self._input_states)

    def set_simulated_input(self, channel: int, state: int):
        """Fuerza una entrada en modo simulado. Sin hardware detrás no hace nada útil."""
        with self._lock:
            if channel in self._input_states and self._input_request is None:
                self._input_states[channel] = int(bool(state))

    # ── Cierre ───────────────────────────────────────────────────────────────

    def close(self):
        """
        Deja las salidas en reposo y suelta las líneas. Idempotente.

        Las salidas se apagan a propósito antes de soltar: una línea liberada vuelve sola
        a su estado de reposo, y hacerlo explícito deja el equipo en un estado conocido en
        vez de en el que el kernel elija.
        """
        with self._lock:
            if self._output_request is not None:
                for offset in self._offset_by_output.values():
                    try:
                        self._output_request.set_value(offset, self._value.INACTIVE)
                    except Exception:
                        pass
                _release(self._output_request)
                self._output_request = None
            if self._input_request is not None:
                _release(self._input_request)
                self._input_request = None
        logger.info("[GPIO] Líneas liberadas.")

    # ── Apertura del chip ────────────────────────────────────────────────────

    def _open(self):
        """Abre las líneas del chip. No propaga errores: sin chip queda en simulado."""
        if not _GPIOD_AVAILABLE:
            self._fail("gpiod no está instalado")
            return
        if not self._offset_by_output and not self._offset_by_input:
            self._fail("no hay líneas declaradas en 'gpio.outputs' ni en 'gpio.inputs'")
            return

        try:
            from gpiod.line import Direction, Value
            self._value = Value
            if self._offset_by_output:
                self._output_request = gpiod.request_lines(
                    self._chip_path, consumer=_CONSUMER_OUTPUTS,
                    config={tuple(self._offset_by_output.values()): gpiod.LineSettings(
                        direction=Direction.OUTPUT, output_value=Value.INACTIVE)})
            if self._offset_by_input:
                self._input_request = gpiod.request_lines(
                    self._chip_path, consumer=_CONSUMER_INPUTS,
                    config={tuple(self._offset_by_input.values()): gpiod.LineSettings(
                        direction=Direction.INPUT)})
        except Exception as e:
            self._output_request = None
            self._input_request = None
            self._fail(str(e))
            return

        logger.info(
            f"[GPIO] Chip {self._chip_path} abierto. Salidas: "
            f"{sorted(self._offset_by_output)}, entradas: {sorted(self._offset_by_input)}."
        )

    def _apply_initial_outputs(self):
        """
        Deja las salidas en el estado declarado en `gpio.initial_outputs`.

        Existe porque una salida en reposo no siempre es la posición segura: un relé de
        habilitación cableado en lógica negada arranca comandado. Lo que no se declara
        queda en reposo.
        """
        for channel, state in _parse_channels(
                self._config.get("gpio.initial_outputs", {})).items():
            if channel in self._offset_by_output:
                self.set_output(channel, state)

    def _fail(self, reason: str):
        self._error = reason
        logger.error(f"[GPIO] Habilitado pero {reason}. Modo simulado.")


class GpioPollingThread(QThread):
    """
    Relee las entradas cada `gpio.polling_interval_ms` y avisa por señal.

    Un hilo y no un QTimer porque `read_input()` toca el bus y bloquea lo que tarde el
    driver; en el hilo de la GUI eso se ve como tirones en la pantalla.
    """

    inputs_updated = Signal(object)   # {canal: 1 | 0 | READ_ERROR} — uno por intervalo

    def __init__(self, gpio_controller: GpioController, config_manager: ConfigManager,
                 parent=None):
        super().__init__(parent)
        self._gpio = gpio_controller
        self._interval_ms = max(_MIN_POLLING_INTERVAL_MS, int(config_manager.get(
            "gpio.polling_interval_ms", _DEFAULT_POLLING_INTERVAL_MS)))

    def run(self):
        logger.info(f"[GPIO] Polling de entradas cada {self._interval_ms} ms.")
        while not self.isInterruptionRequested():
            self.inputs_updated.emit(self._gpio.read_all_inputs())
            # Troceado: con un intervalo largo, un wait entero retrasaría la parada.
            self.msleep(min(self._interval_ms, _DEFAULT_POLLING_INTERVAL_MS))
        logger.info("[GPIO] Polling de entradas detenido.")


# ── Helpers del módulo ───────────────────────────────────────────────────────

def _resolve_chip_path(chip: object) -> str:
    """Acepta `gpiochip0` o `/dev/gpiochip0` y devuelve siempre la ruta completa."""
    path = str(chip or "").strip()
    if not path:
        return ""
    return path if path.startswith("/") else f"/dev/{path}"


def _parse_channels(raw: object) -> dict[int, int]:
    """
    Convierte el mapa del config en `{número de canal: valor}`, ordenado por canal.

    La clave puede venir como `DO1` o como `1`: el canal es el número que lleva al final,
    que es como lo llama el borne. Una clave sin número se descarta con un aviso, porque
    silenciarla dejaría una salida sin comandar y nadie sabría por qué.
    """
    channels = {}
    for key, value in (raw or {}).items():
        digits = "".join(character for character in str(key) if character.isdigit())
        if not digits:
            logger.warning(f"[GPIO] La clave '{key}' no tiene número de canal: se ignora.")
            continue
        channels[int(digits)] = int(value)
    return dict(sorted(channels.items()))


def _release(request: object):
    """Suelta una línea sin propagar el error: se llama al apagar y ahí nada puede fallar."""
    try:
        request.release()
    except Exception:
        pass
