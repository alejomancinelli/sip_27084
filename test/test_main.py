"""Tests del cableado: la salida del proceso y el manejo de señales.

`main.py` no lo importa nadie, así que lo poco testeable de él es lo que decide sin
depender de subsistemas. Acá no se arma la aplicación ni se levanta un event loop."""

import signal

import numpy as np

import main
from system.camera.lens_health import (
    CONDITION_KEYS, LensHealthMonitor, Measurement, Region,
)
from system.inference.result import InferenceResult

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


class TestInferenceFields:
    """
    Los fields de un punto de telemetría de inferencia.

    Lo que se cuida es el proyecto con ciclo: el scheduler cierra los N frames y el tick ve
    un solo resultado ya resumido, así que agregarlo de nuevo no mide nada y tapa lo que sí
    midió.
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
                                                              "_std_std", "_max_mean"))]

    def test_the_sample_count_is_the_frames_of_the_cycle(self):
        """Cuántos ciclos entraron al punto ya lo dice `result_count`."""
        fields = main._build_inference_fields([self._cycle_result()])
        assert (fields["sample_count"], fields["result_count"]) == (5, 1)

    def test_without_a_cycle_the_stats_are_derived(self):
        """Con `frames_per_cycle: 1` no llega nada resumido y el tick es el que agrega."""
        results = [InferenceResult(camera_slot="camera_1", metrics={"load_pct": value})
                   for value in (40.0, 60.0)]
        fields = main._build_inference_fields(results)
        assert (fields["load_pct_mean"], fields["load_pct_std"],
                fields["sample_count"]) == (50.0, 10.0, 2)


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
