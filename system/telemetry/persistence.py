"""
Escritura de la telemetría del sistema, en batch y a través de backends.

Hilo propio. Los subsistemas dejan puntos con `push_data()` desde cualquier hilo y
siguen; este hilo los junta durante `_PUBLISH_INTERVAL_S` y escribe el batch en
cada backend.

La ventana no es configuración: no cambia la resolución de los datos —cada punto
viaja con el instante en que se midió— sino cada cuánto salen las escrituras.

Maquinaria genérica: no sabe qué se mide. Lo que arma es el registro que consumen
los backends:

    measurement : str    nombre de la serie
    tags        : dict   etiquetas
    fields      : dict   valores medidos
    time        : float  epoch en segundos: el del `push_data()`, o el que se le pase

El sello es el del `push_data()` y no el de la escritura: el valor vale por cuándo
se midió, y entre las dos cosas puede pasar una ventana entera. Quien publique un
punto que midió antes —un ciclo que agrega varios resultados y sale por un timer—
pasa el instante real en `time_s` en vez de dejar el del llamado.

Un backend es cualquier AbstractTelemetryBackend; el contrato está en su módulo.
Por defecto se arman los del repo —InfluxDB y MQTT—, que se deshabilitan solos si
su sección del config no los habilita. Para acotar la lista o sumar uno propio, se
pasan al constructor:

    thread = PersistenceThread(cfg, backends=[InfluxDBBackend(cfg)])

Tres puntos del contrato que no se ven en las firmas:
  - `push_data()` no bloquea nunca: con la cola llena descarta el punto que llega y
    lo avisa una sola vez. Es telemetría; no puede frenar a quien la produce.
  - Los backends los toca solo este hilo: se abren en `run()` y se cierran ahí
    mismo. Uno que levante excepción no baja al hilo ni a los otros.
  - Lo que quedó encolado al pedir la interrupción se escribe antes de cerrar. Lo
    que falle en esa escritura se pierde, con el motivo en el log: acá no hay
    reintento ni buffer en disco.
"""

import queue
import time

from PySide6.QtCore import QThread

from .backends import (
    AbstractTelemetryBackend,
    InfluxDBBackend,
    MqttBackend,
    STATUS_DISABLED,
)
from system.config_manager import ConfigManager
from system.logger import logger

# Backends que se arman si el llamador no pasa una lista. Sumar un destino nuevo
# —SQL, otro broker— es implementarlo en system/telemetry/backends/ y sumarlo acá.
_DEFAULT_BACKEND_CLASSES = (InfluxDBBackend, MqttBackend)

_QUEUE_MAXSIZE = 100      # puntos en vuelo; llena, se descarta el que llega
_QUEUE_POLL_S = 0.5       # espera por punto, y latencia máxima de la parada
_PUBLISH_INTERVAL_S = 10  # ventana de acumulación; con _QUEUE_MAXSIZE, tope de 10 puntos/s


class PersistenceThread(QThread):
    """
    Junta la telemetría del sistema y la escribe en batch, en su propio hilo.

    `push_data()` es thread-safe y está pensado para llamarse desde los hilos de
    captura e inferencia: encola y vuelve, sin tocar la red.
    """

    def __init__(self, config_manager: ConfigManager,
                 backends: list[AbstractTelemetryBackend] | None = None):
        super().__init__()
        # El config no se guarda: este hilo no lee ninguna clave, solo se lo pasa a
        # los backends que arma por defecto, que sí leen la suya.
        self._queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        self._backends = backends if backends is not None else [
            backend_class(config_manager) for backend_class in _DEFAULT_BACKEND_CLASSES
        ]
        # Anti-spam del aviso de cola llena (ver push_data).
        self._dropped_points = 0

    # ── API pública ──────────────────────────────────────────────────────────

    def push_data(self, measurement: str, fields: dict, *, tags: dict | None = None,
                  time_s: float | None = None):
        """
        Encola un punto de telemetría, sellado con la hora de este llamado.

        `time_s` sella el punto con otro instante, para cuando el que publica no es el que
        midió: un ciclo que junta varios resultados y sale por un timer sellaría todo con
        la hora del timer, corriendo el dato hasta una ventana entera. Con el instante de
        la medición, la serie queda donde corresponde.

        No bloquea ni levanta: con la cola llena el punto se descarta.
        """
        record = {"measurement": measurement, "tags": tags or {}, "fields": fields,
                  "time": time.time() if time_s is None else float(time_s)}
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._drop_point(measurement)
            return

        if self._dropped_points:
            logger.info(
                f"[Persistence] La cola se liberó; quedaron sin escribir "
                f"{self._dropped_points} puntos."
            )
            self._dropped_points = 0

    def backend_status(self, service_name: str) -> str:
        """
        Estado del backend con ese `service_name`, o 'disabled' si no está armado.

        Los valores posibles son los STATUS_* de AbstractTelemetryBackend.
        """
        for backend in self._backends:
            if backend.service_name == service_name:
                return backend.status
        return STATUS_DISABLED

    def run(self):
        logger.info("[Persistence] Hilo iniciado.")
        self._setup_backends()

        while not self.isInterruptionRequested():
            self._write(self._collect_batch())

        # Lo que ya se aceptó se escribe: descartarlo sería perder puntos que el
        # productor da por entregados.
        self._write(self._drain_queue())
        self._close_backends()
        logger.info("[Persistence] Hilo finalizado.")

    # ── Internos ─────────────────────────────────────────────────────────────

    def _drop_point(self, measurement: str):
        self._dropped_points += 1
        # Sin el latch esto inunda el log: con la cola llena se descarta cada push.
        if self._dropped_points == 1:
            logger.warning(
                f"[Persistence] Cola llena ({_QUEUE_MAXSIZE} puntos): se descarta "
                f"'{measurement}'. Los backends no dan abasto o están caídos."
            )

    def _collect_batch(self) -> list:
        """Junta los puntos que lleguen dentro de la ventana de publicación."""
        deadline_s = time.monotonic() + _PUBLISH_INTERVAL_S
        batch = []
        while not self.isInterruptionRequested():
            pending_s = deadline_s - time.monotonic()
            if pending_s <= 0:
                return batch
            try:
                batch.append(self._queue.get(timeout=min(_QUEUE_POLL_S, pending_s)))
            except queue.Empty:
                pass
        return batch

    def _drain_queue(self) -> list:
        """Vacía la cola sin esperar nada."""
        drained = []
        while True:
            try:
                drained.append(self._queue.get_nowait())
            except queue.Empty:
                return drained

    def _setup_backends(self):
        for backend in self._backends:
            try:
                backend.setup()
            except Exception as e:
                logger.error(f"[Persistence] {backend.service_name} falló en setup(): {e}")

    def _write(self, batch: list):
        if not batch:
            return
        for backend in self._backends:
            try:
                backend.write(batch)
            except Exception as e:
                logger.error(
                    f"[Persistence] {backend.service_name} falló escribiendo "
                    f"{len(batch)} puntos: {e}"
                )

    def _close_backends(self):
        for backend in self._backends:
            try:
                backend.close()
            except Exception as e:
                logger.error(f"[Persistence] {backend.service_name} falló en close(): {e}")
