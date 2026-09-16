"""
Prueba manual de la interfaz: levanta la aplicación completa con cámaras sintéticas.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\ui\\ui_app.py
    Linux:    .venv/bin/python manual_test/ui/ui_app.py

    --simulate-no-license   fuerza el marcador de build compilado para ver, sin compilar
                            de verdad, el cartel de arranque y el chip rojo del footer que
                            aparecen en un equipo entregado sin licencia. No hay
                            `license.lic` al lado de este config, así que el estado que se
                            ve es `absent`.

Lee el `config.yaml` que tiene al lado —**no** el de la app— con el ConfigManager real,
y hace de `main.py` mientras `main.py` no exista: cablea la ventana con los subsistemas
de verdad y no con dobles.

Es también la referencia de cómo se cablea la UI: lo que hace `_DemoApp` es lo que va a
hacer `main.py`, con los mismos métodos públicos. Ver `docs/ui.md`.

**Ningún estado está simulado.** Todo lo que muestra la pantalla lo informó su dueño:

    4 hilos de captura (mock)      frames, fps y temperatura de cada cámara
    1 hilo de inferencia           resultados, anotado e iluminación
    SystemMonitor (en su hilo)     CPU, GPU, RAM, disco, temperaturas y red
    servidor HTTP de video         su `status`, sus clientes y sus URLs
    servidor Modbus TCP + RTU      su `status` por transporte, y el datastore que se
                                   escribe con los registros que muestra la pestaña
    hilo de telemetría             el `status` de los backends de InfluxDB y MQTT
    los packers de system/formats  las palabras de estado y de comunicaciones
    el logger real                 puenteado al log de la barra lateral

Lo único sintético son los frames, que salen del `mock` driver, y el controlador de
GPIO, porque ese módulo todavía no existe en el repo. El servidor RTSP es el único que no
se levanta —necesita GStreamer—: su chip dice `disabled` si el config lo apaga.

Qué mirar:

  - **La grilla muestra las cuatro cámaras del config, no las tres que andan.** Entrada
    y salida entregan frames; «Descarga» está con `enabled: false` y su chip dice
    Deshabilitada en ámbar; «Tolva» tiene un `driver` que no existe y queda en SIN SEÑAL
    con el chip en rojo. Los tres estados son distintos a propósito.
  - **El selector de modo cambia lo que entra, y el anotado sólo se dibuja si se lo
    mira.** En «Video crudo» el motor no gasta una pasada de overlay: el `annotate_gate`
    que recibe está atado al combo. Pasar a «Video anotado» y ver aparecer las cajas, las
    máscaras y el panel de resumen del mock.
  - **El selector de foco** deja una cámara sola y en grande, y permite saltar directo a
    otra sin volver a la grilla; la primera opción vuelve a mostrarlas todas.
  - **La barra lateral se colapsa** con la tira de la izquierda, que queda visible.
  - **El log de la barra lateral y la pestaña Logs son el logger real.** Todo lo que
    logue cualquier subsistema aparece ahí, desde su propio hilo, sin que la GUI se
    trabe: cómo se hace está en `_UiLogHandler`.
  - **Pestaña Hardware**: los números son de este equipo. Cargarlo y ver subir CPU y
    temperatura; los chips de red se arman con las interfaces que informa el monitor, no
    con eth0/eth1 fijos, y el eje de la gráfica de RAM se ajusta a la RAM real.
  - **Pestaña Modbus**: la tabla sale del `register_map.yaml` y los valores están
    codificados por `SCHEMA.encode_batch()`, con las escalas del esquema. La temperatura
    de la cámara aparece ×10 porque así viaja al PLC, y `camera_1_state_bitfield` es la
    palabra que arma `camera_health.pack()`. Los mismos registros se escriben en el
    datastore del servidor, así que se pueden leer desde afuera mientras la prueba corre:

        from pymodbus.client import ModbusTcpClient
        client = ModbusTcpClient("127.0.0.1", port=5502)
        client.connect()
        client.read_holding_registers(80, count=9).registers   # 81-89: salud del equipo

    El mapa es base-1 y el cliente habla en direcciones de PDU, que arrancan en 0: el
    registro 81 se pide como `address=80`.
  - **Los chips de la barra lateral no están simulados**: cada uno dice lo que informó su
    subsistema. Al arrancar, el de Modbus TCP pasa de «Iniciando» a «OK» un par de
    segundos después del bind —ese margen es del servidor, no de la pantalla—, el de RTU
    queda en «Deshabilitado» porque el config lo apaga, y los de telemetría también,
    porque los dos backends están apagados. Prender InfluxDB en la pestaña Telemetría y
    reiniciar la prueba es la forma de ver ese chip informar si conectó o no.
  - **La palabra de comunicaciones del registro 2 sale de los mismos estados que los
    chips**: si la pantalla dice que un canal está andando, su bit está prendido. Es a
    propósito que sea una sola fuente.
  - **Pestaña Video**: abrir en el navegador una de las URLs que muestra —son las del
    servidor que esta prueba levantó de verdad— y ver el contador de Clientes subir. Al
    cerrar la pestaña del navegador, bajar.
  - **Cambiar la configuración desde la pantalla**: editar cualquier campo, Guardar, y
    mirar la consola: imprime el diff contra lo que había en el archivo. Los cambios se
    escriben en el `config.yaml` de al lado. Descartar vuelve a los valores del archivo.
  - **El idioma y el tema se aplican sin reiniciar.** Pestaña Sistema → Idioma `en` o
    `pt` → Guardar: los textos que ya están en pantalla no se retraducen (se armaron al
    construirse), pero todo lo que se abre después sí, y el tema cambia entero al toque.
  - **El valor que no está entre las opciones de un combo no se pierde.** `model_2`
    tiene `type: yolov8_seg`, que no está registrado en la fábrica: la pestaña
    Inferencia lo muestra igual como opción elegida y guardar lo deja tal cual. Ese era
    un bug real: sin eso el combo caía en su primera opción y la escribía encima.
  - **Editar el `config.yaml` de al lado con la prueba corriendo**: al detectar el cambio
    se relee y el panel se recarga solo, así que no vuelve a escribir valores viejos.
  - **Guardar desde la pantalla borra los comentarios del `config.yaml`.** No es un bug de
    la UI: `ConfigManager.save()` reescribe el archivo entero desde el diccionario en
    memoria y el YAML no conserva los comentarios de la lectura. El de esta prueba está
    comentado y vale la pena recuperarlo con `git checkout manual_test/ui/config.yaml`
    después de probar el guardado. En un fork es lo mismo con el `config.yaml` de la app:
    la copia comentada es la que está en git.
  - **Herramienta de ROI**: pestaña Cámaras → Cinta entrada → «Herramienta interactiva de
    ROI». Se dibuja con el mouse sobre el frame en vivo; al guardar, el recuadro azul
    aparece en el panel de la grilla, que lee el ROI del config.
  - **El botón GPIO del header** aparece porque esta prueba inyecta un controlador doble
    —el módulo de GPIO todavía no existe en el repo—. Las salidas conmutan y las
    entradas se mueven solas cada dos segundos.
  - **Sin licencia instalada, el equipo abre igual.** Correr con `--simulate-no-license`
    para verlo: al abrir la ventana aparece un cartel, y el footer muestra un chip rojo
    «SIN LICENCIA» todo el tiempo que dure el estado. Sin el flag, corriendo desde
    fuentes, ninguno de los dos aparece —es el estado `unlicensed_build`, que no cuenta
    como inválido—, que es la corrida normal de esta prueba.
  - Ctrl+C en la consola, o cerrar la ventana, baja los hilos ordenadamente.
"""

