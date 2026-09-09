"""
Tests del cargador del `.env`.

Cada test escribe su propio archivo en un tmp_path y se lo pasa a `load_env_file()`: no
se toca el `.env` del equipo, que tiene los secretos de verdad.
"""

import os

import pytest

from system.env import load_env_file

_VAR = "TEST_ENV_TOKEN"
_OTHER_VAR = "TEST_ENV_OTHER"


@pytest.fixture(autouse=True)
def clean_environment():
    """Deja el entorno como estaba: `load_env_file` escribe en os.environ de verdad."""
    yield
    for name in (_VAR, _OTHER_VAR):
        os.environ.pop(name, None)


def _env_file(tmp_path, content: str) -> str:
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    return str(path)


class TestParsing:
    def test_a_pair_reaches_the_environment(self, tmp_path):
        loaded = load_env_file(_env_file(tmp_path, f"{_VAR}=abc123\n"))
        assert os.environ[_VAR] == "abc123"
        assert loaded == (_VAR,)

    def test_the_value_is_cut_at_the_first_equals(self, tmp_path):
        """Un token de InfluxDB termina en `==`: partirlo por todos lo dejaría a medias."""
        load_env_file(_env_file(tmp_path, f"{_VAR}=zZ1_aB2==\n"))
        assert os.environ[_VAR] == "zZ1_aB2=="

    @pytest.mark.parametrize("line", [
        f'{_VAR}="abc123"',
        f"{_VAR}='abc123'",
        f"  {_VAR} = abc123  ",
        f"export {_VAR}=abc123",
    ])
    def test_the_shapes_an_installer_writes(self, tmp_path, line):
        load_env_file(_env_file(tmp_path, f"{line}\n"))
        assert os.environ[_VAR] == "abc123"

    def test_quotes_inside_the_value_survive(self, tmp_path):
        load_env_file(_env_file(tmp_path, f'{_VAR}=ab"cd\n'))
        assert os.environ[_VAR] == 'ab"cd'

    def test_comments_and_blank_lines_are_ignored(self, tmp_path):
        content = f"# un comentario\n\n{_VAR}=abc123\n\n#{_OTHER_VAR}=nope\n"
        assert load_env_file(_env_file(tmp_path, content)) == (_VAR,)
        assert _OTHER_VAR not in os.environ

    def test_a_line_without_equals_is_ignored(self, tmp_path):
        assert load_env_file(_env_file(tmp_path, "esto no declara nada\n")) == ()

    def test_a_line_without_a_name_is_ignored(self, tmp_path):
        assert load_env_file(_env_file(tmp_path, "=huerfano\n")) == ()

    def test_a_file_saved_from_notepad_still_parses(self, tmp_path):
        """El Notepad de Windows guarda con BOM y sin esto el primer nombre sale sucio."""
        path = tmp_path / ".env"
        path.write_text(f"{_VAR}=abc123\n", encoding="utf-8-sig")
        assert load_env_file(str(path)) == (_VAR,)
        assert os.environ[_VAR] == "abc123"


class TestPrecedence:
    def test_the_process_environment_wins(self, tmp_path):
        """
        El archivo llena huecos, no pisa: así un servicio o un `$env:VAR` puesto a mano
        para una prueba mandan sin que haya que editar el archivo.
        """
        os.environ[_VAR] = "del entorno"
        loaded = load_env_file(_env_file(tmp_path, f"{_VAR}=del archivo\n"))

        assert os.environ[_VAR] == "del entorno"
        assert loaded == (), "una variable que no se tocó no puede figurar como cargada"

    def test_the_rest_of_the_file_still_loads(self, tmp_path):
        os.environ[_VAR] = "del entorno"
        content = f"{_VAR}=del archivo\n{_OTHER_VAR}=nuevo\n"
        assert load_env_file(_env_file(tmp_path, content)) == (_OTHER_VAR,)
        assert os.environ[_OTHER_VAR] == "nuevo"


class TestMissingFile:
    def test_a_missing_file_is_not_an_error(self, tmp_path):
        """Una instalación sin telemetría y con cámaras abiertas no tiene secretos."""
        assert load_env_file(str(tmp_path / "no_existe.env")) == ()

    def test_a_directory_in_place_of_the_file_is_not_an_error(self, tmp_path):
        assert load_env_file(str(tmp_path)) == ()


class TestSecrecy:
    def test_the_values_never_reach_the_log(self, tmp_path, caplog):
        """El log va a consola y a archivo: los nombres sí, los valores nunca."""
        with caplog.at_level("DEBUG"):
            load_env_file(_env_file(tmp_path, f"{_VAR}=secretisimo\n"))

        assert "secretisimo" not in caplog.text
        assert _VAR in caplog.text
