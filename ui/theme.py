"""
Colores y hoja de estilos de la interfaz — único dueño de los colores de la UI.

Dos caminos, y la línea entre ellos es si Qt puede pintar el widget solo:

  - **Widgets estándar** (botones, tablas, inputs, pestañas): los pinta el QSS de
    `ui/styles/{dark,light}.qss`, que se carga con `load_stylesheet()`. Ningún
    widget se pinta con `setStyleSheet()` propio.
  - **Widgets que dibujan** (`QPainter`, series de QtCharts, items de tabla): el QSS
    no los alcanza, así que piden los colores acá con `get_palette()` y los aplican
    en su `set_dark()`.

Las claves de la paleta son estables: un widget nuevo usa las que ya están o suma
una, y las dos paletas la definen. Los colores de las detecciones NO están acá: los
tiene `system/inference/overlay.py`, que es el que dibuja el resultado, y el frame
anotado llega a la UI ya pintado.
"""

import os

from PySide6.QtGui import QColor

from system.logger import logger

# Nombre del archivo de estilos por tema. La clave es lo que devuelve `is_dark`.
_STYLESHEETS = {True: "dark.qss", False: "light.qss"}
_STYLES_DIRNAME = "styles"

# Paleta del tema oscuro. Cada clave es un rol, no un color: `chart_background` se
# usa igual en cualquier gráfica, y el tema claro define las mismas claves.
_PALETTE_DARK = {
    "panel_background":   "#1e1e1e",
    "chart_background":   "#1e1e1e",
    "chart_title":        "#cccccc",
    "chart_grid":         "#4a4a4a",
    "chart_axis":         "#606060",
    "chart_labels":       "#aaaaaa",
    "video_background":   "#000000",
    "video_placeholder":  "#e5e5e5",
    "url_background":     "#484848",
    "url_text":           "#7ec8e3",
    "hint_text":          "#9aa3ad",
    "roi_outline":        "#1e78ff",
    "footer_background":  "#2c2c2c",
    "footer_flash":       "#ffdc00",
    "footer_alert":       "#c62828",
}

# Paleta del tema claro. Mismas claves que la oscura: si falta una, el widget que la
# pide se queda sin color y no hay forma de verlo sin abrir el archivo.
_PALETTE_LIGHT = {
    "panel_background":   "#cfcfcf",
    "chart_background":   "#cfcfcf",
    "chart_title":        "#1e1e1e",
    "chart_grid":         "#bbbbbb",
    "chart_axis":         "#969696",
    "chart_labels":       "#1e1e1e",
    "video_background":   "#101010",
    "video_placeholder":  "#f0f0f0",
    "url_background":     "#dde8f0",
    "url_text":           "#1a5a7a",
    "hint_text":          "#5c646d",
    "roi_outline":        "#1e78ff",
    "footer_background":  "#d8d8d8",
    "footer_flash":       "#ffdc00",
    "footer_alert":       "#c62828",
}

# Color de la serie de cada gráfica de hardware, por rol. No cambian con el tema:
# son la identidad de la serie, como el color de una clase en el overlay.
SERIES_COLORS = {
    "cpu":      "#0d6efd",
    "gpu":      "#198754",
    "ram":      "#ffc107",
    "cpu_temp": "#dc3545",
    "gpu_temp": "#fd7e14",
    "power":    "#6f42c1",
}

# (texto, fondo) por nivel de log. Los items de una QTableWidget se pintan por
# código, así que el QSS no llega.
_LOG_COLORS_DARK = {
    "INFO":    ("#dcdcdc", "#3c3c3c"),
    "WARNING": ("#c8b400", "#3e3a14"),
    "ERROR":   ("#dc5050", "#3e1c1c"),
}
_LOG_COLORS_LIGHT = {
    "INFO":    ("#3c3c3c", "#f5f5f5"),
    "WARNING": ("#826400", "#fff8d2"),
    "ERROR":   ("#b40000", "#ffebeb"),
}


def get_palette(dark: bool) -> dict:
    """Colores del tema como strings hex, listos para `QColor(...)`."""
    return dict(_PALETTE_DARK if dark else _PALETTE_LIGHT)


def get_color(role: str, dark: bool) -> QColor:
    """`QColor` de un rol de la paleta. Un rol que no existe sale magenta y se avisa."""
    palette = _PALETTE_DARK if dark else _PALETTE_LIGHT
    hex_color = palette.get(role)
    if hex_color is None:
        logger.warning(f"[UI] Color '{role}' no está en la paleta del tema.")
        return QColor("#ff00ff")
    return QColor(hex_color)


def get_series_color(role: str) -> QColor:
    """`QColor` de la serie de una gráfica. Igual en los dos temas."""
    return QColor(SERIES_COLORS.get(role, "#0d6efd"))


def get_log_level_colors(dark: bool) -> dict[str, tuple[QColor, QColor]]:
    """(texto, fondo) por nivel de log, ya como `QColor`."""
    source = _LOG_COLORS_DARK if dark else _LOG_COLORS_LIGHT
    return {level: (QColor(fg), QColor(bg)) for level, (fg, bg) in source.items()}


def load_stylesheet(dark: bool) -> str:
    """
    Hoja de estilos del tema. Devuelve '' si no se puede leer, y lo avisa.

    Encoding explícito: los .qss llevan acentos en los comentarios y el default en
    Windows es cp1252, que los decodifica mal.
    """
    styles_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), _STYLES_DIRNAME)
    path = os.path.join(styles_dir, _STYLESHEETS[bool(dark)])
    try:
        with open(path, encoding="utf-8") as stylesheet_file:
            return stylesheet_file.read()
    except OSError as error:
        logger.error(f"[UI] No se pudo leer la hoja de estilos {path}: {error}")
        return ""