import argparse
import io
import logging
import random
import signal
import sys
import threading
from pathlib import Path

import yaml

# La raíz del repo es el primer ancestro que contiene `system/`, no un número fijo de
# niveles: así el script sobrevive a que lo muevan de carpeta.
_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "system").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene system/.")
sys.path.insert(0, str(_REPO_ROOT))

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot        # noqa: E402
from PySide6.QtWidgets import QApplication                               # noqa: E402

from system.camera.capture_thread import CaptureThread                   # noqa: E402
from system.config_manager import ConfigManager                          # noqa: E402
from system.formats import camera_health, com_status, system_status       # noqa: E402
from system.inference.engine import InferenceThread                      # noqa: E402
from system.inference import annotations, metrics                        # noqa: E402
from system.inference.pipeline import BeltPipeline                       # noqa: E402
from system.inference.result import InferenceResult                      # noqa: E402
from system.license import manager as license_manager_module              # noqa: E402
from system.license.manager import LicenseManager                        # noqa: E402
from system.license.request import save_request                          # noqa: E402
from system.logger import logger                                         # noqa: E402
from system.modbus.registers import SCHEMA                               # noqa: E402
from system.modbus.server import SharedModbusServer                      # noqa: E402
from system.system_monitor import SystemMonitor                          # noqa: E402
from system.telemetry.persistence import PersistenceThread               # noqa: E402
from system.video.abstract_video_server import (                         # noqa: E402
    MODE_ANNOTATED, MODES, STATUS_ACTIVE, STATUS_DISABLED, STATUS_ERROR,
)
from system.video.http_server import HttpVideoServer                     # noqa: E402

