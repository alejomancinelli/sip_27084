"""Tests del ConfigManager: singleton, carga y fallback, rutas punteadas,
aislamiento de los valores que entrega, guardado atómico y acceso concurrente."""

import logging
import os
import sys
import threading

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from system.config_manager import ConfigManager, _default_config_path

_VALID_YAML = """\
system:
  log_level: INFO
  paths:
    logs: ./data/logs
cameras:
  camera_1:
    name: CAM
    enabled: true
    roi: null
    tags:
      - uno
      - dos
"""


@pytest.fixture(autouse=True)
def reset_singleton():
    """Cada test arranca sin instancia: si no, el primero fija el path de todos."""
    ConfigManager._instance = None
    yield
    ConfigManager._instance = None


def _write_config(dir_path, text: str = _VALID_YAML) -> str:
    path = os.path.join(str(dir_path), "config.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _manager(config_path: str) -> ConfigManager:
    return ConfigManager(config_path)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestDefaultPath:
    def test_default_path_is_the_config_at_the_repo_root(self):
        expected = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "config.yaml")
        assert _default_config_path() == expected

    def test_default_path_does_not_depend_on_the_cwd(self, tmp_path, monkeypatch):
        """La app se lanza desde donde sea (servicio, systemd, doble clic)."""
        before = _default_config_path()
        monkeypatch.chdir(tmp_path)
        assert _default_config_path() == before


class TestSingleton:
    def test_second_call_returns_the_same_instance(self, tmp_path):
        first = _manager(_write_config(tmp_path))
        assert ConfigManager() is first

    def test_the_first_path_wins(self, tmp_path):
        path = _write_config(tmp_path)
        _manager(path)
        ignored = os.path.join(str(tmp_path), "otro.yaml")
        assert ConfigManager(ignored)._config_path == path

    def test_state_is_shared_between_references(self, tmp_path):
        cfg = _manager(_write_config(tmp_path))
        cfg.set("cameras.camera_1.name", "OTRO")
        assert ConfigManager().get("cameras.camera_1.name") == "OTRO"


class TestLoad:
    def test_valid_file_loads_without_fallback(self, tmp_path):
        cfg = _manager(_write_config(tmp_path))
        assert cfg.is_using_fallback is False
        assert cfg.get("cameras.camera_1.name") == "CAM"

    def test_load_returns_true_on_success(self, tmp_path):
        assert _manager(_write_config(tmp_path)).load() is True

    def test_reload_picks_up_changes_on_disk(self, tmp_path):
        path = _write_config(tmp_path)
        cfg = _manager(path)
        _write_config(tmp_path, "cameras:\n  camera_1:\n    name: NUEVO\n")
        assert cfg.load() is True
        assert cfg.get("cameras.camera_1.name") == "NUEVO"

    @pytest.mark.parametrize("text", [
        "system: [sin cerrar\n",          # YAML inválido
        "- uno\n- dos\n",                 # válido, pero es una lista
        "solo un escalar\n",              # válido, pero no es un mapa
        "",                               # archivo vacío
    ])
    def test_unusable_content_falls_back(self, tmp_path, text):
        cfg = _manager(_write_config(tmp_path, text))
        assert cfg.is_using_fallback is True
        assert cfg.load() is False

    def test_missing_file_falls_back(self, tmp_path):
        cfg = _manager(os.path.join(str(tmp_path), "no_existe.yaml"))
        assert cfg.is_using_fallback is True

    def test_unreadable_file_falls_back_instead_of_raising(self, tmp_path):
        """Un directorio en lugar del archivo: existe, pero open() da OSError."""
        cfg = _manager(str(tmp_path))
        assert cfg.is_using_fallback is True

    def test_the_fallback_mirrors_the_real_config_keys(self, tmp_path):
        """Con claves inventadas el fallback no serviría para leer nada."""
        cfg = _manager(os.path.join(str(tmp_path), "no_existe.yaml"))
        assert cfg.get("system.log_level") == "DEBUG"
        assert cfg.get("system.paths.logs") == "./data/logs"
        assert cfg.get("cameras") == {}

    def test_a_good_reload_clears_the_fallback_state(self, tmp_path):
        path = os.path.join(str(tmp_path), "config.yaml")
        cfg = _manager(path)
        assert cfg.is_using_fallback is True
        _write_config(tmp_path)
        assert cfg.load() is True
        assert cfg.is_using_fallback is False

    def test_a_failed_reload_does_not_keep_the_old_data(self, tmp_path):
        path = _write_config(tmp_path)
        cfg = _manager(path)
        _write_config(tmp_path, "system: [sin cerrar\n")
        cfg.load()
        assert cfg.get("cameras.camera_1.name") is None


