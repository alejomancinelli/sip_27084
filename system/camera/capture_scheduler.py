"""
Ciclo de medición: N frames por cámara agregados en un solo resultado.

Maquinaria genérica: reutilizable entre proyectos. Este archivo no sabe qué se está
midiendo; agrega lo que venga en `metrics` con las funciones de `analysis`.

Un proyecto que responde con una medición cada varios minutos no mide un frame: mide N y
promedia, porque la dispersión entre esos N es lo que dice si el proceso está estable. El
motor no hace eso a propósito —de él sale un resultado por frame, sin promediar—, así que
el ciclo lo orquesta este módulo.

    take_frame(cam, pipe) == True ──► el cableado empuja ese frame al motor
              ▲                                      │
              │                                      ▼
              └── faltan frames ──── on_result_ready(result)
                                                     │
                                        ciclo completo ──► cycle_complete

Dos cosas lo mueven, y son distintas a propósito: **los frames abren y cierran el permiso**
(`take_frame`) y **el tiempo lo trae `tick()`**, que llama el cableado una vez por segundo.
Separarlos es lo que hace que un frame que se pidió y no volvió venza aunque la cámara se
haya caído entera, y es lo que permite apagar la captura entre ciclos: una cámara apagada
no manda frames, así que si el reloj dependiera de ellos no podría volver a prenderse.

**No tiene QTimer propio y es a propósito**: se testea sin event loop de Qt, y no queda una
cadena de `singleShot` viva después del `stop()`.

**Tampoco guarda frames.** Un buffer del último frame de cada cámara cuesta una copia por
frame a ritmo de cámara —decenas de MB/s— para después inferir sobre una imagen que puede
tener segundos. Acá se deja pasar el próximo que llegue, que es más nuevo y no se copió.

Qué fija el contrato:
  - **Una ronda por pipeline, una cámara por vez.** Se piden los N frames de la primera
    cámara, después los de la segunda, y así. Un `cycle_complete` por cámara y por ciclo.
    Serializar es gratis —el motor es un solo hilo— y mantiene el ancho de banda de la red
    en el de una cámara.
  - **Con `frames_per_cycle` en 1 es un pass-through.** El permiso está siempre abierto,
    no se toca `ignore_interval` —así el `interval_s` del motor sigue mandando— y cada
    resultado se reemite tal cual. Un proyecto sin ciclo no cambia de comportamiento.
  - **Un ciclo sin resultados válidos publica ceros, no lo de antes.** Si no se emitiera
    nada, el PLC seguiría leyendo la última medición buena después de que se quemó la
    lámpara. Se emiten las claves que ese par (cámara, pipeline) venía publicando, en cero,
    con `sample_count` en 0. **`sample_count` es la prueba de validez**: dice cuántos de
    los N contaron, que es más que un bit de «anduvo o no».
  - **Un frame que se pidió y nunca volvió no traba la ronda.** A los `capture_timeout_s`
    se lo da por perdido y se sigue: sin eso, un pipeline deshabilitado o un frame que el
    motor descartó dejan el ciclo colgado para siempre mientras el heartbeat sigue latiendo.
  - **El scheduler no toca las cámaras**: pide por `capture_wanted` y el cableado aplica.
    Un subsistema de nivel 3 no llama a otro.

Vive en el hilo de la GUI, que es donde Qt entrega las señales de los hilos de captura y
de inferencia, así que su estado no necesita lock.
"""

import time
from dataclasses import replace

from PySide6.QtCore import QObject, Signal, Slot

from system.config_manager import ConfigManager
from system.logger import logger

from ..inference import analysis
from ..inference.result import InferenceResult

_DEFAULT_FRAMES_PER_CYCLE = 1
_DEFAULT_CYCLE_INTERVAL_S = 0.0    # 0 = la ronda siguiente arranca apenas termina la anterior
_DEFAULT_CAPTURE_TIMEOUT_S = 30.0

# Sufijo de `summarize_metrics` que ya aporta `average_metrics` con el nombre limpio. El
# nombre limpio es el que va al PLC, así que el duplicado se descarta.
_MEAN_SUFFIX = "_mean"

# Motivo con el que sale un ciclo del que no se pudo medir nada.
REASON_EMPTY_CYCLE = "empty_cycle"


