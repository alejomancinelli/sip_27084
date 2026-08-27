"""
Composition root: arma los subsistemas, los conecta por señales y los baja ordenados.

Uso — con el intérprete del venv, no con el `python` del PATH:

    Windows:  .venv\\Scripts\\python.exe main.py
    Linux:    .venv/bin/python main.py

    .venv/bin/python main.py --headless          # sin ventana

Acepta una ruta de config como argumento (`main.py otro-config.yaml`); sin argumento usa
el `config.yaml` de la raíz del repo.

**Modo headless.** Con `--headless`, o con `ui.enabled: false` en el config, no se crea
ninguna ventana: es el modo de un equipo en gabinete sin monitor, de un servicio que
arranca con el sistema, y de un contenedor sin X11. El flag manda sobre el config, para
poder correr una vez sin pantalla sin editar nada.

Qué **no** cambia en headless: el cableado. Los subsistemas son los mismos y se conectan
igual, porque Qt sigue haciendo falta —las señales son el cableado entre hilos, no un
detalle de la GUI—; lo que cambia es que se arma un `QCoreApplication` en vez de un
`QApplication` y que la interfaz la reemplaza un no-op con la misma API, igual que
`NullDriver` reemplaza a una cámara mal configurada. Ningún método de este archivo
pregunta si hay ventana.

Qué sí cambia: sin nadie mirando, el overlay se dibuja sólo si hay un cliente conectado a
un stream anotado, y el log —consola y archivo— es la única forma de ver qué pasa. Por eso
los cambios de estado de cada servicio se loguean, en los dos modos.

**Nadie importa este archivo.** Es el único que conoce a todos los subsistemas, y por eso
es el único lugar donde se los conecta: ninguno de ellos sabe que los otros existen. Un
`QThread` que importa otro `QThread` es un error de diseño, no un atajo.

Lo que este archivo hace, y nada más:

  - Fija el nivel de log y el handler a archivo, que es lo que el logger no puede hacer
    solo porque no lee el config.
  - Instancia cada subsistema con su config y los arranca.
  - Conecta señales con slots, y traduce en el borde: valores físicos al esquema de
    registros, estados de subsistema a la palabra de comunicaciones, dicts de status a la
    UI.
  - Los baja en orden y espera a los hilos.

Lo que **no** hace, y por qué:

  - **No calcula nada del proceso.** Eso es `metrics.py`, que entra por el constructor
    del motor.
  - **No corre bits ni conoce direcciones.** Los bitfields los arma su módulo de
    `system/formats/` y las direcciones salen de `SCHEMA.addr()`; acá sólo se juntan los
    valores físicos.
  - **No dibuja.** El overlay es del motor; las referencias de la planta son annotators.

Los cinco puntos de extensión del motor, y de dónde sale cada uno:

    preprocessor    `tools/image/undistort.py`, si alguna cámara declara calibración
    pipeline        `system/inference/pipeline.py`, que el fork reescribe
    analyzer        `system/inference/metrics.py`, que el fork reescribe
    annotator       `system/inference/annotations.py`, compuesto abajo con `process:`
    annotate_gate   acá: se dibuja si lo mira la UI o un cliente del stream anotado

**Qué toca el fork.** Los tres métodos del bloque «Lo que cambia en cada fork»: qué mira
el operador, qué se dibuja además del resultado, y con qué parámetros se cuenta. El resto
es cableado genérico y se cross-portea sin editar.

Cómo llegan los números al PLC: se publican los registros de salud —heartbeat, hardware,
estado por cámara, palabras de estado— y, de las métricas del analyzer, **las que tengan
una fila con su mismo nombre en `register_map.yaml`**. Una métrica sin fila no se publica y
se avisa una vez: el nombre de la métrica es el contrato con el integrador.
"""

import argparse
import logging
import logging.handlers
import os
import signal
import statistics
import sys
import threading
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QWidget

from system import paths
from system.camera.capture_thread import CaptureThread
from system.config_manager import ConfigManager
from system.formats import camera_health, com_status, system_status
from system.image_collector.collector import ImageCollector
from system.inference import analysis, annotations
from system.inference.engine import InferenceThread
from system.inference.metrics import compute_metrics
from system.inference.pipeline import Pipeline
from system.inference.result import InferenceResult
from system.logger import logger
from system.modbus.registers import REGISTERS, SCHEMA
from system.modbus.server import SharedModbusServer
from system.system_monitor import SystemMonitor
from system.telemetry.persistence import PersistenceThread
from system.version import APP_VERSION
from system.video.abstract_video_server import (
    MODE_ANNOTATED, MODES, STATUS_ACTIVE,
)
from system.video.http_server import HttpVideoServer
from system.video.rtsp_server import RtspVideoServer
from tools.image.undistort import build_undistorter

from ui import service_status
# `MainWindow` y `CameraGrid` se importan dentro de `_QtUi`: en headless no se construye
# ninguna ventana y no hace falta cargar el árbol de vistas.

_METRICS_INTERVAL_S = 1.0         # cada cuánto se pide una métrica de hardware
_REGISTERS_INTERVAL_MS = 1000     # cada cuánto se escriben los registros
_STATUS_INTERVAL_MS = 1000        # cada cuánto se releen los `status` de los subsistemas
_THREAD_STOP_TIMEOUT_MS = 5000    # espera a cada QThread al cerrar
_SERVER_STOP_TIMEOUT_S = 5.0      # espera al hilo del servidor Modbus al cerrar

_HEARTBEAT_MAX = 65535            # el registro es uint16: el contador da la vuelta ahí
_SIGNAL_POLL_INTERVAL_MS = 200     # timer al vacío para que Qt atienda Ctrl+C y SIGTERM

# El muestreo de la telemetría no es configuración: un segundo es la resolución con la
# que se miran estas series y no hay instalación que quiera otra. Más fino no agrega
# información —el hardware no cambia más rápido— y más grueso pierde el detalle de un
# pico. Lo que sí varía entre instalaciones es cuántas cámaras hay, y eso ya entra en la
# cuenta del margen.
_SAMPLE_INTERVAL_MS = 1000
_SAMPLE_INTERVAL_S = _SAMPLE_INTERVAL_MS / 1000