class TestGet:
    def test_nested_value(self, tmp_path):
        assert _manager(_write_config(tmp_path)).get("system.paths.logs") == "./data/logs"

    def test_an_explicit_null_is_not_the_default(self, tmp_path):
        """`roi: null` en el YAML es un valor, no una clave ausente."""
        assert _manager(_write_config(tmp_path)).get("cameras.camera_1.roi", "DEFAULT") is None

    @pytest.mark.parametrize("key_path", [
        "no_existe",                       # clave de primer nivel
        "cameras.camera_9.name",           # nivel intermedio ausente
        "system.log_level.foo",            # el intermedio existe pero es un str
        "cameras.camera_1.roi.x_px",       # el intermedio es null
        "",                                # ruta vacía
    ])
    def test_unresolvable_path_returns_the_default(self, tmp_path, key_path):
        assert _manager(_write_config(tmp_path)).get(key_path, "DEFAULT") == "DEFAULT"

    def test_an_empty_path_does_not_return_the_whole_config(self, tmp_path):
        assert _manager(_write_config(tmp_path)).get("") is None

    def test_mutating_a_returned_dict_does_not_touch_the_config(self, tmp_path):
        cfg = _manager(_write_config(tmp_path))
        cameras = cfg.get("cameras")
        cameras["camera_1"]["name"] = "MUTADO"
        cameras["camera_2"] = {}
        assert cfg.get("cameras.camera_1.name") == "CAM"
        assert cfg.get("cameras.camera_2") is None

    def test_mutating_a_returned_list_does_not_touch_the_config(self, tmp_path):
        cfg = _manager(_write_config(tmp_path))
        cfg.get("cameras.camera_1.tags").append("tres")
        assert cfg.get("cameras.camera_1.tags") == ["uno", "dos"]

    def test_a_missing_key_is_warned_once(self, tmp_path, caplog):
        """Hay `get` que corren por frame: un aviso por llamada tapa el log."""
        cfg = _manager(_write_config(tmp_path))
        with caplog.at_level(logging.WARNING, logger="app"):
            for _ in range(5):
                cfg.get("no.existe", 0)
        assert len([r for r in caplog.records if "no.existe" in r.message]) == 1

    def test_a_reload_allows_warning_about_the_key_again(self, tmp_path, caplog):
        cfg = _manager(_write_config(tmp_path))
        cfg.get("no.existe", 0)
        cfg.load()
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="app"):
            cfg.get("no.existe", 0)
        assert len([r for r in caplog.records if "no.existe" in r.message]) == 1


class TestSet:
    def test_overwrites_an_existing_value(self, tmp_path):
        cfg = _manager(_write_config(tmp_path))
        assert cfg.set("cameras.camera_1.name", "OTRO") is True
        assert cfg.get("cameras.camera_1.name") == "OTRO"

    def test_creates_the_missing_levels(self, tmp_path):
        cfg = _manager(_write_config(tmp_path))
        assert cfg.set("modbus.tcp.port", 502) is True
        assert cfg.get("modbus.tcp.port") == 502

    def test_fills_in_a_null_section(self, tmp_path):
        """`roi: null` es lo que trae la plantilla; escribir ahí tiene que andar."""
        cfg = _manager(_write_config(tmp_path))
        assert cfg.set("cameras.camera_1.roi.x_px", 5) is True
        assert cfg.get("cameras.camera_1.roi") == {"x_px": 5}

    def test_refuses_to_write_through_a_non_mapping(self, tmp_path):
        cfg = _manager(_write_config(tmp_path))
        assert cfg.set("cameras.camera_1.name.foo", 1) is False
        assert cfg.get("cameras.camera_1.name") == "CAM"

    def test_refuses_an_empty_path(self, tmp_path):
        cfg = _manager(_write_config(tmp_path))
        assert cfg.set("", 1) is False

    def test_does_not_touch_the_disk(self, tmp_path):
        path = _write_config(tmp_path)
        _manager(path).set("cameras.camera_1.name", "OTRO")
        assert _read(path) == _VALID_YAML

    def test_stores_a_copy_of_the_value(self, tmp_path):
        cfg = _manager(_write_config(tmp_path))
        roi = {"x_px": 0}
        cfg.set("cameras.camera_1.roi", roi)
        roi["x_px"] = 99
        assert cfg.get("cameras.camera_1.roi.x_px") == 0


