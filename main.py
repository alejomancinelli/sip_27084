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

Los seis puntos de extensión del motor, y de dónde sale cada uno:

    preprocessor    `tools/image/undistort.py`, si alguna cámara declara calibración
    pipeline        `system/inference/pipeline.py`, que el fork reescribe
    classifier      qué detecciones cuentan y con qué clase, compuesto abajo con `process:`
    analyzer        `system/inference/metrics.py`, que el fork reescribe
    annotator       `system/inference/annotations.py`, compuesto abajo con `process:`
    annotate_gate   acá: se dibuja si lo mira la UI o un cliente del stream anotado

**Qué toca el fork.** Los cuatro métodos del bloque «Lo que cambia en cada fork»: qué mira
el operador, qué detecciones cuentan, qué se dibuja además del resultado, y con qué
parámetros se cuenta. El resto es cableado genérico y se cross-portea sin editar.

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
import time
from collections.abc import Callable
from datetime import datetime, timezone

import numpy as np
from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QWidget

from system import paths
from system.camera.capture_scheduler import CaptureScheduler
from system.camera.capture_thread import CaptureThread
from system.camera import lens_health
from system.camera.lens_health import LensHealthMonitor
from system.config_manager import ConfigManager
from system.env import load_env_file
from system.formats import camera_health, com_status, gpio_status, system_status
from system.gpio_control import GpioController, GpioPollingThread
from system.image_collector.collector import ImageCollector
from system.inference import analysis, annotations, metrics, rolling
from system.inference.engine import InferenceThread
from system.inference.pipeline import BeltPipeline
from system.inference.result import InferenceResult
from system.license.manager import (
    PERPETUAL_DAYS_SENTINEL, RECHECK_INTERVAL_MS, LicenseManager, read_feature,
)
from system.license.request import WeakFingerprintError, save_request
from system.logger import logger
from system.modbus.registers import LOAD_ERROR as REGISTER_MAP_ERROR, REGISTERS, SCHEMA
from system.modbus.server import SharedModbusServer
from system.system_monitor import SystemMonitor
from system.telemetry.persistence import PersistenceThread
from system.version import APP_VERSION
from system.video.abstract_video_server import (
    MODE_ANNOTATED, MODES, STATUS_ACTIVE,
)
from system.video.http_server import HttpVideoServer
from system.video.rtsp_server import RtspVideoServer
from tools.image.enhance import build_display_adjust
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

# Métricas que además de publicarse al instante llevan su media de la hora. La clave es la
# del analyzer y el registro de la media se llama igual con `_1h` al final, que es como ya
# lo lee el PLC.
# El slot del modelo de este proyecto: de su sección salen los nombres de clase, que
# son los que deciden cómo se llaman las métricas y qué registro las transporta.
_SEGMENTER_SLOT = "segmenter"

_ROLLING_METRIC_NAMES = ("pct_pellet_norm", "pct_desmenuzado_norm", "pct_carga")
_ROLLING_SUFFIX = "_1h"
# Cuál de las tres es la carga —promedia todas las mediciones, así que su ventana dice
# cuánto se midió— y cuál representa a la composición, que sólo promedia con material.
_ROLLING_LOAD_NAME = "pct_carga"
# Sufijo de las métricas normalizadas: son las que sólo promedian con material en la cinta.
_NORMALIZED_SUFFIX = "_norm"
_ROLLING_COMPOSITION_NAME = "pct_pellet_norm"

# Las dos claves que dicen si se puede confiar en esas medias. Sin ellas, una media de tres
# muestras sobre una hora se lee igual que una hora entera bien medida.
_WINDOW_COVERAGE_KEY = "window_coverage_pct"
_MATERIAL_PRESENT_KEY = "material_present_pct"
_LENS_DEFAULT_INTERVAL_S = 10.0   # cada cuánto se mide la nitidez, si el config no lo dice
_LENS_MIN_INTERVAL_S = 1.0        # medir más seguido que esto no llena antes una ventana de horas
_SIGNAL_POLL_INTERVAL_MS = 200     # timer al vacío para que Qt atienda Ctrl+C y SIGTERM

# Status de una cámara declarada que la licencia deja afuera. Las claves son las estables
# de `AbstractCameraDriver.get_status()`: para el resto del sistema es una cámara que no
# puede operar, igual que una mal configurada, y el motivo viaja en `error` para que la
# pantalla diga por qué en vez de mostrar un hueco.
_UNLICENSED_CAMERA_STATUS = {
    "connected": False,
    "capture_enabled": False,
    "temperature": 0.0,
    "fps_estimated": 0.0,
    "error": "Sobre el cupo de la licencia",
}

# El muestreo de la telemetría no es configuración: un segundo es la resolución con la
# que se miran estas series y no hay instalación que quiera otra. Más fino no agrega
# información —el hardware no cambia más rápido— y más grueso pierde el detalle de un
# pico. Lo que sí varía entre instalaciones es cuántas cámaras hay, y eso ya entra en la
# cuenta del margen.
_SAMPLES_PER_S = 1.0    # una muestra por tick de telemetría
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

# ── Telemetría: la estructura de las series NO es la del template ────────────────
#
# Este equipo ya estaba en servicio con su dashboard cuando se migró al template, así que
# los measurements, los tags y los nombres de campo se conservaron tal como estaban —en
# castellano varios de ellos— en vez de renombrar las series y rehacer los paneles. Una
# serie renombrada no se migra: la historia queda con el nombre viejo y el panel deja de
# encontrarla.
#
# La estructura canónica, que es la que usan los proyectos nuevos, está en
# `docs/influxdb_template.md`. Lo que publica este equipo, en `docs/influxdb.md`.
#
# Todo lo que diverge está en este bloque y en `_build_inference_fields()` /
# `_build_system_fields()`, junto y marcado, para que un cross-port lo vea de una.
_MEASUREMENT_SYSTEM = "sistema"
_MEASUREMENT_CAMERA = "camara"
_MEASUREMENT_SERVICES = "servicios"
_MEASUREMENT_INFERENCE = "inferencia"
_MEASUREMENT_OPTICS = "optica"      # el template lo publica como campos de `camara`

# Centinela de `inference_age_s` mientras nunca hubo una inferencia. Es el máximo de un
# uint16: un 0 se leería como «recién medido», que es justo lo contrario.
_NO_INFERENCE_AGE_S = 65535

