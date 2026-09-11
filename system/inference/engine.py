"""
Hilo de inferencia: corre un pipeline sobre los frames de las cámaras que tiene
asignadas y publica el resultado por señales de Qt.

Maquinaria genérica: reutilizable entre proyectos. Este archivo no sabe qué se está
midiendo. Lo específico del fork está en tres lugares y ninguno es este: el orden de las
etapas (`pipeline.py`), las métricas del proceso (`metrics.py`, que se inyecta como
`analyzer`) y las filas `producer: inference` del mapa de registros.

**Una instancia por pipeline, no por cámara.** Todas las cámaras del pipeline entran por
la misma cola y las atiende este único hilo, así que los pesos se cargan una sola vez y
el acceso a la GPU queda serializado por construcción, sin lock ni copia por cámara. Dos
pipelines distintos son dos hilos y sí corren en paralelo.

    camera_1 ──frame_ready──┐
                            ├──► pending{slot: último frame} ──► pipeline ──► result_ready
    camera_2 ──frame_ready──┘         (uno por cámara)            (etapas)

De qué se ocupa, en este orden, por cada frame:
  1. Le pasa el frame al `preprocessor`, si hay uno. Lo que devuelve es el **frame de
     referencia**: el que ve el modelo, el que queda en `source_bgr`, el espacio en el que
     se expresan el ROI y las detecciones, y el lienzo del overlay. Con un solo espacio de
     coordenadas no hay nada que mapear de vuelta ni cajas que caigan corridas.
  2. Recorta el ROI de esa cámara (`cameras.<slot>.roi`) y mide la iluminación.
  3. Descarta lo que no vale una pasada del modelo: sin modelo cargado, o iluminación por
     debajo de `cameras.<slot>.illumination_min`.
  4. Corre el pipeline y devuelve las detecciones al espacio del frame de referencia.
  5. Le pasa las detecciones al `classifier`, si hay uno: es donde se filtra y se etiqueta
     con lo que depende de la cámara, que el modelo no puede saber porque es uno solo para
     todas las del pipeline.
  6. Promedia la confianza y decide si el resultado es confiable
     (`min_result_confidence_pct`, `min_detections`).
  7. Llama al analyzer —sólo si el resultado es confiable— y dibuja el frame anotado.

Cuatro puntos del contrato que no se ven en las firmas:
  - **Siempre gana el último frame de cada cámara.** Una cámara que pushea más rápido de
    lo que el modelo infiere pierde los frames del medio, no hace cola; y ninguna cámara
    tapa a otra, porque el hilo las atiende por turno.
  - **Un resultado por frame procesado, sin promediar.** El promedio en el tiempo se hace
    afuera con `analysis.average_metrics`: promediar acá escondería la dispersión, que
    suele ser el dato que dice si el proceso está estable.
  - **Un resultado no confiable se emite igual**, con `is_valid` en False y el motivo. El
    frame anotado y la iluminación siguen sirviendo; los números no son una medición.
  - **El motor no sabe quién mira.** Para no dibujar anotaciones que nadie va a ver
    —cuesta más que codificar el frame— `main.py` le pasa un `annotate_gate`, típicamente
    el `has_annotated_clients` del servidor de video.

Cinco cosas entran por el constructor y son las que hacen que este archivo no sepa del
proyecto: el `preprocessor` (qué frame se mide), el `pipeline` (qué modelos y en qué
orden), el `classifier` (qué detecciones cuentan y qué clase les toca), el `analyzer` (qué
significan las detecciones) y el `annotator` (qué se dibuja además de lo que salió de la
inferencia). Todas son opcionales salvo el pipeline, y sin ellas el ciclo funciona igual.

El `classifier` existe porque `predict()` no recibe identidad de cámara: el modelo es uno
por slot de pipeline y lo comparten todas sus cámaras, así que no puede aplicar nada
calibrado por montaje —una escala de píxel, un filtro de tamaño, una clase por medida—. Y
el analyzer tampoco puede, porque no modifica el resultado. Corre entre el pipeline y el
promedio de confianza, así que lo que descarta no cuenta para `min_detections` ni para la
confianza del resultado, y la clase que deja en `class_index` es la que después colorean
el overlay y `analysis.count_by_class`.
"""

