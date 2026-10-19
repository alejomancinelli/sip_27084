"""
Tests del punto de entrada de las verificaciones: qué corre cada etapa y con qué intérprete.

No se corre ni Ruff ni pytest de verdad: `_run` se reemplaza por un doble que anota el
comando y devuelve el código que se le pida. `ci/` no es un paquete importable, así que el
módulo se carga por ruta.
"""

import importlib.util
import os
import sys

import pytest

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.pardir,
    "ci", "check.py")


@pytest.fixture
def check():
    spec = importlib.util.spec_from_file_location("check", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeRun:
    """Anota cada comando; devuelve los códigos de `exit_codes` en orden, y 0 al agotarse."""

    def __init__(self, exit_codes=()):
        self.commands = []
        self._exit_codes = list(exit_codes)

    def __call__(self, command):
        self.commands.append(command)
        return self._exit_codes.pop(0) if self._exit_codes else 0

    def tools(self):
        """Lo que se corrió, sin el intérprete: ['ruff', 'ci/suite_index.py', 'pytest']."""
        names = []
        for command in self.commands:
            names.append(command[2] if command[1] == "-m" else command[1])
        return names


@pytest.fixture
def fake_run(check, monkeypatch):
    run = _FakeRun()
    monkeypatch.setattr(check, "_run", run)
    # Que main() no se relance con el intérprete del venv: corre en este proceso.
    monkeypatch.setattr(check, "_venv_python", lambda: None)
    return run


class TestStages:

    def test_without_a_stage_it_lints_and_then_tests(self, check, fake_run):
        assert check.main([]) == 0
        assert fake_run.tools() == ["ruff", "ci/suite_index.py", "pytest"]

    def test_lint_does_not_run_the_tests(self, check, fake_run):
        check.main(["lint"])
        assert "pytest" not in fake_run.tools()

    def test_a_failing_lint_stops_before_the_tests(self, check, monkeypatch):
        run = _FakeRun(exit_codes=[1])
        monkeypatch.setattr(check, "_run", run)
        monkeypatch.setattr(check, "_venv_python", lambda: None)

        assert check.main([]) == 1
        assert run.tools() == ["ruff"]

    def test_the_index_is_checked_and_not_rewritten(self, check, fake_run):
        check.main(["lint"])
        assert fake_run.commands[1][-1] == "--check"


class TestPytestArguments:

    def test_after_the_separator_they_go_to_pytest(self, check, fake_run):
        check.main(["test", "--", "-k", "lifecycle"])
        assert fake_run.commands[0][-2:] == ["-k", "lifecycle"]
        assert "--" not in fake_run.commands[0]

    def test_the_whole_suite_runs_in_parallel(self, check, fake_run, monkeypatch):
        monkeypatch.setattr(check.importlib.util, "find_spec", lambda name: object())
        check.main(["test"])
        assert fake_run.commands[0][-2:] == ["-n", "auto"]

    def test_a_single_file_does_not_start_workers(self, check, fake_run, monkeypatch):
        monkeypatch.setattr(check.importlib.util, "find_spec", lambda name: object())
        check.main(["test", "--", "test/ui"])
        assert "-n" not in fake_run.commands[0]

    def test_without_xdist_it_runs_serially(self, check, fake_run, monkeypatch):
        monkeypatch.setattr(check.importlib.util, "find_spec", lambda name: None)
        check.main(["test"])
        assert "-n" not in fake_run.commands[0]


class TestInterpreter:

    def test_another_interpreter_hands_over_to_the_venv(self, check, monkeypatch, tmp_path):
        venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
        calls = []
        monkeypatch.setattr(check, "_venv_python", lambda: venv_python)
        monkeypatch.setattr(check.subprocess, "call", lambda command, cwd: calls.append(command) or 7)

        assert check.main(["lint"]) == 7
        assert calls == [[str(venv_python), check.__file__, "lint"]]

    def test_the_venv_interpreter_runs_in_place(self, check, fake_run, monkeypatch):
        venv_python = check.Path(sys.prefix) / "Scripts" / "python.exe"
        monkeypatch.setattr(check, "_venv_python", lambda: venv_python)

        assert check.main(["lint"]) == 0
        assert fake_run.tools() == ["ruff", "ci/suite_index.py"]