# `_QUEUE_MAXSIZE / _PUBLISH_INTERVAL_S` de `persistence.py`. **No es un techo de puntos
# por segundo**: el hilo de telemetría drena la cola de continuo, así que en régimen
# aguanta muchísimo más —medido: 50 puntos/s sin descartar uno—. Es la tasa a la que la
# cola absorbe exactamente una ventana de publicación con el backend trabado: por encima,
# una sola escritura lenta a InfluxDB alcanza para llenarla y empezar a descartar. Por eso
# el arranque avisa, y por eso el aviso habla de tolerancia y no de tope.
# Ver docs/influxdb.md.
_TELEMETRY_STALL_SAFE_POINTS_PER_S = 10.0
_TELEMETRY_QUEUE_POINTS = 100.0    # `_QUEUE_MAXSIZE` de persistence.py, para el aviso

# Measurements de las series de salud. Los nombres son el contrato con los dashboards:
# renombrarlos rompe los paneles que ya consultan.
_MEASUREMENT_SYSTEM = "system"
_MEASUREMENT_CAMERA = "camera"
_MEASUREMENT_SERVICES = "services"
_MEASUREMENT_INFERENCE = "inference"

# Métricas de hardware que van directo a fields. `net_mbps` y `temps_c` no están porque
# son dicts anidados y un field de Influx es escalar: la red se aplana y las zonas
# térmicas no se publican. Filtrar por tipo, en cambio, perdería la red sin avisar.
_SYSTEM_METRIC_KEYS = (
    "cpu_usage_pct", "gpu_usage_pct", "cpu_temp_c", "gpu_temp_c",
    "ram_used_mb", "ram_total_mb", "disk_free_gb", "power_w",
)

_LOG_FILENAME = "app.log"
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUP_COUNT = 3
_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(message)s"
_DEFAULT_LOG_LEVEL = "INFO"
_DEFAULT_LOGS_PATH = "./data/logs"

# Nombres de registro que ya publica el ciclo de salud: una métrica del analyzer que se
# llame igual no los pisa.
_HEALTH_REGISTER_NAMES = frozenset({
    "heartbeat", "system_status_bitfield", "com_status_bitfield",
    "cpu_usage_pct", "gpu_usage_pct", "ram_used_mb", "ram_total_mb",
    "disk_free_gb", "cpu_temp_c", "gpu_temp_c", "power_w",
})

# Todos los nombres del mapa cargado. `SCHEMA.encode_batch()` levanta KeyError con un
# nombre que no existe, así que las métricas se filtran contra esto antes de codificar.
_REGISTER_NAMES = frozenset(reg.name for reg in REGISTERS)

_COLLECTOR_MODE_INTERVAL = "interval"


# ── Log a archivo ────────────────────────────────────────────────────────────────

def _setup_logging(config: ConfigManager):
    """
    Fija el nivel y agrega el handler a archivo, con rotación.

    Es lo que `system/logger.py` no puede hacer solo: no lee el config a propósito, para
    seguir siendo un módulo sin dependencias. Si la carpeta de logs no se puede crear, se
    sigue con la salida a consola y se avisa: no arrancar por no poder loguear sería peor.
    """
    level_name = str(config.get("system.log_level", _DEFAULT_LOG_LEVEL)).upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))

    logs_path = paths.resolve(config.get("system.paths.logs"), _DEFAULT_LOGS_PATH)
    try:
        os.makedirs(logs_path, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(logs_path, _LOG_FILENAME),
            maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(handler)
    except OSError as error:
        logger.error(f"[Main] Sin log a archivo en {logs_path}: {error}")


class _LogBridge(QObject):
    """
    Puente de hilos entre el logger y la ventana.

    El handler corre en el hilo que loguea —cualquiera— y la ventana vive en el de la
    GUI. Tocar un widget desde otro hilo rompe Qt, así que el handler emite esta señal:
    con el receptor en el hilo de la GUI, Qt la entrega en cola y sola.
    """

    message = Signal(str, str, str)   # (nivel, mensaje, módulo) — una por línea de log


class _UiLogHandler(logging.Handler):
    """Handler que manda cada línea del logger a la ventana, por el puente."""

    def __init__(self, bridge: _LogBridge):
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord):
        self._bridge.message.emit(record.levelname, record.getMessage(), record.module)


# ── La interfaz, y su no-op ─────────────────────────────────────────────────────

class _HeadlessUi:
    """
    La interfaz cuando no hay ventana: misma API, no hace nada.

    Es el mismo recurso que `NullDriver` y `NullModel`: el llamador no pregunta si hay
    GUI, le habla igual. Sin esto, cada publicación del cableado necesitaría un `if` y el
    modo headless sería una segunda versión del archivo esperando divergir.
    """

    monitor_content = None

    def build_monitor_content(self, factory: Callable[[], object]):
        """No llama al factory: en headless el widget del fork no se construye."""

    def show(self):
        pass

    def attach_log_handler(self):
        pass

    def detach_log_handler(self):
        pass

    def connect_config_saved(self, slot: Callable[[], None]):
        pass

    def set_service_status(self, service: str, status: str):
        pass

    def set_client_count(self, service: str, count: int):
        pass

    def set_stream_urls(self, service: str, urls: list):
        pass

    def update_hardware_metrics(self, metrics: dict):
        pass

    def update_camera_status(self, status: dict, camera_slot: str):
        pass

    def update_modbus_values(self, registers: dict):
        pass


