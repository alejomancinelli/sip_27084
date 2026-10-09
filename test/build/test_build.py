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


class TestOptionalPackages:
    """
    Lo que la maquinaria necesita que Nuitka incluya a mano, y sólo donde está instalado.
    Va fuera de `_EXTRA_FLAGS`: el GPIO es del template, no de un fork.
    """

    def test_an_installed_package_is_included(self, build_script, monkeypatch):
        monkeypatch.setattr(build_script, "_is_installed", lambda package: True)
        assert "--include-package=gpiod" in build_script._flags("build/out")

    def test_a_missing_one_is_left_out(self, build_script, monkeypatch):
        """En Windows no existe, y pedirlo cortaría el build con «package not found»."""
        monkeypatch.setattr(build_script, "_is_installed", lambda package: False)
        assert not [flag for flag in build_script._flags("build/out")
                    if flag.startswith("--include-package=")]

    def test_the_fork_block_stays_empty_either_way(self, build_script, monkeypatch):
        monkeypatch.setattr(build_script, "_is_installed", lambda package: True)
        assert build_script._EXTRA_FLAGS == ()


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


class TestPlatform:
    """
    Las propiedades y el icono del `.exe` son recursos de un binario PE. En la Jetson no
    hay dónde guardarlos, y el comando no los lleva.
    """

    def test_windows_gets_the_executable_properties(self, build_script, monkeypatch):
        monkeypatch.setattr(build_script, "_IS_WINDOWS", True)
        flags = build_script._flags("build/out")
        assert f"--windows-icon-from-ico={build_script._ICON}" in flags
        assert any(flag.startswith("--product-version=") for flag in flags)

    def test_linux_does_not(self, build_script, monkeypatch):
        monkeypatch.setattr(build_script, "_IS_WINDOWS", False)
        flags = build_script._flags("build/out")
        assert not [flag for flag in flags if flag.startswith("--windows-")]
        assert not any(flag.startswith("--product-version=") for flag in flags)
        assert "--standalone" in flags


def _place_key_modules(root) -> list:
    """Deja los dos módulos de clave como los deja el repositorio de firma."""
    paths = []
    for relative in ("system/license/_public_key.py", "system/inference/models/_model_key.py"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# clave generada\n", encoding="utf-8")
        paths.append(path)
    return paths


class TestKeyModules:
    """
    Las claves entran al binario y se borran después. Una que queda en el disco cambia lo
    que la suite encuentra al importar, y no tiene por qué estar en un checkout.
    """

    def test_both_are_removed(self, build_script, monkeypatch, tmp_path):
        paths = _place_key_modules(tmp_path)
        monkeypatch.setattr(build_script, "_REPO_ROOT", str(tmp_path))
        assert len(build_script._remove_key_modules()) == 2
        assert not any(path.exists() for path in paths)

    def test_without_them_there_is_nothing_to_remove(self, build_script, monkeypatch, tmp_path):
        monkeypatch.setattr(build_script, "_REPO_ROOT", str(tmp_path))
        assert build_script._remove_key_modules() == []

    def test_they_are_removed_even_if_the_build_does_not_finish(self, build_script,
                                                                 monkeypatch, tmp_path):
        paths = _place_key_modules(tmp_path)
        monkeypatch.setattr(build_script, "_REPO_ROOT", str(tmp_path))
        monkeypatch.setattr(build_script.sys, "argv",
                            ["build.py", "--out", str(tmp_path / "out")])

        def interrupted(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(build_script.subprocess, "run", interrupted)
        with pytest.raises(KeyboardInterrupt):
            build_script.main()
        assert not any(path.exists() for path in paths)
