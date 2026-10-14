"""
Inferencia fuera de la app: los pipelines del config, corridos sobre imágenes sueltas.

Maquinaria genérica: reutilizable entre proyectos. Arma cada pipeline habilitado igual que
la app —el mismo `Pipeline`, el mismo preprocessor y los mismos hooks del proyecto— y corre
el ciclo del motor con `InferenceThread.process()`, sin arrancar el hilo. El resultado de
una imagen es el que saldría en vivo si esa imagen llegara de la cámara: no hay un segundo
camino de inferencia que pueda divergir del primero.

    runner = OfflineRunner(config)
    runner.load()
    for result in runner.run_image(frame_bgr, "camera_1"):   # uno por pipeline
        ...
    runner.unload()

Qué no hace, a propósito:
  - **No consulta la licencia.** Es una herramienta de desarrollo y corre desde fuentes,
    donde el estado es `unlicensed_build` igual.
  - **No lee ni escribe archivos.** De dónde salen las imágenes y dónde queda el resultado
    es de quien lo llama; así esto se prueba sin disco.
  - **No respeta `interval_s`** ni la cola de la cámara: cada imagen que se le pasa se
    infiere.

`apply_preprocessor=False` es para las imágenes que ya son frame de referencia —las del
dataset, que se guardan corregidas y ajustadas—: pasarles el preprocessor otra vez les
corregiría el lente y les multiplicaría el brillo dos veces.
"""

import numpy as np

from system.config_manager import ConfigManager
from system.logger import logger

from .engine import InferenceThread
from .hooks import build_hooks
from .pipeline import Pipeline
from .preprocessing import build_preprocessor
from .result import InferenceResult


class OfflineRunner:
    """
    Los pipelines habilitados del config, listos para correr imágenes de a una.

    Se usa desde un solo hilo: los pipelines no son reentrantes. Cargar y descargar los
    modelos es explícito porque es lo caro, y quien corre una carpeta lo paga una vez.
    """

    def __init__(self, config: ConfigManager, *, apply_preprocessor: bool = True):
        preprocessor = build_preprocessor(config) if apply_preprocessor else None
        hooks = build_hooks(config)

        self._pipelines: list[Pipeline] = []
        self._engines: list[InferenceThread] = []
        for pipeline_slot in (config.get("inference.pipelines", {}) or {}):
            if not config.get(f"inference.pipelines.{pipeline_slot}.enabled", True):
                logger.info(f"[Offline] {pipeline_slot} deshabilitado: no se corre.")
                continue
            pipeline = Pipeline(config, pipeline_slot)
            # Sin `annotate_gate`: acá el anotado se dibuja siempre, porque es lo que se
            # viene a mirar.
            self._engines.append(InferenceThread(
                config, pipeline,
                preprocessor=preprocessor,
                classifier=hooks.classifier,
                analyzer=hooks.analyzer,
                annotator=hooks.annotator,
            ))
            self._pipelines.append(pipeline)

    @property
    def pipeline_slots(self) -> tuple[str, ...]:
        return tuple(engine.pipeline_slot for engine in self._engines)

    def load(self):
        """Carga los modelos de todos los pipelines. Una etapa que no carga queda en error."""
        for pipeline in self._pipelines:
            pipeline.load()

    def unload(self):
        for pipeline in self._pipelines:
            pipeline.unload()

    def get_status(self) -> dict:
        """`get_status()` del motor de cada pipeline, por slot: modelos cargados y errores."""
        return {engine.pipeline_slot: engine.get_status() for engine in self._engines}

    def run_image(self, frame_bgr: np.ndarray, camera_slot: str) -> list[InferenceResult]:
        """
        Corre la imagen por cada pipeline que recibe esa cámara, en el orden del config.

        Qué pipelines la reciben lo dice `inference.pipelines.<slot>.cameras`, igual que en
        vivo; una lista vacía las recibe a todas. Una cámara que no entra a ninguno
        devuelve lista vacía.
        """
        return [engine.process(frame_bgr, camera_slot) for engine in self._engines
                if not engine.camera_slots or camera_slot in engine.camera_slots]
