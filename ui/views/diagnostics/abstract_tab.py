"""
Contrato de una pestaña del panel de diagnóstico.

Una pestaña de diagnóstico **muestra**, no edita: recibe lo que le empujan por sus
slots y no lee el config más que para saber qué tiene que mostrar. Por eso no tiene
`load()` ni `save()` como las de configuración; lo único común es el título y el
cambio de tema.

`apply_theme()` existe porque los widgets que pintan con QPainter —las gráficas, los
colores de los items de una tabla— no los alcanza el QSS: el cambio de tema tiene que
bajar hasta ellos por código.
"""

from PySide6.QtWidgets import QWidget

from system.config_manager import ConfigManager


class AbstractDiagnosticsTab(QWidget):
    """Pestaña de diagnóstico. Ver el contrato en el docstring del módulo."""

    TITLE_KEY = ""            # clave de ui/strings.py con el título de la pestaña
    IS_CENTERED = True        # False en las pestañas que ocupan todo el ancho
    # El scroll va en las pestañas que apilan contenido de alto fijo. Una que ya tiene
    # una tabla adentro lo deja en False: la tabla scrollea sola y anidar las dos barras
    # deja la de afuera moviendo una tabla que nunca crece.
    IS_SCROLLABLE = False

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._is_dark = True

    def apply_theme(self, dark: bool):
        """Baja el tema a los widgets que se pintan por código. La base sólo lo recuerda."""
        self._is_dark = dark