def _describe(metrics: dict) -> str:
    """
    Las métricas del ciclo en una línea, sin los estadísticos.

    Sólo los nombres limpios: la dispersión va a la telemetría y al dataset, y meterla acá
    multiplica por cinco el largo de una línea que se lee de un vistazo.
    """
    shown = [f"{name}={value}" for name, value in sorted(metrics.items())
             if not analysis.is_summary_key(name)]
    return "  ".join(shown) if shown else "sin métricas"


class _PipelineCycle:
    """Estado de la ronda de un pipeline: en qué cámara va y qué lleva juntado."""

    def __init__(self, camera_slots: tuple[str, ...]):
        self.camera_slots = camera_slots
        self.camera_index = 0
        self.results: list[InferenceResult] = []
        self.taken_at_s = 0.0        # cuándo se pidió el frame que se está esperando
        self.is_waiting = False
        self.next_round_at_s = 0.0
        # Claves que cada cámara publicó la última vez que midió algo: es lo que se pone
        # en cero cuando un ciclo no mide nada.
        self.known_keys_by_camera: dict[str, tuple[str, ...]] = {}

    @property
    def camera_slot(self) -> str:
        """Cámara a la que le toca, o '' si la ronda no tiene ninguna."""
        if not self.camera_slots:
            return ""
        return self.camera_slots[self.camera_index % len(self.camera_slots)]


