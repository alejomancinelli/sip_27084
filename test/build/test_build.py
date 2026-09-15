"""
Tests de las opciones con las que se llama a Nuitka.

`_flags()` es una función pura: devuelve una lista de strings y no compila nada, así que
esto corre en la misma suite sin hardware que todo lo demás. Lo que hace Nuitka con esas
opciones no se prueba acá — eso se mide compilando, y queda en `build/README.md`.

`build/` no es un paquete importable, así que el módulo se carga por ruta.
"""

import importlib.util
import os

import pytest

_BUILD_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.pardir,
    "build", "build.py")


@pytest.fixture
def build_script():
    spec = importlib.util.spec_from_file_location("build_script", _BUILD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestExtraFlags:
    """
    Las opciones que suma cada fork.

    Existen porque no todo se arregla excluyendo un paquete: el caso que las motivó es un
    import que hay que dejar entrar y desactivar —`--module-parameter=...`—, y sin un
    lugar para eso el fork termina editando `_flags()`, que es maquinaria y se
    cross-portea.
    """

    def test_the_template_adds_none(self, build_script):
        assert build_script._EXTRA_FLAGS == ()

    def test_what_the_fork_declares_ends_up_in_the_command(self, build_script, monkeypatch):
        monkeypatch.setattr(build_script, "_EXTRA_FLAGS",
                            ("--module-parameter=numba-disable-jit=yes",))
        assert "--module-parameter=numba-disable-jit=yes" in build_script._flags("build/out")

    def test_they_come_before_the_exclusions(self, build_script, monkeypatch):
        """
        Una opción del fork puede hablar de un paquete que además se excluye, y Nuitka
        lee la línea en orden. Juntas y en este orden se leen como una sola decisión.
        """
        monkeypatch.setattr(build_script, "_EXTRA_FLAGS", ("--una-opcion",))
        monkeypatch.setattr(build_script, "_EXCLUDED", ("tensorflow",))
        flags = build_script._flags("build/out")
        assert flags.index("--una-opcion") < flags.index("--nofollow-import-to=tensorflow")

    def test_the_machinery_flags_are_still_there(self, build_script, monkeypatch):
        """Sumar no es reemplazar: lo que el template ya pasaba sigue estando."""
        monkeypatch.setattr(build_script, "_EXTRA_FLAGS", ("--una-opcion",))
        flags = build_script._flags("build/out")
        assert "--standalone" in flags
        assert "--enable-plugin=pyside6" in flags


class TestExclusions:
    def test_each_excluded_package_gets_its_own_flag(self, build_script, monkeypatch):
        monkeypatch.setattr(build_script, "_EXCLUDED", ("tensorflow", "keras"))
        flags = build_script._flags("build/out")
        assert "--nofollow-import-to=tensorflow" in flags
        assert "--nofollow-import-to=keras" in flags

    def test_excluding_nothing_is_the_default(self, build_script):
        assert build_script._EXCLUDED == ()
        assert not [f for f in build_script._flags("build/out")
                    if f.startswith("--nofollow-import-to=")]