class TestSave:
    def test_saves_and_survives_a_reload(self, tmp_path):
        path = _write_config(tmp_path)
        cfg = _manager(path)
        cfg.set("cameras.camera_1.roi.x_px", 5)
        assert cfg.save() is True
        assert cfg.load() is True
        assert cfg.get("cameras.camera_1.roi.x_px") == 5

    def test_leaves_no_tmp_file_behind(self, tmp_path):
        path = _write_config(tmp_path)
        _manager(path).save()
        assert os.listdir(str(tmp_path)) == ["config.yaml"]

    def test_keeps_the_key_order(self, tmp_path):
        """Ordenar alfabéticamente haría ilegible el diff de un cambio de la UI."""
        path = _write_config(tmp_path)
        cfg = _manager(path)
        cfg.save()
        assert list(yaml.safe_load(_read(path))) == ["system", "cameras"]

    def test_the_yaml_comments_do_not_survive(self, tmp_path):
        """Límite conocido del guardado: se reescribe desde el dict en memoria."""
        path = _write_config(tmp_path, "system:\n  log_level: INFO   # comentario\n")
        _manager(path).save()
        assert "# comentario" not in _read(path)

    def test_a_value_that_is_not_plain_yaml_fails_without_touching_the_file(self, tmp_path):
        """El dumper completo lo escribiría con un tag !!python/... que después
        safe_load rechaza: el archivo quedaría ilegible para la propia app."""
        path = _write_config(tmp_path)
        cfg = _manager(path)
        cfg.set("system.raro", object())
        assert cfg.save() is False
        assert os.listdir(str(tmp_path)) == ["config.yaml"]
        assert _read(path) == _VALID_YAML

    def test_what_gets_saved_is_readable_again(self, tmp_path):
        path = _write_config(tmp_path)
        cfg = _manager(path)
        cfg.set("cameras.camera_1.tags", ["tres"])
        cfg.save()
        assert yaml.safe_load(_read(path))["cameras"]["camera_1"]["tags"] == ["tres"]

    def test_an_unwritable_path_returns_false(self, tmp_path):
        cfg = _manager(os.path.join(str(tmp_path), "sin_carpeta", "config.yaml"))
        assert cfg.save() is False

    def test_the_fallback_does_not_overwrite_an_existing_file(self, tmp_path):
        """Un YAML roto se arregla a mano; pisarlo con el esqueleto lo pierde."""
        broken = "system: [sin cerrar\n"
        path = _write_config(tmp_path, broken)
        cfg = _manager(path)
        assert cfg.is_using_fallback is True
        assert cfg.save() is False
        assert _read(path) == broken

    def test_the_fallback_bootstraps_a_missing_file(self, tmp_path):
        path = os.path.join(str(tmp_path), "config.yaml")
        cfg = _manager(path)
        assert cfg.save() is True
        assert cfg.load() is True
        assert cfg.is_using_fallback is False
        assert cfg.get("system.log_level") == "DEBUG"


class TestThreadSafety:
    def test_concurrent_writers_and_saves_do_not_lose_keys(self, tmp_path):
        cfg = _manager(_write_config(tmp_path))
        errors = []

        def work(index: int):
            try:
                for i in range(20):
                    cfg.set(f"cameras.camera_{index}.roi.x_px", i)
                    cfg.save()
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=work, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert errors == []
        assert cfg.load() is True
        for index in range(4):
            assert cfg.get(f"cameras.camera_{index}.roi.x_px") == 19

    def test_iterating_what_get_returns_is_safe_while_another_thread_writes(self, tmp_path):
        """Sin la copia, el consumidor recorre el dict vivo y sin lock."""
        cfg = _manager(_write_config(tmp_path))
        errors = []
        stop = threading.Event()

        def write_slots():
            index = 0
            while not stop.is_set() and index < 300:
                cfg.set(f"cameras.camera_{index}", {"name": "x"})
                index += 1

        writer = threading.Thread(target=write_slots, daemon=True)
        writer.start()
        try:
            for _ in range(300):
                for slot, data in cfg.get("cameras", {}).items():
                    assert slot
        except RuntimeError as error:
            errors.append(error)
        finally:
            stop.set()
            writer.join(timeout=5)

        assert errors == []