class _QtUi:
    """
    La ventana de verdad, detrás de la misma API que `_HeadlessUi`.

    Construye `MainWindow` y es la única que sabe que existe: el cableado no toca
    `diagnostics_view` ni `monitor_view` por su cuenta.
    """

    def __init__(self, config: ConfigManager):
        from ui.main_window import MainWindow

        self._window = MainWindow(config)
        self.monitor_content = None
        self._log_bridge: _LogBridge | None = None
        self._log_handler: _UiLogHandler | None = None

    def build_monitor_content(self, factory: Callable[[], object]):
        """Arma el widget del área central con el factory del fork y lo entra a la vista."""
        self.monitor_content = factory()
        self._window.monitor_view.set_content(self.monitor_content)

    def show(self):
        self._window.show()

    def attach_log_handler(self):
        """Manda cada línea del logger al log de la ventana, cruzando de hilo por señal."""
        self._log_bridge = _LogBridge()
        self._log_bridge.message.connect(self._window.log_event)
        self._log_handler = _UiLogHandler(self._log_bridge)
        logger.addHandler(self._log_handler)

    def detach_log_handler(self):
        if self._log_handler is not None:
            logger.removeHandler(self._log_handler)
            self._log_handler = None

    def connect_config_saved(self, slot: Callable[[], None]):
        self._window.config_saved.connect(slot)

    def set_service_status(self, service: str, status: str):
        self._window.set_service_status(service, status)

    def set_client_count(self, service: str, count: int):
        self._window.set_client_count(service, count)

    def set_stream_urls(self, service: str, urls: list):
        self._window.set_stream_urls(service, urls)

    def update_hardware_metrics(self, metrics: dict):
        self._window.diagnostics_view.update_hardware_metrics(metrics)

    def update_camera_status(self, status: dict, camera_slot: str):
        self._window.diagnostics_view.update_camera_status(status, camera_slot)

    def update_modbus_values(self, registers: dict):
        self._window.diagnostics_view.update_modbus_values(registers)


def create_ui(config: ConfigManager, *, headless: bool):
    """
    Devuelve la interfaz que corresponde: la ventana, o el no-op.

    El flag manda sobre `ui.enabled` del config: sirve para correr una vez sin pantalla
    —por SSH, en un contenedor— sin editar el archivo de la instalación.
    """
    if headless or not config.get("ui.enabled", True):
        logger.info("[Main] Modo headless: sin ventana.")
        return _HeadlessUi()
    return _QtUi(config)


# ── Monitor de hardware, fuera del hilo de la GUI ────────────────────────────────

class _MetricsPoller(QThread):
    """
    Pide las métricas de hardware en su propio hilo y las publica por señal.

    **No es un adorno: `get_metrics()` bloquea.** En Windows la primera llamada tarda ~2 s
    —la consulta WMI de las zonas térmicas y `nvidia-smi` arrancan un proceso cada una— y
    vuelve a tardar cada vez que se le vence el cacheo de temperatura. Llamarlo desde un
    QTimer del hilo de la GUI congela la ventana ese tiempo y retrasa la entrega de todas
    las señales en cola, así que los hilos de captura parecen caídos cuando lo único que
    pasó es que nadie procesó sus eventos.

    El módulo de monitoreo es sincrónico a propósito —así se testea sin Qt—: quien lo usa
    desde una GUI es el que pone el hilo.
    """

    metrics_ready = Signal(object)    # dict de get_metrics() — uno por intervalo

    def __init__(self, monitor: SystemMonitor, interval_s: float, parent=None):
        super().__init__(parent)
        self._monitor = monitor
        self._interval_s = interval_s

    def run(self):
        while not self.isInterruptionRequested():
            self.metrics_ready.emit(self._monitor.get_metrics())
            for _ in range(int(self._interval_s * 10)):
                if self.isInterruptionRequested():
                    return
                self.msleep(100)


# ── La aplicación ───────────────────────────────────────────────────────────────