from ui import service_status                                            # noqa: E402
from ui.main_window import MainWindow                                    # noqa: E402
from ui.widgets.camera_grid import CameraGrid                            # noqa: E402

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

_METRICS_INTERVAL_S = 1.0        # cada cuánto se pide una métrica de hardware nueva
_REGISTERS_INTERVAL_MS = 1000    # cada cuánto se escriben los registros y se refresca la tabla
_STATUS_INTERVAL_MS = 1000       # cada cuánto se releen los `status` de los subsistemas
_GPIO_INTERVAL_MS = 2000         # cada cuánto se mueven las entradas del GPIO doble
_CONFIG_WATCH_INTERVAL_MS = 1000 # cada cuánto se mira si cambió el config.yaml

_HEARTBEAT_MAX = 65535           # el registro es uint16: el contador da la vuelta ahí
_SERVER_STOP_TIMEOUT_S = 5.0     # espera al hilo del servidor Modbus al cerrar

_GPIO_INPUT_COUNT = 4
_GPIO_OUTPUT_COUNT = 4


# ── El logger real, puenteado a la UI ────────────────────────────────────────────

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
        # Sin try/except: si formatear falla, que se vea en la consola.
        self._bridge.message.emit(record.levelname, record.getMessage(), record.module)


# ── Monitor de hardware, fuera del hilo de la GUI ───────────────────────────────

class _MetricsPoller(QThread):
    """
    Pide las métricas de hardware en su propio hilo y las publica por señal.

    **No es un adorno: `get_metrics()` bloquea.** En Windows la primera llamada tarda
    ~2 s —la consulta WMI de las zonas térmicas y `nvidia-smi` arrancan un proceso cada
    una— y vuelve a tardar cada vez que se le vence el cacheo de temperatura. Llamarlo
    desde un QTimer del hilo de la GUI congela la ventana ese tiempo y retrasa la entrega
    de todas las señales en cola, así que el hilo de captura parece caído cuando lo único
    que pasó es que nadie procesó sus eventos.

    `main.py` tiene que hacer lo mismo. El módulo de monitoreo es sincrónico a propósito
    —así se testea sin Qt—: quien lo usa desde una GUI es el que pone el hilo.
    """

    metrics_ready = Signal(object)    # dict de get_metrics() — uno por intervalo

    def __init__(self, monitor: SystemMonitor, interval_s: float, parent=None):
        super().__init__(parent)
        self._monitor = monitor
        self._interval_s = interval_s

    def run(self):
        while not self.isInterruptionRequested():
            self.metrics_ready.emit(self._monitor.get_metrics())
            # Sleep interrumpible: `wait()` sobre un evento propio despertaría antes,
            # pero para una prueba manual alcanza con dormir en tramos cortos.
            for _ in range(int(self._interval_s * 10)):
                if self.isInterruptionRequested():
                    return
                self.msleep(100)


# ── Doble del controlador de GPIO ───────────────────────────────────────────────

class _FakeGpioController:
    """
    Controlador de GPIO de mentira, con la interfaz que espera `ui/dialogs/gpio_dialog.py`.

    Está acá y no en el repo porque el módulo de GPIO todavía no existe: cuando exista,
    esta clase se borra y se cablea el de verdad.
    """

    hardware_available = False
    input_count = _GPIO_INPUT_COUNT
    output_count = _GPIO_OUTPUT_COUNT

    def __init__(self):
        self._outputs = {number: 0 for number in range(1, _GPIO_OUTPUT_COUNT + 1)}

    def get_output_state(self, number: int) -> int:
        return self._outputs.get(number, 0)

    def set_output(self, number: int, state: int) -> bool:
        self._outputs[number] = int(state)
        logger.info(f"[GPIO] DO{number} -> {'ON' if state else 'OFF'}")
        return True


