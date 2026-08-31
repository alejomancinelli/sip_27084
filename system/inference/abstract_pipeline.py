"""
Contrato del pipeline de inferencia: la secuencia de modelos que se corre sobre un
frame.

Maquinaria genérica: reutilizable entre proyectos. Acá está la maquinaria de las
etapas —crear los modelos por slot, cargarlos, medirle el tiempo a cada una— y no
ninguna secuencia concreta.

**El orden de las etapas es código, no configuración.** Vive en la implementación
concreta —`pipeline.py` en el template—, porque encadenar dos modelos es lógica del
proceso: qué recorta el segundo, qué hace con lo que encontró el primero, cuándo no
vale la pena correrlo. Eso no se expresa en una lista del config.yaml. Lo que sí es
configuración es de dónde sale cada modelo (`inference.models.<slot>`) y qué cámaras
entran al pipeline (`inference.pipelines.<slot>.cameras`).

Una instancia por pipeline, no por cámara: todas las cámaras del pipeline la comparten
—las corre un único hilo, ver `engine`—, así que los pesos se cargan una sola vez sin
lock ni copia por cámara.

Qué fija el contrato:
  - **`model_slots` declara las etapas** y `load()` las crea con la fábrica en ese
    orden. Un slot que no se pudo configurar queda como NullModel y su etapa devuelve
    lista vacía, sin romper las demás.
  - **`run()` devuelve las detecciones finales** en coordenadas del frame que recibió, y
    deja el tiempo de cada etapa en `stage_times_ms`.
  - **Lo que una etapa dice del frame entero va en `labels`**, no en las detecciones. Una
    clasificación no tiene forma ni posición: el pipeline la anota con `_set_label()` y el
    motor la copia al resultado. Es también la forma de contar lo que se decidió cuando
    una etapa se saltea: la etapa que no corrió no deja tiempo en `stage_times_ms`, pero
    el veredicto que la salteó queda a la vista.
  - **El pipeline no dibuja, no filtra por resultado y no calcula métricas del proceso.**
    Eso es de `overlay`, del motor y del analyzer, en ese orden.
  - **No propaga los errores de una etapa hacia adentro:** los deja subir al motor, que
    es el que marca el resultado como no confiable con el motivo `error`.

`is_synthetic` en True dice que alguna etapa es un modelo sintético, y viaja hasta el
resultado: lo que sale no es una medición del proceso.
"""

import time
from abc import ABC, abstractmethod

import numpy as np

from system.config_manager import ConfigManager

from .models.abstract_model import AbstractModel
from .models.model_factory import create_model
from .result import Detection


class AbstractPipeline(ABC):
    """
    Contrato del pipeline: ciclo de vida de sus modelos, corrida de las etapas y estado
    legible desde afuera.

    La subclase declara `model_slots` e implementa `_run()`, que es donde va el orden.
    """

    # Slots de `inference.models` que usa el pipeline, en el orden en que se cargan. La
    # implementación concreta lo pisa.
    model_slots: tuple[str, ...] = ()

    def __init__(self, config_manager: ConfigManager, pipeline_slot: str):
        self.pipeline_slot = pipeline_slot
        self._config = config_manager
        self._models: dict[str, AbstractModel] = {}
        self._stage_times_ms: dict[str, float] = {}
        self._labels: dict[str, str] = {}

    # ── Estado ───────────────────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        """True si todas las etapas declaradas tienen su modelo cargado."""
        if not self.model_slots:
            return False
        return all(model.is_loaded for model in self._models.values()) \
            and len(self._models) == len(self.model_slots)

    @property
    def is_synthetic(self) -> bool:
        """True si alguna etapa es un modelo sintético: lo que sale no es una medición."""
        return any(model.is_synthetic for model in self._models.values())

    @property
    def stage_times_ms(self) -> dict:
        """Duración de cada etapa de la última corrida, por slot de modelo."""
        return dict(self._stage_times_ms)

    @property
    def labels(self) -> dict:
        """Veredictos de frame de la última corrida: {nombre: valor}, todos texto."""
        return dict(self._labels)

    def get_status(self) -> dict:
        """
        Estado del pipeline con claves estables:

            pipeline_slot : str
            loaded        : bool — todas las etapas cargadas
            synthetic     : bool
            models        : {model_slot: get_status() del modelo}
        """
        return {
            "pipeline_slot": self.pipeline_slot,
            "loaded": self.is_loaded,
            "synthetic": self.is_synthetic,
            "models": {slot: model.get_status() for slot, model in self._models.items()},
        }

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    def load(self):
        """
        Crea y carga los modelos de `model_slots`, en orden. Idempotente.

        No propaga errores: un modelo que no pudo cargar deja el motivo en su propio
        status y su etapa devuelve lista vacía.
        """
        for model_slot in self.model_slots:
            if model_slot in self._models:
                continue
            model = create_model(self._config, model_slot)
            model.load()
            self._models[model_slot] = model

    def unload(self):
        """Descarga todas las etapas y suelta las instancias. Idempotente."""
        for model in self._models.values():
            model.unload()
        self._models.clear()
        self._stage_times_ms.clear()
        self._labels.clear()

    # ── Corrida ──────────────────────────────────────────────────────────────

    def run(self, frame_bgr: np.ndarray) -> list[Detection]:
        """
        Corre el pipeline sobre el frame y devuelve las detecciones finales.

        Reinicia los tiempos por etapa y los labels antes de llamar a `_run()`, así que
        los dos corresponden siempre a la última corrida y un veredicto viejo no
        sobrevive al frame que lo produjo.
        """
        self._stage_times_ms = {}
        self._labels = {}
        return self._run(frame_bgr)

    @abstractmethod
    def _run(self, frame_bgr: np.ndarray) -> list[Detection]:
        """
        Encadena las etapas. Acá va el orden, que es específico del proyecto.

        Se llama desde el hilo del motor, un frame a la vez.
        """

    def _run_stage(self, model_slot: str, frame_bgr: np.ndarray,
                   previous: list[Detection] | None = None) -> list[Detection]:
        """
        Corre una etapa midiéndole el tiempo. Devuelve [] si ese modelo no cargó.

        `previous` es lo que trajo la etapa anterior: el modelo decide si recorta por
        ahí o si lo ignora y mira el frame completo.
        """
        model = self._models.get(model_slot)
        if model is None or not model.is_loaded:
            return []
        started_s = time.perf_counter()
        try:
            return model.predict(frame_bgr, previous)
        finally:
            self._stage_times_ms[model_slot] = round((time.perf_counter() - started_s) * 1000, 1)

    def _set_label(self, name: str, value: object):
        """
        Anota un veredicto de frame de esta corrida. El valor se guarda como texto.

        Es para lo que la imagen entera dice y no una instancia: el estado de la cinta, el
        modo en el que quedó el pipeline, por qué se salteó una etapa. Los números del
        proceso no van acá: los calcula el analyzer y viajan en `metrics`.
        """
        self._labels[name] = str(value)
