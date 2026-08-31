"""Tests del cableado: la salida del proceso y el manejo de señales.

`main.py` no lo importa nadie, así que lo poco testeable de él es lo que decide sin
depender de subsistemas. Acá no se arma la aplicación ni se levanta un event loop."""

import signal

import main

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


class TestInterruptSignals:
    def test_ctrl_c_and_sigterm_are_always_handled(self):
        assert {signal.SIGINT, signal.SIGTERM} <= set(main._interrupt_signals())

    def test_ctrl_break_is_handled_where_it_exists(self):
        """ONLY_WINDOWS: sin atenderlo el proceso muere sin ejecutar un paso del cierre."""
        expected = hasattr(signal, "SIGBREAK")
        handled = any(getattr(s, "name", "") == "SIGBREAK" for s in main._interrupt_signals())
        assert handled is expected