class _FakeGpioThread(QObject):
    """Emite entradas al azar cada `_GPIO_INTERVAL_MS`, como haría el hilo de polling."""

    inputs_updated = Signal(object)    # {número de DI: 0 | 1}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start(_GPIO_INTERVAL_MS)

    def _on_tick(self):
        self.inputs_updated.emit(
            {number: random.randint(0, 1) for number in range(1, _GPIO_INPUT_COUNT + 1)}
        )


# ── El cableado ─────────────────────────────────────────────────────────────────

class _DemoApp(QObject):
    """
    Hace de `main.py`: construye los subsistemas, la ventana, y los conecta.

    QObject y no una clase suelta a propósito: los slots que reciben señales de los
    hilos de captura y de inferencia tienen que ser de un QObject que viva en el hilo de
    la GUI, para que Qt los entregue en cola. Conectar una función suelta los ejecutaría
    en el hilo que emite, tocando widgets desde afuera del hilo de la GUI.
    """

    def __init__(self, config: ConfigManager, *, simulate_no_license: bool = False,
                 parent=None):
        super().__init__(parent)
        self._config = config
        self._capture_threads: list = []
        self._heartbeat = 0
        self._config_mtime_s = _CONFIG_PATH.stat().st_mtime
        self._config_snapshot = _read_config_file()

        # ── Ventana y área central ───────────────────────────────────────────
        self._window = MainWindow(config)
        self._grid = CameraGrid(config)
        self._window.monitor_view.set_content(self._grid)
        self._window.config_saved.connect(self._on_config_saved)

        # ── Licencia ─────────────────────────────────────────────────────────
        # Corriendo desde fuentes el estado es `unlicensed_build` y no se enforcea nada,
        # que es justamente lo que hay que poder ver en la pestaña.
        #
        # `--simulate-no-license` fuerza el marcador de build compilado para ver el
        # cartel de arranque y el chip rojo del footer sin compilar de verdad: no hay
        # `license.lic` al lado de este config, así que el estado que resulta es
        # `absent`. No es un atajo que salte una verificación —sigue siendo la firma la
        # que decide si un archivo real validaría—, sólo cambia si el subsistema mira.
        if simulate_no_license:
            license_manager_module.IS_COMPILED = True
        self._license = LicenseManager(config)
        self._window.update_license(self._license.get_status())
        self._window.license_export_requested.connect(self._on_license_export_requested)
        self._window.license_install_requested.connect(self._on_license_install_requested)

        # ── Log real hacia la UI ─────────────────────────────────────────────
        self._log_bridge = _LogBridge(self)
        self._log_bridge.message.connect(self._window.log_event)
        self._log_handler = _UiLogHandler(self._log_bridge)
        logger.addHandler(self._log_handler)

        # ── Servidor HTTP de video ───────────────────────────────────────────
        # Se levanta de verdad: es lo que hace reales las URLs y el contador de
        # clientes de la pestaña Video.
        self._http_video = HttpVideoServer(config)
        self._http_video.start()

        # ── Servidor Modbus ──────────────────────────────────────────────────
        # También de verdad, y por dos razones: los chips de TCP y RTU dicen lo que
        # pasó al levantar cada transporte, y los registros que muestra la pestaña se
        # escriben en el datastore, así que un poller Modbus los puede leer.
        # `run()` bloquea, así que va en su propio hilo, como en manual_test/modbus/.
        self._modbus = SharedModbusServer(config)
        self._modbus_thread = threading.Thread(
            target=self._modbus.run, name="modbus-ui-demo", daemon=True
        )

        # ── Telemetría ───────────────────────────────────────────────────────
        # El hilo arma sus backends y cada uno deja su estado en `backend_status()`.
        # Con los dos apagados en el config quedan en `disabled`, que es lo que los
        # chips tienen que decir; prendiendo InfluxDB en la pestaña Telemetría y
        # reiniciando, el mismo chip pasa a informar si conectó o no.
        self._telemetry = PersistenceThread(config)

        # ── Inferencia ───────────────────────────────────────────────────────
        # Un hilo por pipeline habilitado. `annotate_gate` atado al combo de la
        # grilla: si nadie mira el anotado, el motor no lo dibuja.
        self._engines: list = []
        for pipeline_slot in (config.get("inference.pipelines", {}) or {}):
            if not config.get(f"inference.pipelines.{pipeline_slot}.enabled", True):
                logger.info(f"[Demo] {pipeline_slot} deshabilitado: no se le levanta hilo.")
                continue
            engine = InferenceThread(
                config, BeltPipeline(config, pipeline_slot),
                analyzer=metrics.build_analyzer(
                    class_names=config.get(
                        "inference.models.segmenter.class_names", []) or [],
                    belt_roi_px=config.get("process.belt_roi_px", {}) or {},
                    dark_background_threshold=config.get(
                        "process.dark_background_threshold", {}) or {}),
                annotator=annotations.chain(
                    annotations.belt_roi_annotator(
                        config.get("process.belt_roi_px", {}) or {}),
                    annotations.composition_panel_annotator(
                        config.get("inference.models.segmenter.class_names", []) or [])),
                annotate_gate=self._is_annotated_watched,
            )
            engine.result_ready.connect(self._on_result_ready)
            self._engines.append(engine)

        # ── Captura ──────────────────────────────────────────────────────────
        for camera_slot in (config.get("cameras", {}) or {}):
            thread = CaptureThread(config, camera_slot)
            thread.frame_ready.connect(self._on_frame_ready)
            # Tres consumidores del mismo status y ninguno sabe de los otros: los chips
            # del panel de esa cámara, el de FPS del diagnóstico, y la palabra de estado
            # que va al PLC.
            thread.status_updated.connect(self._grid.update_status)
            thread.status_updated.connect(
                self._window.diagnostics_view.update_camera_status
            )
            thread.status_updated.connect(self._on_camera_status)
            self._capture_threads.append(thread)

        # ── GPIO doble ───────────────────────────────────────────────────────
        self._gpio = _FakeGpioController()
        self._gpio_thread = _FakeGpioThread(self)
        self._window.set_gpio(self._gpio, self._gpio_thread)

        # ── Monitor de hardware y timers ─────────────────────────────────────
        self._monitor = SystemMonitor(config)
        self._metrics_poller = _MetricsPoller(self._monitor, _METRICS_INTERVAL_S)
        self._metrics_poller.metrics_ready.connect(self._on_metrics_ready)
        self._last_metrics: dict = {}
        self._camera_status: dict[str, dict] = {}
        self._camera_illumination: dict[str, int] = {}
        self._timers = [
            self._start_timer(_REGISTERS_INTERVAL_MS, self._on_registers_tick),
            self._start_timer(_STATUS_INTERVAL_MS, self._on_status_tick),
            self._start_timer(_CONFIG_WATCH_INTERVAL_MS, self._on_config_watch_tick),
        ]

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    def start(self):
        self._modbus_thread.start()
        self._telemetry.start()
        self._metrics_poller.start()
        for engine in self._engines:
            engine.start()
        for thread in self._capture_threads:
            thread.start()
        self._window.showMaximized()   # como la app de verdad
        logger.info(
            f"[Demo] {len(self._capture_threads)} cámaras, {len(self._engines)} pipeline(s). "
            f"Config: {_CONFIG_PATH.name}"
        )

    def stop(self):
        """Baja timers, hilos y servidores. Idempotente: el cierre puede llegar dos veces."""
        for timer in self._timers:
            timer.stop()
        logger.removeHandler(self._log_handler)
        qt_threads = (self._capture_threads + self._engines
                      + [self._metrics_poller, self._telemetry])
        for thread in qt_threads:
            thread.requestInterruption()
        for thread in qt_threads:
            thread.wait(3000)
        self._http_video.stop()
        self._modbus.stop()
        self._modbus_thread.join(timeout=_SERVER_STOP_TIMEOUT_S)
        if self._modbus_thread.is_alive():
            print(f"El hilo del Modbus no cerró en {_SERVER_STOP_TIMEOUT_S:.0f} s.")
        print("\nHilos y servidores detenidos.")

    # ── Slots de los hilos ───────────────────────────────────────────────────

    @Slot(object, str)
    def _on_frame_ready(self, frame_bgr, camera_slot: str):
        """
        Un frame nuevo de una cámara: a la grilla, a la inferencia y al stream crudo.

        En modo crudo la grilla lo muestra tal cual; en modo anotado espera el que
        devuelve el motor, que es el mismo array con el resultado dibujado.
        """
        if self._grid.mode != MODE_ANNOTATED:
            self._grid.update_frame(frame_bgr, camera_slot)
        self._http_video.push_raw(camera_slot, frame_bgr)
        for engine in self._engines:
            engine.push_frame(frame_bgr, camera_slot)

    @Slot(object, str)
    def _on_camera_status(self, status: dict, camera_slot: str):
        self._camera_status[camera_slot] = dict(status)

    @Slot(object)
    def _on_result_ready(self, result: InferenceResult):
        """Un resultado del motor: el frame anotado a la grilla y al stream anotado."""
        self._camera_illumination[result.camera_slot] = result.illumination_pct
        self._grid.set_illumination_pct(result.camera_slot, result.illumination_pct)
        # Telemetría: encola y vuelve, sin tocar la red. Con los backends apagados el
        # punto se descarta; prendiendo InfluxDB en el config, sale de verdad.
        self._telemetry.push_data(
            "inference",
            {"confidence_pct": result.confidence_pct,
             "detection_count": result.detection_count,
             "illumination_pct": result.illumination_pct,
             **result.metrics},
            tags={"camara_id": result.camera_slot, "pipeline": result.pipeline_slot},
        )
        if result.annotated_bgr is None:
            return
        if self._grid.mode == MODE_ANNOTATED:
            self._grid.update_frame(result.annotated_bgr, result.camera_slot)
        self._http_video.push_annotated(result.camera_slot, result.annotated_bgr)

    def _is_annotated_watched(self, camera_slot: str) -> bool:
        """
        Gate del anotado: se dibuja si lo mira la grilla o un cliente del stream.

        Es la firma que espera el motor —`(camera_slot) -> bool`— y la razón por la que
        el overlay no se gasta cuando nadie lo está viendo.
        """
        return (self._grid.mode == MODE_ANNOTATED
                or self._http_video.has_annotated_clients(camera_slot))

    # ── Timers ───────────────────────────────────────────────────────────────

    @Slot(object)
    def _on_metrics_ready(self, metrics: dict):
        """Slot de `_MetricsPoller`: llega ya medido, desde el hilo del poller."""
        self._last_metrics = metrics
        self._window.diagnostics_view.update_hardware_metrics(metrics)

    def _on_registers_tick(self):
        """
        Llena la tabla de registros como lo haría `main.py`: valores físicos al esquema.

        Ninguna dirección se escribe acá: se piden por nombre y las escalas las aplica
        `SCHEMA.encode_batch()`. Los bitfields los arman sus propios módulos.
        """
        # La última medición del poller, no una nueva: `get_metrics()` bloquea y esto
        # corre en el hilo de la GUI. Hasta que llegue la primera, no hay nada que poner.
        metrics = self._last_metrics
        if not metrics:
            return
        self._heartbeat = (self._heartbeat + 1) % _HEARTBEAT_MAX
        statuses = self._get_service_statuses()

        values = {
            "heartbeat": self._heartbeat,
            "cpu_usage_pct": metrics["cpu_usage_pct"],
            "gpu_usage_pct": metrics["gpu_usage_pct"],
            "ram_used_mb": metrics["ram_used_mb"],
            "ram_total_mb": metrics["ram_total_mb"],
            "disk_free_gb": metrics["disk_free_gb"],
            "cpu_temp_c": metrics["cpu_temp_c"],
            "gpu_temp_c": metrics["gpu_temp_c"],
            "power_w": metrics["power_w"],
            "system_status_bitfield": system_status.pack(
                model_loaded=bool(self._engines),
                inference_error=False,
                fallback_config=self._config.is_using_fallback,
                dead_thread=any(not t.isRunning() for t in self._capture_threads),
                license_invalid=self._license.should_report_invalid(),
            ),
            # Los seis canales, cada uno con el estado que informó su dueño: es la
            # misma fuente que alimenta los chips de la pantalla, así que el operador y
            # el PLC no pueden estar viendo cosas distintas.
            "com_status_bitfield": com_status.pack(
                rtsp_status=statuses[service_status.SERVICE_VIDEO_RTSP],
                http_video_status=statuses[service_status.SERVICE_VIDEO_HTTP],
                influxdb_status=statuses[service_status.SERVICE_INFLUXDB],
                mqtt_status=statuses[service_status.SERVICE_MQTT],
                modbus_tcp_status=statuses[service_status.SERVICE_MODBUS_TCP],
                modbus_rtu_status=statuses[service_status.SERVICE_MODBUS_RTU],
            ),
        }

        # La primera cámara es la que el mapa del template publica: el resto de las
        # filas las agrega el fork cuando las necesita.
        first_slot = next(iter(self._config.get("cameras", {}) or {}), None)
        status = self._camera_status.get(first_slot, {})
        if status:
            values["camera_1_state_bitfield"] = camera_health.pack(
                connected=bool(status.get("connected")),
                config_error=status.get("error"),
                fps_estimated=float(status.get("fps_estimated", 0.0)),
                capture_enabled=bool(status.get("capture_enabled", True)),
                is_synthetic=True,
            )
            values["camera_1_temperature_c"] = status.get("temperature", 0.0)
            values["camera_1_fps"] = status.get("fps_estimated", 0.0)
        values["camera_1_illumination_pct"] = self._camera_illumination.get(first_slot, 0)

        # Un solo `encode_batch` para los dos consumidores: el datastore que lee el PLC
        # y la tabla que mira el operador. Si se codificara dos veces, podrían
        # divergir.
        registers = SCHEMA.encode_batch(values)
        self._modbus.update_block(registers)
        self._window.diagnostics_view.update_modbus_values(registers)

    def _on_status_tick(self):
        """
        Relee el `status` de cada subsistema y lo publica. Nada simulado.

        Los subsistemas no emiten señal cuando cambian de estado: exponen una property
        que se consulta. Por eso esto es un timer y no un slot, y por eso está todo en
        un solo lugar —el mismo que va a tener `main.py`— en vez de repartido.
        """
        for service, status in self._get_service_statuses().items():
            self._window.set_service_status(service, status)

        self._window.set_client_count(
            service_status.SERVICE_VIDEO_HTTP, self._http_video.get_client_count()
        )
        self._window.set_stream_urls(
            service_status.SERVICE_VIDEO_HTTP, self._build_http_urls()
        )

    def _get_service_statuses(self) -> dict:
        """
        Estado de los seis canales, cada uno preguntado a su dueño.

        El RTSP es el único que no se levanta en esta prueba —necesita GStreamer—: se
        informa `disabled` si el config lo apaga, y si está prendido se dice que no
        levantó, que es la verdad de esta corrida.
        """
        rtsp_status = (STATUS_DISABLED if not self._config.get("video.rtsp.enabled", False)
                       else STATUS_ERROR)
        return {
            service_status.SERVICE_MODBUS_TCP: self._modbus.tcp_status,
            service_status.SERVICE_MODBUS_RTU: self._modbus.rtu_status,
            service_status.SERVICE_VIDEO_HTTP: self._http_video.status,
            service_status.SERVICE_VIDEO_RTSP: rtsp_status,
            service_status.SERVICE_INFLUXDB: self._telemetry.backend_status("influxdb"),
            service_status.SERVICE_MQTT: self._telemetry.backend_status("mqtt"),
        }

    def _on_config_watch_tick(self):
        """Relee el config si el archivo cambió por afuera y recarga el panel."""
        try:
            mtime_s = _CONFIG_PATH.stat().st_mtime
        except OSError:
            return
        if mtime_s == self._config_mtime_s:
            return
        self._config_mtime_s = mtime_s
        self._config.load()
        self._config_snapshot = _read_config_file()
        self._window.reload_config_view()
        logger.info(f"[Demo] {_CONFIG_PATH.name} cambió por afuera: releído y panel recargado.")

    # ── Licencia ─────────────────────────────────────────────────────────────
    # Los dos slots hacen lo mismo que hará `main.py`: la pantalla eligió una ruta y el
    # cableado es el que toca el disco.

    @Slot(str)
    def _on_license_export_requested(self, path: str):
        try:
            written = save_request(self._config, path)
        except OSError as e:
            self._window.show_license_result(False, f"No se pudo escribir: {e}")
            return
        self._window.show_license_result(True, f"Solicitud escrita en {written}")

    @Slot(str)
    def _on_license_install_requested(self, path: str):
        is_installed, message = self._license.install(path)
        self._window.update_license(self._license.get_status())
        self._window.show_license_result(is_installed, message)

    # ── Configuración guardada desde la pantalla ─────────────────────────────

    def _on_config_saved(self):
        """Imprime en consola qué cambió, que es lo que interesa al probar el panel."""
        self._config_mtime_s = _CONFIG_PATH.stat().st_mtime
        current = _read_config_file()
        changes = _diff_config(self._config_snapshot, current)
        self._config_snapshot = current

        print(f"\n-- {_CONFIG_PATH.name} guardado --")
        if not changes:
            print("   sin cambios respecto de lo que había en el archivo")
        for path, old, new in changes:
            print(f"   {path}: {old!r} -> {new!r}")
        print()

    # ── Internos ─────────────────────────────────────────────────────────────

    def _build_http_urls(self) -> list[str]:
        """
        Las URLs del servidor HTTP, armadas como las armaría `main.py`.

        Se componen acá y no en la UI a propósito: la forma de la ruta es del servidor
        de video, y la vista sólo muestra la lista que le pasan. El RTSP ya las publica
        con `get_stream_urls()`; el HTTP todavía no, así que las arma quien cablea.
        """
        if self._http_video.status != STATUS_ACTIVE:
            return []
        port = int(self._config.get("video.http.port", 8091))
        return [
            f"http://127.0.0.1:{port}/{camera_slot}/{mode}"
            for camera_slot in (self._config.get("cameras", {}) or {})
            for mode in MODES
        ]

    def _start_timer(self, interval_ms: int, slot) -> QTimer:
        timer = QTimer(self)
        timer.timeout.connect(slot)
        timer.start(interval_ms)
        return timer


