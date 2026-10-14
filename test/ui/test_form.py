"""
Tests de los constructores del formulario: la tarjeta de una pestaña no depende de lo que
tiene adentro.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget    # noqa: E402

import ui.widgets.form as form                                              # noqa: E402

_WIDE_WINDOW_PX = 1920
_NARROW_WINDOW_PX = 900
_WINDOW_HEIGHT_PX = 600


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


def _card_width(content: str, window_px: int, *, center: bool = True) -> int:
    """Ancho de la tarjeta de un contenido, mostrada en una ventana de `window_px`."""
    inner = QWidget()
    QVBoxLayout(inner).addWidget(QLabel(content))
    wrapped = form.wrap_in_card(inner, center=center, scroll=True)
    wrapped.resize(window_px, _WINDOW_HEIGHT_PX)
    wrapped.show()
    QApplication.processEvents()
    width = wrapped.findChild(QWidget, form._CARD_OBJECT_NAME).width()
    wrapped.close()
    return width


class TestCardWidth:
    def test_contents_of_different_width_give_the_same_card(self):
        """Si la tarjeta tomara el ancho del contenido, el fondo saltaría entre pestañas."""
        narrow = _card_width("Modo:", _WIDE_WINDOW_PX)
        wide = _card_width("Confianza mínima del resultado (%): " * 4, _WIDE_WINDOW_PX)
        assert narrow == wide == form._CARD_WIDTH_PX

    def test_a_narrow_window_shrinks_every_card_alike(self):
        """No es un ancho fijo: sin scroll horizontal, cortaría los campos."""
        narrow = _card_width("Modo:", _NARROW_WINDOW_PX)
        wide = _card_width("Confianza mínima del resultado (%): " * 4, _NARROW_WINDOW_PX)
        assert narrow == wide
        assert form._CARD_MIN_WIDTH_PX <= narrow < form._CARD_WIDTH_PX

    def test_a_card_that_is_not_centered_takes_the_whole_width(self):
        assert _card_width("tabla", _WIDE_WINDOW_PX, center=False) > form._CARD_WIDTH_PX
