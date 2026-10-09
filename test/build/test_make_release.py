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

    def _write(self, make_release, tmp_path, app_name: str = "Inspección de piezas", *,
               accelerated: bool = True):
        release, program = tmp_path / "release", tmp_path / "release" / "program"
        program.mkdir(parents=True)
        make_release._write_linux_scripts(str(release), str(program), "main.bin", app_name,
                                          accelerated=accelerated)
        return release

    def test_the_launchers_exec_the_nuitka_binary(self, make_release, tmp_path):
        release = self._write(make_release, tmp_path)
        launcher = (release / "Inspección de piezas.sh").read_text(encoding="utf-8")
        headless = (release / "Inspección de piezas sin pantalla.sh").read_text(encoding="utf-8")
        assert 'exec "$BASE/program/main.bin" "$@"' in launcher
        assert 'exec "$BASE/program/main.bin" --headless "$@"' in headless
        assert 'source "$BASE/_entorno.sh"' in launcher

    def test_the_data_dir_lives_in_one_file(self, make_release, tmp_path):
        release = self._write(make_release, tmp_path)
        scripts = {path.name: path.read_text(encoding="utf-8") for path in release.iterdir()
                   if path.is_file()}
        assert [name for name, text in scripts.items()
                if "SIP_DATA_DIR=" in text] == ["_entorno.sh"]

    @pytest.mark.parametrize("accelerated", [True, False])
    def test_no_placeholder_is_left_unfilled(self, make_release, tmp_path, accelerated):
        release = self._write(make_release, tmp_path, accelerated=accelerated)
        for path in release.iterdir():
            if path.is_file():
                assert not re.search(r"@[A-Z_]+@", path.read_text(encoding="utf-8")), path.name

    def test_the_accelerated_launcher_imports_only_what_travels(self, make_release, tmp_path):
        """
        Lo de terceros está en `program/site-packages` y no en el venv del equipo de
        build; tampoco tiene que colarse lo que el usuario tenga en `~/.local`.
        """
        release = self._write(make_release, tmp_path, accelerated=True)
        launcher = (release / "Inspección de piezas.sh").read_text(encoding="utf-8")
        assert 'export PYTHONPATH="$BASE/program/site-packages"' in launcher
        assert "export PYTHONNOUSERSITE=1" in launcher
        assert launcher.index("PYTHONPATH=") < launcher.index("exec ")

    def test_the_standalone_launcher_sets_no_python_path(self, make_release, tmp_path):
        release = self._write(make_release, tmp_path, accelerated=False)
        launcher = (release / "Inspección de piezas.sh").read_text(encoding="utf-8")
        assert "PYTHONPATH" not in launcher

    @pytest.mark.parametrize("accelerated, packages", [(True, "program/site-packages"),
                                                       (False, "program")])
    def test_the_camera_check_looks_where_the_packages_travel(self, make_release, tmp_path,
                                                              accelerated, packages):
        release = self._write(make_release, tmp_path, accelerated=accelerated)
        check = (release / "Verificar camara.sh").read_text(encoding="utf-8")
        assert f'PACKAGES="$BASE/{packages}"' in check

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


def _install(site, name: str, files: tuple, requires: tuple = ()):
    """Una distribución instalada como la deja pip: su metadata, su RECORD y sus archivos."""
    dist_info = site / f"{name.replace('-', '_')}-1.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
        + "".join(f"Requires-Dist: {requirement}\n" for requirement in requires),
        encoding="utf-8")
    record = (*files, f"{dist_info.name}/METADATA", f"{dist_info.name}/RECORD")
    (dist_info / "RECORD").write_text("".join(f"{file},,\n" for file in record),
                                      encoding="utf-8")
    for file in files:
        if not file.startswith(".."):
            (site / file).parent.mkdir(parents=True, exist_ok=True)
            (site / file).write_text("", encoding="utf-8")


class TestSitePackages:
    """
    Lo de terceros del build acelerado: el venv probado, sin lo de compilar y testear.

    Lo que se fija es qué queda afuera y por qué, no el tamaño: un paquete de runtime que
    falta no da un error al armar, da un `ModuleNotFoundError` en la planta.
    """

    @pytest.fixture
    def site(self, tmp_path):
        site = tmp_path / "site-packages"
        _install(site, "Nuitka", ("nuitka/__init__.py", "../../../bin/nuitka"))
        _install(site, "pytest", ("pytest/__init__.py", "_pytest/main.py", "py.py",
                                  "__pycache__/py.cpython-310.pyc"))
        _install(site, "numpy", ("numpy/__init__.py",))
        return site

    def test_the_build_and_test_packages_stay_behind(self, make_release, site, tmp_path):
        target = tmp_path / "program" / "site-packages"
        make_release._copy_site_packages(str(site), str(target))
        names = {path.name for path in target.iterdir()}
        assert {"numpy", "numpy-1.0.dist-info", "__pycache__"} <= names
        assert not names & {"nuitka", "Nuitka-1.0.dist-info", "pytest", "_pytest", "py.py",
                            "pytest-1.0.dist-info"}

    def test_one_that_a_runtime_package_needs_travels(self, make_release, site, tmp_path):
        """Sacarla rompería en la planta un import que en el equipo de build anduvo."""
        _install(site, "zstandard", ("zstandard/__init__.py",))
        _install(site, "polars", ("polars/__init__.py",), requires=("zstandard>=0.20",))
        target = tmp_path / "program" / "site-packages"
        make_release._copy_site_packages(str(site), str(target))
        assert (target / "zstandard").is_dir()

    def test_a_test_extra_does_not_keep_it(self, make_release, site, tmp_path):
        """Casi todo paquete declara `pytest; extra == "test"`: no es una dependencia real."""
        _install(site, "pillow", ("PIL/__init__.py",), requires=('pytest; extra == "tests"',))
        target = tmp_path / "program" / "site-packages"
        make_release._copy_site_packages(str(site), str(target))
        assert not (target / "pytest").exists()

    @pytest.mark.skipif(os.name == "nt", reason="los symlinks de apt son de la Jetson")
    def test_the_system_packages_travel_as_symlinks(self, make_release, site, tmp_path):
        """`gi` y `tensorrt` vienen de apt y tienen que resolver contra el JetPack del equipo."""
        system_gi = tmp_path / "dist-packages" / "gi"
        system_gi.mkdir(parents=True)
        (site / "gi").symlink_to(system_gi)
        target = tmp_path / "program" / "site-packages"
        make_release._copy_site_packages(str(site), str(target))
        assert (target / "gi").is_symlink()
        assert os.readlink(target / "gi") == str(system_gi)


class TestDesktopEntryId:
    def test_accents_and_spaces_become_a_plain_slug(self, make_release):
        assert (make_release._desktop_entry_id("Inspección de calidad de piezas")
                == "inspeccion-de-calidad-de-piezas")

    def test_a_name_without_ascii_letters_still_gets_one(self, make_release):
        assert make_release._desktop_entry_id("・・・") == "app"


class TestFindExecutable:
    def test_linux_names_the_binary_main_bin(self, make_release, tmp_path, monkeypatch):
        monkeypatch.setattr(make_release, "_EXECUTABLE_NAMES", ("main.bin", "main"))
        (tmp_path / "main.bin").write_bytes(b"")
        assert make_release._find_executable(str(tmp_path)) == "main.bin"

    def test_a_dist_without_a_binary_says_so_with_an_empty_name(self, make_release, tmp_path):
        assert make_release._find_executable(str(tmp_path)) == ""
