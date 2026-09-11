"""
Tests de las dos raíces.

Corriendo del repo `APP_DIR` y `DATA_DIR` son la misma carpeta, así que un test que sólo
mire los valores de hoy no afirma nada: lo que hay que fijar es **de dónde sale cada una**
y qué archivo cuelga de cuál, que es lo que cambia al compilar.
"""

import importlib
import os

import system.paths as paths


def _reloaded(monkeypatch, **environ):
    """Recarga el módulo con otro entorno: las raíces se calculan al importar."""
    for key, value in environ.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return importlib.reload(paths)


class TestRoots:
    def test_in_the_repo_both_roots_are_the_repo(self):
        """Sin compilar coinciden, y por eso el bug de confundirlas no se ve acá."""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(paths.__file__)))
        assert paths.APP_DIR == repo
        assert paths.DATA_DIR == repo

    def test_the_environment_moves_the_data_and_not_the_program(self, monkeypatch, tmp_path):
        """
        Es lo que deja actualizar el programa sin pisar la calibración de la planta.

        El ejecutable vive en una carpeta que se reemplaza entera; el config, el mapa de
        registros y los pesos no pueden estar adentro.
        """
        reloaded = _reloaded(monkeypatch, **{paths.DATA_DIR_ENV: str(tmp_path)})
        try:
            assert reloaded.DATA_DIR == str(tmp_path)
            assert reloaded.APP_DIR != str(tmp_path)
        finally:
            _reloaded(monkeypatch, **{paths.DATA_DIR_ENV: None})

    def test_an_empty_variable_is_not_a_path(self, monkeypatch):
        """Una variable declarada y vacía tiene que caer al default, no a la raíz."""
        reloaded = _reloaded(monkeypatch, **{paths.DATA_DIR_ENV: "   "})
        try:
            assert reloaded.DATA_DIR == reloaded.APP_DIR
        finally:
            _reloaded(monkeypatch, **{paths.DATA_DIR_ENV: None})


class TestResolve:
    def test_a_relative_path_hangs_off_the_data_root(self):
        assert paths.resolve("./data/logs", "x") == os.path.join(
            paths.DATA_DIR, "data", "logs")

    def test_an_absolute_path_is_left_alone(self):
        absolute = os.path.abspath(os.path.join(os.sep, "var", "logs"))
        assert paths.resolve(absolute, "x") == os.path.normpath(absolute)

    def test_an_empty_value_falls_back_to_the_default(self):
        assert paths.resolve("", "./data/x") == os.path.join(paths.DATA_DIR, "data", "x")
        assert paths.resolve(None, "./data/x") == os.path.join(paths.DATA_DIR, "data", "x")

    def test_it_follows_the_data_root_and_not_the_program(self, monkeypatch, tmp_path):
        reloaded = _reloaded(monkeypatch, **{paths.DATA_DIR_ENV: str(tmp_path)})
        try:
            assert reloaded.resolve("./data/logs", "x").startswith(str(tmp_path))
        finally:
            _reloaded(monkeypatch, **{paths.DATA_DIR_ENV: None})


class TestAppFile:
    def test_a_bundled_asset_hangs_off_the_program(self):
        assert paths.app_file("ui", "styles", "dark.qss") == os.path.join(
            paths.APP_DIR, "ui", "styles", "dark.qss")

    def test_it_ignores_where_the_data_lives(self, monkeypatch, tmp_path):
        """Un QSS no está donde el operador guarda su config: viaja con el ejecutable."""
        reloaded = _reloaded(monkeypatch, **{paths.DATA_DIR_ENV: str(tmp_path)})
        try:
            assert not reloaded.app_file("ui", "styles").startswith(str(tmp_path))
        finally:
            _reloaded(monkeypatch, **{paths.DATA_DIR_ENV: None})