import threading
import time
from collections.abc import Callable

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

from system.config_manager import ConfigManager
from system.logger import logger

from . import analysis, overlay
from .abstract_pipeline import AbstractPipeline
from .result import (REASON_DARK_FRAME, REASON_ERROR, REASON_FEW_DETECTIONS,
                     REASON_LOW_CONFIDENCE, REASON_NO_MODEL, InferenceResult,
                     offset_detections)

_STATUS_INTERVAL_S = 2.0     # período de la telemetría de `status_updated`
_WAIT_TICK_S = 0.2           # espera sin frames; período de reacción a la interrupción
_WARN_PERIOD_S = 10.0        # techo de los avisos que salen del camino por frame
_DEFAULT_INTERVAL_S = 0.0    # 0 = se infiere cada frame que llega


class InferenceThread(QThread):
    """
    Corre un pipeline de inferencia sobre los frames de sus cámaras, en su propio hilo.

    `push_frame` es barata y thread-safe: la llaman los hilos de captura y vuelve sin
    inferir nada. `result_ready` sale desde este hilo, así que lo que se conecte del otro
    lado no puede bloquear.

    Las dependencias van explícitas: el preprocessor decide qué frame se mide, el pipeline
    qué modelos corren y en qué orden, el classifier qué detecciones cuentan y con qué
    clase, el analyzer qué significan las detecciones, el annotator qué se dibuja además de
    lo que salió de la inferencia, y el gate quién está mirando. Sin preprocessor se mide el
    frame de la cámara; sin classifier salen las detecciones tal como las dejó el pipeline;
    sin analyzer el resultado viaja con `metrics` vacío; sin annotator, con el anotado
    estándar.
    """

    result_ready = Signal(object)    # InferenceResult — uno por frame procesado
    status_updated = Signal(object)  # dict de get_status() — cada _STATUS_INTERVAL_S

    def __init__(self, config_manager: ConfigManager, pipeline: AbstractPipeline, *,
                 preprocessor: Callable[[np.ndarray, str], np.ndarray] | None = None,
                 classifier: Callable[[list, str], list] | None = None,
                 analyzer: Callable[[InferenceResult], dict] | None = None,
                 annotator: Callable[[np.ndarray, InferenceResult], None] | None = None,
                 annotate_gate: Callable[[str], bool] | None = None):
        super().__init__()
        self.pipeline_slot = pipeline.pipeline_slot
        self._config = config_manager
        self._pipeline = pipeline
        self._preprocessor = preprocessor
        self._classifier = classifier
        self._analyzer = analyzer
        self._annotator = annotator
        self._annotate_gate = annotate_gate

        # Último frame de cada cámara esperando su turno, y el turno mismo: el índice
        # rota sobre los slots pendientes para que ninguna cámara tape a las otras.
        self._pending: dict[str, np.ndarray] = {}
        self._pending_lock = threading.Lock()
        self._frame_available = threading.Event()
        self._next_slot_index = 0
        self._last_push_s: dict[str, float] = {}

        self._counters = {"processed": 0, "dropped": 0, "invalid": 0}
        self._last_inference_time_ms = 0.0
        self._last_status_time_s = 0.0
        self._stats_lock = threading.Lock()
        self._last_warn_s: dict[str, float] = {}

    # ── API pública ──────────────────────────────────────────────────────────

    @property
    def is_enabled(self) -> bool:
        """True si el pipeline está habilitado en el config. Se lee en cada push."""
        return bool(self._config.get(f"inference.pipelines.{self.pipeline_slot}.enabled", True))

    @property
    def camera_slots(self) -> tuple[str, ...]:
        """Cámaras que entran a este pipeline; vacío = todas las que le pusheen."""
        cameras = self._config.get(f"inference.pipelines.{self.pipeline_slot}.cameras", []) or []
        return tuple(str(slot) for slot in cameras)

    def push_frame(self, frame_bgr: np.ndarray | None, camera_slot: str, *,
                   ignore_interval: bool = False):
        """
        Deja el frame de una cámara para que se infiera. Barata y thread-safe.

        Es el slot de `CaptureThread.frame_ready` y respeta el orden de su payload, así
        que `main.py` las conecta directo. El frame se copia acá, porque el driver reusa
        su buffer en cuanto la llamada vuelve.

        Descarta sin avisar el frame que llega antes de que venza `interval_s`, y cuenta
        en `dropped` el que reemplaza a uno que el hilo todavía no consumió.
        `ignore_interval` fuerza la inferencia de ese frame: es lo que necesita un
        proyecto que captura con la luz encendida y no quiere esperar el período.
        """
        if frame_bgr is None or frame_bgr.size == 0 or not self.is_enabled:
            return
        if not self._accepts(camera_slot):
            return

        interval_s = self._get_interval_s()
        now_s = time.monotonic()
        with self._pending_lock:
            last_push_s = self._last_push_s.get(camera_slot, 0.0)
            if not ignore_interval and interval_s > 0 and (now_s - last_push_s) < interval_s:
                return
            self._last_push_s[camera_slot] = now_s
            replaced = camera_slot in self._pending
            self._pending[camera_slot] = frame_bgr.copy()
        if replaced:
            self._count("dropped")
        self._frame_available.set()

    def annotate(self, result: InferenceResult) -> np.ndarray | None:
        """
        Dibuja el resultado con las mismas opciones y el mismo annotator que el ciclo.

        Es para quien agrega varios resultados en una medición: el frame que eligió como
        representativo se anotó con SUS números, no con los del ciclo, así que hay que
        volver a dibujarlo o la imagen y el registro dicen cosas distintas.

        Corre en el hilo del que llama, no en éste. Es una sola pasada de dibujo por
        ciclo, no por frame.

        **No pasa por el gate**, y por eso es un método aparte. El gate existe por el costo
        por frame: dibujar cuesta más que codificar, así que a 5 fps no se dibuja para
        nadie. Ese argumento no aplica a un dibujo cada varios minutos, y aplicarlo igual
        tiene un costo peor: la pregunta «¿alguien mira?» se contesta en el instante del
        dibujo, así que si justo entonces la UI estaba en crudo y ningún cliente estaba
        conectado, la medición se queda sin imagen hasta el ciclo siguiente. El que se
        conecta después no tiene nada que ver, y no es que llegue tarde: nunca se dibujó.
        """
        return self._annotate(result, respect_gate=False)

    def get_status(self) -> dict:
        """
        Estado del motor con claves estables:

            pipeline_slot          : str
            running                : bool — el hilo está girando
            enabled                : bool — `inference.pipelines.<slot>.enabled`
            loaded                 : bool — todas las etapas con su modelo cargado
            synthetic              : bool — alguna etapa es un modelo sintético
            cameras                : cámaras asignadas; vacío = todas
            queued                 : frames esperando turno, uno por cámara como máximo
            processed / dropped    : frames inferidos y descartados por llegar de más
            invalid                : resultados que no son una medición del proceso
            last_inference_time_ms : duración del último frame procesado
            models                 : {model_slot: get_status() del modelo}
        """
        pipeline_status = self._pipeline.get_status()
        with self._pending_lock:
            queued = len(self._pending)
        with self._stats_lock:
            counters = dict(self._counters)
            last_inference_time_ms = self._last_inference_time_ms
        return {
            "pipeline_slot": self.pipeline_slot,
            "running": self.isRunning(),
            "enabled": self.is_enabled,
            "loaded": pipeline_status["loaded"],
            "synthetic": pipeline_status["synthetic"],
            "cameras": self.camera_slots,
            "queued": queued,
            "processed": counters["processed"],
            "dropped": counters["dropped"],
            "invalid": counters["invalid"],
            "last_inference_time_ms": round(last_inference_time_ms, 1),
            "models": pipeline_status["models"],
        }

    def run(self):
        self._pipeline.load()
        status = self._pipeline.get_status()
        stages = ", ".join(status["models"]) or "ninguna"
        logger.info(f"[Inference/{self.pipeline_slot}] Hilo iniciado. Etapas: {stages}.")
        if status["synthetic"]:
            # Mismo criterio que el aviso del modelo: es una condición declarada y no
            # una falla, y viaja igual en `is_synthetic` del resultado. Acá además
            # sería el segundo aviso del mismo hecho en dos líneas del log.
            logger.debug(
                f"[Inference/{self.pipeline_slot}] Alguna etapa es un modelo sintético: "
                f"los resultados no son una medición del proceso."
            )
        for model_slot, model_status in status["models"].items():
            if model_status["error"]:
                logger.error(
                    f"[Inference/{self.pipeline_slot}] Etapa '{model_slot}' sin modelo: "
                    f"{model_status['error']}."
                )

        while not self.isInterruptionRequested():
            job = self._take_next()
            if job is None:
                self._frame_available.wait(_WAIT_TICK_S)
                self._emit_status_if_due()
                continue

            camera_slot, frame_bgr = job
            result = self._process(frame_bgr, camera_slot)
            self.result_ready.emit(result)
            self._emit_status_if_due()

        logger.info(f"[Inference/{self.pipeline_slot}] Descargando los modelos y cerrando el hilo.")
        self._pipeline.unload()

    # ── Cola de frames ───────────────────────────────────────────────────────

    def _accepts(self, camera_slot: str) -> bool:
        """True si esa cámara está asignada al pipeline. Sin lista declarada, todas."""
        camera_slots = self.camera_slots
        if not camera_slots or camera_slot in camera_slots:
            return True
        self._warn_throttled(
            f"foreign_{camera_slot}",
            f"Llegó un frame de {camera_slot}, que no está en "
            f"'inference.pipelines.{self.pipeline_slot}.cameras': se descarta."
        )
        return False

    def _take_next(self) -> tuple[str, np.ndarray] | None:
        """
        Saca el frame de la próxima cámara por turno, o None si no hay nada pendiente.

        El turno rota sobre las cámaras que tienen frame: con el modelo más lento que la
        captura, tomar siempre la primera dejaría a las demás sin inferir nunca.
        """
        with self._pending_lock:
            if not self._pending:
                self._frame_available.clear()
                return None
            camera_slots = sorted(self._pending)
            index = self._next_slot_index % len(camera_slots)
            self._next_slot_index = index + 1
            camera_slot = camera_slots[index]
            return camera_slot, self._pending.pop(camera_slot)

    # ── Inferencia de un frame ───────────────────────────────────────────────

    def _process(self, frame_bgr: np.ndarray, camera_slot: str) -> InferenceResult:
        """Corre el ciclo completo sobre un frame y devuelve el resultado ya anotado."""
        started_s = time.perf_counter()
        frame_bgr = self._preprocess(frame_bgr, camera_slot)
        roi_px = self._read_roi_px(camera_slot, frame_bgr)
        region_bgr = self._crop(frame_bgr, roi_px)

        result = InferenceResult(
            camera_slot=camera_slot,
            pipeline_slot=self.pipeline_slot,
            source_bgr=frame_bgr,
            roi_px=roi_px,
            illumination_pct=_compute_illumination_pct(region_bgr),
            is_synthetic=self._pipeline.is_synthetic,
        )

        reason = self._gate_frame(result, camera_slot)
        if not reason:
            try:
                detections = self._pipeline.run(region_bgr)
            except Exception as e:
                logger.error(
                    f"[Inference/{self.pipeline_slot}] Fallo en el pipeline para "
                    f"{camera_slot}: {e}", exc_info=True)
                reason = REASON_ERROR
            else:
                if roi_px is not None:
                    offset_detections(detections, roi_px[0], roi_px[1])
                detections = self._classify(detections, camera_slot)
                result.detections = detections
                result.labels = self._pipeline.labels
                result.stage_times_ms = self._pipeline.stage_times_ms
                result.confidence_pct = analysis.average_confidence_pct(detections)
                reason = self._gate_result(result)

        result.is_valid = not reason
        result.invalid_reason = reason
        # Antes del analyzer: es parte del resultado que el analyzer recibe.
        result.inference_time_ms = (time.perf_counter() - started_s) * 1000
        if result.is_valid:
            result.metrics = self._run_analyzer(result)
        result.annotated_bgr = self._annotate(result)

        self._publish(result)
        return result

    def _gate_frame(self, result: InferenceResult, camera_slot: str) -> str:
        """
        Motivo por el que este frame no merece una pasada del modelo, o '' si la merece.

        La iluminación se mide sobre el ROI y no sobre el frame entero: es la zona que se
        analiza, y es la que se apaga cuando la luz falla.
        """
        if not self._pipeline.is_loaded:
            self._warn_throttled(
                "no_model",
                "El pipeline no tiene todas sus etapas cargadas: los resultados salen "
                "vacíos y marcados como no confiables."
            )
            return REASON_NO_MODEL

        illumination_min = int(self._config.get(f"cameras.{camera_slot}.illumination_min", 0) or 0)
        if result.illumination_pct < illumination_min:
            # A nivel DEBUG a propósito: con la luz apagada esto pasa en cada frame.
            logger.debug(
                f"[Inference/{self.pipeline_slot}] {camera_slot}: iluminación "
                f"{result.illumination_pct} < {illumination_min}. No se infiere."
            )
            return REASON_DARK_FRAME
        return ""

    def _gate_result(self, result: InferenceResult) -> str:
        """
        Motivo por el que el resultado no es una medición confiable, o '' si lo es.

        Las detecciones no se borran: el frame anotado las muestra igual y el recolector
        de dataset las puede usar para filtrar. Lo que cambia es que nadie las publique
        como medición.
        """
        section = f"inference.pipelines.{self.pipeline_slot}"
        min_detections = int(self._config.get(f"{section}.min_detections", 0) or 0)
        min_confidence_pct = float(self._config.get(f"{section}.min_result_confidence_pct", 0) or 0)

        if result.detection_count < min_detections:
            return REASON_FEW_DETECTIONS
        if result.detections and result.confidence_pct < min_confidence_pct:
            return REASON_LOW_CONFIDENCE
        return ""

    def _preprocess(self, frame_bgr: np.ndarray, camera_slot: str) -> np.ndarray:
        """
        Frame de referencia de este ciclo: lo que devuelva el preprocessor, o el de cámara.

        Es el único lugar donde se decide sobre qué imagen se mide, y por eso lo que sale
        de acá es lo que ve el modelo Y el lienzo del overlay: no pueden divergir. Un
        preprocessor que cambia el tamaño está permitido —recortar al corregir el lente es
        normal— y el ROI se lee después, así que sus coordenadas son de la imagen corregida.

        Un preprocessor que falla no pierde el frame: se mide el de cámara y se avisa.
        """
        if self._preprocessor is None:
            return frame_bgr
        try:
            preprocessed = self._preprocessor(frame_bgr, camera_slot)
        except Exception as e:
            name = getattr(self._preprocessor, "__name__", repr(self._preprocessor))
            self._warn_throttled(
                "preprocessor",
                f"El preprocessor {name} falló: {e}. Se mide el frame de cámara.")
            return frame_bgr
        if preprocessed is None or preprocessed.size == 0:
            self._warn_throttled(
                "preprocessor_empty",
                "El preprocessor devolvió un frame vacío. Se mide el frame de cámara.")
            return frame_bgr
        return preprocessed

    def _classify(self, detections: list, camera_slot: str) -> list:
        """
        Detecciones que cuentan, con la clase que les corresponde en esta cámara.

        Corre después de devolver las coordenadas al frame de referencia, así que el
        classifier recibe áreas y posiciones en el espacio en el que está calibrado.

        Un classifier que falla no pierde el frame: siguen las detecciones sin filtrar y se
        avisa. Es la misma decisión que en el preprocessor y el analyzer, y la alternativa
        —tirar el resultado— convertiría un error de configuración en una caída de la línea.
        """
        if self._classifier is None:
            return detections
        try:
            classified = self._classifier(detections, camera_slot)
        except Exception as e:
            name = getattr(self._classifier, "__name__", repr(self._classifier))
            self._warn_throttled(
                "classifier",
                f"El classifier {name} falló para {camera_slot}: {e}. "
                f"Las detecciones salen sin filtrar.")
            return detections
        return list(classified) if classified is not None else []

    def _run_analyzer(self, result: InferenceResult) -> dict:
        """Métricas del proyecto. Un analyzer que falla no se lleva puesto el resultado."""
        if self._analyzer is None:
            return {}
        try:
            return dict(self._analyzer(result) or {})
        except Exception as e:
            self._warn_throttled("analyzer", f"El analyzer falló: {e}. Resultado sin métricas.")
            return {}

    def _annotate(self, result: InferenceResult, *,
                  respect_gate: bool = True) -> np.ndarray | None:
        """
        Frame anotado, o None si el config lo apagó o nadie está mirando.

        Dibujar cuesta más que codificar, así que un stream sin clientes no se anota; el
        gate lo decide `main.py`, que es el único que sabe quién está conectado.
        `respect_gate` en False lo saltea: lo usa `annotate()`, que dibuja una vez por
        medición y no una vez por frame. Lo que apaga el config no se saltea nunca.

        Primero el anotado estándar y después el annotator del proyecto, que dibuja encima
        sus referencias. Un annotator que falla no se lleva puesto el frame: queda lo que
        haya alcanzado a dibujar más todo lo estándar.
        """
        if not self._config.get("inference.overlay.enabled", True):
            return None
        if (respect_gate and self._annotate_gate is not None
                and not self._annotate_gate(result.camera_slot)):
            return None
        try:
            annotated = overlay.annotate(result, self._read_overlay_options())
        except Exception as e:
            self._warn_throttled("overlay", f"No se pudo anotar el frame: {e}.")
            return None

        if annotated is not None and self._annotator is not None:
            name = getattr(self._annotator, "__name__", repr(self._annotator))
            try:
                self._annotator(annotated, result)
            except Exception as e:
                self._warn_throttled("annotator", f"El annotator {name} falló: {e}.")
        return annotated

    # ── Lectura de config ────────────────────────────────────────────────────

    def _get_interval_s(self) -> float:
        """
        Período mínimo entre frames aceptados de una misma cámara. 0 = todos.

        Se cuenta desde el push anterior aceptado y no desde el fin de la inferencia: así
        una inferencia lenta no acumula deuda que después se pague con una ráfaga.
        """
        interval_s = self._config.get(
            f"inference.pipelines.{self.pipeline_slot}.interval_s", _DEFAULT_INTERVAL_S)
        return max(0.0, float(interval_s or 0.0))

    def _read_roi_px(self, camera_slot: str,
                     frame_bgr: np.ndarray) -> tuple[int, int, int, int] | None:
        """
        ROI de esa cámara acotado al frame, o None si está apagado o no queda nada.

        Un ancho o alto en 0 llega hasta el borde: es lo que permite recortar sólo de un
        lado sin tener que escribir el tamaño del sensor en el config.
        """
        roi = self._config.get(f"cameras.{camera_slot}.roi", {}) or {}
        if not isinstance(roi, dict) or not roi.get("enabled", False):
            return None

        height_px, width_px = frame_bgr.shape[:2]
        x_px = max(0, min(width_px - 1, int(roi.get("x_px", 0) or 0)))
        y_px = max(0, min(height_px - 1, int(roi.get("y_px", 0) or 0)))
        roi_width_px = int(roi.get("width_px", 0) or 0) or (width_px - x_px)
        roi_height_px = int(roi.get("height_px", 0) or 0) or (height_px - y_px)
        roi_width_px = min(roi_width_px, width_px - x_px)
        roi_height_px = min(roi_height_px, height_px - y_px)

        if roi_width_px <= 0 or roi_height_px <= 0:
            self._warn_throttled(
                f"roi_{camera_slot}",
                f"El ROI de {camera_slot} queda vacío contra un frame de "
                f"{width_px}x{height_px}: se infiere el frame completo."
            )
            return None
        return (x_px, y_px, roi_width_px, roi_height_px)

    def _read_overlay_options(self) -> overlay.OverlayOptions:
        """Opciones de dibujado desde `inference.overlay`."""
        defaults = overlay.OverlayOptions()
        colors = self._config.get("inference.overlay.class_colors_bgr", []) or []
        return overlay.OverlayOptions(
            draw_boxes=bool(self._config.get("inference.overlay.draw_boxes", defaults.draw_boxes)),
            draw_masks=bool(self._config.get("inference.overlay.draw_masks", defaults.draw_masks)),
            mask_style=str(self._config.get("inference.overlay.mask_style",
                                            defaults.mask_style)),
            mask_alpha=float(self._config.get("inference.overlay.mask_alpha",
                                              defaults.mask_alpha)),
            draw_labels=bool(self._config.get("inference.overlay.draw_labels",
                                              defaults.draw_labels)),
            draw_roi=bool(self._config.get("inference.overlay.draw_roi", defaults.draw_roi)),
            draw_timestamp=bool(self._config.get("inference.overlay.draw_timestamp",
                                                 defaults.draw_timestamp)),
            crop_to_roi=bool(self._config.get("inference.overlay.crop_to_roi",
                                              defaults.crop_to_roi)),
            font_scale=float(self._config.get("inference.overlay.font_scale",
                                              defaults.font_scale)),
            thickness=int(self._config.get("inference.overlay.thickness", defaults.thickness)),
            class_colors_bgr=tuple(tuple(color) for color in colors),
        )

    # ── Internos ─────────────────────────────────────────────────────────────

    def _crop(self, frame_bgr: np.ndarray,
              roi_px: tuple[int, int, int, int] | None) -> np.ndarray:
        """Recorte del ROI, o el frame tal cual si no hay ROI."""
        if roi_px is None:
            return frame_bgr
        x_px, y_px, width_px, height_px = roi_px
        return frame_bgr[y_px:y_px + height_px, x_px:x_px + width_px]

    def _publish(self, result: InferenceResult):
        """Deja las cuentas donde `get_status()` las pueda leer sin esperar a una inferencia."""
        with self._stats_lock:
            self._counters["processed"] += 1
            if not result.is_valid:
                self._counters["invalid"] += 1
            self._last_inference_time_ms = result.inference_time_ms

    def _count(self, key: str):
        with self._stats_lock:
            self._counters[key] += 1

    def _emit_status_if_due(self):
        if (time.time() - self._last_status_time_s) <= _STATUS_INTERVAL_S:
            return
        self._last_status_time_s = time.time()
        self.status_updated.emit(self.get_status())

    def _warn_throttled(self, key: str, message: str):
        """Avisa como máximo una vez cada `_WARN_PERIOD_S` por clave."""
        now_s = time.monotonic()
        with self._stats_lock:
            last_warn_s = self._last_warn_s.get(key, 0.0)
            if now_s - last_warn_s < _WARN_PERIOD_S:
                return
            self._last_warn_s[key] = now_s
        logger.warning(f"[Inference/{self.pipeline_slot}] {message}")


def _compute_illumination_pct(frame_bgr: np.ndarray) -> int:
    """Brillo medio del frame, 0–100. Es lo que dice si la luz de la escena está encendida."""
    if frame_bgr is None or frame_bgr.size == 0:
        return 0
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
    return min(100, int(float(np.mean(gray)) * 100 / 255))
