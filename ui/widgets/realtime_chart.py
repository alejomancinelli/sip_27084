"""
Gráfica de línea en tiempo real: los últimos N valores de una serie.

Se le empujan valores con `push()` y ella se encarga del resto. El rango del eje Y se
puede mover en caliente con `set_y_max()`, que es lo que hace la gráfica de RAM cuando
el monitor informa cuánta memoria tiene el equipo.

Los colores salen de `ui/theme.py`: QtCharts pinta con QPainter y el QSS no lo alcanza.
"""

from collections import deque

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QMargins, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ui import theme

_CHART_MARGIN_PX = 4
_SERIES_WIDTH_PX = 2
_MIN_HEIGHT_PX = 120
_Y_TICK_COUNT = 5


class RealtimeChart(QWidget):
    """
    Gráfica de línea de ventana fija.

    `series_role` es la clave de color en `theme.SERIES_COLORS`: la serie mantiene su
    color en los dos temas, porque el color es la identidad de la serie y no del tema.
    """

    def __init__(self, title: str, *, max_points: int = 60,
                 y_min: float = 0.0, y_max: float = 100.0,
                 series_role: str = "cpu", parent=None):
        super().__init__(parent)
        self._max_points = max_points
        self._values: deque = deque([0.0] * max_points, maxlen=max_points)
        self._y_min = y_min
        self._y_max = y_max

        self._series = QLineSeries()
        pen = QPen(theme.get_series_color(series_role))
        pen.setWidth(_SERIES_WIDTH_PX)
        self._series.setPen(pen)

        self._chart = QChart()
        self._chart.addSeries(self._series)
        self._chart.setTitle(title)
        self._chart.legend().hide()
        self._chart.setMargins(QMargins(_CHART_MARGIN_PX, _CHART_MARGIN_PX,
                                        _CHART_MARGIN_PX, _CHART_MARGIN_PX))

        self._axis_x = QValueAxis()
        self._axis_x.setRange(0, max_points)
        self._axis_x.setLabelsVisible(False)

        self._axis_y = QValueAxis()
        self._axis_y.setRange(y_min, y_max)
        self._axis_y.setLabelFormat("%.0f")
        self._axis_y.setTickCount(_Y_TICK_COUNT)
        # Cuando las `_Y_TICK_COUNT` etiquetas no entran a lo alto, QtCharts trunca y por
        # defecto las deja a todas en '...': el eje queda sin un solo número. Apagado,
        # dibuja las que entran y saltea el resto, que es lo que uno quiere de una
        # gráfica apretada. Aparece con la pestaña de hardware en una pantalla baja,
        # donde cada gráfica se achica hasta `_MIN_HEIGHT_PX`.
        self._axis_y.setTruncateLabels(False)

        self._chart.addAxis(self._axis_x, Qt.AlignBottom)
        self._chart.addAxis(self._axis_y, Qt.AlignLeft)
        self._series.attachAxis(self._axis_x)
        self._series.attachAxis(self._axis_y)

        view = QChartView(self._chart)
        view.setRenderHint(QPainter.Antialiasing)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(view)
        self.setMinimumHeight(_MIN_HEIGHT_PX)

        self.set_dark(True)
        self._refresh()

    def push(self, value: float):
        """Agrega un valor al final de la ventana y redibuja."""
        self._values.append(float(value))
        self._refresh()

    def set_y_max(self, y_max: float):
        """Mueve el techo del eje Y. Ignora un valor que no sea mayor al piso."""
        if y_max <= self._y_min or y_max == self._y_max:
            return
        self._y_max = float(y_max)
        self._axis_y.setRange(self._y_min, self._y_max)
        self._refresh()

    def set_dark(self, dark: bool):
        self._chart.setBackgroundBrush(theme.get_color("chart_background", dark))
        self._chart.setTitleBrush(theme.get_color("chart_title", dark))
        grid = theme.get_color("chart_grid", dark)
        axis = QPen(theme.get_color("chart_axis", dark))
        labels = theme.get_color("chart_labels", dark)
        for value_axis in (self._axis_x, self._axis_y):
            value_axis.setGridLineColor(grid)
            value_axis.setLinePen(axis)
            value_axis.setLabelsColor(labels)

    def _refresh(self):
        self._series.clear()
        for index, value in enumerate(self._values):
            self._series.append(index, max(self._y_min, min(self._y_max, value)))