class Application(QObject):
    """
    Dueño de todos los subsistemas y de las conexiones entre ellos.

    QObject y no una clase suelta a propósito: los slots que reciben señales de los hilos
    de captura y de inferencia tienen que ser de un QObject que viva en el hilo de la GUI,
    para que Qt los entregue en cola. Conectar una función suelta los ejecutaría en el
    hilo que emite, tocando widgets desde afuera del hilo de la GUI.
    """

    def __init__(self, config: ConfigManager, ui, parent=None):
        super().__init__(parent)
        self._config = config
        self._ui = ui
        self._heartbeat = 0
        self._last_metrics: dict = {}
        self._camera_status: dict[str, dict] = {}
        self._camera_illumination: dict[str, int] = {}
        self._metrics_by_camera: dict[str, dict] = {}
        # Resultados de cada par (cámara, pipeline) desde la última muestra. La clave es el
        # par y no la cámara: una cámara puede entrar a dos pipelines —dos modelos mirando
        # lo mismo con distinto propósito— y con la cámara sola los dos se mezclarían en un
        # promedio que no significa nada. Se toca sólo desde el hilo de la GUI —el slot del
        # resultado llega en cola y el tick es un QTimer—, así que no necesita lock.
        self._pending_results: dict[tuple[str, str], list] = {}
        self._unmapped_metrics: set = set()
        self._last_service_statuses: dict[str, str] = {}

        # ── Interfaz ─────────────────────────────────────────────────────────
        # En headless esto es el no-op: el factory del widget no se llama y no se
        # construye ni un widget.
        self._ui.build_monitor_content(self._build_monitor_content)
        self._ui.attach_log_handler()

        # ── Servidores de video ──────────────────────────────────────────────
        self._http_video = HttpVideoServer(config)
        self._rtsp_video = RtspVideoServer(config)

        # ── Modbus ───────────────────────────────────────────────────────────
        # `run()` bloquea hasta que `stop()` lo desarme, así que va en su propio hilo.
        self._modbus = SharedModbusServer(config)
        self._modbus_thread = threading.Thread(
            target=self._modbus.run, name="modbus", daemon=True
        )

        # ── Telemetría y dataset ─────────────────────────────────────────────
        self._telemetry = PersistenceThread(config)
        self._collector = ImageCollector(config)

        # ── Inferencia ───────────────────────────────────────────────────────
        # Un hilo por pipeline habilitado: las cámaras que comparten pipeline comparten
        # el hilo, así los pesos se cargan una vez y el acceso a la GPU queda
        # serializado por construcción.
        preprocessor = self._build_preprocessor()
        analyzer = self._build_analyzer()
        annotator = self._build_annotator()
        self._engines: list[InferenceThread] = []
        for pipeline_slot in (config.get("inference.pipelines", {}) or {}):
            if not config.get(f"inference.pipelines.{pipeline_slot}.enabled", True):
                logger.info(f"[Main] {pipeline_slot} deshabilitado: sin hilo de inferencia.")
                continue
            engine = InferenceThread(
                config, Pipeline(config, pipeline_slot),
                preprocessor=preprocessor,
                analyzer=analyzer,
                annotator=annotator,
                annotate_gate=self._is_annotated_watched,
            )
            engine.result_ready.connect(self._on_result_ready)
            self._engines.append(engine)

        # ── Captura ──────────────────────────────────────────────────────────
        self._capture_threads: list[CaptureThread] = []
        for camera_slot in (config.get("cameras", {}) or {}):
            thread = CaptureThread(config, camera_slot)
            thread.frame_ready.connect(self._on_frame_ready)
            thread.status_updated.connect(self._on_camera_status)
            self._forward_status_to_content(thread)
            self._capture_threads.append(thread)

        # ── Hardware y timers ────────────────────────────────────────────────
        self._monitor = SystemMonitor(config)
        self._metrics_poller = _MetricsPoller(self._monitor, _METRICS_INTERVAL_S)
        self._metrics_poller.metrics_ready.connect(self._on_metrics_ready)
        # El tag que llevan todas las series: distingue equipos que comparten bucket.
        self._telemetry_tags = {"device": str(config.get("system.device_id", "") or "")}
        self._timers = [
            self._start_timer(_REGISTERS_INTERVAL_MS, self._on_registers_tick),
            self._start_timer(_STATUS_INTERVAL_MS, self._on_status_tick),
            self._start_timer(_SAMPLE_INTERVAL_MS, self._on_telemetry_tick),
        ]
        self._warn_if_telemetry_margin_is_thin()

        self._ui.connect_config_saved(self._on_config_saved)

    # ── Lo que cambia en cada fork ───────────────────────────────────────────
    #
    # Tres decisiones del proyecto, juntas y marcadas para que un diff las muestre de
    # una: qué mira el operador, con qué parámetros se cuenta, y qué se dibuja además
    # del resultado. Todo lo demás de este archivo es cableado genérico.

    def _build_monitor_content(self) -> QWidget:
        """
        Widget del área central del monitor.

        Por defecto la grilla de cámaras, que muestra todas las de `cameras:`. Un
        proyecto con otra disposición —tres por pantalla, paneles de proceso al costado—
        arma su widget acá; lo único que tiene que respetar es la misma API por slot
        (`update_frame(frame, slot)` y `update_status(status, slot)`). Ver `docs/ui.md`.

        El import es local a propósito: en headless este método no se llama, así que el
        widget no se construye ni se carga su módulo.
        """
        from ui.widgets.camera_grid import CameraGrid

        return CameraGrid(self._config)

    def _build_analyzer(self):
        """
        Qué significan las detecciones. El del template no necesita `process:`.

        Cuando la cuenta necesita parámetros de la planta, esto pasa a ser una factory
        que los recibe ya leídos —`build_analyzer(scale_px_per_mm=...)`—, y así
        `metrics.py` se sigue testeando con tres números y sin archivo.
        """
        return compute_metrics

    def _build_annotator(self):
        """
        Annotators de la planta, compuestos con los valores de `process:`.

        Las factories de `annotations.py` no leen el config a propósito: reciben números.
        Leerlo acá es lo que garantiza que el annotator que dibuja el límite y el analyzer
        que mide contra ese límite usen el mismo valor, y que el operador y el PLC no
        puedan estar mirando cosas distintas.
        """
        max_load_y_px = self._config.get("process.max_load_y_px", {}) or {}
        if not max_load_y_px:
            return None
        return annotations.chain(annotations.max_load_line_annotator(max_load_y_px))

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    def start(self):
        """Arranca servidores e hilos y muestra la ventana."""
        self._http_video.start()
        self._rtsp_video.start()
        self._modbus_thread.start()
        self._telemetry.start()
        self._collector.start()
        self._metrics_poller.start()
        for engine in self._engines:
            engine.start()
        for thread in self._capture_threads:
            thread.start()

        self._ui.show()
        logger.info(
            f"[Main] v{APP_VERSION} — {len(self._capture_threads)} cámara(s), "
            f"{len(self._engines)} pipeline(s), config "
            f"{'de rescate' if self._config.is_using_fallback else 'leída'}."
        )
        if self._collector.is_active and self._collector.mode != _COLLECTOR_MODE_INTERVAL:
            logger.info(
                "[Main] El recolector está en modo on_demand: no guarda por su cuenta. "
                "Los guardados los pide quien orqueste el ciclo con save_now()."
            )

    def stop(self):
        """Baja timers, hilos y servidores en orden. Idempotente."""
        for timer in self._timers:
            timer.stop()
        self._ui.detach_log_handler()

        # Primero la captura: sin frames nuevos, la inferencia drena lo que tenga.
        qt_threads = (self._capture_threads + self._engines
                      + [self._metrics_poller, self._telemetry])
        for thread in qt_threads:
            thread.requestInterruption()
        for thread in qt_threads:
            if not thread.wait(_THREAD_STOP_TIMEOUT_MS):
                logger.warning(f"[Main] Un hilo no cerró en {_THREAD_STOP_TIMEOUT_MS} ms.")

        self._collector.stop()
        self._http_video.stop()
        self._rtsp_video.stop()
        self._modbus.stop()
        self._modbus_thread.join(timeout=_SERVER_STOP_TIMEOUT_S)
        if self._modbus_thread.is_alive():
            logger.warning("[Main] El hilo del Modbus no cerró a tiempo.")
        logger.info("[Main] Subsistemas detenidos.")

    # ── Slots de los hilos ───────────────────────────────────────────────────

    @Slot(object, str)
    def _on_frame_ready(self, frame_bgr: np.ndarray | None, camera_slot: str):
        """
        Un frame nuevo: a la UI, al stream crudo y a la inferencia.

        En modo crudo la UI lo muestra tal cual; en anotado espera el que devuelve el
        motor, que es el mismo array con el resultado dibujado. Todo lo que se conecte
        acá tiene que ser barato o descartar: cualquier demora frena la captura.
        """
        content = self._ui.monitor_content
        if content is not None and getattr(content, "mode", None) != MODE_ANNOTATED:
            content.update_frame(frame_bgr, camera_slot)
        self._http_video.push_raw(camera_slot, frame_bgr)
        self._rtsp_video.push_raw(camera_slot, frame_bgr)
        for engine in self._engines:
            engine.push_frame(frame_bgr, camera_slot)

    @Slot(object, str)
    def _on_camera_status(self, status: dict, camera_slot: str):
        """Tres consumidores del mismo status: los chips de la cámara, el FPS del
        diagnóstico y la palabra de estado que va al PLC."""
        self._camera_status[camera_slot] = dict(status)
        self._ui.update_camera_status(status, camera_slot)

    @Slot(object)
    def _on_result_ready(self, result: InferenceResult):
        """
        Un resultado del motor: a la UI, a los streams, al dataset y a la telemetría.

        Los registros no se escriben acá: los publica el ciclo de `_on_registers_tick`,
        que junta las métricas con la salud y codifica una sola vez.
        """
        self._camera_illumination[result.camera_slot] = result.illumination_pct
        content = self._ui.monitor_content
        if content is not None:
            setter = getattr(content, "set_illumination_pct", None)
            if setter is not None:
                setter(result.camera_slot, result.illumination_pct)

        if result.annotated_bgr is not None:
            if content is not None and getattr(content, "mode", None) == MODE_ANNOTATED:
                content.update_frame(result.annotated_bgr, result.camera_slot)
            self._http_video.push_annotated(result.camera_slot, result.annotated_bgr)
            self._rtsp_video.push_annotated(result.camera_slot, result.annotated_bgr)

        # Dataset: `push_frame` encola y vuelve; escribe el hilo del recolector.
        if self._collector.mode == _COLLECTOR_MODE_INTERVAL:
            self._collector.push_frame(
                result.camera_slot, result.source_bgr,
                annotated_bgr=result.annotated_bgr, inference=result.to_dict(),
            )

        # Telemetría: al buzón, no a la cola. Lo publica el tick, agregado, porque una
        # serie por resultado escala con los fps y no agrega información. Los no
        # confiables entran igual: cuántos se cayeron y por qué es justo lo que se quiere
        # poder preguntar después.
        self._pending_results.setdefault(
            (result.camera_slot, result.pipeline_slot), []
        ).append(result)
        self._store_metrics(result)

    def _is_annotated_watched(self, camera_slot: str) -> bool:
        """
        Gate del anotado: se dibuja si lo mira la UI o un cliente de un stream anotado.

        Es la firma que espera el motor —`(camera_slot) -> bool`— y la razón por la que el
        overlay no se gasta cuando nadie lo está viendo.
        """
        content = self._ui.monitor_content
        if content is not None and getattr(content, "mode", None) == MODE_ANNOTATED:
            return True
        return (self._http_video.has_annotated_clients(camera_slot)
                or self._rtsp_video.has_annotated_clients(camera_slot))

    # ── Timers ───────────────────────────────────────────────────────────────

    @Slot(object)
    def _on_metrics_ready(self, metrics: dict):
        """Slot de `_MetricsPoller`: llega ya medido, desde el hilo del poller."""
        self._last_metrics = metrics
        self._ui.update_hardware_metrics(metrics)

    def _on_registers_tick(self):
        """
        Escribe el datastore del Modbus y refresca la tabla de la UI.

        Ninguna dirección se escribe acá: los valores van por nombre y las escalas las
        aplica `SCHEMA.encode_batch()`. Los bitfields los arma su propio módulo.
        """
        self._heartbeat = (self._heartbeat + 1) % _HEARTBEAT_MAX
        statuses = self._get_service_statuses()

        values = {
            "heartbeat": self._heartbeat,
            "system_status_bitfield": system_status.pack(
                model_loaded=all(e.get_status()["loaded"] for e in self._engines),
                inference_error=any(not e.get_status()["running"] for e in self._engines),
                fallback_config=self._config.is_using_fallback,
                dead_thread=any(not t.isRunning() for t in self._capture_threads),
            ),
            # Los seis canales, cada uno con el estado que informó su dueño: es la misma
            # fuente que alimenta los chips, así que el operador y el PLC no pueden estar
            # viendo cosas distintas.
            "com_status_bitfield": com_status.pack(
                rtsp_status=statuses[service_status.SERVICE_VIDEO_RTSP],
                http_video_status=statuses[service_status.SERVICE_VIDEO_HTTP],
                influxdb_status=statuses[service_status.SERVICE_INFLUXDB],
                mqtt_status=statuses[service_status.SERVICE_MQTT],
                modbus_tcp_status=statuses[service_status.SERVICE_MODBUS_TCP],
                modbus_rtu_status=statuses[service_status.SERVICE_MODBUS_RTU],
            ),
        }

        # Las métricas de hardware pueden tardar unos segundos en aparecer: la primera
        # lectura del monitor bloquea. Mientras no estén, el latido y las palabras de
        # estado salen igual — un PLC que vigila el heartbeat no puede quedarse esperando
        # al primer `nvidia-smi`.
        metrics = self._last_metrics
        if metrics:
            values.update({
                "cpu_usage_pct": metrics["cpu_usage_pct"],
                "gpu_usage_pct": metrics["gpu_usage_pct"],
                "ram_used_mb": metrics["ram_used_mb"],
                "ram_total_mb": metrics["ram_total_mb"],
                "disk_free_gb": metrics["disk_free_gb"],
                "cpu_temp_c": metrics["cpu_temp_c"],
                "gpu_temp_c": metrics["gpu_temp_c"],
                "power_w": metrics["power_w"],
            })
        values.update(self._build_camera_registers())
        values.update(self._build_metric_registers())

        # Un solo `encode_batch` para los dos consumidores —el datastore que lee el PLC y
        # la tabla que mira el operador—: codificar dos veces los deja divergir.
        registers = SCHEMA.encode_batch(values)
        self._modbus.update_block(registers)
        self._ui.update_modbus_values(registers)

    def _on_status_tick(self):
        """
        Relee el `status` de cada subsistema y lo publica en la UI.

        Los subsistemas no emiten señal cuando cambian de estado: exponen una property
        que se consulta. Por eso esto es un timer y no un slot, y por eso está todo en un
        solo lugar en vez de repartido.
        """
        statuses = self._get_service_statuses()
        for service, status in statuses.items():
            self._ui.set_service_status(service, status)
            # Sin ventana el log es la única forma de ver que un canal se cayó, así que el
            # cambio se loguea en los dos modos: en el de ventana también queda en la
            # pestaña de logs, que es donde alguien lo va a buscar después.
            if self._last_service_statuses.get(service) != status:
                self._last_service_statuses[service] = status
                logger.info(f"[Main] {service}: {status}")

        for service, server in ((service_status.SERVICE_VIDEO_HTTP, self._http_video),
                                (service_status.SERVICE_VIDEO_RTSP, self._rtsp_video)):
            self._ui.set_client_count(service, server.get_client_count())
        self._ui.set_stream_urls(
            service_status.SERVICE_VIDEO_HTTP, self._build_http_urls()
        )
        self._ui.set_stream_urls(
            service_status.SERVICE_VIDEO_RTSP, self._rtsp_video.get_stream_urls()
        )

    def _on_telemetry_tick(self):
        """
        Publica las series de salud: hardware, estado por cámara y canales de salida.

        **Por tick y no por evento.** La cola de telemetría aguanta unos 10 puntos por
        segundo y pasado eso descarta el que llega; una serie de salud por evento no
        agregaría información y le comería el presupuesto a la inferencia. Un hueco en la
        serie también informa: dice que el equipo no estuvo publicando.
        """
        if self._last_metrics:
            self._telemetry.push_data(
                _MEASUREMENT_SYSTEM,
                _build_system_fields(self._last_metrics),
                tags=self._telemetry_tags,
            )

        for camera_slot in (self._config.get("cameras", {}) or {}):
            status = self._camera_status.get(camera_slot)
            if status is None:
                continue
            self._telemetry.push_data(
                _MEASUREMENT_CAMERA,
                {
                    "connected": int(bool(status.get("connected"))),
                    "capture_enabled": int(bool(status.get("capture_enabled", True))),
                    "misconfigured": int(bool(status.get("error"))),
                    "fps_estimated": float(status.get("fps_estimated", 0.0)),
                    "temperature_c": float(status.get("temperature", 0.0)),
                    "illumination_pct": int(self._camera_illumination.get(camera_slot, 0)),
                },
                tags={**self._telemetry_tags, "camera": camera_slot},
            )

        # Un campo por canal, con el mismo criterio que el bit que lee el PLC: 1 es
        # «levantó y está andando», y lo decide `com_status`, no este archivo.
        self._telemetry.push_data(
            _MEASUREMENT_SERVICES,
            {service: int(com_status.is_active(status))
             for service, status in self._get_service_statuses().items()},
            tags=self._telemetry_tags,
        )

        self._publish_inference()

    def _publish_inference(self):
        """
        Un punto por par (cámara, pipeline) con lo que llegó desde la muestra anterior.

        **Se agrega, no se muestrea.** Con inferencia de 200 ms y una muestra por segundo
        entran cinco resultados: promediarlos usa los cinco y el desvío dice si el proceso
        estuvo estable en ese segundo, mientras que quedarse con el último tiraría cuatro.
        `sample_count` deja ver cuántos entraron, así que la ventana es visible en el dato.

        El buzón se vacía en el mismo paso: nada se publica dos veces, y un par que no
        produjo nada no genera punto —el hueco en la serie dice que no midió—.
        """
        for (camera_slot, pipeline_slot), results in self._pending_results.items():
            if not results:
                continue
            self._telemetry.push_data(
                _MEASUREMENT_INFERENCE,
                _build_inference_fields(results),
                tags={**self._telemetry_tags,
                      "camera": camera_slot, "pipeline": pipeline_slot},
                # El instante del último resultado, no el del tick: el punto vale por
                # cuándo se midió y entre las dos cosas hay hasta una muestra entera.
                time_s=results[-1].timestamp_s,
            )
        self._pending_results.clear()

    def _warn_if_telemetry_margin_is_thin(self):
        """
        Avisa al arrancar si la telemetría queda sin margen ante un InfluxDB trabado.

        No es que la cola no dé abasto en régimen: la drena un hilo y aguanta muchísimo
        más. Lo que se achica al publicar más puntos es el colchón: la cola guarda 100, así
        que a `N` puntos/s tolera `100/N` segundos de escritura trabada antes de descartar.
        Por debajo de una ventana de publicación de margen, una sola escritura lenta ya
        pierde datos, y eso conviene saberlo al arrancar y no por un hueco en el dashboard.

        Con el muestreo fijo en un segundo, lo único que mueve la cuenta es cuántas cámaras
        y cuántos pares (cámara, pipeline) hay, así que el remedio no es un parámetro sino
        agrandar la cola.
        """
        camera_count = len(self._config.get("cameras", {}) or {})
        pair_count = sum(len(e.camera_slots) or camera_count for e in self._engines)
        points_per_s = (2 + camera_count + pair_count) / _SAMPLE_INTERVAL_S
        if points_per_s <= _TELEMETRY_STALL_SAFE_POINTS_PER_S:
            return
        tolerance_s = _TELEMETRY_QUEUE_POINTS / points_per_s
        logger.warning(
            f"[Main] La telemetría publica ~{points_per_s:.1f} puntos/s "
            f"({camera_count} cámara(s), {pair_count} par(es) cámara-pipeline): con la cola "
            f"en {_TELEMETRY_QUEUE_POINTS:.0f} puntos tolera {tolerance_s:.1f} s de InfluxDB "
            f"trabado antes de descartar. Agrandar `_QUEUE_MAXSIZE` en persistence.py da más "
            f"margen. Ver docs/influxdb.md."
        )

    def _on_config_saved(self):
        """
        Se guardó el config desde la pantalla.

        La UI ya aplicó lo suyo —idioma y tema—. El resto lo lee cada subsistema cuando lo
        usa, y lo que se fija al arrancar necesita reinicio: el propio panel lo avisa.
        """
        logger.info("[Main] config.yaml guardado desde la interfaz.")

    # ── Traducción al esquema de registros ───────────────────────────────────

    def _build_camera_registers(self) -> dict:
        """
        Estado por cámara, para los slots que tengan fila en el mapa.

        Los nombres se arman con la clave del slot —`camera_1_fps`— y se filtran contra el
        mapa cargado: una instalación que agrega `camera_2` a `register_map.yaml` la
        publica sin tocar este archivo.
        """
        values = {}
        for camera_slot in (self._config.get("cameras", {}) or {}):
            status = self._camera_status.get(camera_slot)
            candidates = {
                f"{camera_slot}_illumination_pct":
                    self._camera_illumination.get(camera_slot, 0),
            }
            if status:
                candidates[f"{camera_slot}_state_bitfield"] = camera_health.pack(
                    connected=bool(status.get("connected")),
                    config_error=status.get("error"),
                    fps_estimated=float(status.get("fps_estimated", 0.0)),
                    capture_enabled=bool(status.get("capture_enabled", True)),
                    is_synthetic=self._is_synthetic(camera_slot),
                )
                candidates[f"{camera_slot}_temperature_c"] = status.get("temperature", 0.0)
                candidates[f"{camera_slot}_fps"] = status.get("fps_estimated", 0.0)
            values.update({name: value for name, value in candidates.items()
                           if name in _REGISTER_NAMES})
        return values

    def _build_metric_registers(self) -> dict:
        """
        Métricas del analyzer que tienen una fila con su mismo nombre en el mapa.

        Es el contrato entre `metrics.py` y `register_map.yaml`: la clave de la métrica es
        el nombre del registro. Una métrica sin fila no se publica y se avisa una vez —no
        por ciclo—, porque lo más probable es que sea un nombre mal escrito de un lado o
        del otro y verlo una vez alcanza para corregirlo.

        **Ojo con varias cámaras publicando la misma métrica**: hay un registro por
        nombre, así que la última cámara del recorrido gana y el PLC ve un valor que
        cambia de origen sin avisar. Un proyecto que mide lo mismo en dos cintas prefija
        el nombre con el slot —`camera_1_coverage_pct`— y declara una fila por cámara,
        igual que hacen los registros de estado.
        """
        values = {}
        for camera_slot, metrics in self._metrics_by_camera.items():
            for name, value in metrics.items():
                if name in _HEALTH_REGISTER_NAMES:
                    continue
                if name in _REGISTER_NAMES:
                    values[name] = value
                elif name not in self._unmapped_metrics:
                    self._unmapped_metrics.add(name)
                    logger.info(
                        f"[Main] La métrica '{name}' de {camera_slot} no tiene fila en "
                        f"register_map.yaml: no se publica al PLC."
                    )
        return values

    # ── Internos ─────────────────────────────────────────────────────────────

    def _build_preprocessor(self):
        """
        Corrector de lente, si alguna cámara declara calibración.

        Lo que devuelve pasa a ser el frame de referencia del ciclo: el que ve el modelo,
        el que queda en `source_bgr`, el espacio del ROI y el lienzo del overlay. Sin
        calibración declarada devuelve None y el motor no gasta la pasada.
        """
        calibration_by_camera = {
            camera_slot: self._config.get(f"cameras.{camera_slot}.calibration", {}) or {}
            for camera_slot in (self._config.get("cameras", {}) or {})
        }
        undistorter = build_undistorter(calibration_by_camera)
        if undistorter is not None:
            logger.info("[Main] Corrección de lente activa: el frame de referencia va corregido.")
        return undistorter

    def _get_service_statuses(self) -> dict:
        """Estado de los seis canales de salida, cada uno preguntado a su dueño."""
        return {
            service_status.SERVICE_MODBUS_TCP: self._modbus.tcp_status,
            service_status.SERVICE_MODBUS_RTU: self._modbus.rtu_status,
            service_status.SERVICE_VIDEO_HTTP: self._http_video.status,
            service_status.SERVICE_VIDEO_RTSP: self._rtsp_video.status,
            service_status.SERVICE_INFLUXDB: self._telemetry.backend_status("influxdb"),
            service_status.SERVICE_MQTT: self._telemetry.backend_status("mqtt"),
        }

    def _build_http_urls(self) -> list[str]:
        """
        URLs del servidor HTTP, armadas acá y no en la UI.

        La forma de la ruta es del servidor de video: si la compusiera la vista, el
        formato quedaría definido en dos lugares. El RTSP ya las publica con
        `get_stream_urls()`; el HTTP todavía no, así que las arma quien cablea.
        """
        if self._http_video.status != STATUS_ACTIVE:
            return []
        port = int(self._config.get("video.http.port", 8091))
        return [
            f"http://127.0.0.1:{port}/{camera_slot}/{mode}"
            for camera_slot in (self._config.get("cameras", {}) or {})
            for mode in MODES
        ]

    def _is_synthetic(self, camera_slot: str) -> bool:
        for thread in self._capture_threads:
            if thread.camera_slot == camera_slot:
                return thread.is_synthetic
        return False

    def _forward_status_to_content(self, thread: CaptureThread):
        """Conecta el status al widget del monitor, si el que puso el fork lo acepta."""
        content = self._ui.monitor_content
        if content is not None and hasattr(content, "update_status"):
            thread.status_updated.connect(content.update_status)

    def _store_metrics(self, result: InferenceResult):
        """Guarda las métricas del último resultado de cada cámara, para los registros."""
        if result.is_valid and result.metrics:
            self._metrics_by_camera[result.camera_slot] = dict(result.metrics)

    def _start_timer(self, interval_ms: int, slot) -> QTimer:
        timer = QTimer(self)
        timer.timeout.connect(slot)
        timer.start(interval_ms)
        return timer


