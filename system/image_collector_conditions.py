"""
Condiciones de guardado para el recolector de dataset.

Módulo puro: sin Qt, sin ConfigManager, sin I/O. Cada factory devuelve un predicado
que recibe el dict de inferencia de una cámara y responde si ese frame merece
guardarse. `ImageCollector` aplica todas las que le pasaron: alcanza que una diga
que no para que el frame no llegue a disco.

Las factories son genéricas; los nombres de los campos son del dict que produce la
inferencia y, por lo tanto, específicos del proyecto. Por eso van como parámetro y
no incrustados acá.

    conditions = [field_at_least_condition("confidence_pct", 75.0),
                  field_truthy_condition("alarm")]

Cada predicado lleva su `__name__` armado con los valores que recibió, así el log
del recolector dice cuál fue la condición que descartó el frame.
"""

from collections.abc import Callable


def field_at_least_condition(field: str, minimum: float) -> Callable[[dict], bool]:
    """
    Pasa cuando inference[field] llega a `minimum`. Un campo ausente vale 0.

    Un valor no numérico (None, texto) no cumple el mínimo en vez de levantar
    excepción: el recolector trata a la condición que falla como frame a descartar,
    y un campo que la inferencia no pudo calcular es exactamente eso, no un error de
    la condición.
    """
    def check(inference: dict) -> bool:
        value = inference.get(field, 0)
        try:
            return bool(value >= minimum)
        except TypeError:
            return False
    check.__name__ = f"field_at_least({field!r}, {minimum})"
    return check


def field_truthy_condition(field: str) -> Callable[[dict], bool]:
    """Pasa cuando inference[field] es truthy. Sirve para los flags de alarma."""
    def check(inference: dict) -> bool:
        return bool(inference.get(field))
    check.__name__ = f"field_truthy({field!r})"
    return check


def min_confidence_condition(threshold_pct: float) -> Callable[[dict], bool]:
    """Pasa cuando la confianza de la inferencia llega al umbral, en escala 0–100."""
    return field_at_least_condition("confidence_pct", threshold_pct)