class CaptureScheduler(QObject):
    """
    Orquesta el ciclo de medición de uno o varios pipelines.

    `take_frame` es el permiso para empujar un frame al motor y **se consume**: contesta
    True una sola vez por frame pedido. `on_result_ready` es el slot de
    `InferenceThread.result_ready`, y `tick()` lo llama el cableado una vez por segundo.
    """

    # InferenceResult del ciclo — uno por cámara y por ciclo. Lleva los frames y las
    # detecciones del representativo, y en `metrics` el resumen de los N.
    cycle_complete = Signal(object)
    # (camera_slot, enabled) — sólo cuando cambia. El cableado se lo pasa al hilo de
    # captura; con `idle_cameras_between_cycles` apagado no se emite nunca.
    capture_wanted = Signal(str, bool)

    def __init__(self, config_manager: ConfigManager, pipeline_slots: tuple[str, ...],
                 parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._cycles = {slot: _PipelineCycle(self._read_camera_slots(slot))
                        for slot in pipeline_slots}
        self._capture_enabled: dict[str, bool] = {}
        self._running = False

    # ── API pública ──────────────────────────────────────────────────────────

    def start(self):
        """Abre la primera ronda de cada pipeline. Idempotente."""
        if self._running:
            return
        self._running = True
        for cycle in self._cycles.values():
            self._open_round(cycle)
        self._refresh_capture()
        logger.info(f"[Scheduler] Ciclo iniciado para {', '.join(self._cycles) or 'nada'}.")

    def stop(self):
        """Corta las rondas y devuelve las cámaras a capturar. Idempotente."""
        if not self._running:
            return
        self._running = False
        for cycle in self._cycles.values():
            cycle.results = []
            cycle.is_waiting = False
        # Nadie queda a oscuras porque se paró el ciclo: el stream crudo y la vista del
        # operador no dependen de que haya una medición en curso.
        self._refresh_capture()
        logger.info("[Scheduler] Ciclo detenido.")

    def tick(self):
        """
        Le pasa el tiempo al ciclo. La llama el cableado una vez por segundo.

        Es lo único que hace avanzar lo que depende del reloj, y por eso no depende de que
        las cámaras estén mandando frames.
        """
        if not self._running:
            return
        for pipeline_slot, cycle in self._cycles.items():
            if self.is_cycled(pipeline_slot):
                self._expire_if_late(cycle, pipeline_slot)
        self._refresh_capture()

    def is_cycled(self, pipeline_slot: str) -> bool:
        """True si este pipeline mide de a varios frames y hay que forzarle el intervalo."""
        return self._read_frames_per_cycle(pipeline_slot) > 1

    def take_frame(self, camera_slot: str, pipeline_slot: str) -> bool:
        """
        True si este frame es el que el ciclo estaba esperando. **Consume el permiso.**

        La llamada tiene efecto: contesta True una sola vez por frame pedido y queda
        esperando el resultado. Es lo que hace que no haya que guardar el frame en ningún
        lado —se deja pasar el que llegue— y que el cableado sea una sola línea.

        Un pipeline sin ciclo contesta siempre que sí: el ritmo lo sigue poniendo el
        `interval_s` del motor.
        """
        cycle = self._cycles.get(pipeline_slot)
        if cycle is None or not self.is_cycled(pipeline_slot):
            return True
        if not self._running or cycle.is_waiting:
            return False
        if camera_slot != cycle.camera_slot or not self._is_round_open(cycle):
            return False

        cycle.is_waiting = True
        cycle.taken_at_s = time.monotonic()
        return True

    @Slot(object)
    def on_result_ready(self, result: InferenceResult):
        """Slot de `result_ready`: suma el frame al ciclo y lo cierra si ya están los N."""
        cycle = self._cycles.get(result.pipeline_slot)
        if cycle is None or not self.is_cycled(result.pipeline_slot):
            self.cycle_complete.emit(result)
            return
        if not self._running or not cycle.is_waiting:
            return
        if result.camera_slot != cycle.camera_slot:
            return

        cycle.is_waiting = False
        cycle.results.append(result)
        if len(cycle.results) >= self._read_frames_per_cycle(result.pipeline_slot):
            self._close_camera(cycle, result.pipeline_slot)

    def get_status(self) -> dict:
        """
        Estado del ciclo con claves estables:

            running   : bool
            pipelines : {pipeline_slot: {camera, collected, frames_per_cycle, waiting}}
        """
        pipelines = {}
        for pipeline_slot, cycle in self._cycles.items():
            pipelines[pipeline_slot] = {
                "camera": cycle.camera_slot,
                "collected": len(cycle.results),
                "frames_per_cycle": self._read_frames_per_cycle(pipeline_slot),
                "waiting": cycle.is_waiting,
            }
        return {"running": self._running, "pipelines": pipelines}

    # ── Ronda ────────────────────────────────────────────────────────────────

    def _open_round(self, cycle: _PipelineCycle):
        cycle.camera_index = 0
        cycle.results = []
        cycle.is_waiting = False

    def _is_round_open(self, cycle: _PipelineCycle) -> bool:
        """True cuando ya venció la espera entre rondas."""
        return time.monotonic() >= cycle.next_round_at_s

    def _expire_if_late(self, cycle: _PipelineCycle, pipeline_slot: str):
        """Da por perdido el frame que se pidió y nunca volvió."""
        if not cycle.is_waiting:
            return
        timeout_s = float(self._read_option(pipeline_slot, "capture_timeout_s",
                                            _DEFAULT_CAPTURE_TIMEOUT_S) or 0.0)
        if timeout_s <= 0 or (time.monotonic() - cycle.taken_at_s) < timeout_s:
            return
        cycle.is_waiting = False
        logger.warning(
            f"[Scheduler] {pipeline_slot}/{cycle.camera_slot}: el frame pedido no volvió "
            f"en {timeout_s:g} s. Se sigue con el ciclo.")
        # Sin un solo resultado en todo el timeout la cámara no está dando nada: se cierra
        # igual para que la ronda avance, en vez de reintentar contra una cámara caída.
        if not cycle.results:
            self._close_camera(cycle, pipeline_slot)

    def _close_camera(self, cycle: _PipelineCycle, pipeline_slot: str):
        """Emite el resultado del ciclo de esta cámara y pasa a la siguiente."""
        camera_slot = cycle.camera_slot
        self.cycle_complete.emit(self._build_cycle_result(cycle, camera_slot, pipeline_slot))
        cycle.results = []
        cycle.is_waiting = False
        cycle.camera_index += 1
        if cycle.camera_index >= len(cycle.camera_slots):
            interval_s = float(self._read_option(pipeline_slot, "cycle_interval_s",
                                                 _DEFAULT_CYCLE_INTERVAL_S) or 0.0)
            cycle.next_round_at_s = time.monotonic() + max(0.0, interval_s)
            self._open_round(cycle)
        self._refresh_capture()

    # ── Captura entre ciclos ─────────────────────────────────────────────────

    def _refresh_capture(self):
        """
        Pide prender o apagar la captura de cada cámara, sólo cuando cambia.

        Apagar entre mediciones libera el ancho de banda del bus y el CPU del hilo de
        captura, y el costo es que el stream crudo y la vista del operador se quedan sin
        imagen mientras tanto: por eso lo decide el config y viene apagado.
        """
        wanted = self._wanted_capture()
        for camera_slot, enabled in wanted.items():
            if self._capture_enabled.get(camera_slot) == enabled:
                continue
            self._capture_enabled[camera_slot] = enabled
            self.capture_wanted.emit(camera_slot, enabled)

    def _wanted_capture(self) -> dict[str, bool]:
        """
        Qué cámara tiene que estar capturando ahora mismo, de las que el ciclo gobierna.

        Sólo entran las cámaras de un pipeline que pidió apagarlas entre mediciones. De las
        demás no se opina: una cámara con `cameras.<slot>.enabled: false` está apagada a
        propósito, y decir «prendida» acá la prendería.

        Una cámara que además está en un pipeline en tiempo real captura igual: alcanza con
        que alguien la quiera.
        """
        managed: set[str] = set()
        wanted: dict[str, bool] = {}
        for pipeline_slot, cycle in self._cycles.items():
            idles = (self.is_cycled(pipeline_slot) and bool(self._read_option(
                pipeline_slot, "idle_cameras_between_cycles", False)))
            if idles:
                managed.update(cycle.camera_slots)
            for camera_slot in cycle.camera_slots:
                if idles and self._running:
                    enabled = (camera_slot == cycle.camera_slot
                               and self._is_round_open(cycle))
                else:
                    enabled = True
                wanted[camera_slot] = wanted.get(camera_slot, False) or enabled
        return {slot: enabled for slot, enabled in wanted.items() if slot in managed}

    # ── Agregación ───────────────────────────────────────────────────────────

    def _build_cycle_result(self, cycle: _PipelineCycle, camera_slot: str,
                            pipeline_slot: str) -> InferenceResult:
        """
        Un resultado por ciclo: los frames del representativo, las métricas de los N.

        El representativo es el frame más cercano al promedio, así que lo que se guarda en
        el dataset y se muestra no es uno cualquiera de los N.
        """
        valid = [result for result in cycle.results if result.is_valid]
        if not valid:
            return self._build_empty_result(cycle, camera_slot, pipeline_slot)

        summary = analysis.summarize_metrics(valid)
        metrics = dict(analysis.average_metrics(valid))
        metrics.update({key: value for key, value in summary.items()
                        if not key.endswith(_MEAN_SUFFIX)})
        cycle.known_keys_by_camera[camera_slot] = tuple(metrics)

        representative = analysis.pick_representative(valid) or valid[0]
        logger.info(
            f"[Scheduler] {pipeline_slot}/{camera_slot}: ciclo con "
            f"{len(valid)}/{len(cycle.results)} frames — {_describe(metrics)}")
        # `replace` y no mutación: ese resultado ya viajó a la UI y a los streams.
        return replace(representative, metrics=metrics)

    def _build_empty_result(self, cycle: _PipelineCycle, camera_slot: str,
                            pipeline_slot: str) -> InferenceResult:
        """Ceros explícitos: el PLC no puede quedarse leyendo la última medición buena."""
        metrics = {key: 0 for key in cycle.known_keys_by_camera.get(camera_slot, ())}
        metrics["sample_count"] = 0
        logger.warning(
            f"[Scheduler] {pipeline_slot}/{camera_slot}: ningún frame del ciclo midió "
            f"({len(cycle.results)} recibidos). Se publican ceros.")
        if cycle.results:
            return replace(cycle.results[-1], metrics=metrics,
                           is_valid=False, invalid_reason=REASON_EMPTY_CYCLE)
        return InferenceResult(camera_slot=camera_slot, pipeline_slot=pipeline_slot,
                               metrics=metrics, is_valid=False,
                               invalid_reason=REASON_EMPTY_CYCLE)

    # ── Configuración ────────────────────────────────────────────────────────

    def _read_camera_slots(self, pipeline_slot: str) -> tuple[str, ...]:
        """Cámaras de la ronda; vacío en el pipeline significa todas las declaradas."""
        declared = self._read_option(pipeline_slot, "cameras", []) or []
        if declared:
            return tuple(str(slot) for slot in declared)
        return tuple(self._config.get("cameras", {}) or {})

    def _read_frames_per_cycle(self, pipeline_slot: str) -> int:
        return max(1, int(self._read_option(pipeline_slot, "frames_per_cycle",
                                            _DEFAULT_FRAMES_PER_CYCLE) or 1))

    def _read_option(self, pipeline_slot: str, key: str, default: object) -> object:
        return self._config.get(f"inference.pipelines.{pipeline_slot}.{key}", default)