def _parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Captura, inferencia y publicación del resultado.")
    parser.add_argument("config", nargs="?", default=None,
                        help="ruta del config.yaml; por defecto el de la raíz del repo")
    parser.add_argument("--headless", action="store_true",
                        help="no abrir la ventana; también se puede con ui.enabled: false")
    return parser.parse_args(argv)


def _build_inference_fields(results: list) -> dict:
    """
    Campos de un punto de inferencia a partir de los resultados de una muestra.

    Tres grupos, y cada uno se agrega sobre lo que corresponde:

      - **Cuántos y por qué**: `result_count`, `invalid_count` y el último
        `invalid_reason`. Sin esto no hay forma de preguntar cuántas mediciones se
        cayeron; el motivo es el del último descarte, y `invalid_count` da la magnitud.
      - **Escalares del resultado**, que no están en `metrics` y se promedian acá. La
        confianza y el conteo de detecciones sólo sobre los confiables —promediar la
        confianza de un descarte da un número que no significa nada—; el tiempo y la
        iluminación sobre todos, porque valen igual y la iluminación baja es justamente
        uno de los motivos de descarte.
      - **Las métricas del proyecto**, con media, desvío y rango, vía
        `analysis.summarize_metrics()`, que ya devuelve el dict plano que los fields
        necesitan y agrega `sample_count`.
    """
    valid = [r for r in results if r.is_valid]
    last_invalid = next((r for r in reversed(results) if not r.is_valid), None)

    fields: dict = {
        "result_count": len(results),
        "invalid_count": len(results) - len(valid),
        "invalid_reason": last_invalid.invalid_reason if last_invalid is not None else "",
    }
    for key in ("confidence_pct", "detection_count"):
        fields.update(_mean_field(key, [getattr(r, key) for r in valid]))
    for key in ("inference_time_ms", "illumination_pct"):
        fields.update(_mean_field(key, [getattr(r, key) for r in results]))

    # Un pico de latencia es lo que se quiere ver, y el promedio lo esconde.
    times_ms = [r.inference_time_ms for r in results]
    if times_ms:
        fields["inference_time_ms_max"] = round(max(times_ms), 2)

    # Tiempo por etapa: la clave es el slot del modelo, así que un pipeline de dos etapas
    # publica dos campos y se ve cuál se llevó el tiempo.
    stage_samples: dict[str, list] = {}
    for result in results:
        for model_slot, elapsed_ms in (result.stage_times_ms or {}).items():
            stage_samples.setdefault(str(model_slot), []).append(float(elapsed_ms))
    for model_slot, samples in stage_samples.items():
        fields.update(_mean_field(f"stage_{model_slot}_ms", samples))

    fields.update(analysis.summarize_metrics(results))
    return fields