# Métrica de hardware -> el nombre con el que ya está en el bucket. Los de la izquierda
# son los de `SystemMonitor.get_metrics()`; los de la derecha, los que la versión anterior
# del equipo publicó y que el dashboard consulta.
#
# `net_mbps` y `temps_c` no están porque son dicts anidados y un field de Influx es
# escalar: la red se aplana más abajo y las zonas térmicas no se publican. Filtrar por
# tipo, en cambio, perdería la red sin avisar.
_LEGACY_SYSTEM_FIELDS = {
    "cpu_usage_pct": "cpu_usage",
    "gpu_usage_pct": "gpu_usage",
    "ram_used_mb": "ram_mb",
    "disk_free_gb": "disk_gb",
    "cpu_temp_c": "temp_cpu",
    "gpu_temp_c": "temp_gpu",
    "power_w": "power_w",
    # El único que la serie vieja no tenía. Es un campo nuevo, así que no choca con nada.
    "ram_total_mb": "ram_total_mb",
}

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

    def update_license(self, status: dict):
        pass

    def show_license_result(self, is_ok: bool, message: str):
        pass

    def connect_license_requests(self, export_slot: Callable[[str], None],
                                 install_slot: Callable[[str], None]):
        """En headless nadie aprieta un botón: el mismo flujo va por `python -m system.license`."""

    def connect_lens_calibration(self, calibrate_slot: Callable[[str], None]):
        """Sin pantalla no hay quién limpie el vidrio y apriete calibrar: se hereda la
        referencia que ya tenga el config."""

    def refresh_lens_reference(self, camera_slot: str):
        pass

    def set_gpio(self, gpio_controller: object, gpio_thread: object = None):
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
        # Maximizada y no `show()`: es una pantalla de planta y siempre se la termina
        # maximizando a mano. El `resize()` de la ventana sigue valiendo — es el tamaño al
        # que vuelve cuando el operador la restaura.
        self._window.showMaximized()

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

    def update_license(self, status: dict):
        self._window.update_license(status)

    def show_license_result(self, is_ok: bool, message: str):
        self._window.show_license_result(is_ok, message)

    def connect_license_requests(self, export_slot: Callable[[str], None],
                                 install_slot: Callable[[str], None]):
        self._window.license_export_requested.connect(export_slot)
        self._window.license_install_requested.connect(install_slot)

    def connect_lens_calibration(self, calibrate_slot: Callable[[str], None]):
        self._window.lens_calibration_requested.connect(calibrate_slot)

    def refresh_lens_reference(self, camera_slot: str):
        self._window.refresh_lens_reference(camera_slot)

    def set_gpio(self, gpio_controller: object, gpio_thread: object = None):
        """Habilita el botón de GPIO del header y le pasa con qué alimentarlo."""
        self._window.set_gpio(gpio_controller, gpio_thread)


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

    def __init__(self, config: ConfigManager, ui, license_manager: LicenseManager,
                 parent=None):
        super().__init__(parent)
        self._config = config
        self._ui = ui
        # Ya construido y evaluado por `main()`, que lo necesitó antes de Qt para decidir
        # si el equipo arranca. Se recibe hecho en vez de construirlo de nuevo: leer la
        # huella cuesta un proceso de WMI y el veredicto tiene que ser uno solo.
        self._license = license_manager
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
        # Último frame anotado de cada cámara. Se guarda para poder volver a mostrarlo
        # cuando el operador pasa a crudo y vuelve: el siguiente sale recién con la
        # medición siguiente, que puede estar a varios minutos.
        self._last_annotated: dict[str, np.ndarray] = {}
        # Ajuste de visualización: se arma una vez y se rehace al guardar el config.
        self._display_adjust = self._read_display_adjust()
        self._pending_results: dict[tuple[str, str], list] = {}
        self._unmapped_metrics: set = set()
        self._last_service_statuses: dict[str, str] = {}
        # Cuándo llegó la última medición, para publicar su antigüedad: un dashboard que
        # muestra el último valor no distingue «estable» de «dejó de medir hace una hora».
        self._last_inference_s: float | None = None

        # ── Interfaz ─────────────────────────────────────────────────────────
        # En headless esto es el no-op: el factory del widget no se llama y no se
        # construye ni un widget.
        self._ui.build_monitor_content(self._build_monitor_content)
        self._connect_content_mode()
        self._ui.attach_log_handler()

        # ── Servidores de video ──────────────────────────────────────────────
        self._http_video = HttpVideoServer(config)
        self._rtsp_video = RtspVideoServer(config)

        # ── Modbus ───────────────────────────────────────────────────────────
        # `run()` bloquea hasta que `stop()` lo desarme, así que va en su propio hilo.
        self._modbus = _build_modbus_server(config)
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
        classifier = self._build_classifier()
        analyzer = self._build_analyzer()
        annotator = self._build_annotator()
        self._engines: list[InferenceThread] = []
        for pipeline_slot in (config.get("inference.pipelines", {}) or {}):
            if not config.get(f"inference.pipelines.{pipeline_slot}.enabled", True):
                logger.info(f"[Main] {pipeline_slot} deshabilitado: sin hilo de inferencia.")
                continue
            # Un feature no licenciado y un `enabled: false` se ven igual desde afuera
            # —el pipeline no corre— y son cosas distintas: uno se arregla comprando y el
            # otro editando el config. Por eso son dos mensajes y no uno.
            feature = read_feature(config.get(f"inference.pipelines.{pipeline_slot}.feature"))
            if not self._license.has_feature(feature):
                logger.warning(
                    f"[Main] {pipeline_slot} no arranca: la licencia no habilita "
                    f"'{feature}'. Sin hilo de inferencia para ese pipeline."
                )
                continue
            engine = InferenceThread(
                config, BeltPipeline(config, pipeline_slot),
                preprocessor=preprocessor,
                classifier=classifier,
                analyzer=analyzer,
                annotator=annotator,
                annotate_gate=self._is_annotated_watched,
                # El anotado se ve como el stream crudo: los dos los mira una persona.
                canvas_adjust=self._for_display,
            )
            engine.result_ready.connect(self._on_result_ready)
            self._engines.append(engine)

        # ── Ciclo de medición ────────────────────────────────────────────────
        # El motor emite un resultado por frame; el scheduler junta los N que forman una
        # medición. Con `frames_per_cycle: 1` es un pass-through y cada resultado sale tal
        # cual, así que el camino es uno solo y no hay que preguntar si hay ciclo.
        self._scheduler = CaptureScheduler(
            config, tuple(engine.pipeline_slot for engine in self._engines))
        for engine in self._engines:
            engine.result_ready.connect(self._scheduler.on_result_ready)
        self._scheduler.cycle_complete.connect(self._on_cycle_complete)
        self._scheduler.capture_wanted.connect(self._on_capture_wanted)

        # ── Captura ──────────────────────────────────────────────────────────
        # El cupo de la licencia corta por orden de declaración, y el corte es
        # determinista: que la cámara que se apaga cambie entre arranques sería peor que
        # el límite mismo. La que queda afuera no desaparece de la pantalla —se publica su
        # estado con el motivo, como cualquier cámara mal configurada—, porque un hueco
        # manda al operador a revisar un cable que está bien.
        declared_cameras = tuple(config.get("cameras", {}) or {})
        licensed_cameras = self._license.allowed_camera_slots(declared_cameras)
        self._capture_threads: dict[str, CaptureThread] = {}
        for camera_slot in declared_cameras:
            if camera_slot not in licensed_cameras:
                logger.warning(
                    f"[Main] {camera_slot} no arranca: la licencia habilita "
                    f"{self._license.max_cameras} cámaras y hay {len(declared_cameras)} "
                    f"declaradas."
                )
                self._on_camera_status(_UNLICENSED_CAMERA_STATUS, camera_slot)
                self._push_status_to_content(_UNLICENSED_CAMERA_STATUS, camera_slot)
                continue
            thread = CaptureThread(config, camera_slot)
            thread.frame_ready.connect(self._on_frame_ready)
            thread.status_updated.connect(self._on_camera_status)
            self._forward_status_to_content(thread)
            self._capture_threads[camera_slot] = thread

        # ── Salud de la óptica ───────────────────────────────────────────────
        # Una ventana y una referencia por cámara: el veredicto es un máximo, así que
        # compartirlas dejaría que un solo vidrio limpio tape a todos los demás.
        self._lens_monitor = LensHealthMonitor(
            window_s=float(config.get("lens_health.window_s", 28800) or 28800),
            threshold_pct=float(config.get("lens_health.threshold_pct", 50) or 50),
            analysis_width_px=int(config.get("lens_health.analysis_width_px", 960) or 960),
        )
        for camera_slot in declared_cameras:
            self._lens_monitor.set_reference(
                camera_slot,
                config.get(f"cameras.{camera_slot}.lens_health.reference.variance", 0.0),
            )
        # Último frame crudo de cada cámara, para medirlo en el tick. Es la referencia al
        # array, no una copia: medir por frame costaría milisegundos por cámara y la
        # ventana es de horas, así que alcanza con el último que haya cuando toque.
        self._last_raw_frame: dict[str, np.ndarray] = {}
        # Cámaras calibrando ahora: slot -> (varianzas, lumas). Vacío casi siempre.
        self._lens_calibrations: dict[str, tuple[list, list]] = {}

        # ── Medias móviles de la hora ────────────────────────────────────────
        # Viven acá y no en el analyzer porque hay que envejecerlas en cada tick, haya
        # medición o no: una inferencia caída tiene que drenar la ventana en vez de dejarla
        # con el último promedio bueno. El analyzer corre sólo cuando hay resultado, así
        # que ahí la ventana se congelaría; además así sigue sin estado entre llamadas.
        window_s = float(config.get("process.rolling_window_s", 3600) or 3600)
        self._rolling = {name: rolling.RollingMean(window_s)
                         for name in _ROLLING_METRIC_NAMES}

        # ── GPIO ─────────────────────────────────────────────────────────────
        self._gpio = GpioController(config)
        self._gpio_thread = GpioPollingThread(self._gpio, config)
        self._gpio_thread.inputs_updated.connect(self._on_gpio_inputs)
        self._gpio_inputs: dict[int, int] = {}
        self._ui.set_gpio(self._gpio, self._gpio_thread)

        # ── Hardware y timers ────────────────────────────────────────────────
        self._monitor = SystemMonitor(config)
        self._metrics_poller = _MetricsPoller(self._monitor, _METRICS_INTERVAL_S)
        self._metrics_poller.metrics_ready.connect(self._on_metrics_ready)
        # El tag que llevan todas las series: distingue equipos que comparten bucket.
        # Es `proyecto` y no `device` porque es el que ya usa el dashboard — ver el bloque
        # de measurements arriba.
        self._telemetry_tags = {"proyecto": str(config.get("project.project_id", "") or "")}
        self._legacy_net_labels = config.get(
            "telemetry.influxdb.legacy_net_labels", {}) or {}
        self._legacy_camera_ids = config.get(
            "telemetry.influxdb.legacy_camera_ids", {}) or {}
        # Qué cámara alimentó las ventanas de la hora. Se publica con su tag para que el
        # bloque quede en la misma serie que la medición, como en la versión anterior.
        self._rolling_camera_slot = ""
        self._timers = [
            self._start_timer(_REGISTERS_INTERVAL_MS, self._on_registers_tick),
            self._start_timer(_STATUS_INTERVAL_MS, self._on_status_tick),
            self._start_timer(_SAMPLE_INTERVAL_MS, self._on_telemetry_tick),
            self._start_timer(RECHECK_INTERVAL_MS, self._on_license_tick),
            self._start_timer(self._read_lens_interval_ms(), self._on_lens_tick),
        ]
        self._warn_if_telemetry_margin_is_thin()

        self._ui.connect_config_saved(self._on_config_saved)
        self._ui.connect_license_requests(self._on_license_export_requested,
                                          self._on_license_install_requested)
        self._ui.connect_lens_calibration(self._on_lens_calibration_requested)
        self._ui.update_license(self._license.get_status())

    # ── Lo que cambia en cada fork ───────────────────────────────────────────
    #
    # Cinco decisiones del proyecto, juntas y marcadas para que un diff las muestre de
    # una: qué mira el operador, qué detecciones cuentan, con qué parámetros se cuenta,
    # qué se dibuja además del resultado, y con qué parámetros quedó medido lo que se
    # guarda. Todo lo demás de este archivo es cableado genérico.

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

    def _build_dataset_context(self, camera_slot: str) -> dict | None:
        """
        Con qué parámetros se midió: lo que hace falta para reanalizar el dataset después.

        Se guarda tal cual en el JSON de cada captura, y el colector no mira adentro. Acá
        van los dos valores que convierten los píxeles segmentados en los porcentajes que
        quedaron escritos: el rectángulo contra el que se midió la carga y el umbral con el
        que se descartó el fondo oscuro.

        Sin esto el dataset no se puede releer. Los píxeles se pueden volver a contar con
        cualquier ROI, pero saber **cuál** estaba puesto cuando se guardó es lo único que
        dice si esa carga sigue siendo comparable con la de hoy: mover el rectángulo entre
        dos muestras y no anotarlo hace incomparables las dos.
        """
        context = {}
        belt_roi_px = (self._config.get("process.belt_roi_px", {}) or {}).get(camera_slot)
        if belt_roi_px:
            context["belt_roi_px"] = dict(belt_roi_px)
        thresholds = (self._config.get(
            f"inference.models.{_SEGMENTER_SLOT}.params.dark_background_threshold", {})
            or {})
        if thresholds:
            context["dark_background_threshold"] = dict(thresholds)
        return context or None

    def _build_classifier(self):
        """
        Qué detecciones cuentan y con qué clase. Este proyecto no filtra ni reetiqueta.

        Todo lo que hay que decidir sobre una detección lo decide el modelo con su
        `min_confidence_pct`, y el reparto entre clases se resuelve contando píxeles en el
        analyzer. No hay nada calibrado por cámara —hay una sola cinta— que el modelo no
        pueda saber.
        """
        return None

    def _build_analyzer(self):
        """
        Qué significan las detecciones: la composición y la carga de la cinta.

        Los nombres de clase salen del modelo y no de `process:` para que haya un solo
        dueño: son los que deciden cómo se llaman las métricas —`pct_pellet_norm`— y por lo
        tanto qué registro las transporta. Declararlos dos veces dejaría que el mapa de
        registros y el modelo hablen de clases distintas sin que nada falle.
        """
        return metrics.build_analyzer(
            class_names=self._config.get(
                f"inference.models.{_SEGMENTER_SLOT}.class_names", []) or [],
            belt_roi_px=self._config.get("process.belt_roi_px", {}) or {},
        )

    def _build_annotator(self):
        """
        Lo que se dibuja además del resultado: el rectángulo de cinta y el panel.

        Lee los mismos valores que el analyzer y en el mismo lugar, que es la razón de que
        se lean acá y no adentro de cada factory: el rectángulo que ve el operador y el que
        se usó para calcular el porcentaje que salió al PLC son el mismo, y no pueden
        discrepar.
        """
        return annotations.chain(
            annotations.belt_roi_annotator(
                self._config.get("process.belt_roi_px", {}) or {}),
            annotations.composition_panel_annotator(
                self._config.get(f"inference.models.{_SEGMENTER_SLOT}.class_names", [])
                or [],
                font_scale=float(self._config.get("inference.overlay.font_scale", 0) or 0)),
        )

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
        for thread in self._capture_threads.values():
            thread.start()
        self._scheduler.start()
        self._gpio_thread.start()

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

        self._scheduler.stop()
        # Primero la captura: sin frames nuevos, la inferencia drena lo que tenga.
        qt_threads = (list(self._capture_threads.values()) + self._engines
                      + [self._metrics_poller, self._telemetry, self._gpio_thread])
        for thread in qt_threads:
            thread.requestInterruption()
        for thread in qt_threads:
            if not thread.wait(_THREAD_STOP_TIMEOUT_MS):
                logger.warning(f"[Main] Un hilo no cerró en {_THREAD_STOP_TIMEOUT_MS} ms.")

        self._collector.stop()
        # Después del hilo de polling: cerrar las líneas mientras alguien las lee deja al
        # polling leyendo una línea liberada.
        self._gpio.close()
        self._http_video.stop()
        self._rtsp_video.stop()
        self._modbus.stop()
        self._modbus_thread.join(timeout=_SERVER_STOP_TIMEOUT_S)
        if self._modbus_thread.is_alive():
            logger.warning("[Main] El hilo del Modbus no cerró a tiempo.")
        logger.info("[Main] Subsistemas detenidos.")

    # ── Slots de los hilos ───────────────────────────────────────────────────

    def _read_display_adjust(self):
        """El ajuste de visualización del config, o None si no cambia nada."""
        return build_display_adjust(
            gamma=float(self._config.get("video.display.gamma", 1.0) or 1.0),
            clahe_clip=float(self._config.get("video.display.clahe_clip", 0.0) or 0.0),
        )

    def _for_display(self, frame_bgr: np.ndarray | None) -> np.ndarray | None:
        """
        El frame como lo tiene que ver una persona.

        Sin ajuste configurado devuelve el mismo array: no se copia un frame para dejarlo
        igual, y esto corre por cada frame de cada cámara.
        """
        if self._display_adjust is None or frame_bgr is None or frame_bgr.size == 0:
            return frame_bgr
        return self._display_adjust(frame_bgr)

    @Slot(object, str)
    def _on_frame_ready(self, frame_bgr: np.ndarray | None, camera_slot: str):
        """
        Un frame nuevo: a la UI, al stream crudo y a la inferencia.

        En modo crudo la UI lo muestra tal cual; en anotado espera el que devuelve el
        motor, que es el mismo array con el resultado dibujado. Todo lo que se conecte
        acá tiene que ser barato o descartar: cualquier demora frena la captura.
        """
        # Lo que mira una persona puede llevar gamma y contraste local; lo que se mide,
        # nunca. `_for_display` devuelve un frame nuevo, así que el que sigue viaje hacia
        # el motor y el dataset es el de la cámara.
        display_bgr = self._for_display(frame_bgr)
        content = self._ui.monitor_content
        if content is not None:
            # El crudo va siempre, mire lo que mire: es el que necesita la herramienta de
            # ROI, que define coordenadas del sensor y no del anotado.
            setter = getattr(content, "update_raw_frame", None)
            if setter is not None:
                setter(frame_bgr, camera_slot)
            if getattr(content, "mode", None) != MODE_ANNOTATED:
                content.update_frame(display_bgr, camera_slot)
        self._http_video.push_raw(camera_slot, display_bgr)
        self._rtsp_video.push_raw(camera_slot, display_bgr)
        if frame_bgr is None or frame_bgr.size == 0:
            return
        # Para el tick de la óptica, que mide cada varios segundos y no por frame. Se
        # guarda el crudo: la nitidez se mide sobre lo que entrega el sensor, no sobre la
        # imagen con gamma, que le cambiaría el contraste y con él la varianza.
        self._last_raw_frame[camera_slot] = frame_bgr
        for engine in self._engines:
            # El ciclo decide si este frame entra: con `frames_per_cycle: 1` siempre
            # que sí, y el ritmo lo sigue poniendo el `interval_s` del motor. El permiso
            # se pide último porque se consume: pedirlo por un frame que el motor va a
            # descartar deja al ciclo esperando un resultado que no llega.
            if self._scheduler.take_frame(camera_slot, engine.pipeline_slot):
                engine.push_frame(
                    frame_bgr, camera_slot,
                    ignore_interval=self._scheduler.is_cycled(engine.pipeline_slot))

    @Slot(object, str)
    def _on_camera_status(self, status: dict, camera_slot: str):
        """Tres consumidores del mismo status: los chips de la cámara, el FPS del
        diagnóstico y la palabra de estado que va al PLC."""
        self._camera_status[camera_slot] = dict(status)
        self._ui.update_camera_status(status, camera_slot)

    @Slot(object)
    def _on_result_ready(self, result: InferenceResult):
        """
        Un resultado del motor, uno por frame: sólo la iluminación de la cámara.

        El frame anotado NO sale de acá. Lo publica `_on_cycle_complete`, con el
        representativo del ciclo: es el único que se corresponde con los números que
        salieron al PLC, y es el que tiene que quedar hasta la medición siguiente.
        """
        self._camera_illumination[result.camera_slot] = result.illumination_pct
        content = self._ui.monitor_content
        if content is not None:
            setter = getattr(content, "set_illumination_pct", None)
            if setter is not None:
                setter(result.camera_slot, result.illumination_pct)

    @Slot(object)
    def _on_cycle_complete(self, result: InferenceResult):
        """
        La medición del ciclo: al dataset, a la telemetría y a los registros.

        Va separado de `_on_result_ready` porque son dos caminos distintos: el operador
        mira los N frames del ciclo, pero la medición es una sola. Con
        `frames_per_cycle: 1` el scheduler reemite cada resultado y los dos caminos
        corren igual de seguido.
        """
        annotated_bgr = self._annotate_cycle(result)
        if annotated_bgr is not None:
            result.annotated_bgr = annotated_bgr
            self._last_annotated[result.camera_slot] = annotated_bgr
            content = self._ui.monitor_content
            if content is not None and getattr(content, "mode", None) == MODE_ANNOTATED:
                content.update_frame(annotated_bgr, result.camera_slot)
            # Los servidores retienen el último frame que se les dio, así que el anotado
            # del ciclo queda publicado hasta que lo reemplace el de la medición siguiente.
            self._http_video.push_annotated(result.camera_slot, annotated_bgr)
            self._rtsp_video.push_annotated(result.camera_slot, annotated_bgr)

        # Dataset: `push_frame` encola y vuelve; escribe el hilo del recolector. El
        # contexto se arma una vez y vale para los dos modos: es del par (cámara, momento)
        # y no del camino por el que se guarda.
        context = self._build_dataset_context(result.camera_slot)
        if self._collector.mode == _COLLECTOR_MODE_INTERVAL:
            self._collector.push_frame(
                result.camera_slot, result.source_bgr,
                annotated_bgr=result.annotated_bgr, inference=result.to_dict(),
                context=context,
            )
        elif self._scheduler.is_cycled(result.pipeline_slot):
            # En on_demand el que pide la captura es el ciclo, y es una por medición.
            # `save_now` escribe en este hilo: por frame sería un guardado sincrónico en
            # el hilo de la GUI, que es justo lo que su contrato desaconseja.
            self._collector.save_now(
                result.camera_slot, result.source_bgr,
                annotated_bgr=result.annotated_bgr, inference=result.to_dict(),
                context=context,
            )

        content = self._ui.monitor_content
        if content is not None:
            setter = getattr(content, "update_cycle", None)
            if setter is not None:
                setter(result)

        # Telemetría: al buzón, no a la cola. Lo publica el tick, agregado, porque una
        # serie por resultado escala con los fps y no agrega información. Los no
        # confiables entran igual: cuántos se cayeron y por qué es justo lo que se quiere
        # poder preguntar después.
        self._pending_results.setdefault(
            (result.camera_slot, result.pipeline_slot), []
        ).append(result)
        self._store_metrics(result)

    def _update_rolling(self, results: list):
        """
        Suma **una** muestra por tick a las ventanas de la hora, con lo medido desde el
        anterior.

        Una por tick y no una por frame: la ventana se compara contra el ritmo de
        publicación para saber cuánto de la hora se llegó a medir, y metiendo los quince
        frames de cada segundo esa cuenta daría siempre llena aunque la inferencia se
        hubiera caído media hora. Por eso lo que entra es el promedio del segundo.

        **La composición sólo promedia lo que tenía material y la carga promedia todo.** En
        una cinta vacía la composición no es 50/50 ni 0/0: no existe, y meterla en el
        promedio lo corre hacia donde no hay proceso. La carga sí: ahí el 0 es una medición
        legítima, y es justamente el dato de que la cinta estuvo parada.

        Envejecer va aparte y en cada tick, en `_build_rolling_registers()`: si se hiciera
        sólo acá, una inferencia caída dejaría la ventana congelada con su último promedio.

        Con más de una cámara midiendo lo mismo, las ventanas son una sola y la última
        cámara del recorrido gana. Una instalación con dos cintas necesita una ventana por
        cámara, igual que necesita una fila de registro por cámara.
        """
        averaged = analysis.average_metrics(results)
        if not averaged:
            return
        self._rolling_camera_slot = results[-1].camera_slot
        now_s = time.monotonic()
        self._last_inference_s = now_s
        composition_pct = {name: averaged[name] for name in _ROLLING_METRIC_NAMES
                           if name.endswith(_NORMALIZED_SUFFIX) and name in averaged}
        has_material = rolling.has_material(composition_pct)
        for name, window in self._rolling.items():
            value = averaged.get(name)
            if value is None:
                continue
            if name.endswith(_NORMALIZED_SUFFIX) and not has_material:
                continue
            window.add(now_s, float(value))

    def _build_rolling_registers(self) -> dict:
        """
        Las medias de la hora y las dos claves que dicen si se les puede creer.

        Envejece las ventanas en cada llamada, haya habido medición o no: es lo que hace
        que una inferencia caída las vacíe en vez de dejarlas con el último promedio bueno.

        Una ventana sin muestras publica **cero y no su último valor**, igual que la versión
        anterior del equipo y que la telemetría: un registro que se queda quieto se lee como
        una medición que no cambió, que es justo lo contrario de lo que pasó. El que dice si
        hay algo detrás de ese cero es `window_coverage_pct`.
        """
        now_s = time.monotonic()
        for window in self._rolling.values():
            window.tick(now_s)

        values = {f"{name}{_ROLLING_SUFFIX}": window.mean() or 0.0
                  for name, window in self._rolling.items()}
        # La cobertura se mide sobre la carga, que es la que promedia todas las mediciones:
        # es la ventana que representa cuánto se midió de verdad.
        load = self._rolling[_ROLLING_LOAD_NAME]
        values[_WINDOW_COVERAGE_KEY] = load.fill_pct(expected_hz=_SAMPLES_PER_S)
        values[_MATERIAL_PRESENT_KEY] = rolling.ratio_pct(
            self._rolling[_ROLLING_COMPOSITION_NAME].count(), load.count())
        return values

    def _annotate_cycle(self, result: InferenceResult) -> np.ndarray | None:
        """
        El frame anotado del ciclo, con los números del ciclo.

        El representativo se anotó en el motor con SUS métricas, y el scheduler le puso
        encima las del ciclo: la imagen diría una cosa y el registro otra. Por eso se
        redibuja, y por eso se redibuja acá y no en el scheduler, que no dibuja.

        Sin ciclo —`frames_per_cycle: 1`— el anotado que trae ya es el correcto y se usa
        tal cual: redibujar sería una pasada de dibujo más por frame.
        """
        if not self._scheduler.is_cycled(result.pipeline_slot):
            return result.annotated_bgr
        for engine in self._engines:
            if engine.pipeline_slot == result.pipeline_slot:
                return engine.annotate(result)
        return result.annotated_bgr

    @Slot(str, bool)
    def _on_capture_wanted(self, camera_slot: str, enabled: bool):
        """El scheduler pide prender o apagar una cámara entre mediciones."""
        thread = self._capture_threads.get(camera_slot)
        if thread is not None:
            thread.set_capture_enabled(enabled)

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
    def _on_gpio_inputs(self, states_by_channel: dict):
        """Última lectura de las entradas. La palabra la arma el tick de registros."""
        self._gpio_inputs = states_by_channel

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
                dead_thread=any(not t.isRunning()
                                for t in self._capture_threads.values()),
                license_invalid=self._license.should_report_invalid(),
            ),
            "license_days_remaining": self._license_days_remaining(),
            **self._build_clock_registers(),
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
        values.update(self._build_rolling_registers())
        values.update({
            "gpio_inputs_bitfield": gpio_status.pack_inputs(
                self._gpio_inputs, hardware_available=self._gpio.hardware_available),
            "gpio_outputs_bitfield": gpio_status.pack_outputs(
                self._gpio.get_all_outputs(),
                hardware_available=self._gpio.hardware_available),
        })
        # Con la política `degrade` las mediciones no se publican, pero el canal sigue
        # sirviendo: el latido, la salud del equipo y las palabras de estado salen igual.
        # Bajar el Modbus dejaría al PLC viendo un enlace muerto, indistinguible de un
        # cable cortado, y el integrador saldría a buscar el problema equivocado. Los
        # registros de proceso quedan con su último valor, y el bit de licencia dice por
        # qué dejaron de moverse.
        if self._license.is_publishing_allowed():
            values.update(self._build_metric_registers())

        # Un solo `encode_batch` para los dos consumidores —el datastore que lee el PLC y
        # la tabla que mira el operador—: codificar dos veces los deja divergir.
        registers = SCHEMA.encode_batch(self._mapped_only(values))
        self._modbus.update_block(registers)
        self._ui.update_modbus_values(registers)

    def _on_status_tick(self):
        """
        Relee el `status` de cada subsistema y lo publica en la UI.

        Los subsistemas no emiten señal cuando cambian de estado: exponen una property
        que se consulta. Por eso esto es un timer y no un slot, y por eso está todo en un
        solo lugar en vez de repartido.
        """
        self._scheduler.tick()
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
            service_status.SERVICE_VIDEO_HTTP, self._http_video.get_stream_urls()
        )
        self._ui.set_stream_urls(
            service_status.SERVICE_VIDEO_RTSP, self._rtsp_video.get_stream_urls()
        )

    # ── Salud de la óptica ───────────────────────────────────────────────────

    def _read_lens_interval_ms(self) -> int:
        """Cada cuánto se mide la nitidez. Se fija al arrancar, como el resto de timers."""
        interval_s = float(self._config.get("lens_health.interval_s",
                                            _LENS_DEFAULT_INTERVAL_S)
                           or _LENS_DEFAULT_INTERVAL_S)
        return int(max(_LENS_MIN_INTERVAL_S, interval_s) * 1000)

    def _on_lens_tick(self):
        """
        Mide la nitidez del último frame de cada cámara y sostiene la ventana de todas.

        Se mide acá y no en `_on_frame_ready` porque la ventana es de horas: una pasada del
        Laplaciano por cámara cada varios segundos la llena de sobra, y hacerlo por frame
        le comería milisegundos a la captura sin agregar información.

        Con la detección apagada las ventanas igual envejecen, así que al prenderla de
        nuevo nadie hereda un veredicto viejo de cuando nadie estaba mirando.
        """
        now_s = time.monotonic()
        if not self._config.get("lens_health.enabled", True):
            self._lens_monitor.discard_expired(now_s)
            return

        for camera_slot in (self._config.get("cameras", {}) or {}):
            roi = self._config.get(f"cameras.{camera_slot}.roi", {}) or {}
            try:
                # Con la cámara caída el frame es None: el monitor envejece la ventana
                # igual y esa cámara pasa a «no disponible» sola.
                measurement = self._lens_monitor.update(
                    camera_slot, self._last_raw_frame.get(camera_slot), roi, now_s=now_s)
            except Exception as error:
                # Nunca tumbar el tick por una medición. Sin muestras la ventana se vacía
                # y el estado pasa a «no disponible», que es lo que corresponde.
                logger.error(f"[Main] No se pudo medir la nitidez de {camera_slot}: {error}")
                continue
            if measurement is not None and camera_slot in self._lens_calibrations:
                self._collect_lens_calibration(camera_slot, measurement)

    def _is_lens_dirty(self, camera_slot: str) -> bool:
        """True sólo con el veredicto de alarma: «no disponible» no es un vidrio sucio."""
        return self._lens_monitor.get_state(
            camera_slot, now_s=time.monotonic()) == lens_health.STATE_ALARM

    @Slot(str)
    def _on_lens_calibration_requested(self, camera_slot: str):
        """
        Arranca la calibración de esa cámara: junta N muestras y fija su referencia.

        Se calibra con el lente recién limpiado, porque lo que se está guardando es el
        techo de nitidez de ese montaje. Cada cámara se calibra por su cuenta y las que ya
        lo estaban siguen vigiladas mientras tanto.
        """
        sample_count = int(self._config.get("lens_health.calibration_samples", 30) or 30)
        self._lens_calibrations[camera_slot] = ([], [])
        logger.info(f"[Main] Calibrando la óptica de {camera_slot}: "
                    f"{sample_count} muestras, una cada "
                    f"{self._read_lens_interval_ms() / 1000:.0f} s.")

    def _collect_lens_calibration(self, camera_slot: str, measurement):
        """Suma una muestra y, al llegar al total, escribe la referencia en el config."""
        variances, lumas = self._lens_calibrations[camera_slot]
        variances.append(measurement.variance)
        lumas.append(measurement.luma)
        target_count = int(self._config.get("lens_health.calibration_samples", 30) or 30)
        if len(variances) < target_count:
            return

        del self._lens_calibrations[camera_slot]
        reference = lens_health.summarize_calibration(variances, lumas)
        if reference is None:
            return
        reference["calibrated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        # Con qué exposición, ganancia, rotación y ROI se midió. No invalida nada: si
        # después cambian, la pantalla avisa que el número puede haberse movido.
        reference["conditions"] = lens_health.get_current_conditions(
            self._config.get(f"cameras.{camera_slot}.acquisition", {}) or {},
            self._config.get(f"cameras.{camera_slot}.rotation", None),
            self._config.get(f"cameras.{camera_slot}.roi", {}) or {},
        )
        prefix = f"cameras.{camera_slot}.lens_health.reference"
        for key, value in reference.items():
            self._config.set(f"{prefix}.{key}", value)
        self._config.save()
        self._lens_monitor.set_reference(camera_slot, reference["variance"])
        self._ui.refresh_lens_reference(camera_slot)
        logger.info(
            f"[Main] Óptica de {camera_slot} calibrada: varianza={reference['variance']:.1f} "
            f"luma={reference['luma']:.1f} dispersión={reference['dispersion_pct']:.1f} % "
            f"({reference['sample_count']} muestras)."
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
                _build_system_fields(self._last_metrics, self._legacy_net_labels),
                tags=self._telemetry_tags,
            )

        now_s = time.monotonic()
        for camera_slot in (self._config.get("cameras", {}) or {}):
            status = self._camera_status.get(camera_slot)
            if status is None:
                continue
            lens = self._lens_monitor.get_status(camera_slot, now_s=now_s)
            camera_tags = self._camera_tags(camera_slot)
            self._telemetry.push_data(
                _MEASUREMENT_CAMERA,
                {
                    "connected": int(bool(status.get("connected"))),
                    "capture_enabled": int(bool(status.get("capture_enabled", True))),
                    "misconfigured": int(bool(status.get("error"))),
                    "fps": float(status.get("fps_estimated", 0.0)),
                    "temperatura": float(status.get("temperature", 0.0)),
                },
                tags=camera_tags,
            )
            # La óptica va en su propio measurement y no como campos de `camara`, que es
            # como lo publica el template: es la serie que el dashboard ya consulta para
            # ver el vidrio ensuciarse semanas antes de que el bit se prenda.
            self._telemetry.push_data(
                _MEASUREMENT_OPTICS, _build_optics_fields(lens), tags=camera_tags)

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
        entran cinco resultados: promediarlos usa los cinco, mientras que quedarse con el
        último tiraría cuatro. `frames` deja ver cuántos entraron, así que la ventana de
        agregación es visible en el dato.

        El buzón se vacía en el mismo paso: nada se publica dos veces. **Lo que sí sale en
        todos los ticks es el bloque de la ventana de una hora**, aunque el par no haya
        medido nada: es justamente cuando importa verlo caer, y es lo que distingue una
        medición estable de una que dejó de llegar.

        También alimenta las medias móviles, con los mismos resultados agregados: una
        muestra por tick, que es contra lo que se compara la cobertura de la ventana.
        """
        if not self._license.is_publishing_allowed():
            # Se vacía igual. Si no, el buzón crece sin techo mientras la licencia no
            # valga, y el día que se renueve saldría de golpe un promedio de semanas.
            self._pending_results.clear()
            return

        for (camera_slot, pipeline_slot), results in self._pending_results.items():
            if not results:
                continue
            self._update_rolling(results)
            self._telemetry.push_data(
                _MEASUREMENT_INFERENCE,
                _build_inference_fields(results),
                tags={**self._camera_tags(camera_slot), "pipeline": pipeline_slot},
                # El instante del último resultado, no el del tick: el punto vale por
                # cuándo se midió y entre las dos cosas hay hasta una muestra entera.
                time_s=results[-1].timestamp_s,
            )
        self._pending_results.clear()
        self._publish_rolling()

    def _publish_rolling(self):
        """
        El bloque de la ventana de una hora, en todos los ticks.

        Va aparte del punto de inferencia y no adentro porque tiene que salir **aunque no
        se haya medido nada**: si se publicara sólo con el otro, el día que la inferencia
        se cae dejarían de llegar los dos y el dashboard mostraría el último valor bueno
        para siempre. Acá el desplome de la cobertura es el que cuenta lo que pasó.

        Lleva el tag de la cámara que las alimentó, no porque las ventanas sean suyas
        —son una sola para el equipo, igual que los registros 6-10— sino porque así queda
        en la misma serie en la que el dashboard ya las venía leyendo. Con una segunda
        cámara esto deja de tener sentido y las ventanas pasan a ser por cámara.
        """
        fields = {f"{name}{_ROLLING_SUFFIX}": float(window.mean() or 0.0)
                  for name, window in self._rolling.items()}
        load = self._rolling[_ROLLING_LOAD_NAME]
        fields["cobertura_pct"] = load.fill_pct(expected_hz=_SAMPLES_PER_S)
        fields["material_presente_pct"] = rolling.ratio_pct(
            self._rolling[_ROLLING_COMPOSITION_NAME].count(), load.count())
        fields["inference_age_s"] = self._inference_age_s()
        self._telemetry.push_data(_MEASUREMENT_INFERENCE, fields,
                                  tags=self._camera_tags(self._rolling_camera_slot))

    def _inference_age_s(self) -> int:
        """
        Segundos desde la última medición, o el centinela mientras no hubo ninguna.

        Un 0 mientras nunca se midió se leería como «recién medido», que es exactamente lo
        contrario de lo que pasa.
        """
        if self._last_inference_s is None:
            return _NO_INFERENCE_AGE_S
        return min(_NO_INFERENCE_AGE_S,
                   int(round(time.monotonic() - self._last_inference_s)))

    def _camera_tags(self, camera_slot: str) -> dict:
        """
        Los tags de una serie por cámara, con el id que el dashboard ya conoce.

        El valor del tag **no es la clave del slot**: la versión anterior publicaba
        `cam1` y una serie con otro valor de tag es otra serie, así que un panel filtrando
        por `cam1` no encontraría nada. La traducción está en
        `telemetry.influxdb.legacy_camera_ids`; una cámara que no figure sale con su slot.
        """
        if not camera_slot:
            return dict(self._telemetry_tags)
        return {**self._telemetry_tags,
                "camara_id": self._legacy_camera_ids.get(camera_slot, camera_slot)}

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
        # El gamma y el contraste se ajustan mirando la imagen, así que pedir un reinicio
        # por cada prueba los volvería inusables. Es la excepción que se puede hacer sin
        # riesgo: no tocan la medición, sólo cómo se ve.
        self._display_adjust = self._read_display_adjust()
        # La referencia de cada óptica también: es el número que la pantalla deja editar a
        # mano para copiar el de un equipo gemelo, y esperar un reinicio para que tome
        # efecto haría parecer que no se guardó. El umbral y la ventana sí esperan, como
        # el resto de lo que se fija al arrancar.
        for camera_slot in (self._config.get("cameras", {}) or {}):
            self._lens_monitor.set_reference(
                camera_slot,
                self._config.get(f"cameras.{camera_slot}.lens_health.reference.variance",
                                 0.0),
            )

    # ── Licencia ─────────────────────────────────────────────────────────────

    def _on_license_tick(self):
        """
        Revalida la licencia con el proceso andando y publica el estado.

        Sin esto alcanzaría con congelar el reloj antes de arrancar y no reiniciar nunca:
        la validación del arranque no vuelve a correr. No se relee el archivo —lo que
        cambia mientras el proceso vive es la fecha, no la firma—, salvo que no hubiera
        licencia, y ahí sí mira de nuevo por si alguien acaba de dejarla en su lugar.

        El estado se publica en cada tick y no sólo cuando cambia: los días restantes
        bajan sin que cambie el estado, y el contador de la pantalla quedaría viejo.
        Un cambio de estado ya lo loguea el propio manager.

        **Lo que no hace es frenar el equipo.** Que la licencia se caiga a mitad de un
        turno no baja la línea: eso lo decide la política, y el corte por falta de
        licencia es una decisión de arranque —ahí no hay nada tomado ni publicado que
        desarmar—.
        """
        self._license.recheck()
        self._ui.update_license(self._license.get_status())

    @Slot(str)
    def _on_license_export_requested(self, path: str):
        """La pantalla eligió dónde escribir la solicitud; el archivo lo escribe acá."""
        try:
            written = save_request(self._config, path)
        except WeakFingerprintError as e:
            logger.error(f"[Licencia] {e}")
            self._ui.show_license_result(False, str(e))
            return
        except OSError as e:
            logger.error(f"[Licencia] No se pudo escribir la solicitud: {e}")
            self._ui.show_license_result(False, str(e))
            return

        logger.info(f"[Licencia] Solicitud escrita en {written}.")
        self._ui.show_license_result(True, written)

    @Slot(str)
    def _on_license_install_requested(self, path: str):
        """
        La pantalla eligió un `.lic`; verificarlo e instalarlo es del cableado.

        El orden importa: primero el estado nuevo y después el resultado, porque lo que
        el operador tiene que leer al lado del estado es qué pasó con lo que apretó.
        """
        is_installed, message = self._license.install(path)
        self._ui.update_license(self._license.get_status())
        self._ui.show_license_result(is_installed, message)

        if is_installed:
            logger.info(f"[Licencia] {message}")
        else:
            logger.error(f"[Licencia] No se instaló: {message}")

    # ── Traducción al esquema de registros ───────────────────────────────────

    def _mapped_only(self, values: dict) -> dict:
        """
        Deja pasar los valores que tienen fila en el mapa y avisa una vez por el resto.

        El mapa de cada instalación declara lo que ese PLC lee, y no tiene por qué
        declarar todo lo que el cableado sabe medir: una que no reporta la RAM total
        simplemente no pone la fila —y ese valor igual se usa, por ejemplo para la escala
        del gráfico de RAM—. Sin este filtro `encode_batch` levanta `KeyError` y el ciclo
        de registros se cae entero por un valor de más.
        """
        mapped = {}
        for name, value in values.items():
            if name in _REGISTER_NAMES:
                mapped[name] = value
            elif name not in self._unmapped_metrics:
                self._unmapped_metrics.add(name)
                # A nivel DEBUG y no INFO: los nombres que llegan acá son los del bloque
                # de salud, que están escritos en este archivo y no pueden estar mal
                # tipeados. Que un mapa no declare uno es una decisión de la instalación,
                # no algo que el operador tenga que ir a corregir.
                logger.debug(f"[Main] '{name}' no tiene fila en el mapa: no se publica.")
        return mapped

    def _build_clock_registers(self) -> dict:
        """
        El reloj del equipo en segundos epoch UTC, partido en palabra alta y baja.

        **El PLC es el único reloj confiable que hay en una instalación sin internet.** El
        vencimiento se evalúa contra el reloj de este equipo, y quien tiene admin acá puede
        atrasarlo; lo que el equipo guarda para detectarlo vive en un disco que ese mismo
        admin controla, así que solo no alcanza. Publicando la hora, el PLC la compara con
        la suya y un atraso se ve desde afuera.

        Acá no se compara ni se juzga nada: se publica el dato y el programa del PLC decide
        con qué tolerancia alarma. Es el mismo criterio que con todo lo demás — el equipo
        entrega valores y el integrador arma la lógica.

        **El corte en dos palabras se hace acá y no en el esquema** porque el esquema es
        una fila por registro y sostener ese invariante vale más que ahorrar dos líneas:
        el mapa sigue teniendo un nombre por dirección, que es lo que el integrador lee.
        El día que un fork necesite publicar varios valores de 32 bits, lo que corresponde
        es que el esquema aprenda a hacerlo, no repetir esto en cada llamador.
        """
        epoch_s = int(datetime.now(timezone.utc).timestamp())
        return {
            "clock_epoch_s_high": (epoch_s >> 16) & 0xFFFF,
            "clock_epoch_s_low": epoch_s & 0xFFFF,
        }

    def _license_days_remaining(self) -> int:
        """
        Días de licencia que se publican al PLC.

        Una perpetua publica un centinela alto y constante: el registro es un entero sin
        signo y no hay forma de decir «no vence» con un número, así que se dice «falta
        muchísimo», que es lo que una alarma por umbral necesita. El 0 cubre los tres
        casos en que no queda nada de qué fiarse —vencida, sin licencia, o con el reloj
        movido—; cuál de los tres es lo dice el bit de la palabra de estado.
        """
        days = self._license.days_remaining
        if days is not None:
            return days
        return PERPETUAL_DAYS_SENTINEL if self._license.is_valid else 0

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
                    # El bit 6 es independiente de la adquisición: el vidrio puede estar
                    # sucio con la cámara sana. No hace falta ningún registro nuevo.
                    dirty_lens=self._is_lens_dirty(camera_slot),
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

        Lo que agrega `summarize_metrics` al cerrar un ciclo —`sample_count` y los
        estadísticos por métrica— se descarta sin avisar: son para la telemetría y el
        dataset, nunca tuvieron fila, y con diez métricas son treinta líneas de log que
        tapan justamente el aviso del nombre mal escrito.

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
                elif analysis.is_summary_key(name):
                    # La dispersión de un ciclo es para la telemetría y el dataset, no
                    # para el PLC: no tener fila es lo esperado y avisarlo sería tapar el
                    # aviso que sí importa, el del nombre mal escrito.
                    continue
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

    def _is_synthetic(self, camera_slot: str) -> bool:
        thread = self._capture_threads.get(camera_slot)
        return bool(thread is not None and thread.is_synthetic)

    @Slot(str)
    def _on_content_mode_changed(self, mode: str):
        """
        El operador pasó a anotado: se le devuelve el último anotado de cada cámara.

        Sin esto la vista queda en el último frame crudo hasta la medición siguiente, que
        con un ciclo de varios minutos es una imagen vieja que no dice nada. La cámara que
        todavía no midió se limpia en vez de dejarse como está, por lo mismo: el crudo que
        quedó pintado se lee como si fuera el resultado de una inferencia que no corrió.
        """
        if mode != MODE_ANNOTATED:
            return
        content = self._ui.monitor_content
        if content is None:
            return
        clear = getattr(content, "clear_frame", None)
        for camera_slot in getattr(content, "camera_slots", ()):
            frame_bgr = self._last_annotated.get(camera_slot)
            if frame_bgr is not None:
                content.update_frame(frame_bgr, camera_slot)
            elif clear is not None:
                clear(camera_slot)

    def _connect_content_mode(self):
        """Escucha el selector de modo del widget del monitor, si tiene uno."""
        content = self._ui.monitor_content
        signal = getattr(content, "mode_changed", None)
        if signal is not None:
            signal.connect(self._on_content_mode_changed)
        # El widget arranca en el modo que eligió el fork y esa primera vez no pasa por la
        # señal, así que el estado inicial se arma acá: en anotado, los paneles quedan
        # esperando la primera medición en vez de mostrando lo que haya.
        mode = getattr(content, "mode", None)
        if mode is not None:
            self._on_content_mode_changed(mode)

    def _forward_status_to_content(self, thread: CaptureThread):
        """Conecta el status al widget del monitor, si el que puso el fork lo acepta."""
        content = self._ui.monitor_content
        if content is not None and hasattr(content, "update_status"):
            thread.status_updated.connect(content.update_status)

    def _push_status_to_content(self, status: dict, camera_slot: str):
        """
        Empuja un status al widget del monitor para una cámara sin hilo propio.

        Es el caso de la que queda afuera del cupo de la licencia: no hay `CaptureThread`
        cuya señal conectar, así que sin esto el chip se queda con el placeholder de
        arranque para siempre. Una cámara que dice «Iniciando» y no arranca nunca manda a
        revisar un cable que está bien, que es justo lo que el cupo no tiene que provocar.
        """
        content = self._ui.monitor_content
        if content is not None and hasattr(content, "update_status"):
            content.update_status(status, camera_slot)

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


def _build_modbus_server(config: ConfigManager) -> SharedModbusServer:
    """
    El servidor Modbus, con el motivo por el que no debería arrancar si lo hay.

    **`registers.py` detecta que el mapa no se pudo leer, pero no decide nada**: deja el
    motivo en `LOAD_ERROR` y es el cableado el que se lo pasa al servidor. Sin eso los dos
    transportes toman su puerto igual y sirven el datastore vacío, que es el peor de los
    resultados: un PLC leyendo ceros no puede distinguir «no hay medición» de «falta el
    mapa», y manda al integrador a buscar el problema al lado equivocado.

    El resto del equipo sigue andando —mira, mide, guarda el dataset y publica a la
    telemetría—: lo único que se cae es la publicación al PLC, que sin mapa no tendría a
    dónde escribir.
    """
    return SharedModbusServer(config, blocked_reason=REGISTER_MAP_ERROR)


def _build_inference_fields(results: list) -> dict:
    """
    Campos de un punto de inferencia a partir de los resultados de una muestra.

    **Los nombres son los que ya consulta el dashboard** y por eso no llevan los sufijos
    `_mean` que agrega el template: una serie renombrada no se migra, la historia queda con
    el nombre viejo y el panel deja de encontrarla. Ver el bloque de measurements arriba y
    `docs/influxdb.md`.

    Tres grupos:

      - **Las métricas del proceso**, promediadas sobre los resultados. Son las que salen
        del analyzer, así que la lista no se escribe acá: lo que el analyzer publique
        termina en la serie con su propio nombre. Lo que el ciclo ya resumió —`sample_count`
        y la dispersión por métrica— viaja tal cual y no se vuelve a agregar: derivarlo de
        nuevo sobre una sola muestra daría desvío 0 y taparía el de los N frames.
      - **Escalares del resultado**, con sus nombres de siempre. La confianza sólo sobre los
        confiables —promediar la de un descarte da un número que no significa nada—; el
        tiempo y la iluminación sobre todos, porque valen igual y la iluminación baja es
        justamente uno de los motivos de descarte.
      - **Cuántos y por qué**: `frames`, `invalid_count` y el último `invalid_reason`. Sin
        esto no hay forma de preguntar cuántas mediciones se cayeron.

    Los enteros se publican como enteros: InfluxDB fija el tipo de cada campo con el primer
    punto que recibe, y el bucket de este equipo ya los tiene así desde la versión anterior.
    """
    valid = [r for r in results if r.is_valid]
    last_invalid = next((r for r in reversed(results) if not r.is_valid), None)

    fields: dict = {
        "frames": len(results),
        "invalid_count": len(results) - len(valid),
        "invalid_reason": last_invalid.invalid_reason if last_invalid is not None else "",
    }
    fields.update({name: _rounded(name, value)
                   for name, value in analysis.average_metrics(results).items()
                   if name not in _SCALAR_METRIC_NAMES})

    if valid:
        fields["confianza"] = round(statistics.fmean(r.confidence_pct for r in valid))
    if results:
        fields["iluminacion"] = round(
            statistics.fmean(r.illumination_pct for r in results))
        fields["inference_time_ms"] = round(
            statistics.fmean(r.inference_time_ms for r in results), 2)
    return fields


# Métricas que el analyzer re-exporta sólo para que lleguen al PLC —el mapa de registros se
# indexa por nombre de métrica—. En la telemetría salen del resultado y con el nombre que ya
# usa el dashboard, así que acá se descartan para no publicar el mismo dato dos veces.
_SCALAR_METRIC_NAMES = ("confidence_pct", "inference_time_ms")

_INTEGER_FIELD_PREFIX = "pct_"


def _rounded(name: str, value: object) -> object:
    """
    Redondea una métrica al tipo con el que ya está guardada en el bucket.

    **Todos los porcentajes instantáneos se guardaron como enteros**, incluidas la
    composición y la carga: la versión anterior los pasaba por `round()` sin decimales. Los
    de la ventana de una hora sí son decimales, pero no salen por acá.

    InfluxDB fija el tipo de cada campo con el primer punto que lo trae, así que un decimal
    donde había un entero no se convierte: el backend rechaza la escritura entera y se
    pierden también los puntos de las otras series del mismo batch.
    """
    number = float(value)
    return round(number) if name.startswith(_INTEGER_FIELD_PREFIX) else round(number, 2)


def _build_optics_fields(lens: dict) -> dict:
    """
    Campos de la serie de óptica, con los nombres que ya consulta el dashboard.

    Los cuatro primeros salen siempre; los de la última medición sólo si hubo alguna. Una
    cámara caída deja de publicarlos en vez de repetir el último valor bueno: el hueco en
    la serie dice que no se midió, y un número repetido no.
    """
    fields = {
        "estado": int(lens["state"]),
        "nitidez_max_pct": int(lens["sharpness_max_pct"]),
        "muestras": int(lens["sample_count"]),
        "referencia": float(lens["reference_variance"]),
    }
    if lens["max_variance"] is not None:
        fields["varianza_max"] = float(lens["max_variance"])
    if lens["variance"] is not None:
        fields["varianza"] = float(lens["variance"])
        fields["nitidez_pct"] = int(lens["sharpness_pct"])
        fields["luma"] = float(lens["luma"])
    return fields


def _build_system_fields(metrics: dict, legacy_labels: dict | None = None) -> dict:
    """
    Métricas de hardware como fields de Influx: escalares, enteras y con la red aplanada.

    **Los nombres y los tipos son los que la serie ya tiene en el bucket**, que no son los
    de `get_metrics()`: la versión anterior del equipo publicaba `cpu_usage` y no
    `cpu_usage_pct`, y todo en entero. La traducción está en `_LEGACY_SYSTEM_FIELDS`.

    `net_mbps` llega como `{interfaz: {rx_mbps, tx_mbps}}` y un field de Influx es
    escalar, así que cada interfaz se abre en dos campos con su nombre adentro. Filtrar
    los valores no escalares —que es lo obvio— dejaría el tráfico de red afuera sin que
    nadie se enterara.

    **Los campos de red se llaman `rx_<etiqueta>` / `tx_<etiqueta>`** y no
    `net_<interfaz>_rx_mbps` como en el template, con la etiqueta de
    `telemetry.influxdb.legacy_net_labels`: el dashboard conoce las interfaces por su papel
    —`eth0` es la de cámaras— y no por el nombre que les puso el sistema operativo, que
    además cambia al reinstalar. Una interfaz sin etiqueta declarada sale con su nombre
    crudo, que es mejor que no salir.

    `temps_c` no se publica: es el detalle por zona térmica que ya resumen `cpu_temp_c` y
    `gpu_temp_c`, y sus nombres cambian entre equipos, así que no sirve para un dashboard
    que tenga que andar en los dos.
    """
    legacy_labels = legacy_labels or {}
    fields = {legacy: _as_integer(metrics[key])
              for key, legacy in _LEGACY_SYSTEM_FIELDS.items() if key in metrics}
    for iface, throughput in (metrics.get("net_mbps") or {}).items():
        label = legacy_labels.get(iface) or str(iface).replace(" ", "_")
        fields[f"rx_{label}"] = _as_integer(throughput.get("rx_mbps", 0))
        fields[f"tx_{label}"] = _as_integer(throughput.get("tx_mbps", 0))
    return fields


def _as_integer(value: object) -> int:
    """
    Entero, que es el tipo con el que toda la serie de hardware está en el bucket.

    **InfluxDB fija el tipo de cada campo con el primer punto que lo trae**, y estos los
    fijó la versión anterior del equipo, que publicaba enteros. Mandar un float ahora no
    convierte nada: el backend rechaza la escritura entera con un conflicto de tipo, así
    que se pierden también los puntos de las demás series que iban en el mismo batch.
    """
    return int(round(float(value)))


def main(argv: list | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    config = ConfigManager(args.config)
    _setup_logging(config)
    # Después del log y antes de cualquier subsistema: los que usan un secreto lo leen
    # del entorno cuando arrancan, y de qué se cargó tiene que quedar constancia.
    load_env_file()

    # Antes de Qt y de cualquier subsistema, para que el veredicto quede en el log pase lo
    # que pase después. Un equipo sin licencia utilizable YA NO aborta acá: abre igual, en
    # modo de puesta en marcha —sin pipelines, sin publicar nada—, porque un binario
    # compilado no tiene detrás un intérprete donde correr la CLI para generar la
    # solicitud o instalar el archivo que vuelve. La pestaña de licencia es ese lugar.
    license_manager = LicenseManager(config)

    headless = args.headless or not config.get("ui.enabled", True)
    # `QCoreApplication` en headless: `QApplication` necesita una plataforma gráfica y en
    # un equipo sin display no arranca. El event loop es el mismo, y es el que hace falta
    # igual, porque las señales son el cableado entre los hilos.
    app = QCoreApplication(sys.argv) if headless else QApplication(sys.argv)

    application = Application(config, create_ui(config, headless=headless), license_manager)
    app.aboutToQuit.connect(application.stop)
    application.start()

    # Ctrl+C y el SIGTERM de un servicio, con el event loop de Qt corriendo: sin esto la
    # señal queda esperando a que Qt devuelva el control, que no pasa. El timer al vacío
    # es lo que le da al intérprete la chance de atenderla.
    #
    for signal_number in _interrupt_signals():
        signal.signal(signal_number, lambda *_: app.quit())
    interrupt_timer = QTimer()
    interrupt_timer.timeout.connect(lambda: None)
    interrupt_timer.start(_SIGNAL_POLL_INTERVAL_MS)

    exit_code = app.exec()
    return _exit(exit_code, config)


def _interrupt_signals() -> tuple:
    """
    Señales con las que se le pide a la app que cierre.

    ONLY_WINDOWS: `SIGBREAK` es el Ctrl+Break de Windows y no existe en POSIX. Sin
    atenderlo el proceso muere con 0xC000013A **sin ejecutar un solo paso de `stop()`**:
    las cámaras quedan tomadas y el datastore del Modbus con el último valor publicado.
    El `hasattr` es lo que deja el mismo código corriendo en Linux.
    """
    signal_numbers = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signal_numbers.append(signal.SIGBREAK)
    return tuple(signal_numbers)


def _exit(exit_code: int, config: ConfigManager) -> int:
    """
    Cierra el log y, si el config lo pide, termina el proceso sin desarmar el intérprete.

    Con `system.hard_exit: true` no se vuelve de acá. Hace falta cuando el proceso aborta
    en el *teardown* del intérprete y no en el cierre: un framework de inferencia con hilos
    nativos y Qt cargados a la vez pueden abortar con 0xC0000409 al descargarse, aun con
    todos los hilos de la app cerrados limpiamente. `stop()` ya corrió por `aboutToQuit`,
    así que lo que se saltea es la destrucción de módulos, no el cierre ordenado.

    Viene apagado porque saltea también los `atexit` y el flush de lo que no sea el log, y
    porque el que lo necesita lo descubre al desplegar, no antes.
    """
    logging.shutdown()
    if config.get("system.hard_exit", False):
        os._exit(exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
