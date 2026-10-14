"""
Lo del proyecto que entra al motor de inferencia: qué detecciones cuentan, qué significan
y qué se dibuja además del resultado.

**Específico del proyecto — no cross-portar tal cual.** Es uno de los archivos que se
reescriben en cada fork, junto con `pipeline.py` y `metrics.py`. Lo que se reescribe es el
cuerpo de `build_hooks()`; `EngineHooks` es la forma en que sale y no se toca.

Es acá, y no en `main.py`, porque lo usan dos: la app y la corrida de imágenes sueltas. Las
dos tienen que armar el motor con los mismos hooks, o una imagen suelta pasaría con un
analyzer que no es el que corre en la línea.

**`process:` se lee una sola vez, arriba de `build_hooks()`**, y los mismos números van a
cada hook. Las factories de `annotations.py` y de `metrics.py` no leen el config a
propósito: reciben números. Leerlo una vez es lo que garantiza que el rectángulo de cinta
que ve el operador y el que se usó para calcular la carga que salió al PLC sean el mismo.
Así cada factory se testea con tres números y sin archivo.

En esta cinta: no se filtra ni se reetiqueta, el analyzer cuenta composición y carga por
píxeles, y se dibujan el rectángulo de cinta y el panel de composición.
"""

from collections.abc import Callable
from dataclasses import dataclass

from system.config_manager import ConfigManager

from . import annotations, metrics
from .pipeline import CLASS_NAMES
from .result import Detection, InferenceResult

Classifier = Callable[[list[Detection], str], list[Detection]]
Analyzer = Callable[[InferenceResult], dict]


@dataclass(frozen=True)
class EngineHooks:
    """
    Los tres puntos de extensión del motor que son del proyecto. None = el motor no lo usa.

        classifier : qué detecciones cuentan y con qué clase. Recibe
                     `(detections, camera_slot)` y devuelve las que quedan, ya en
                     coordenadas del frame completo; lo que descarte no cuenta para la
                     confianza ni para `min_detections`, y la clase que deje en
                     `class_index` es la que colorea el overlay. Es donde va lo que depende
                     de la cámara y el modelo no puede saber: una escala de píxel, un filtro
                     de tamaño, una clase que sale de la medida.
        analyzer   : qué significan las detecciones; ver `metrics.py`.
        annotator  : qué se dibuja encima del anotado estándar; ver `annotations.py`.
    """

    classifier: Classifier | None = None
    analyzer: Analyzer | None = None
    annotator: annotations.Annotator | None = None


def build_hooks(config: ConfigManager) -> EngineHooks:
    """
    Arma los hooks de la cinta con los valores de `process:`.

    **Sin classifier.** Todo lo que hay que decidir sobre una detección lo decide el modelo
    con su `min_confidence_pct`, el fondo oscuro lo descarta el pipeline, y el reparto entre
    clases se resuelve contando píxeles en el analyzer.

    Los nombres de clase son los de `pipeline.CLASS_NAMES`, que es su única declaración:
    deciden cómo se llaman las métricas —`pct_pellet_norm`— y por lo tanto qué registro las
    transporta. La paleta del panel es la misma con la que el motor pinta las máscaras, así
    que una clase se ve del mismo color en los dos lados.
    """
    belt_roi_px = config.get("process.belt_roi_px", {}) or {}
    colors = config.get("inference.overlay.class_colors_bgr", []) or []

    analyzer = metrics.build_analyzer(class_names=list(CLASS_NAMES), belt_roi_px=belt_roi_px)
    annotator = annotations.chain(
        annotations.belt_roi_annotator(belt_roi_px),
        annotations.composition_panel_annotator(
            list(CLASS_NAMES),
            class_colors_bgr=tuple(tuple(color) for color in colors),
            font_scale=float(config.get("inference.overlay.font_scale", 0) or 0)),
    )
    return EngineHooks(analyzer=analyzer, annotator=annotator)
