"""Tests del cableado: la salida del proceso y el manejo de señales.

`main.py` no lo importa nadie, así que lo poco testeable de él es lo que decide sin
depender de subsistemas. Acá no se arma la aplicación ni se levanta un event loop."""

import asyncio
import signal
import threading
import time

import numpy as np
import pytest

import main
from system.camera.lens_health import (
    CONDITION_KEYS, LensHealthMonitor, Measurement, Region,
)
from system import gpio_control
from system.formats import gpio_status, pipeline_status
from system.inference import pipeline_factory, rolling
from system.inference.abstract_pipeline import AbstractPipeline
from system.inference.hooks import EngineHooks
from system.inference.result import InferenceResult
from system.modbus import registers
from system.modbus.server import STATUS_DISABLED, STATUS_ERROR

_EXIT_CODE = 3


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = dict(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class TestExit:
    def test_by_default_it_returns_instead_of_killing_the_process(self):
        """Matar el proceso saltea los `atexit`: no puede ser el default."""
        assert main._exit(_EXIT_CODE, _MockConfig()) == _EXIT_CODE

    def test_hard_exit_terminates_without_unwinding(self, monkeypatch):
        killed = []
        monkeypatch.setattr(main.os, "_exit", killed.append)
        main._exit(_EXIT_CODE, _MockConfig(**{"system.hard_exit": True}))
        assert killed == [_EXIT_CODE]

    def test_the_log_is_flushed_either_way(self, monkeypatch):
        """Lo último que se escribe es lo que explica por qué se cerró."""
        calls = []
        monkeypatch.setattr(main.logging, "shutdown", lambda: calls.append("shutdown"))
        monkeypatch.setattr(main.os, "_exit", lambda code: calls.append("exit"))
        main._exit(_EXIT_CODE, _MockConfig(**{"system.hard_exit": True}))
        assert calls == ["shutdown", "exit"]


class TestUnlicensedCamera:
    """El status que el cableado publica por una cámara que la licencia deja afuera."""

    def test_it_uses_the_stable_keys_of_a_driver(self):
        # Lo consumen la grilla, el bitfield que va al PLC y la serie de telemetría, y
        # ninguno pregunta de dónde salió el status. Una clave de menos acá es un KeyError
        # en el tick de telemetría, lejos de donde se escribió.
        assert set(main._UNLICENSED_CAMERA_STATUS) >= {
            "connected", "capture_enabled", "temperature", "fps_estimated"}

    def test_it_reads_as_a_camera_that_cannot_operate(self):
        status = main._UNLICENSED_CAMERA_STATUS
        assert status["connected"] is False
        assert status["capture_enabled"] is False

    def test_it_says_why(self):
        """Sin motivo, la pantalla muestra una cámara caída y mandan a revisar un cable."""
        assert main._UNLICENSED_CAMERA_STATUS.get("error")


class TestUnlicensedCameraOnScreen:
    """
    Que la cámara fuera de cupo llegue al widget del monitor.

    El status ya viaja por `_on_camera_status()`, que alimenta los chips de la UI, el FPS
    del diagnóstico y la palabra que va al PLC. Lo que faltaba es el widget del área
    central: se alimenta conectando la señal de un `CaptureThread`, y esta cámara no tiene
    ninguno. Sin este empujón el chip se queda con el placeholder de arranque para
    siempre, y una cámara que dice «Iniciando» y no arranca nunca manda a revisar un cable
    que está bien.
    """

    class _Content:
        """Widget del monitor que acepta status, que es lo único que se le pide."""

        def __init__(self):
            self.updates: list = []

        def update_status(self, status: dict, camera_slot: str):
            self.updates.append((camera_slot, status))

    class _Ui:
        def __init__(self, content):
            self.monitor_content = content

    def _application(self, content):
        app = main.Application.__new__(main.Application)
        app._ui = self._Ui(content)
        return app

    def test_the_status_reaches_the_monitor_widget(self):
        content = self._Content()
        self._application(content)._push_status_to_content(
            main._UNLICENSED_CAMERA_STATUS, "camera_3")
        assert content.updates == [("camera_3", main._UNLICENSED_CAMERA_STATUS)]

    def test_a_widget_without_the_method_is_not_a_crash(self):
        """El fork elige el widget del centro y no está obligado a aceptar status."""
        content = object()
        self._application(content)._push_status_to_content({}, "camera_3")

    def test_headless_has_no_widget_to_push_to(self):
        """Sin ventana `monitor_content` es None, y el cableado no pregunta si la hay."""
        self._application(None)._push_status_to_content({}, "camera_3")


class TestInterruptSignals:
    def test_ctrl_c_and_sigterm_are_always_handled(self):
        assert {signal.SIGINT, signal.SIGTERM} <= set(main._interrupt_signals())

    def test_ctrl_break_is_handled_where_it_exists(self):
        """ONLY_WINDOWS: sin atenderlo el proceso muere sin ejecutar un paso del cierre."""
        expected = hasattr(signal, "SIGBREAK")
        handled = any(getattr(s, "name", "") == "SIGBREAK" for s in main._interrupt_signals())
        assert handled is expected

    def test_the_handler_schedules_the_quit_instead_of_running_it(self, monkeypatch):
        """
        El handler puede correr con un lock tomado, y en Qt 6 `quit()` corre `stop()` en el
        acto: cerrar desde ahí trababa el cierre con hilos que pedían ese lock.
        """
        scheduled, quits = [], []

        class FakeTimer:
            @staticmethod
            def singleShot(interval_ms: int, slot: object):
                scheduled.append((interval_ms, slot))

        class FakeApp:
            def quit(self):
                quits.append(True)

        app = FakeApp()
        monkeypatch.setattr(main, "QTimer", FakeTimer)
        main._build_interrupt_handler(app)(signal.SIGTERM, None)
        assert quits == []
        assert scheduled == [(0, app.quit)]


class TestModbusServerWiring:
    """
    Que el motivo por el que el mapa no cargó llegue al servidor.

    `registers.py` sólo lo detecta y lo deja en `LOAD_ERROR`; quien decide es el
    cableado. Si no se lo pasa, los dos transportes toman su puerto y sirven el datastore
    vacío —un PLC leyendo ceros, indistinguible de «no hay medición»—, que es justo lo que
    `registers.py` existe para evitar. Que el servidor sepa negarse ya está probado en
    `test/modbus/test_server.py`; lo que se prueba acá es que se entere.
    """

    _REASON = "No se pudo cargar register_map.yaml: [Errno 2] No such file or directory"

    class _MockConfig:
        """ConfigManager mínimo, con lo que el servidor le pide al construirse.

        Los dos transportes van apagados: el bloqueo le gana al `disabled`, así que la
        diferencia se ve igual y ningún test de esta clase toma un puerto de verdad.
        """

        def __init__(self):
            self._values = {
                "modbus.slave_id": 1,
                "modbus.tcp": {"enabled": False},
                "modbus.rtu": {"enabled": False},
            }

        def get(self, key: str, default: object = None) -> object:
            return self._values.get(key, default)

    def test_without_a_map_the_server_refuses_to_serve(self, monkeypatch, caplog):
        monkeypatch.setattr(main, "REGISTER_MAP_ERROR", self._REASON)
        server = main._build_modbus_server(self._MockConfig())
        with caplog.at_level("ERROR"):
            asyncio.run(server.run_tcp_server())
        assert server.tcp_status == STATUS_ERROR
        assert self._REASON in caplog.text

    def test_with_the_map_loaded_nothing_blocks_it(self, monkeypatch):
        """
        El caso normal, mirado donde se distingue: apagado por config el estado es
        `disabled`. Si el bloqueo se disparara igual sería `error`, que es otra cosa y
        manda a mirar otro archivo.
        """
        monkeypatch.setattr(main, "REGISTER_MAP_ERROR", "")
        server = main._build_modbus_server(self._MockConfig())
        asyncio.run(server.run_tcp_server())
        assert server.tcp_status == STATUS_DISABLED

    def test_it_passes_the_error_that_registers_reports(self):
        """
        No una copia ni un texto propio: el mismo valor. Un segundo vocabulario de «no
        cargó el mapa» sería otra cosa más que mantener de acuerdo.
        """
        assert main.REGISTER_MAP_ERROR is registers.LOAD_ERROR


def _belt_result(pellet_pct: float, fines_pct: float, load_pct: float) -> InferenceResult:
    return InferenceResult(camera_slot="camera_1",
                           metrics={"pct_pellet_norm": pellet_pct,
                                    "pct_desmenuzado_norm": fines_pct,
                                    "pct_carga": load_pct})


class TestInferenceFields:
    """
    Los fields de un punto de telemetría de inferencia.

    Los nombres son los que ya consulta el dashboard de este equipo, así que lo que se
    cuida acá es que no se muevan: ver el bloque de measurements de `main.py`.
    """

    def _cycle_result(self) -> InferenceResult:
        """Lo que emite el scheduler al cerrar un ciclo: la media y su dispersión."""
        return InferenceResult(
            camera_slot="camera_1",
            metrics={"load_pct": 50.0, "load_pct_std": 10.0, "load_pct_min": 40.0,
                     "load_pct_max": 60.0, "sample_count": 5},
        )

    def test_the_cycle_dispersion_travels_untouched(self):
        fields = main._build_inference_fields([self._cycle_result()])
        assert (fields["load_pct_std"], fields["load_pct_min"],
                fields["load_pct_max"]) == (10.0, 40.0, 60.0)

    def test_nothing_gets_a_second_round_of_suffixes(self):
        """`load_pct_max_max` es el síntoma de agregar lo ya agregado."""
        fields = main._build_inference_fields([self._cycle_result()])
        assert not [name for name in fields if name.endswith(("_max_max", "_min_min",
                                                              "_std_std", "_mean"))]

    def test_the_frame_count_is_how_many_results_entered(self):
        """El dashboard lo lee como `frames` desde la primera versión del equipo."""
        fields = main._build_inference_fields([self._cycle_result()])
        assert (fields["sample_count"], fields["frames"]) == (5, 1)

    def test_the_process_metrics_keep_their_own_names(self):
        """Sin sufijo: un `_mean` dejaría los paneles existentes sin serie que consultar."""
        results = [InferenceResult(camera_slot="camera_1", metrics={"pct_carga": value})
                   for value in (40.0, 60.0)]
        fields = main._build_inference_fields(results)
        assert fields["pct_carga"] == 50.0 and "pct_carga_mean" not in fields

    def test_class_percentages_stay_integers(self):
        """InfluxDB fija el tipo del campo con el primer punto: el bucket ya los tiene int."""
        results = [InferenceResult(camera_slot="camera_1", metrics={"pct_pellet": value})
                   for value in (40, 62)]
        assert main._build_inference_fields(results)["pct_pellet"] == 51

    def test_the_composition_is_an_integer_too(self):
        """La versión anterior redondeaba también la composición y la carga."""
        results = [InferenceResult(camera_slot="camera_1",
                                   metrics={"pct_pellet_norm": 33.7, "pct_carga": 50.0})]
        assert main._build_inference_fields(results)["pct_pellet_norm"] == 34

    def test_the_composition_of_a_second_adds_up_to_100_with_empty_frames(self):
        """Un frame vacío no aporta un 0 a cada clase: la cinta no cambió en ese segundo."""
        results = [_belt_result(0.0, 0.0, 0.0)] * 5 + [_belt_result(70.0, 30.0, 60.0)] * 10
        fields = main._build_inference_fields(results)
        assert (fields["pct_pellet_norm"], fields["pct_desmenuzado_norm"]) == (70, 30)

    def test_the_load_of_a_second_still_counts_the_empty_frames(self):
        results = [_belt_result(0.0, 0.0, 0.0)] * 5 + [_belt_result(70.0, 30.0, 60.0)] * 10
        assert main._build_inference_fields(results)["pct_carga"] == 40

    def test_the_composition_of_a_second_is_weighted_by_load(self):
        """Σ píxeles por clase sobre Σ detectados: un frame casi vacío no pesa como uno lleno."""
        results = [_belt_result(100.0, 0.0, 10.0), _belt_result(0.0, 100.0, 90.0)]
        fields = main._build_inference_fields(results)
        assert (fields["pct_pellet_norm"], fields["pct_desmenuzado_norm"]) == (10, 90)

    def test_a_second_without_material_publishes_the_composition_as_zero(self):
        """Como la versión anterior: el que dice que no hubo material es `pct_carga`."""
        fields = main._build_inference_fields([_belt_result(0.0, 0.0, 0.0)] * 3)
        assert (fields["pct_pellet_norm"], fields["pct_desmenuzado_norm"]) == (0, 0)

    def test_confidence_and_illumination_use_their_legacy_names(self):
        results = [InferenceResult(camera_slot="camera_1", confidence_pct=87.0,
                                   illumination_pct=42, inference_time_ms=12.5)]
        fields = main._build_inference_fields(results)
        assert (fields["confianza"], fields["iluminacion"]) == (87, 42)

    def test_the_scalars_are_not_published_twice(self):
        """El analyzer los re-exporta como métricas sólo para que lleguen al PLC."""
        results = [InferenceResult(camera_slot="camera_1", inference_time_ms=12.5,
                                   metrics={"confidence_pct": 87.0,
                                            "inference_time_ms": 99.0})]
        fields = main._build_inference_fields(results)
        assert "confidence_pct" not in fields and fields["inference_time_ms"] == 12.5

    def test_a_discarded_result_is_counted_with_its_reason(self):
        results = [InferenceResult(camera_slot="camera_1", is_valid=False,
                                   invalid_reason="dark_frame")]
        fields = main._build_inference_fields(results)
        assert (fields["invalid_count"], fields["invalid_reason"]) == (1, "dark_frame")

    def test_confidence_only_averages_the_reliable_ones(self):
        results = [InferenceResult(camera_slot="camera_1", confidence_pct=90.0),
                   InferenceResult(camera_slot="camera_1", confidence_pct=10.0,
                                   is_valid=False)]
        assert main._build_inference_fields(results)["confianza"] == 90


def _build_rolling_application():
    """`Application` sin `__init__`, con sólo las ventanas de la hora."""
    app = main.Application.__new__(main.Application)
    app._rolling_load = rolling.RollingMean(3600.0)
    app._rolling_weighted = {name: rolling.RollingMean(3600.0)
                             for name in main._ROLLING_COMPOSITION_NAMES}
    app._rolling_material = rolling.RollingMean(3600.0)
    return app


class _FrameSink:
    """Motor o servidor de video: registra lo que le empujan."""

    def __init__(self, pipeline_slot: str = "", *, raw_clients: bool = False,
                 cameras: tuple = (), annotated_clients: bool = False):
        self.pipeline_slot = pipeline_slot
        self.raw_clients = raw_clients
        self.annotated_clients = annotated_clients
        self._cameras = cameras
        self.pushed: list = []
        self.annotated: list = []

    def has_annotated_clients(self, camera_slot: str) -> bool:
        return self.annotated_clients

    def push_annotated(self, camera_slot, frame_bgr):
        self.annotated.append((camera_slot, frame_bgr))

    def has_camera(self, camera_slot: str) -> bool:
        return not self._cameras or camera_slot in self._cameras

    def push_frame(self, frame_bgr, camera_slot, ignore_interval=False):
        self.pushed.append((frame_bgr, camera_slot, ignore_interval))

    def has_raw_clients(self, camera_slot: str) -> bool:
        return self.raw_clients

    def push_raw(self, camera_slot, frame_bgr):
        self.pushed.append((frame_bgr, camera_slot))


class _CycleStub:
    """El scheduler: qué pipelines miden de a varios frames, y el permiso siempre en sí."""

    def __init__(self, *cycled: str):
        self._cycled = cycled

    def is_cycled(self, pipeline_slot: str) -> bool:
        return pipeline_slot in self._cycled

    def take_frame(self, camera_slot: str, pipeline_slot: str) -> bool:
        return True


class _ContentSpy:
    mode = "raw"

    def __init__(self):
        self.shown: list = []

    def update_frame(self, frame_bgr, camera_slot):
        self.shown.append(frame_bgr)


def _build_frame_application(*, engines=(), cycled=(), raw_clients=False, content=None,
                             gates=None, scheduler=None, annotated_clients=False):
    """`Application` sin `__init__` —sí con su QObject— y sólo lo que toca el camino del frame."""
    import types
    from PySide6.QtCore import QObject
    app = main.Application.__new__(main.Application)
    QObject.__init__(app)
    app._engines = list(engines)
    app._scheduler = scheduler or _CycleStub(*cycled)
    app._frame_gates = dict(gates or {})
    app._frame_signals = main.FrameSignals()
    app._gate_open_by_pair = {}
    app._gate_warned_s = {}
    app._gates_lock = threading.Lock()
    app._composer = main.FrameComposer()
    app._last_annotated = {}
    app._http_video = _FrameSink(raw_clients=raw_clients, annotated_clients=annotated_clients)
    app._rtsp_video = _FrameSink()
    app._display_adjust = None
    app._last_raw_frame = {}
    app._gui_frames = {}
    app._gui_frames_lock = threading.Lock()
    app._ui = types.SimpleNamespace(monitor_content=content)
    app.notified = []
    app._frame_for_gui.connect(app.notified.append)
    return app


class TestFramePath:
    """
    El frame de cámara no pasa entero por la GUI: la inferencia y el video crudo lo toman
    en el hilo de captura, y la GUI recibe sólo el último de cada cámara cuando puede.
    """

    def test_an_uncycled_engine_gets_the_frame_in_the_capture_thread(self):
        engine = _FrameSink("pipeline_1")
        app = _build_frame_application(engines=[engine])
        frame = np.zeros((4, 4, 3), np.uint8)
        app._on_frame_captured(frame, "camera_1")
        assert engine.pushed == [(frame, "camera_1", False)]

    def test_an_engine_only_gets_its_own_cameras(self):
        """Dos pipelines repartiéndose cámaras: un frame ajeno ni se le ofrece."""
        first = _FrameSink("pipeline_1", cameras=("camera_1",))
        second = _FrameSink("pipeline_2", cameras=("camera_2",))
        app = _build_frame_application(engines=[first, second])
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_2")
        assert first.pushed == []
        assert [camera_slot for _, camera_slot, _ in second.pushed] == ["camera_2"]

    def test_a_cycled_engine_waits_for_the_gui(self):
        """Su permiso lo maneja el scheduler, que no es thread-safe."""
        engine = _FrameSink("pipeline_1")
        app = _build_frame_application(engines=[engine], cycled=["pipeline_1"])
        frame = np.zeros((4, 4, 3), np.uint8)
        app._on_frame_captured(frame, "camera_1")
        assert engine.pushed == []
        app._on_frame_for_gui("camera_1")
        assert engine.pushed == [(frame, "camera_1", True)]

    def test_the_raw_stream_gets_the_frame_only_with_clients(self):
        app = _build_frame_application()
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        assert app._http_video.pushed == []
        app._http_video.raw_clients = True
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        assert len(app._http_video.pushed) == 1

    def test_the_gui_is_notified_once_while_a_frame_waits(self):
        """Con la GUI ocupada, la cola tenía un frame de 9 MB por cada uno capturado."""
        app = _build_frame_application()
        for _ in range(5):
            app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        assert app.notified == ["camera_1"]

    def test_the_gui_takes_the_latest_frame(self):
        content = _ContentSpy()
        app = _build_frame_application(content=content)
        frames = [np.full((4, 4, 3), value, np.uint8) for value in (1, 2, 3)]
        for frame in frames:
            app._on_frame_captured(frame, "camera_1")
        app._on_frame_for_gui("camera_1")
        assert content.shown == [frames[-1]]

    def test_after_the_gui_takes_it_the_next_frame_notifies_again(self):
        app = _build_frame_application(content=_ContentSpy())
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        app._on_frame_for_gui("camera_1")
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        assert app.notified == ["camera_1", "camera_1"]

    def test_each_camera_has_its_own_mailbox(self):
        app = _build_frame_application()
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_2")
        assert app.notified == ["camera_1", "camera_2"]


class _GrainPipeline(AbstractPipeline):
    model_slots = ("grain",)

    def _run(self, frame_bgr, camera_slot):
        return []


class _PersonPipeline(AbstractPipeline):
    model_slots = ("person",)

    def _run(self, frame_bgr, camera_slot):
        return []


class _LicenseStub:
    """La licencia: habilita todo menos los features que se le nombren."""

    def __init__(self, *denied: str):
        self._denied = denied

    def has_feature(self, feature: str) -> bool:
        return feature not in self._denied


def _build_engine_application(monkeypatch, pipelines: dict, *denied_features: str):
    """`Application` sin `__init__` —sí con su QObject—, con lo que lee `_build_engines()`."""
    from PySide6.QtCore import QObject
    monkeypatch.setitem(pipeline_factory._PIPELINE_CLASSES, "grain", _GrainPipeline)
    monkeypatch.setitem(pipeline_factory._PIPELINE_CLASSES, "person", _PersonPipeline)
    monkeypatch.setattr(main, "build_preprocessor", lambda config: None)
    monkeypatch.setattr(main, "build_hooks", lambda config: EngineHooks())
    values = {"inference.pipelines": pipelines}
    for pipeline_slot, section in pipelines.items():
        values.update({f"inference.pipelines.{pipeline_slot}.{key}": value
                       for key, value in section.items()})
    app = main.Application.__new__(main.Application)
    QObject.__init__(app)
    app._config = _MockConfig(**values)
    app._license = _LicenseStub(*denied_features)
    app._display_adjust = None
    return app


class TestEngines:
    """Un hilo por pipeline habilitado y licenciado, con la clase que pide su `type`."""

    def test_each_slot_gets_the_class_its_type_declares(self, monkeypatch):
        app = _build_engine_application(monkeypatch, {
            "pipeline_1": {"type": "grain"}, "pipeline_2": {"type": "person"}})
        engines = app._build_engines()
        assert [type(engine._pipeline) for engine in engines] == [_GrainPipeline,
                                                                  _PersonPipeline]

    def test_two_slots_of_the_same_type_get_their_own_engine(self, monkeypatch):
        app = _build_engine_application(monkeypatch, {
            "pipeline_1": {"type": "person"}, "pipeline_2": {"type": "person"}})
        engines = app._build_engines()
        assert [engine.pipeline_slot for engine in engines] == ["pipeline_1", "pipeline_2"]
        assert engines[0]._pipeline is not engines[1]._pipeline

    def test_a_disabled_pipeline_gets_no_engine(self, monkeypatch):
        app = _build_engine_application(monkeypatch, {
            "pipeline_1": {"type": "grain"}, "pipeline_2": {"enabled": False}})
        assert [engine.pipeline_slot for engine in app._build_engines()] == ["pipeline_1"]

    def test_an_unlicensed_feature_gets_no_engine(self, monkeypatch):
        app = _build_engine_application(monkeypatch, {
            "pipeline_1": {"type": "grain"},
            "pipeline_2": {"type": "person", "feature": "safety"}}, "safety")
        assert [engine.pipeline_slot for engine in app._build_engines()] == ["pipeline_1"]

    def test_an_unlicensed_pipeline_is_remembered_for_its_status_word(self, monkeypatch):
        """Es la decisión del arranque: la palabra no le vuelve a preguntar a la licencia."""
        app = _build_engine_application(monkeypatch, {
            "pipeline_1": {"type": "grain"},
            "pipeline_2": {"type": "person", "feature": "safety"},
            "pipeline_3": {"enabled": False}}, "safety")
        app._build_engines()
        assert app._unlicensed_pipelines == {"pipeline_2"}

    def test_each_result_records_whether_its_pair_was_valid(self):
        import types
        app = main.Application.__new__(main.Application)
        app._camera_illumination = {}
        app._last_valid_by_pair = {}
        app._ui = types.SimpleNamespace(monitor_content=None)
        app._on_result_ready(InferenceResult(camera_slot="camera_1",
                                             pipeline_slot="pipeline_2", is_valid=False))
        assert app._last_valid_by_pair == {("camera_1", "pipeline_2"): False}


def _build_metrics_application(monkeypatch):
    """`Application` sin `__init__`, con lo que guarda y publica las métricas."""
    monkeypatch.setattr(main, "_REGISTER_NAMES", frozenset({"grain_pct", "person_count"}))
    app = main.Application.__new__(main.Application)
    app._metrics_by_pair = {}
    app._unmapped_metrics = set()
    app._colliding_metrics = set()
    return app


class TestMetricRegisters:
    """Las métricas se guardan por par (cámara, pipeline), no por cámara."""

    def test_two_pipelines_on_one_camera_keep_their_own_metrics(self, monkeypatch):
        app = _build_metrics_application(monkeypatch)
        app._store_metrics(InferenceResult(camera_slot="camera_1", pipeline_slot="pipeline_1",
                                           metrics={"grain_pct": 40.0}))
        app._store_metrics(InferenceResult(camera_slot="camera_1", pipeline_slot="pipeline_2",
                                           metrics={"person_count": 1}))
        assert app._build_metric_registers() == {"grain_pct": 40.0, "person_count": 1}

    def test_two_pairs_publishing_the_same_metric_are_warned_once(self, monkeypatch, caplog):
        """Comparten registro y el PLC ve el último: el log nombra los dos pares."""
        app = _build_metrics_application(monkeypatch)
        for pipeline_slot in ("pipeline_1", "pipeline_2"):
            app._store_metrics(InferenceResult(camera_slot="camera_1",
                                               pipeline_slot=pipeline_slot,
                                               metrics={"grain_pct": 40.0}))
        with caplog.at_level("WARNING"):
            app._build_metric_registers()
            app._build_metric_registers()
        warnings = [record.getMessage() for record in caplog.records
                    if "grain_pct" in record.getMessage()]
        assert len(warnings) == 1
        assert "camera_1/pipeline_1" in warnings[0] and "camera_1/pipeline_2" in warnings[0]

    def test_one_pair_per_metric_is_not_warned(self, monkeypatch, caplog):
        app = _build_metrics_application(monkeypatch)
        app._store_metrics(InferenceResult(camera_slot="camera_1", pipeline_slot="pipeline_1",
                                           metrics={"grain_pct": 40.0}))
        with caplog.at_level("WARNING"):
            app._build_metric_registers()
        assert "grain_pct" not in caplog.text


class _StatusEngine:
    """Motor visto desde la palabra de su pipeline: sólo su `get_status()`."""

    def __init__(self, pipeline_slot: str, *, loaded: bool = True, running: bool = True,
                 synthetic: bool = False):
        self.pipeline_slot = pipeline_slot
        self._status = {"loaded": loaded, "running": running, "synthetic": synthetic}

    def get_status(self) -> dict:
        return dict(self._status)


_PIPELINE_WORDS = frozenset(f"pipeline_{i}_state_bitfield" for i in (1, 2, 3))


def _build_pipeline_application(monkeypatch, pipelines: dict, *engines,
                                unlicensed: tuple = ()):
    """`Application` sin `__init__`, con lo que arma la palabra de cada pipeline."""
    monkeypatch.setattr(main, "_REGISTER_NAMES", _PIPELINE_WORDS)
    values = {"inference.pipelines": pipelines}
    for pipeline_slot, section in pipelines.items():
        values.update({f"inference.pipelines.{pipeline_slot}.{key}": value
                       for key, value in section.items()})
    app = main.Application.__new__(main.Application)
    app._config = _MockConfig(**values)
    app._engines = list(engines)
    app._unlicensed_pipelines = set(unlicensed)
    app._last_valid_by_pair = {}
    app._gate_open_by_pair = {}
    app._gates_lock = threading.Lock()
    return app


def _pipeline_word(app, pipeline_slot: str = "pipeline_1") -> int:
    return app._build_pipeline_registers()[f"{pipeline_slot}_state_bitfield"]


class TestPipelineWords:
    """Una palabra por pipeline del config: el que no corre dice por qué."""

    def test_a_healthy_pipeline_is_zero(self, monkeypatch):
        app = _build_pipeline_application(monkeypatch, {"pipeline_1": {}},
                                          _StatusEngine("pipeline_1"))
        assert _pipeline_word(app) == 0

    def test_an_unlicensed_pipeline_says_so(self, monkeypatch):
        """No tiene motor, así que además no carga ni corre: el bit 0 dice por qué."""
        app = _build_pipeline_application(monkeypatch, {"pipeline_1": {}},
                                          unlicensed=("pipeline_1",))
        word = _pipeline_word(app)
        assert word & (1 << pipeline_status.BIT_NOT_LICENSED)
        assert word & (1 << pipeline_status.BIT_NOT_RUNNING)
        assert not word & (1 << pipeline_status.BIT_DISABLED)

    def test_a_disabled_pipeline_says_so(self, monkeypatch):
        app = _build_pipeline_application(monkeypatch, {"pipeline_1": {"enabled": False}})
        word = _pipeline_word(app)
        assert word & (1 << pipeline_status.BIT_DISABLED)
        assert not word & (1 << pipeline_status.BIT_NOT_LICENSED)

    def test_a_mock_pipeline_is_marked_synthetic(self, monkeypatch):
        app = _build_pipeline_application(monkeypatch, {"pipeline_1": {}},
                                          _StatusEngine("pipeline_1", synthetic=True))
        assert _pipeline_word(app) == 1 << pipeline_status.BIT_SYNTHETIC

    def test_an_invalid_last_result_on_any_camera_is_reported(self, monkeypatch):
        app = _build_pipeline_application(monkeypatch, {"pipeline_1": {}},
                                          _StatusEngine("pipeline_1"))
        app._last_valid_by_pair = {("camera_1", "pipeline_1"): True,
                                   ("camera_2", "pipeline_1"): False,
                                   ("camera_2", "pipeline_2"): True}
        assert _pipeline_word(app) == 1 << pipeline_status.BIT_INVALID_RESULT

    def test_another_pipeline_invalid_result_is_not_counted(self, monkeypatch):
        app = _build_pipeline_application(monkeypatch, {"pipeline_1": {}},
                                          _StatusEngine("pipeline_1"))
        app._last_valid_by_pair = {("camera_1", "pipeline_2"): False}
        assert _pipeline_word(app) == 0

    def test_every_declared_pipeline_gets_its_word(self, monkeypatch):
        app = _build_pipeline_application(
            monkeypatch, {"pipeline_1": {}, "pipeline_2": {"enabled": False}},
            _StatusEngine("pipeline_1"))
        assert set(app._build_pipeline_registers()) == {"pipeline_1_state_bitfield",
                                                       "pipeline_2_state_bitfield"}

    def test_without_a_row_in_the_map_nothing_is_published(self, monkeypatch):
        """La instalación publica los pipelines para los que declara fila."""
        app = _build_pipeline_application(monkeypatch, {"pipeline_9": {}},
                                          _StatusEngine("pipeline_9"))
        assert app._build_pipeline_registers() == {}

    def test_the_words_go_out_while_the_license_is_not_valid(self, monkeypatch):
        """Es salud, no medición: con la licencia caída tiene que seguir moviéndose."""
        import types
        app = _build_pipeline_application(monkeypatch, {"pipeline_1": {}},
                                          _StatusEngine("pipeline_1"))
        app._config.is_using_fallback = False
        app._heartbeat = 0
        app._last_metrics = {}
        app._capture_threads = {}
        app._license = types.SimpleNamespace(should_report_invalid=lambda: True,
                                             is_publishing_allowed=lambda: False)
        app._get_service_statuses = lambda: {
            service: "disabled" for service in (
                main.service_status.SERVICE_VIDEO_RTSP,
                main.service_status.SERVICE_VIDEO_HTTP,
                main.service_status.SERVICE_INFLUXDB, main.service_status.SERVICE_MQTT,
                main.service_status.SERVICE_MODBUS_TCP,
                main.service_status.SERVICE_MODBUS_RTU)}
        app._license_days_remaining = lambda: 0
        app._build_clock_registers = lambda: {}
        app._build_camera_registers = lambda: {}
        app._build_gpio_registers = lambda: {}
        # Las medias de la hora son del fork: este test es del template y no las arma.
        app._build_rolling_registers = lambda: {}
        published: dict = {}
        app._mapped_only = lambda values: published.update(values) or {}
        app._modbus = types.SimpleNamespace(update_block=lambda registers: None)
        app._ui = types.SimpleNamespace(update_modbus_values=lambda registers: None)
        app._on_registers_tick()
        assert published["pipeline_1_state_bitfield"] == 0


class _PainterEngine:
    """Motor de un pipeline visto desde el combinado: dibuja su marca en una esquina."""

    def __init__(self, pipeline_slot: str, level: int):
        self.pipeline_slot = pipeline_slot
        self._level = level

    def draw_onto(self, canvas_bgr, result):
        canvas_bgr[0, 0] = self._level

    def draw_timestamp(self, canvas_bgr, result):
        pass


class _AnnotatedSink:
    """Servidor de video: registra el anotado que le empujan por cámara."""

    def __init__(self):
        self.pushed: list = []

    def push_annotated(self, camera_slot, frame_bgr):
        self.pushed.append((camera_slot, frame_bgr))


def _build_display_application(*engines, collector_active: bool = False):
    """`Application` sin `__init__`, con lo que toca el camino del anotado del ciclo."""
    import types
    app = main.Application.__new__(main.Application)
    app._engines = list(engines)
    app._composer = main.FrameComposer()
    app._gate_open_by_pair = {}
    app._gates_lock = threading.Lock()
    app._annotate_cycle = lambda result: result.annotated_bgr
    content = _ContentSpy()
    content.mode = main.MODE_ANNOTATED
    app._ui = types.SimpleNamespace(monitor_content=content)
    app._http_video = _AnnotatedSink()
    app._rtsp_video = _AnnotatedSink()
    app._last_annotated = {}
    app._pending_results = {}
    app._store_metrics = lambda result: None
    app._collector = types.SimpleNamespace(
        should_collect=lambda camera_slot: collector_active)
    app.sent = []
    app._send_to_dataset = lambda result: app.sent.append(result.annotated_bgr)
    return app


def _annotated_result(pipeline_slot: str, timestamp_s: float) -> InferenceResult:
    return InferenceResult(camera_slot="camera_1", pipeline_slot=pipeline_slot,
                           timestamp_s=timestamp_s,
                           annotated_bgr=np.zeros((4, 4, 3), np.uint8))


class TestMergedAnnotated:
    """Una cámara que miran varios pipelines muestra un solo anotado, con todas las capas."""

    def test_one_pipeline_per_camera_shows_the_engine_frame_unchanged(self):
        app = _build_display_application(_PainterEngine("pipeline_1", 50))
        result = _annotated_result("pipeline_1", 10.0)
        app._on_cycle_complete(result)
        assert app._http_video.pushed[-1][1] is result.annotated_bgr
        assert app._ui.monitor_content.shown[-1] is result.annotated_bgr

    def test_two_pipelines_on_one_camera_show_one_merged_frame(self):
        app = _build_display_application(_PainterEngine("pipeline_1", 50),
                                         _PainterEngine("pipeline_2", 90))
        app._on_cycle_complete(_annotated_result("pipeline_1", 10.0))
        person = _annotated_result("pipeline_2", 10.1)
        app._on_cycle_complete(person)
        shown = app._http_video.pushed[-1][1]
        assert shown is not person.annotated_bgr
        assert tuple(shown[0, 0]) == (50, 50, 50), "lleva encima la capa de pipeline_1"
        assert app._last_annotated["camera_1"] is shown
        assert app._rtsp_video.pushed[-1][1] is shown

    def test_an_older_result_is_not_shown(self):
        """Un ciclo con el instante de su representativo no hace saltar el video atrás."""
        app = _build_display_application(_PainterEngine("pipeline_1", 50),
                                         _PainterEngine("pipeline_2", 90))
        app._on_cycle_complete(_annotated_result("pipeline_2", 10.5))
        app._on_cycle_complete(_annotated_result("pipeline_1", 10.0))
        assert len(app._http_video.pushed) == 1

    def test_the_dataset_gets_each_pipeline_own_annotated_frame(self):
        """Su JSON trae sólo las detecciones de ese pipeline: la imagen dice lo mismo."""
        app = _build_display_application(_PainterEngine("pipeline_1", 50),
                                         _PainterEngine("pipeline_2", 90),
                                         collector_active=True)
        grain = _annotated_result("pipeline_1", 10.0)
        person = _annotated_result("pipeline_2", 10.1)
        app._on_cycle_complete(grain)
        app._on_cycle_complete(person)
        assert app.sent[0] is grain.annotated_bgr and app.sent[1] is person.annotated_bgr
        assert tuple(person.annotated_bgr[0, 0]) == (0, 0, 0)


class _CountingCycle(_CycleStub):
    """Scheduler que registra cada permiso pedido: pedirlo lo consume."""

    def __init__(self, *cycled: str):
        super().__init__(*cycled)
        self.taken: list = []

    def take_frame(self, camera_slot: str, pipeline_slot: str) -> bool:
        self.taken.append((camera_slot, pipeline_slot))
        return True


def _closed_on(*camera_slots: str):
    """Compuerta que corta las cámaras nombradas y deja pasar el resto."""
    def gate(camera_slot, signals):
        return camera_slot not in camera_slots
    return gate


class TestFrameGates:
    """Una señal de la planta corta los frames de una cámara a un pipeline, y sólo a ése."""

    def test_the_template_declares_no_gates(self):
        app = main.Application.__new__(main.Application)
        assert app._build_frame_gates() == {}

    def test_a_closed_gate_stops_that_camera_for_that_pipeline_only(self):
        grain = _FrameSink("pipeline_1")
        person = _FrameSink("pipeline_2")
        app = _build_frame_application(engines=[grain, person],
                                       gates={"pipeline_1": _closed_on("camera_1")})
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_2")
        assert [camera_slot for _, camera_slot, _ in grain.pushed] == ["camera_2"]
        assert [camera_slot for _, camera_slot, _ in person.pushed] == ["camera_1", "camera_2"]

    def test_the_gate_receives_the_camera_and_the_current_inputs(self):
        seen = []
        app = _build_frame_application(
            engines=[_FrameSink("pipeline_1")],
            gates={"pipeline_1": lambda camera_slot, signals: seen.append(
                (camera_slot, dict(signals.gpio_inputs))) or True})
        app._on_gpio_inputs_updated({1: 1, 2: 0})
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        assert seen == [("camera_1", {1: 1, 2: 0})]

    def test_new_inputs_replace_the_snapshot_instead_of_changing_it(self):
        """Lo leen los hilos de captura sin lock: modificarlo en el lugar los dejaría a medias."""
        app = _build_frame_application()
        app._gpio_inputs = {}
        app._on_gpio_inputs_updated({1: 1})
        before = app._frame_signals
        app._on_gpio_inputs_updated({1: 0})
        assert dict(before.gpio_inputs) == {1: 1}
        assert dict(app._frame_signals.gpio_inputs) == {1: 0}

    def test_the_snapshot_cannot_be_modified(self):
        signals = main.FrameSignals(gpio_inputs=main.MappingProxyType({1: 1}))
        with pytest.raises(TypeError):
            signals.gpio_inputs[1] = 0

    def test_a_gated_cycled_pipeline_takes_no_permit(self):
        """Pedir el permiso lo consume: por un frame cortado, el ciclo esperaría en vano."""
        engine = _FrameSink("pipeline_1")
        scheduler = _CountingCycle("pipeline_1")
        app = _build_frame_application(engines=[engine], scheduler=scheduler,
                                       gates={"pipeline_1": _closed_on("camera_1")})
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        app._on_frame_for_gui("camera_1")
        assert scheduler.taken == [] and engine.pushed == []

    def test_an_open_gate_on_a_cycled_pipeline_takes_the_permit(self):
        engine = _FrameSink("pipeline_1")
        scheduler = _CountingCycle("pipeline_1")
        app = _build_frame_application(engines=[engine], scheduler=scheduler,
                                       gates={"pipeline_1": _closed_on("camera_9")})
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        app._on_frame_for_gui("camera_1")
        assert scheduler.taken == [("camera_1", "pipeline_1")]

    def test_a_failing_gate_lets_frames_through_and_warns_once(self, caplog):
        """Un error de configuración no para la línea."""
        def broken(camera_slot, signals):
            raise KeyError("tilt_input")

        engine = _FrameSink("pipeline_1")
        app = _build_frame_application(engines=[engine], gates={"pipeline_1": broken})
        with caplog.at_level("WARNING"):
            for _ in range(3):
                app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        assert len(engine.pushed) == 3
        assert len([r for r in caplog.records if "compuerta" in r.getMessage()]) == 1

    def test_the_last_decision_feeds_the_pipeline_word(self, monkeypatch):
        app = _build_pipeline_application(monkeypatch, {"pipeline_1": {}},
                                          _StatusEngine("pipeline_1"))
        app._frame_gates = {"pipeline_1": _closed_on("camera_1")}
        app._gate_warned_s = {}
        app._is_gate_open("pipeline_1", "camera_1", main.FrameSignals())
        assert _pipeline_word(app) == 1 << pipeline_status.BIT_GATED
        app._is_gate_open("pipeline_1", "camera_2", main.FrameSignals())
        assert _pipeline_word(app) == 1 << pipeline_status.BIT_GATED, "una cámara alcanza"

    def test_a_reopened_gate_clears_the_bit(self, monkeypatch):
        app = _build_pipeline_application(monkeypatch, {"pipeline_1": {}},
                                          _StatusEngine("pipeline_1"))
        app._gate_warned_s = {}
        app._frame_gates = {"pipeline_1": _closed_on("camera_1")}
        app._is_gate_open("pipeline_1", "camera_1", main.FrameSignals())
        app._frame_gates = {"pipeline_1": _closed_on()}
        app._is_gate_open("pipeline_1", "camera_1", main.FrameSignals())
        assert _pipeline_word(app) == 0

    def test_a_gate_on_a_cycled_pipeline_is_warned_at_startup(self, caplog):
        app = _build_frame_application(engines=[_FrameSink("pipeline_1")],
                                       cycled=["pipeline_1"],
                                       gates={"pipeline_1": _closed_on()})
        with caplog.at_level("WARNING"):
            app._check_frame_gates()
        assert "pipeline_1" in caplog.text and "ciclo" in caplog.text

    def test_a_gate_for_a_pipeline_without_thread_is_warned_at_startup(self, caplog):
        """Un slot mal escrito no cortaría nada, y parecería andar."""
        app = _build_frame_application(engines=[_FrameSink("pipeline_1")],
                                       gates={"pipeline_7": _closed_on()})
        with caplog.at_level("WARNING"):
            app._check_frame_gates()
        assert "pipeline_7" in caplog.text


class TestGatedOverlay:
    """El overlay de un proceso cortado se va en el acto, y una cámara sin nada se ve en vivo."""

    def _with_grain_result(self, **kwargs):
        """Aplicación con un resultado de grano vigente en `camera_1`."""
        app = _build_frame_application(engines=[_FrameSink("pipeline_1")], **kwargs)
        app._composer.add(InferenceResult(camera_slot="camera_1", pipeline_slot="pipeline_1"),
                          time.monotonic())
        return app

    def test_closing_the_gate_drops_the_overlay_on_the_next_frame(self):
        app = self._with_grain_result(gates={"pipeline_1": _closed_on("camera_1")})
        frame = np.zeros((4, 4, 3), np.uint8)
        app._on_frame_captured(frame, "camera_1")
        app._on_frame_for_gui("camera_1")
        assert app._composer.has_fresh("camera_1", time.monotonic()) is False

    def test_a_camera_with_nothing_fresh_shows_the_live_frame(self):
        """Si no, el último anotado —overlay incluido— quedaba congelado."""
        app = self._with_grain_result(gates={"pipeline_1": _closed_on("camera_1")},
                                      annotated_clients=True)
        frame = np.full((4, 4, 3), 7, np.uint8)
        app._on_frame_captured(frame, "camera_1")
        app._on_frame_for_gui("camera_1")
        assert app._http_video.annotated == [("camera_1", frame)]
        assert app._last_annotated["camera_1"] is frame

    def test_nobody_watching_means_nothing_is_pushed(self):
        app = _build_frame_application()
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        app._on_frame_for_gui("camera_1")
        assert app._http_video.annotated == []

    def test_a_fresh_result_keeps_the_annotated_frame(self):
        """Un ciclo largo sigue vigente hasta el siguiente: su medición queda en pantalla."""
        app = self._with_grain_result(annotated_clients=True)
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        app._on_frame_for_gui("camera_1")
        assert app._http_video.annotated == []

    def test_a_result_that_arrives_after_the_gate_closed_is_not_shown(self):
        """Salió de un frame anterior al corte: el proceso ya terminó."""
        app = _build_display_application(_PainterEngine("pipeline_1", 50),
                                         collector_active=True)
        app._gate_open_by_pair[("camera_1", "pipeline_1")] = False
        result = _annotated_result("pipeline_1", 10.0)
        app._on_cycle_complete(result)
        assert app._http_video.pushed == []
        assert app.sent == [result.annotated_bgr], "al dataset va igual"


class TestDatasetGating:
    """
    Con la recolección apagada, o sin esa cámara, el resultado no se serializa: corre por
    frame en la GUI.
    """

    def _application(self, collector_active: bool):
        import types
        app = main.Application.__new__(main.Application)
        app._annotate_cycle = lambda result: None
        app._ui = types.SimpleNamespace(monitor_content=None)
        app._pending_results = {}
        app._store_metrics = lambda result: None
        app._collector = types.SimpleNamespace(
            should_collect=lambda camera_slot: collector_active)
        app.sent = []
        app._send_to_dataset = app.sent.append
        return app

    def test_nothing_is_built_with_the_collector_off(self):
        app = self._application(collector_active=False)
        app._on_cycle_complete(_belt_result(70.0, 30.0, 60.0))
        assert app.sent == []

    def test_the_result_goes_to_the_dataset_with_the_collector_on(self):
        app = self._application(collector_active=True)
        result = _belt_result(70.0, 30.0, 60.0)
        app._on_cycle_complete(result)
        assert app.sent == [result]

    def test_the_telemetry_keeps_the_numbers_without_the_frames(self):
        """Guardado entero, cada resultado retenía dos frames de 9 MB hasta el tick."""
        from system.inference.result import Detection
        app = self._application(collector_active=False)
        result = _belt_result(70.0, 30.0, 60.0)
        result.source_bgr = np.zeros((8, 8, 3), np.uint8)
        result.annotated_bgr = np.zeros((8, 8, 3), np.uint8)
        result.detections = [Detection(class_index=0, class_name="pellet",
                                       confidence_pct=90.0, bbox_px=(0, 0, 4, 4))]
        app._on_cycle_complete(result)

        (kept,) = app._pending_results[("camera_1", "")]
        assert (kept.source_bgr, kept.annotated_bgr, kept.detections) == (None, None, [])
        assert kept.metrics == result.metrics and kept.timestamp_s == result.timestamp_s
        assert result.source_bgr is not None, "el resultado original no se toca"


class TestHourlyMeans:
    """Lo que entra a las ventanas de la hora con cada tick, y cómo se publica."""

    def test_empty_frames_do_not_dilute_the_composition_of_the_tick(self):
        """Cinco vacíos de quince no pueden hacer que pellet + desmenuzado sume 67 %."""
        app = _build_rolling_application()
        results = [_belt_result(0.0, 0.0, 0.0)] * 5 + [_belt_result(70.0, 30.0, 60.0)] * 10

        app._update_rolling(results)
        values = app._build_rolling_registers()

        assert (values["pct_pellet_norm_1h"], values["pct_desmenuzado_norm_1h"]) == (70.0, 30.0)

    def test_the_composition_of_the_hour_is_a_ratio_of_sums(self):
        """Un segundo con poco material no pesa lo mismo que uno con la cinta llena."""
        app = _build_rolling_application()
        app._update_rolling([_belt_result(100.0, 0.0, 10.0)])
        app._update_rolling([_belt_result(0.0, 100.0, 90.0)])

        values = app._build_rolling_registers()

        assert values["pct_pellet_norm_1h"] == pytest.approx(10.0)
        assert values["pct_desmenuzado_norm_1h"] == pytest.approx(90.0)

    def test_the_composition_of_the_hour_adds_up_to_100(self):
        app = _build_rolling_application()
        for pellet_pct, load_pct in ((80.0, 25.0), (55.0, 70.0), (0.0, 0.0), (91.0, 12.0)):
            app._update_rolling([_belt_result(pellet_pct, 100.0 - pellet_pct, load_pct)
                                 if load_pct else _belt_result(0.0, 0.0, 0.0)])

        values = app._build_rolling_registers()

        total = values["pct_pellet_norm_1h"] + values["pct_desmenuzado_norm_1h"]
        assert total == pytest.approx(100.0)

    def test_an_hour_without_material_publishes_zero(self):
        app = _build_rolling_application()
        app._update_rolling([_belt_result(0.0, 0.0, 0.0)])

        values = app._build_rolling_registers()

        assert (values["pct_pellet_norm_1h"], values["pct_carga_1h"]) == (0.0, 0.0)
        assert values[main._MATERIAL_PRESENT_KEY] == 0

    def test_the_load_still_averages_the_empty_frames(self):
        """En la carga el 0 es una medición: dice que la cinta estuvo vacía ese rato."""
        app = _build_rolling_application()
        results = [_belt_result(0.0, 0.0, 0.0)] * 5 + [_belt_result(70.0, 30.0, 60.0)] * 10

        app._update_rolling(results)

        assert app._build_rolling_registers()["pct_carga_1h"] == 40.0

    def test_an_empty_tick_counts_as_measured_but_without_material(self):
        app = _build_rolling_application()
        app._update_rolling([_belt_result(70.0, 30.0, 60.0)])
        app._update_rolling([_belt_result(0.0, 0.0, 0.0)])

        values = app._build_rolling_registers()

        assert values["pct_pellet_norm_1h"] == 70.0
        assert values[main._MATERIAL_PRESENT_KEY] == 50

    def test_unreliable_frames_feed_neither_window(self):
        app = _build_rolling_application()
        unreliable = InferenceResult(camera_slot="camera_1", is_valid=False,
                                     metrics={"pct_pellet_norm": 10.0,
                                              "pct_desmenuzado_norm": 90.0,
                                              "pct_carga": 5.0})
        app._update_rolling([unreliable, _belt_result(70.0, 30.0, 60.0)])

        values = app._build_rolling_registers()

        assert (values["pct_pellet_norm_1h"], values["pct_carga_1h"]) == (70.0, 60.0)


class _WritableMockConfig(_MockConfig):
    """`_MockConfig` que además acepta `set()` y `save()`, como hace la calibración."""

    def __init__(self, **overrides):
        super().__init__(**overrides)
        self.saved = 0

    def set(self, key: str, value: object):
        self._values[key] = value

    def save(self) -> bool:
        self.saved += 1
        return True


class _SpyUi:
    """Lo único que el cableado de la óptica le pide a la interfaz."""

    def __init__(self):
        self.refreshed: list = []

    def refresh_lens_reference(self, camera_slot: str):
        self.refreshed.append(camera_slot)


def _build_lens_application(**overrides):
    """
    `Application` sin `__init__`, con sólo lo que el cableado de la óptica toca.

    Construirla de verdad levantaría cámaras, Modbus y un event loop; lo que se prueba
    acá es la decisión, que no depende de nada de eso.
    """
    app = main.Application.__new__(main.Application)
    app._config = _WritableMockConfig(**overrides)
    app._ui = _SpyUi()
    app._lens_calibrations = {}
    app._lens_monitor = LensHealthMonitor(window_s=60.0, threshold_pct=50.0)
    return app


class TestLensInterval:
    def test_it_takes_the_interval_from_the_config(self):
        app = _build_lens_application(**{"lens_health.interval_s": 30})
        assert app._read_lens_interval_ms() == 30000

    def test_without_the_key_it_falls_back_to_the_default(self):
        app = _build_lens_application()
        assert app._read_lens_interval_ms() == int(main._LENS_DEFAULT_INTERVAL_S * 1000)

    def test_an_absurdly_short_interval_is_clamped(self):
        """Medir más seguido que esto no llena antes una ventana de horas, y cada
        medición es una pasada del Laplaciano por cámara."""
        app = _build_lens_application(**{"lens_health.interval_s": 0.05})
        assert app._read_lens_interval_ms() == int(main._LENS_MIN_INTERVAL_S * 1000)

    def test_a_zero_interval_reads_as_a_value_that_is_not_there(self):
        """Un 0 no es «medir sin parar»: acá el interruptor es `enabled`, así que un 0 es
        un error de tipeo y vale más el default que el ritmo más agresivo posible."""
        app = _build_lens_application(**{"lens_health.interval_s": 0})
        assert app._read_lens_interval_ms() == int(main._LENS_DEFAULT_INTERVAL_S * 1000)


class TestLensDirtyBit:
    """Qué llega al bit de lente sucio de la palabra de estado de la cámara."""

    def _feed(self, app, camera_slot: str, variance_reference: float):
        app._lens_monitor.set_reference(camera_slot, variance_reference)
        frame = np.zeros((80, 120, 3), np.uint8)
        frame[::8, :] = 255
        for now_s in range(0, 200, 10):
            app._lens_monitor.update(camera_slot, frame, None, now_s=now_s)

    def test_an_uncalibrated_camera_never_reports_a_dirty_lens(self):
        """«No disponible» no es un vidrio sucio: sin referencia no hay nada que afirmar."""
        app = _build_lens_application()
        self._feed(app, "camera_1", 0.0)
        assert app._is_lens_dirty("camera_1") is False

    def test_sharpness_below_the_threshold_reports_a_dirty_lens(self):
        app = _build_lens_application()
        self._feed(app, "camera_1", 1e9)      # referencia inalcanzable
        assert app._is_lens_dirty("camera_1") is True

    def test_a_camera_that_never_measured_does_not_report_one(self):
        assert _build_lens_application()._is_lens_dirty("camera_9") is False


class TestLensCalibration:
    def _measurement(self, variance: float):
        region = Region(0, 0, 10, 10, False)
        return Measurement(variance=variance, luma=100.0, region=region,
                           width_px=10, height_px=10)

    def test_it_does_not_write_anything_before_the_last_sample(self):
        app = _build_lens_application(**{"lens_health.calibration_samples": 3})
        app._lens_calibrations["camera_1"] = ([], [])
        for _ in range(2):
            app._collect_lens_calibration("camera_1", self._measurement(400.0))
        assert app._config.saved == 0
        assert "camera_1" in app._lens_calibrations

    def test_the_last_sample_writes_the_reference_and_persists_it(self):
        app = _build_lens_application(**{"lens_health.calibration_samples": 3})
        app._lens_calibrations["camera_1"] = ([], [])
        for _ in range(3):
            app._collect_lens_calibration("camera_1", self._measurement(400.0))
        prefix = "cameras.camera_1.lens_health.reference"
        assert app._config.get(f"{prefix}.variance") == 400.0
        assert app._config.get(f"{prefix}.sample_count") == 3
        assert app._config.saved == 1

    def test_it_records_the_conditions_it_calibrated_under(self):
        """Sin esto no hay forma de avisar que alguien movió la exposición después."""
        app = _build_lens_application(**{"lens_health.calibration_samples": 1})
        app._lens_calibrations["camera_1"] = ([], [])
        app._collect_lens_calibration("camera_1", self._measurement(400.0))
        conditions = app._config.get("cameras.camera_1.lens_health.reference.conditions")
        assert set(conditions) == set(CONDITION_KEYS)

    def test_it_stamps_when_the_calibration_was_taken(self):
        app = _build_lens_application(**{"lens_health.calibration_samples": 1})
        app._lens_calibrations["camera_1"] = ([], [])
        app._collect_lens_calibration("camera_1", self._measurement(400.0))
        assert app._config.get("cameras.camera_1.lens_health.reference.calibrated_at")

    def test_the_new_reference_takes_effect_without_a_restart(self):
        """Se le pasa al monitor además de escribirla: si no, la cámara recién calibrada
        seguiría en «no disponible» hasta reiniciar."""
        app = _build_lens_application(**{"lens_health.calibration_samples": 1})
        app._lens_calibrations["camera_1"] = ([], [])
        app._collect_lens_calibration("camera_1", self._measurement(400.0))
        assert app._lens_monitor.get_status(
            "camera_1", now_s=0.0)["reference_variance"] == 400.0

    def test_it_tells_the_screen_to_reload_the_reference(self):
        app = _build_lens_application(**{"lens_health.calibration_samples": 1})
        app._lens_calibrations["camera_1"] = ([], [])
        app._collect_lens_calibration("camera_1", self._measurement(400.0))
        assert app._ui.refreshed == ["camera_1"]

    def test_calibrating_one_camera_leaves_the_others_alone(self):
        app = _build_lens_application(**{"lens_health.calibration_samples": 1})
        app._lens_monitor.set_reference("camera_2", 900.0)
        app._lens_calibrations["camera_1"] = ([], [])
        app._collect_lens_calibration("camera_1", self._measurement(400.0))
        assert app._lens_monitor.get_status(
            "camera_2", now_s=0.0)["reference_variance"] == 900.0


class TestLegacyFieldTypes:
    """
    Los nombres y los tipos con los que cada serie ya está guardada en el bucket.

    **InfluxDB fija el tipo de cada campo con el primer punto que lo trae.** Estos los fijó
    la versión anterior del equipo, así que mandar un decimal donde había un entero no
    convierte nada: el backend rechaza la escritura entera y se pierden también los puntos
    de las otras series que iban en el mismo batch. Un renombre hace algo peor, porque no
    falla: el panel deja de encontrar la serie y nadie se entera.

    Por eso esto se afirma campo por campo y no por muestreo.
    """

    _SYSTEM_METRICS = {
        "cpu_usage_pct": 34.2, "gpu_usage_pct": 61.0,
        "cpu_temp_c": 52.4, "gpu_temp_c": 58.9,
        "ram_used_mb": 3820.7, "ram_total_mb": 16384, "disk_free_gb": 214.7,
        "power_w": 18.4,
        "net_mbps": {"enP1p1s0": {"rx_mbps": 12.6, "tx_mbps": 0.9}},
        "temps_c": {"zona": 52.0},
    }

    def _system(self) -> dict:
        return main._build_system_fields(self._SYSTEM_METRICS, {"enP1p1s0": "eth0"})

    def test_the_hardware_fields_keep_their_old_names(self):
        assert set(self._system()) == {
            "cpu_usage", "gpu_usage", "ram_mb", "ram_total_mb", "disk_gb",
            "temp_cpu", "temp_gpu", "power_w", "rx_eth0", "tx_eth0",
        }

    def test_every_hardware_field_is_an_integer(self):
        assert all(isinstance(value, int) for value in self._system().values())

    def test_the_thermal_zones_are_not_published(self):
        """Sus nombres cambian entre equipos: no sirven para un dashboard portable."""
        assert "temps_c" not in self._system() and "zona" not in self._system()

    def test_an_interface_without_a_label_still_gets_published(self):
        fields = main._build_system_fields(self._SYSTEM_METRICS, {})
        assert "rx_enP1p1s0" in fields

    def _inference(self) -> dict:
        result = InferenceResult(
            camera_slot="camera_1", confidence_pct=87.4, illumination_pct=58,
            inference_time_ms=196.3,
            metrics={"pct_pellet": 41.4, "pct_desmenuzado": 8.6, "pct_fondo": 50.2,
                     "pct_carga": 63.28, "pct_pellet_norm": 82.04,
                     "pct_desmenuzado_norm": 17.96},
        )
        return main._build_inference_fields([result])

    def test_every_instantaneous_percentage_is_an_integer(self):
        """Incluidas la composición y la carga: la versión anterior las redondeaba."""
        fields = self._inference()
        percentages = {name: value for name, value in fields.items()
                       if name.startswith("pct_")}
        assert percentages and all(isinstance(v, int) for v in percentages.values())

    def test_confidence_and_illumination_are_integers(self):
        fields = self._inference()
        assert isinstance(fields["confianza"], int)
        assert isinstance(fields["iluminacion"], int)

    def test_the_inference_time_stays_decimal(self):
        assert isinstance(self._inference()["inference_time_ms"], float)

    def test_the_frame_count_is_an_integer(self):
        assert isinstance(self._inference()["frames"], int)

    _LENS = {"state": 0, "sharpness_max_pct": 96, "sample_count": 2880,
             "reference_variance": 50.7, "max_variance": 48.7, "variance": 47.9,
             "sharpness_pct": 94, "luma": 11.8}

    def test_the_optics_fields_keep_their_names_and_types(self):
        fields = main._build_optics_fields(self._LENS)
        assert {name: type(value) for name, value in sorted(fields.items())} == {
            "estado": int, "nitidez_max_pct": int, "muestras": int, "referencia": float,
            "varianza_max": float, "varianza": float, "nitidez_pct": int, "luma": float,
        }

    def test_a_camera_that_never_measured_publishes_only_the_fixed_fields(self):
        """El hueco en la serie dice que no se midió; un número repetido, no."""
        lens = {**self._LENS, "max_variance": None, "variance": None,
                "sharpness_pct": None, "luma": None}
        assert set(main._build_optics_fields(lens)) == {
            "estado", "nitidez_max_pct", "muestras", "referencia"}


# ── GPIO ─────────────────────────────────────────────────────────────────────────

def _build_gpio_application(monkeypatch, **overrides):
    """
    `Application` sin `__init__`, con un `GpioController` de verdad en modo simulado.

    Sin `gpiod` el controlador degrada adentro, que es el camino de cualquier equipo de
    desarrollo y el que tiene que seguir publicando: lo que se prueba es que el cableado
    no pregunte y deje la marca de «sin GPIO» en las dos palabras.
    """
    monkeypatch.setattr(gpio_control, "_GPIOD_AVAILABLE", False)
    config = _MockConfig(**{
        "gpio.enabled": True,
        "gpio.outputs": {"DO1": 51, "DO2": 52},
        "gpio.inputs": {"DI1": 105, "DI2": 106},
        **overrides,
    })
    app = main.Application.__new__(main.Application)
    app._gpio = gpio_control.GpioController(config)
    app._gpio_inputs = {}
    return app


class TestGpioRegisters:
    def test_both_words_say_there_is_no_gpio_without_the_library(self, monkeypatch):
        """Una entrada en cero y un equipo sin de dónde leerla no pueden verse igual."""
        registers = _build_gpio_application(monkeypatch)._build_gpio_registers()
        unavailable = 1 << gpio_status.BIT_UNAVAILABLE
        assert registers["gpio_inputs_bitfield"] & unavailable
        assert registers["gpio_outputs_bitfield"] & unavailable

    def test_the_inputs_word_carries_the_last_polled_reading(self, monkeypatch):
        app = _build_gpio_application(monkeypatch)
        app._on_gpio_inputs_updated({1: 0, 2: 1})
        word = app._build_gpio_registers()["gpio_inputs_bitfield"]
        assert word == gpio_status.pack_inputs({2: 1}, hardware_available=False)

    def test_the_outputs_word_echoes_what_was_commanded(self, monkeypatch):
        app = _build_gpio_application(monkeypatch)
        app._gpio.set_output(2, 1)
        word = app._build_gpio_registers()["gpio_outputs_bitfield"]
        assert word == gpio_status.pack_outputs({2: 1}, hardware_available=False)

    def test_both_words_have_a_row_in_the_map(self, monkeypatch):
        """Sin fila, `_mapped_only` las descarta en silencio y el PLC nunca las ve."""
        names = set(_build_gpio_application(monkeypatch)._build_gpio_registers())
        assert names <= main._REGISTER_NAMES

    def test_an_analyzer_metric_cannot_overwrite_them(self, monkeypatch):
        names = set(_build_gpio_application(monkeypatch)._build_gpio_registers())
        assert names <= main._HEALTH_REGISTER_NAMES


class _StopRecorder:
    """Cualquier subsistema que `stop()` toque: anota cada llamada en un log compartido."""

    def __init__(self, name: str, calls: list):
        self._name = name
        self._calls = calls

    def __getattr__(self, method: str):
        def record(*args, **kwargs):
            self._calls.append((self._name, method))
            return True
        return record


class TestGpioShutdown:
    def test_the_polling_stops_before_the_lines_are_released(self):
        """Cerrar las líneas con el hilo leyéndolas lo deja leyendo una línea liberada."""
        calls: list = []
        app = main.Application.__new__(main.Application)
        for name in ("_ui", "_scheduler", "_metrics_poller", "_telemetry", "_gpio_thread",
                     "_gpio", "_collector", "_http_video", "_rtsp_video", "_modbus",
                     "_modbus_thread"):
            setattr(app, name, _StopRecorder(name, calls))
        app._timers = []
        app._capture_threads = {}
        app._engines = []

        app.stop()

        assert calls.index(("_gpio_thread", "wait")) < calls.index(("_gpio", "close"))


class TestHeadlessUi:
    def test_it_answers_everything_the_window_answers(self):
        """El cableado no pregunta si hay ventana: un método que falte es un crash en headless."""
        window_methods = {name for name in vars(main._QtUi)
                          if callable(getattr(main._QtUi, name)) and not name.startswith("_")}
        assert window_methods <= set(dir(main._HeadlessUi))