def _read_config_file() -> dict:
    """
    El config.yaml tal como está escrito en el disco.

    Se lee el archivo y no el ConfigManager porque el diff que interesa es contra lo que
    estaba guardado, y porque `get()` entrega ramas y no el árbol entero.
    """
    try:
        with io.open(_CONFIG_PATH, encoding="utf-8") as config_file:
            return yaml.safe_load(config_file) or {}
    except (OSError, yaml.YAMLError) as error:
        logger.error(f"[Demo] No se pudo leer {_CONFIG_PATH}: {error}")
        return {}


def _diff_config(before: dict, after: dict, prefix: str = "") -> list:
    """Diferencias entre dos configs, como [(ruta punteada, antes, después), ...]."""
    changes = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in before:
                changes.append((path, "<nueva>", after[key]))
            elif key not in after:
                changes.append((path, before[key], "<borrada>"))
            else:
                changes += _diff_config(before[key], after[key], path)
        return changes
    if before != after:
        changes.append((prefix, before, after))
    return changes


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prueba manual de la interfaz completa.")
    parser.add_argument(
        "--simulate-no-license", action="store_true",
        help="fuerza el build como compilado para ver el cartel y el chip de licencia "
             "sin licencia instalada, sin compilar de verdad.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not _CONFIG_PATH.exists():
        print(f"Falta {_CONFIG_PATH}.")
        return 2

    app = QApplication(sys.argv)
    config = ConfigManager(str(_CONFIG_PATH))

    demo = _DemoApp(config, simulate_no_license=args.simulate_no_license)
    app.aboutToQuit.connect(demo.stop)
    demo.start()

    # Ctrl+C en la consola con el event loop de Qt corriendo: sin esto la señal queda
    # esperando a que Qt devuelva el control, que con la ventana abierta no pasa.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    interrupt_timer = QTimer()
    interrupt_timer.timeout.connect(lambda: None)
    interrupt_timer.start(200)

    print(f"Ventana abierta. Config: {_CONFIG_PATH}")
    print("Ctrl+C acá, o cerrar la ventana, para terminar.\n")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
