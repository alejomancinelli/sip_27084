"""
Media móvil sobre una ventana de tiempo, para las tendencias que lee el PLC.

Módulo puro: sin Qt, sin ConfigManager, sin numpy y sin I/O. El reloj entra por parámetro
—`now_s`— y no se lo pide a nadie: así una ventana de ocho horas se prueba en tres líneas
sin esperar ocho horas.

**Es para agregar en el tiempo, no dentro de un ciclo.** La dispersión de los N frames de
una medición la resuelve `analysis.summarize_metrics()`; esto es lo otro: qué viene
pasando en la última hora, que es lo que el operador mira para decidir si el proceso se
está yendo.

Dos decisiones que explican la forma de la clase:

  - **La evicción es por tiempo y no por cantidad.** Una ventana de N muestras dura lo que
    dure el ritmo de medición: si la inferencia se pone lenta, esa ventana pasa a cubrir
    horas sin que nadie se entere.
  - **`tick()` va separado de `add()`.** Quien publica llama a `tick()` en cada ciclo, haya
    medición o no, y así una inferencia muerta **drena** la ventana en vez de congelarla
    con el último promedio bueno. Cuánto de la ventana se llegó a medir lo dice
    `fill_pct()`, que es el número que le permite al PLC saber si puede confiar en la
    media —una media de tres muestras sobre una hora no es una media—.
"""

_PCT = 100.0


class RollingMean:
    """
    Media de los valores agregados dentro de una ventana de tiempo.

    No es thread-safe: la usa quien publica, desde un solo hilo.
    """

    def __init__(self, window_s: float):
        self._window_s = float(window_s)
        self._samples: list[tuple[float, float]] = []

    def add(self, now_s: float, value: float):
        """Agrega una muestra y envejece la ventana."""
        self._samples.append((float(now_s), float(value)))
        self.tick(now_s)

    def tick(self, now_s: float):
        """
        Envejece la ventana sin agregar nada.

        Se llama en cada ciclo de publicación, haya muestra o no: es lo que hace que una
        inferencia caída vacíe la ventana en vez de dejarla con el último valor bueno.
        """
        cutoff_s = float(now_s) - self._window_s
        self._samples = [sample for sample in self._samples if sample[0] > cutoff_s]

    def mean(self) -> float | None:
        """Media de la ventana, o None si no quedó ninguna muestra."""
        if not self._samples:
            return None
        return sum(value for _, value in self._samples) / len(self._samples)

    def count(self) -> int:
        return len(self._samples)

    def fill_pct(self, expected_hz: float = 1.0) -> int:
        """
        Qué porcentaje de la ventana se llegó a medir, 0–100.

        Es lo que valida la media: dice cuántas de las muestras que la ventana esperaba
        llegaron de verdad. Con `expected_hz` en 0 devuelve 0, porque sin ritmo esperado no
        hay contra qué comparar.
        """
        if expected_hz <= 0 or self._window_s <= 0:
            return 0
        expected = self._window_s * expected_hz
        return ratio_pct(len(self._samples), int(round(expected)))


def has_material(fraction_pct_by_class: dict) -> bool:
    """
    Si la muestra representa una composición real y no una cinta vacía.

    La composición de una cinta sin material no es 50/50 ni 0/0: no existe, y promediarla
    con las que sí tienen material corre la media hacia donde no hay proceso. Por eso la
    carga promedia todo —ahí el 0 es una medición legítima— y la composición sólo estas.

    Es el punto único de enganche para afinar el criterio: un mínimo de carga, o el
    veredicto de un modelo de cinta vacía, se agregan acá y valen para los dos consumidores.
    """
    return sum(float(value) for value in fraction_pct_by_class.values()) > 0


def ratio_pct(part: int, total: int) -> int:
    """Porcentaje entero de `part` sobre `total`, acotado a 0–100. Sin total, 0."""
    if total <= 0:
        return 0
    return max(0, min(100, int(round(_PCT * part / total))))
