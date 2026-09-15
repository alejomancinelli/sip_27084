"""
Tests de la paleta: que los dos temas definan lo mismo.

`ui/theme.py` importa sólo `QColor`, que no necesita un `QApplication` ni una pantalla,
así que esto corre en la misma suite sin GUI que todo lo demás. Lo que se pinta con
widgets —y hace falta una ventana para mirarlo— no se prueba acá.
"""

from ui import theme


class TestPaletteKeys:
    """
    Las dos paletas declaran las mismas claves.

    Es lo que promete el docstring del módulo, y el modo en que se rompe es silencioso:
    un widget que pide un rol que sólo existe en el tema oscuro sale magenta en el claro
    —`get_color()` avisa, pero recién cuando alguien cambia de tema y mira.
    """

    def test_both_themes_declare_the_same_roles(self):
        assert set(theme.get_palette(True)) == set(theme.get_palette(False))

    def test_no_role_is_left_empty(self):
        for dark in (True, False):
            palette = theme.get_palette(dark)
            assert all(palette.values()), f"rol vacío en el tema {'oscuro' if dark else 'claro'}"


class TestFooterAlert:
    """
    El rol del footer sin licencia válida.

    La barra entera se pinta con esto mientras `should_report_invalid()` lo pida. Es un
    rol de paleta y no un color inline porque `ui/theme.py` es el único dueño de los
    colores de la UI.
    """

    def test_the_alert_role_exists_in_both_themes(self):
        for dark in (True, False):
            assert theme.get_palette(dark)["footer_alert"]

    def test_it_is_not_the_same_colour_as_the_normal_footer(self):
        """Si fueran iguales la alerta no se vería, y el test de arriba pasaría igual."""
        for dark in (True, False):
            palette = theme.get_palette(dark)
            assert palette["footer_alert"] != palette["footer_background"]

    def test_the_flash_colour_stays_distinct_from_the_alert(self):
        """
        El destello del mensaje interpola hacia el fondo, que sin licencia es el rojo.
        Con el mismo color de los dos lados no habría destello que ver.
        """
        for dark in (True, False):
            palette = theme.get_palette(dark)
            assert palette["footer_flash"] != palette["footer_alert"]
