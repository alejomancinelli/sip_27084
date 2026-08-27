"""
Chip de estado: una etiqueta con forma de píldora y cuatro colores semánticos.

El color no se escribe acá: `set_state()` deja el estado en la propiedad Qt `state`
y el QSS del tema lo pinta con `StatusChip[state="ok"]`. Así los cuatro colores viven
con el resto de los colores del tema y el chip cambia solo al cambiar de tema.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

# Estados del chip. Son los que el QSS conoce por selector, así que agregar uno es
# sumar la constante acá y su regla en los dos temas.
CHIP_OK = "ok"
CHIP_WARNING = "warning"
CHIP_ERROR = "error"
CHIP_INFO = "info"

_PROPERTY_STATE = "state"


class StatusChip(QLabel):
    """Etiqueta de estado con color semántico. Arranca en ámbar."""

    def __init__(self, text: str = "", parent: QLabel | None = None):
        super().__init__(text, parent)
        self.setObjectName("statusChip")
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.set_state(CHIP_WARNING)

    def set_state(self, state: str, text: str = ""):
        """Cambia el color y, si se pasa, el texto. `state` es uno de los CHIP_*."""
        if text:
            self.setText(text)
        if self.property(_PROPERTY_STATE) == state:
            return
        self.setProperty(_PROPERTY_STATE, state)
        # Qt no reevalúa el QSS al cambiar una propiedad: hay que pedirlo.
        self.style().unpolish(self)
        self.style().polish(self)
