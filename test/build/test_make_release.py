"""
Tests del armado del entregable: qué se copia y qué no.

Se prueban las piezas puras —copiar, leer el nombre de la app—, no `main()`, que necesita
un `main.dist` de Nuitka. `build/` no es un paquete importable, así que el módulo se carga
por ruta.
"""

import importlib.util
import os
import re

import pytest

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.pardir,
    "build", "make_release.py")


@pytest.fixture
def make_release():
    spec = importlib.util.spec_from_file_location("make_release", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestModelsDirectory:
    """
    La carpeta de los pesos viaja entera, porque su contenido lo decide cada fork.

    El `.gitkeep` es lo que la hace existir en un checkout limpio: sin él, un fork que
    todavía no tiene modelo no tiene carpeta, y `make_release.py` copia una ruta que no
    está. Lo que no puede pasar es que ese marcador termine en el equipo de la planta.
    """

    def test_the_gitkeep_does_not_travel(self, make_release, tmp_path):
        source = tmp_path / "models"
        source.mkdir()
        (source / ".gitkeep").write_text("", encoding="utf-8")
        (source / "modelo.onnx").write_bytes(b"pesos")

        target = tmp_path / "installation" / "models"
        assert make_release._copy_into(str(source), str(target))
        assert [item.name for item in target.iterdir()] == ["modelo.onnx"]

    def test_a_fork_without_a_model_yet_is_not_a_failure(self, make_release, tmp_path):
        """
        Copiar la carpeta vacía devuelve False y no entra a `missing`: un fork sin modelo
        propio arma su entregable igual. `__pycache__` tampoco viaja.
        """
        source = tmp_path / "models"
        source.mkdir()
        (source / ".gitkeep").write_text("", encoding="utf-8")

        target = tmp_path / "installation" / "models"
        assert make_release._copy_into(str(source), str(target))
        assert list(target.iterdir()) == []

    def test_a_directory_that_is_not_there_says_so(self, make_release, tmp_path):
        assert not make_release._copy_into(str(tmp_path / "no-existe"),
                                           str(tmp_path / "destino"))

    def test_the_models_directory_is_the_one_the_repo_ships(self, make_release):
        """
        El nombre está en una constante y la carpeta existe en el repo: si alguien
        renombra una de las dos, el entregable sale sin pesos y nadie se entera hasta
        instalarlo.
        """
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(_SCRIPT)))
        assert os.path.isdir(os.path.join(repo_root, make_release._MODELS_DIR))


class TestLinuxRelease:
    """
    El entregable de la Jetson: lanzadores en bash, accesos `.desktop` y verificador.

    Se arma desde Windows sólo para leer lo que escribe; el permiso de ejecución se prueba
    donde existe.
    """

    def _write(self, make_release, tmp_path, app_name: str = "Detección de pellet"):
        release, program = tmp_path / "release", tmp_path / "release" / "program"
        program.mkdir(parents=True)
        make_release._write_linux_scripts(str(release), str(program), "main.bin", app_name)
        return release

    def test_the_launchers_exec_the_nuitka_binary(self, make_release, tmp_path):
        release = self._write(make_release, tmp_path)
        launcher = (release / "Detección de pellet.sh").read_text(encoding="utf-8")
        headless = (release / "Detección de pellet sin pantalla.sh").read_text(encoding="utf-8")
        assert 'exec "$BASE/program/main.bin" "$@"' in launcher
        assert 'exec "$BASE/program/main.bin" --headless "$@"' in headless
        assert 'source "$BASE/_entorno.sh"' in launcher

    def test_the_data_dir_lives_in_one_file(self, make_release, tmp_path):
        release = self._write(make_release, tmp_path)
        scripts = {path.name: path.read_text(encoding="utf-8") for path in release.iterdir()
                   if path.is_file()}
        assert [name for name, text in scripts.items()
                if "SIP_DATA_DIR=" in text] == ["_entorno.sh"]

    def test_no_placeholder_is_left_unfilled(self, make_release, tmp_path):
        release = self._write(make_release, tmp_path)
        for path in release.iterdir():
            if path.is_file():
                assert not re.search(r"@[A-Z_]+@", path.read_text(encoding="utf-8")), path.name

    def test_the_scripts_have_unix_line_endings(self, make_release, tmp_path):
        """Un `\r` al final del shebang hace que bash no encuentre el intérprete."""
        release = self._write(make_release, tmp_path)
        for path in release.iterdir():
            if path.is_file():
                assert b"\r" not in path.read_bytes(), path.name

    @pytest.mark.skipif(os.name == "nt", reason="NTFS no guarda el permiso de ejecución")
    def test_the_scripts_are_executable(self, make_release, tmp_path):
        release = self._write(make_release, tmp_path)
        for path in release.iterdir():
            if path.is_file():
                assert os.access(path, os.X_OK), path.name

    def test_the_shortcut_quotes_a_name_with_spaces_and_quotes(self, make_release, tmp_path):
        release = self._write(make_release, tmp_path, app_name="Línea 2 'norte'")
        text = (release / "Crear accesos directos.sh").read_text(encoding="utf-8")
        assert "NAME='Línea 2 '\"'\"'norte'\"'\"''" in text
        assert "ENTRY_ID=linea-2-norte" in text

    def test_the_icon_travels_with_the_program(self, make_release, tmp_path):
        """El `.desktop` pide el icono por ruta adentro de `program/`: tiene que estar."""
        self._write(make_release, tmp_path)
        assert (tmp_path / "release" / "program" / make_release._program_icon()).is_file()


class TestDesktopEntryId:
    def test_accents_and_spaces_become_a_plain_slug(self, make_release):
        assert (make_release._desktop_entry_id("Detección de calidad de pellet")
                == "deteccion-de-calidad-de-pellet")

    def test_a_name_without_ascii_letters_still_gets_one(self, make_release):
        assert make_release._desktop_entry_id("・・・") == "app"


class TestFindExecutable:
    def test_linux_names_the_binary_main_bin(self, make_release, tmp_path, monkeypatch):
        monkeypatch.setattr(make_release, "_EXECUTABLE_NAMES", ("main.bin", "main"))
        (tmp_path / "main.bin").write_bytes(b"")
        assert make_release._find_executable(str(tmp_path)) == "main.bin"

    def test_a_dist_without_a_binary_says_so_with_an_empty_name(self, make_release, tmp_path):
        assert make_release._find_executable(str(tmp_path)) == ""
