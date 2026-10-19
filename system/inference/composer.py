"""
Qué se ve en el frame anotado de una cámara cuando la miran varios pipelines.

Maquinaria genérica: reutilizable entre proyectos. Módulo puro: sin Qt, sin ConfigManager
y sin reloj propio —el instante entra como `now_s`, como en `rolling.py`—, así que se
prueba con números.

Con dos pipelines sobre una cámara —personas cada 100 ms y grano cada 500 ms— cada
resultado trae su propio frame anotado, y mostrarlos tal cual llegan hace parpadear el
overlay: cinco de cada seis frames llevan sólo las personas y el sexto sólo el grano. Lo
que resuelve este módulo es la regla, no el dibujo:

  - **El frame lo pone el resultado más nuevo de la cámara.** `add()` dice si un resultado
    maneja la imagen: su `timestamp_s` es posterior al del último frame mostrado de esa
    cámara. Uno más viejo —el de un ciclo, que conserva el instante de su frame
    representativo— no maneja: sólo aporta su overlay al frame siguiente, y así el video
    no salta para atrás.
  - **Encima van los demás pipelines de esa cámara**, con su último resultado: `others()`.
  - **Un resultado viejo se descarta.** Pasados `_STALE_FACTOR` períodos de su par
    (cámara, pipeline) —con piso en `_STALE_MIN_S`— deja de dibujarse: un pipeline caído no
    puede quedar congelado en pantalla con su último veredicto. El período es el mayor
    entre el `interval_s` del pipeline y el tiempo que pasó entre sus dos últimos
    resultados en esa cámara: con `interval_s` en 0 y un modelo que tarda dos segundos, el
    configurado solo lo daría por muerto entre una inferencia y la siguiente.
  - **Lo que la planta cortó se olvida en el acto.** `forget()` borra el par —su resultado
    y su período—: es lo que hace quien cablea cuando una compuerta corta los frames, para
    que el overlay se vaya en el frame siguiente y no tres períodos después.
  - **Sin nada vigente, la cámara se ve en vivo.** `has_fresh()` dice si a una cámara le
    queda algún resultado vigente; sin ninguno, quien cablea muestra el frame de la cámara
    en lugar de dejar congelado el último anotado.

`compose()` junta las tres cosas: guarda el resultado y, si maneja, devuelve el frame a
mostrar con los overlays de los demás dibujados encima. El dibujo no es de acá: lo hace el
motor de cada pipeline (`draw_onto()` y `draw_timestamp()`, ver `Painter`), con sus propias
opciones y su annotator, igual que en su propio anotado.

Con un pipeline por cámara `others()` está siempre vacío y todo resultado maneja: la imagen
es la del motor, sin copia.

Lo que se guarda es el resultado sin sus frames: para dibujarlo encima de otro alcanzan
sus detecciones y su ROI, y retener dos frames por par sería memoria que no se usa.

No es thread-safe: se usa desde un solo hilo.
"""

from collections.abc import Mapping
from dataclasses import replace
from typing import Protocol

import numpy as np

from .result import InferenceResult

_STALE_FACTOR = 3.0   # períodos de su par que un resultado sigue vigente
_STALE_MIN_S = 1.0    # piso: con `interval_s` en 0 el período es el de la cámara


class Painter(Protocol):
    """Lo que `compose()` le pide al motor de cada pipeline. Los dos dibujan in-place."""

    def draw_onto(self, canvas_bgr: np.ndarray, result: InferenceResult): ...

    def draw_timestamp(self, canvas_bgr: np.ndarray, result: InferenceResult): ...