def _mean_field(name: str, samples: list) -> dict:
    """`{name}_mean` sobre las muestras, o nada si no hubo ninguna."""
    if not samples:
        return {}
    return {f"{name}_mean": round(statistics.fmean(float(s) for s in samples), 2)}


def _build_system_fields(metrics: dict) -> dict:
    """
    Métricas de hardware como fields de Influx: escalares, con la red aplanada.

    `net_mbps` llega como `{interfaz: {rx_mbps, tx_mbps}}` y un field de Influx es
    escalar, así que cada interfaz se abre en dos campos con su nombre adentro. Filtrar
    los valores no escalares —que es lo obvio— dejaría el tráfico de red afuera sin que
    nadie se enterara.

    `temps_c` no se publica: es el detalle por zona térmica que ya resumen `cpu_temp_c` y
    `gpu_temp_c`, y sus nombres cambian entre equipos, así que no sirve para un dashboard
    que tenga que andar en los dos.
    """
    fields = {key: metrics[key] for key in _SYSTEM_METRIC_KEYS if key in metrics}
    for iface, throughput in (metrics.get("net_mbps") or {}).items():
        safe_iface = str(iface).replace(" ", "_")
        fields[f"net_{safe_iface}_rx_mbps"] = float(throughput.get("rx_mbps", 0.0))
        fields[f"net_{safe_iface}_tx_mbps"] = float(throughput.get("tx_mbps", 0.0))
    return fields


def main(argv: list | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    config = ConfigManager(args.config)
    _setup_logging(config)

    headless = args.headless or not config.get("ui.enabled", True)
    # `QCoreApplication` en headless: `QApplication` necesita una plataforma gráfica y en
    # un equipo sin display no arranca. El event loop es el mismo, y es el que hace falta
    # igual, porque las señales son el cableado entre los hilos.
    app = QCoreApplication(sys.argv) if headless else QApplication(sys.argv)

    application = Application(config, create_ui(config, headless=headless))
    app.aboutToQuit.connect(application.stop)
    application.start()

    # Ctrl+C y el SIGTERM de un servicio, con el event loop de Qt corriendo: sin esto la
    # señal queda esperando a que Qt devuelva el control, que no pasa. El timer al vacío
    # es lo que le da al intérprete la chance de atenderla.
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, lambda *_: app.quit())
    interrupt_timer = QTimer()
    interrupt_timer.timeout.connect(lambda: None)
    interrupt_timer.start(_SIGNAL_POLL_INTERVAL_MS)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
