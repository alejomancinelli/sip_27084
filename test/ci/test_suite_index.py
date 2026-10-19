"""
Tests del índice de la suite: qué se cuenta, cómo se agrupa y cuándo se lo da por viejo.

Cada test arma su propia carpeta `test/` en `tmp_path`, así que no depende de la suite
real ni cambia cuando se agrega un test. `ci/` no es un paquete importable, así que el
módulo se carga por ruta.
"""

import importlib.util
import os
import sys
import textwrap

import pytest

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.pardir,
    "ci", "suite_index.py")


@pytest.fixture
def suite_index(monkeypatch):
    spec = importlib.util.spec_from_file_location("suite_index", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Los dataclass buscan su módulo en sys.modules mientras se arman.
    monkeypatch.setitem(sys.modules, "suite_index", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_project(suite_index, tmp_path, monkeypatch):
    """Un proyecto con `test/` y `docs/` vacíos, y el módulo apuntando ahí."""
    (tmp_path / "test").mkdir()
    (tmp_path / "docs").mkdir()
    monkeypatch.setattr(suite_index, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(suite_index, "TEST_DIR", tmp_path / "test")
    monkeypatch.setattr(suite_index, "INDEX_PATH", tmp_path / "docs" / "tests.md")
    return tmp_path


def _write(path, source: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source), encoding="utf-8")


class TestParse:

    def test_classes_and_loose_tests_are_grouped(self, suite_index, fake_project):
        _write(fake_project / "test" / "test_a.py", '''
            """Resumen del archivo.

            Detalle que no entra en el índice."""

            def test_loose():
                pass

            class TestFirst:
                """Lo que prueba la clase."""

                def test_one(self):
                    pass

                def helper(self):
                    pass
        ''')

        parsed = suite_index.parse_test_file(fake_project / "test" / "test_a.py")

        assert parsed.path == "test/test_a.py"
        assert parsed.summary == "Resumen del archivo."
        assert [(group.name, group.tests) for group in parsed.groups] == [
            ("(sin clase)", ["test_loose"]),
            ("TestFirst", ["test_one"]),
        ]
        assert parsed.groups[1].summary == "Lo que prueba la clase."

    def test_a_class_without_tests_is_left_out(self, suite_index, fake_project):
        _write(fake_project / "test" / "test_a.py", '''
            class TestHelpers:
                def build(self):
                    pass
        ''')

        assert suite_index.parse_test_file(fake_project / "test" / "test_a.py").groups == []

    def test_a_parametrized_test_counts_once(self, suite_index, fake_project):
        _write(fake_project / "test" / "test_a.py", '''
            import pytest

            @pytest.mark.parametrize("value", [1, 2, 3])
            def test_values(value):
                pass
        ''')

        assert suite_index.parse_test_file(fake_project / "test" / "test_a.py").test_count == 1

    def test_only_test_files_are_collected(self, suite_index, fake_project):
        _write(fake_project / "test" / "test_a.py", "def test_x():\n    pass\n")
        _write(fake_project / "test" / "helpers.py", "def test_not_a_test():\n    pass\n")

        assert [item.path for item in suite_index.collect()] == ["test/test_a.py"]


class TestMarkdown:

    def test_the_root_folder_goes_first(self, suite_index, fake_project):
        _write(fake_project / "test" / "video" / "test_v.py", "def test_x():\n    pass\n")
        _write(fake_project / "test" / "test_root.py", "def test_x():\n    pass\n")

        markdown = suite_index.build_markdown(suite_index.collect())

        assert markdown.index("## test/\n") < markdown.index("## test/video/\n")

    def test_the_totals_add_up(self, suite_index, fake_project):
        _write(fake_project / "test" / "test_a.py", "def test_x():\n    pass\n")
        _write(fake_project / "test" / "b" / "test_b.py", '''
            class TestB:
                def test_one(self):
                    pass

                def test_two(self):
                    pass
        ''')

        markdown = suite_index.build_markdown(suite_index.collect())

        assert "**2 archivos, 2 grupos, 3 funciones de test.**" in markdown

    def test_the_summary_column_needs_one_class_docstring(self, suite_index, fake_project):
        _write(fake_project / "test" / "test_a.py", '''
            class TestA:
                def test_x(self):
                    pass
        ''')

        markdown = suite_index.build_markdown(suite_index.collect())

        assert "| Grupo | Tests |" in markdown
        assert "Qué prueba" not in markdown

    def test_a_pipe_in_a_docstring_does_not_break_the_table(self, suite_index, fake_project):
        _write(fake_project / "test" / "test_a.py", '''
            class TestA:
                """Entrada | salida."""

                def test_x(self):
                    pass
        ''')

        markdown = suite_index.build_markdown(suite_index.collect())

        assert "Entrada \\| salida." in markdown


class TestCheck:

    def test_a_missing_index_is_stale(self, suite_index, fake_project):
        _write(fake_project / "test" / "test_a.py", "def test_x():\n    pass\n")

        assert suite_index.main(["--check"]) == 1

    def test_a_fresh_index_passes(self, suite_index, fake_project):
        _write(fake_project / "test" / "test_a.py", "def test_x():\n    pass\n")

        assert suite_index.main([]) == 0
        assert suite_index.main(["--check"]) == 0

    def test_a_new_test_makes_it_stale(self, suite_index, fake_project):
        _write(fake_project / "test" / "test_a.py", "def test_x():\n    pass\n")
        suite_index.main([])

        _write(fake_project / "test" / "test_a.py", "def test_x():\n    pass\n\n"
                                                    "def test_y():\n    pass\n")

        assert suite_index.main(["--check"]) == 1

    def test_check_does_not_write(self, suite_index, fake_project):
        _write(fake_project / "test" / "test_a.py", "def test_x():\n    pass\n")

        suite_index.main(["--check"])

        assert not (fake_project / "docs" / "tests.md").exists()