class FrameComposer:
    """
    Último resultado de cada par (cámara, pipeline) y el último frame mostrado por cámara.

    `interval_s_by_pipeline` es el período configurado entre resultados de cada pipeline;
    uno que no figura cuenta sólo con el período observado y el piso.
    """

    def __init__(self, interval_s_by_pipeline: dict[str, float] | None = None):
        self._interval_s_by_pipeline: dict[str, float] = {}
        self.set_intervals(interval_s_by_pipeline or {})
        # (cámara, pipeline) -> (resultado sin frames, instante en que llegó, segundos desde
        # el resultado anterior del par; 0 si es el primero)
        self._latest: dict[tuple[str, str], tuple[InferenceResult, float, float]] = {}
        self._shown_timestamp_s: dict[str, float] = {}

    def set_intervals(self, interval_s_by_pipeline: dict[str, float]):
        """Reemplaza los períodos configurados. Lo guardado se conserva."""
        self._interval_s_by_pipeline = {
            pipeline_slot: max(0.0, float(interval_s))
            for pipeline_slot, interval_s in interval_s_by_pipeline.items()
        }

    def compose(self, result: InferenceResult, annotated_bgr: np.ndarray, now_s: float,
                painter_by_pipeline: Mapping[str, Painter]) -> np.ndarray | None:
        """
        Guarda el resultado y devuelve el frame a mostrar de su cámara, o None si no maneja.

        Sin otros pipelines vigentes en esa cámara devuelve `annotated_bgr` mismo, sin
        copia. Con otros, dibuja cada uno sobre una copia con su `Painter` y la hora al
        final con el de `result`: el motor la dibuja última para que nada la tape, y el
        combinado tiene que conservarlo. Un pipeline sin `Painter` no se dibuja.
        `annotated_bgr` no se modifica: es el anotado propio de `result`.
        """
        if not self.add(result, now_s):
            return None
        others = self.others(result.camera_slot, result.pipeline_slot, now_s)
        if not others:
            return annotated_bgr
        merged = annotated_bgr.copy()
        for other in others:
            painter = painter_by_pipeline.get(other.pipeline_slot)
            if painter is not None:
                painter.draw_onto(merged, other)
        painter = painter_by_pipeline.get(result.pipeline_slot)
        if painter is not None:
            painter.draw_timestamp(merged, result)
        return merged

    def add(self, result: InferenceResult, now_s: float) -> bool:
        """
        Guarda el resultado y devuelve True si su frame tiene que ser el que se muestre.

        Maneja el primero de cada cámara y, después, el que es posterior al último
        mostrado. Uno que no maneja igual queda guardado para `others()`.
        """
        camera_slot = result.camera_slot
        pair = (camera_slot, result.pipeline_slot)
        previous = self._latest.get(pair)
        gap_s = now_s - previous[1] if previous is not None else 0.0
        self._latest[pair] = (replace(result, source_bgr=None, annotated_bgr=None),
                              now_s, gap_s)
        shown_s = self._shown_timestamp_s.get(camera_slot)
        if shown_s is not None and result.timestamp_s <= shown_s:
            return False
        self._shown_timestamp_s[camera_slot] = result.timestamp_s
        return True

    def others(self, camera_slot: str, pipeline_slot: str,
               now_s: float) -> list[InferenceResult]:
        """
        Último resultado vigente de cada otro pipeline de esa cámara.

        Excluye el del propio `pipeline_slot` y los que ya pasaron su tiempo de vigencia.
        El orden es estable —el del primer resultado de cada pipeline desde que se lo
        olvidó—, así que lo que se dibuja encima de qué no cambia de un frame al siguiente.
        """
        return [result for (camera, pipeline), (result, added_s, gap_s)
                in self._latest.items()
                if camera == camera_slot and pipeline != pipeline_slot
                and not self._is_stale(pipeline, added_s, gap_s, now_s)]

    def has_fresh(self, camera_slot: str, now_s: float) -> bool:
        """True si a esa cámara le queda algún resultado vigente, de cualquier pipeline."""
        return any(camera == camera_slot and not self._is_stale(pipeline, added_s, gap_s, now_s)
                   for (camera, pipeline), (_, added_s, gap_s) in self._latest.items())

    def forget(self, camera_slot: str, pipeline_slot: str):
        """
        Olvida el par: su último resultado deja de dibujarse ya, y su período se vuelve a
        medir desde el próximo. Sin esto último, la pausa entera contaría como un período y
        el par quedaría vigente mucho después de morir.
        """
        self._latest.pop((camera_slot, pipeline_slot), None)

    def _is_stale(self, pipeline_slot: str, added_s: float, gap_s: float,
                  now_s: float) -> bool:
        period_s = max(self._interval_s_by_pipeline.get(pipeline_slot, 0.0), gap_s)
        return now_s - added_s > max(_STALE_MIN_S, _STALE_FACTOR * period_s)
