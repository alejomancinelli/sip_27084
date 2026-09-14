"""Tests del cableado: la salida del proceso y el manejo de señales.

`main.py` no lo importa nadie, así que lo poco testeable de él es lo que decide sin
depender de subsistemas. Acá no se arma la aplicación ni se levanta un event loop."""

import signal

import main
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
