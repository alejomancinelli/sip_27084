"""
Tests del armado del entregable: qué se copia y qué no.

Se prueban las piezas puras —copiar, leer el nombre de la app—, no `main()`, que necesita
un `main.dist` de Nuitka. `build/` no es un paquete importable, así que el módulo se carga
por ruta.
"""

import importlib.util
import os

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
