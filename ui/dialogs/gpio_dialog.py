"""
Diálogo de GPIO: estado de las entradas digitales y comando de las salidas.

Es la mitad de interfaz de `system/gpio_control.py`, y espera un controlador con esta
interfaz mínima:

    hardware_available -> bool          # False = sin librería de sistema, modo simulado
    input_count        -> int           # cuántas DI tiene el equipo
    output_count       -> int           # cuántas DO tiene el equipo
    get_output_state(number) -> int     # 0 | 1
    set_output(number, state) -> bool   # True si el comando se aplicó

La cantidad de canales la dice el controlador y no una constante de acá: cambia con el
equipo, y un diálogo que dibuja cuatro DI en una placa de ocho miente. Un controlador
que no informa la cantidad cae a `_DEFAULT_CHANNEL_COUNT`.

Las entradas se refrescan con `update_inputs()`, que es el slot que hay que conectar a
la señal de polling del controlador. Ninguna salida cambia sola: sólo por click.
"""

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QDialog, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from ui.strings import tr

_DEFAULT_CHANNEL_COUNT = 4
_INDICATOR_WIDTH_PX = 84
_INDICATOR_HEIGHT_PX = 60
_STATE_UNKNOWN = -1

# Estado -> valor de la propiedad Qt que pinta el QSS. El indicador no elige colores.
_STATE_PROPERTY = "state"
_STATE_NAMES = {1: "high", 0: "low", _STATE_UNKNOWN: "unknown"}


class GpioDialog(QDialog):
    """
    Ventana no modal de entradas y salidas digitales.

    No modal a propósito: se deja abierta al costado mientras se mira el resto de la
    aplicación, que es cómo se usa cuando hay que probar una salida en planta.
    """

    def __init__(self, gpio_controller: object, parent=None):
        super().__init__(parent)
        self._gpio = gpio_controller
        self.setWindowTitle(tr("gpio_title"))
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.setModal(False)

        self._indicators: dict[int, _IndicatorLabel] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        if not getattr(self._gpio, "hardware_available", False):
            warning = QLabel(tr("gpio_simulated"))
            warning.setObjectName("warningBanner")
            warning.setAlignment(Qt.AlignCenter)
            layout.addWidget(warning)

        layout.addWidget(self._build_inputs_box())
        layout.addWidget(self._build_outputs_box())

        close_button = QPushButton(tr("dialog_close"))
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button, alignment=Qt.AlignRight)

    # ── API pública ──────────────────────────────────────────────────────────

    @Slot(object)
    def update_inputs(self, states: dict):
        """Slot para {número de DI: 0 | 1}. Un número que no existe se ignora."""
        for number, state in states.items():
            indicator = self._indicators.get(int(number))
            if indicator is not None:
                indicator.set_state(int(state))

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_inputs_box(self) -> QGroupBox:
        box = QGroupBox(tr("gpio_box_inputs"))
        inner = QHBoxLayout(box)
        for number in range(1, self._get_channel_count("input_count") + 1):
            indicator = _IndicatorLabel(f"DI{number}")
            self._indicators[number] = indicator
            inner.addWidget(indicator)
        inner.addStretch()
        return box

    def _build_outputs_box(self) -> QGroupBox:
        box = QGroupBox(tr("gpio_box_outputs"))
        inner = QHBoxLayout(box)
        for number in range(1, self._get_channel_count("output_count") + 1):
            inner.addWidget(_OutputButton(number, self._gpio))
        inner.addStretch()
        return box

    def _get_channel_count(self, attribute: str) -> int:
        return int(getattr(self._gpio, attribute, _DEFAULT_CHANNEL_COUNT))


class _IndicatorLabel(QLabel):
    """LED de una entrada digital. El color lo pone el QSS por la propiedad `state`."""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.setObjectName("gpioIndicator")
        self._name = name
        self._state = None
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(_INDICATOR_WIDTH_PX, _INDICATOR_HEIGHT_PX)
        self.set_state(_STATE_UNKNOWN)

    def set_state(self, state: int):
        if state == self._state:
            return
        self._state = state
        texts = {1: tr("gpio_high"), 0: tr("gpio_low")}
        self.setText(f"{self._name}\n{texts.get(state, '---')}")
        self.setProperty(_STATE_PROPERTY, _STATE_NAMES.get(state, "unknown"))
        self.style().unpolish(self)
        self.style().polish(self)


class _OutputButton(QPushButton):
    """
    Botón de una salida digital.

    El estado que muestra es el que confirmó el controlador: si `set_output()` devuelve
    False el botón no cambia, porque mostrar ON con la salida en OFF es peor que no
    responder al click.
    """

    def __init__(self, number: int, gpio_controller: object, parent=None):
        super().__init__(parent)
        self.setObjectName("gpioOutput")
        self._number = number
        self._gpio = gpio_controller
        self._state = int(gpio_controller.get_output_state(number))
        self.setFixedSize(_INDICATOR_WIDTH_PX, _INDICATOR_HEIGHT_PX)
        self._refresh()
        self.clicked.connect(self._on_clicked)

    def _on_clicked(self):
        if not self._gpio.set_output(self._number, 1 - self._state):
            return
        self._state = 1 - self._state
        self._refresh()

    def _refresh(self):
        self.setText(f"DO{self._number}\n{tr('gpio_on') if self._state else tr('gpio_off')}")
        self.setProperty(_STATE_PROPERTY, _STATE_NAMES[self._state])
        self.style().unpolish(self)
        self.style().polish(self)
