"""
Tests de la lectura de pesos del contrato: `_read_weights()` y la metadata protegida.

Sin framework de inferencia: la implementación de prueba se queda con los bytes y no los
interpreta, que es justo lo que hace falta para verificar el camino —descifrado, motivo
del fallo y precedencia de la metadata— sin GPU ni pesos de verdad.
"""

import numpy as np
import pytest

from system.inference.models import encrypted_weights as ew
from system.inference.models import model_key
from system.inference.models.abstract_model import (STATUS_ERROR, STATUS_LOADED,
                                                    STATUS_UNLOADED, TASK_DETECTION,
                                                    AbstractModel)
from system.inference.models.mock_model import MockModel
from system.inference.result import Detection

_SLOT = "model_1"
_WEIGHTS = b"bytes-de-unos-pesos" * 32
_KEY = bytes(range(32))
_OTHER_KEY = bytes(range(32, 64))


class _StubConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, section: dict | None = None):
        self._values = {}
        if section is not None:
            self._values[f"inference.models.{_SLOT}"] = section
            for key, value in section.items():
                self._values[f"inference.models.{_SLOT}.{key}"] = value

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _WeightsModel(AbstractModel):
    """Guarda los bytes que le devuelve el contrato y no los interpreta."""

    task = TASK_DETECTION

    def __init__(self, config, model_slot: str):
        super().__init__(config, model_slot)
        self.weights = b""

    def load(self):
        self.weights = self._read_weights(self._get_model_path())
        if not self.weights:
            return
        self._set_loaded()

    def predict(self, frame_bgr: np.ndarray,
                previous: list[Detection] | None = None) -> list[Detection]:
        return []

    def unload(self):
        self._status = STATUS_UNLOADED


@pytest.fixture
def with_key(monkeypatch):
    """Deja el build con la clave de prueba, como si el módulo generado estuviera."""
    monkeypatch.setattr(model_key, "get_model_key", lambda: _KEY)
    monkeypatch.setattr(model_key, "get_key_label", lambda: "test-1")


@pytest.fixture
def without_key(monkeypatch):
    """Deja el build sin clave, que es el estado de un clon del repo."""
    monkeypatch.setattr(model_key, "get_model_key", lambda: None)


def _write(tmp_path, name: str, payload: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(payload)
    return str(path)


def _model(path: str, **section) -> _WeightsModel:
    section.setdefault("path", path)
    model = _WeightsModel(_StubConfig(section), _SLOT)
    model.load()
    return model


class TestPlainWeights:
    def test_plain_weights_load_without_any_key(self, tmp_path, without_key):
        model = _model(_write(tmp_path, "best.pt", _WEIGHTS))
        assert model.weights == _WEIGHTS
        assert model.status == STATUS_LOADED

    def test_plain_weights_leave_no_metadata(self, tmp_path, without_key):
        model = _model(_write(tmp_path, "best.pt", _WEIGHTS), class_names=["a", "b"])
        assert model._weights_metadata == {}
        assert model._get_class_name(1) == "b"


class TestProtectedWeights:
    def test_protected_weights_open_with_the_key_of_the_build(self, tmp_path, with_key):
        raw = ew.pack(_WEIGHTS, _KEY)
        model = _model(_write(tmp_path, "best.pt.enc", raw))
        assert model.weights == _WEIGHTS
        assert model.status == STATUS_LOADED

    def test_the_metadata_wins_over_the_config(self, tmp_path, with_key):
        raw = ew.pack(_WEIGHTS, _KEY, {"class_names": ["tornillo", "tuerca"],
                                       "min_confidence_pct": 30.0})
        model = _model(_write(tmp_path, "best.pt.enc", raw),
                       class_names=["viejo"], min_confidence_pct=90.0)
        assert model._get_class_name(1) == "tuerca"
        assert model.min_confidence_pct == 30.0

    def test_the_config_still_answers_what_the_metadata_does_not_declare(self, tmp_path,
                                                                        with_key):
        raw = ew.pack(_WEIGHTS, _KEY, {"class_names": ["tornillo"]})
        model = _model(_write(tmp_path, "best.pt.enc", raw), max_side_px=640)
        assert model._get_max_side_px() == 640

    def test_the_metadata_cannot_move_the_path(self, tmp_path, with_key):
        # Un archivo que declarara desde dónde se carga sería un archivo diciendo cómo
        # abrirse: la ruta la sigue poniendo el config.
        path = _write(tmp_path, "best.pt.enc", ew.pack(_WEIGHTS, _KEY, {"path": "otro.pt"}))
        model = _model(path)
        assert model._get_model_path() == path

    def test_a_task_mismatch_in_the_metadata_fails_before_the_first_frame(self, tmp_path,
                                                                         with_key):
        raw = ew.pack(_WEIGHTS, _KEY, {"task": "segmentation"})
        model = _model(_write(tmp_path, "best.pt.enc", raw))
        assert model.status == STATUS_ERROR
        assert not model.weights
        assert "segmentation" in model.error

    def test_a_task_that_agrees_loads(self, tmp_path, with_key):
        raw = ew.pack(_WEIGHTS, _KEY, {"task": "detect"})
        assert _model(_write(tmp_path, "best.pt.enc", raw)).status == STATUS_LOADED


class TestFailuresLeaveTheReason:
    def test_without_a_key_the_reason_names_the_key(self, tmp_path, without_key):
        model = _model(_write(tmp_path, "best.pt.enc", ew.pack(_WEIGHTS, _KEY)))
        assert model.status == STATUS_ERROR
        assert "clave" in model.error.lower()

    def test_the_wrong_key_does_not_open_the_weights(self, tmp_path, monkeypatch):
        monkeypatch.setattr(model_key, "get_model_key", lambda: _OTHER_KEY)
        model = _model(_write(tmp_path, "best.pt.enc", ew.pack(_WEIGHTS, _KEY)))
        assert model.status == STATUS_ERROR
        assert not model.weights

    def test_a_flipped_bit_does_not_load(self, tmp_path, with_key):
        raw = bytearray(ew.pack(_WEIGHTS, _KEY))
        raw[-1] ^= 0x01
        model = _model(_write(tmp_path, "best.pt.enc", bytes(raw)))
        assert model.status == STATUS_ERROR

    def test_a_truncated_file_does_not_load(self, tmp_path, with_key):
        raw = ew.pack(_WEIGHTS, _KEY)[:ew.HEADER_SIZE + 4]
        model = _model(_write(tmp_path, "best.pt.enc", raw))
        assert model.status == STATUS_ERROR

    def test_a_missing_file_names_the_path(self, tmp_path, with_key):
        model = _model(str(tmp_path / "no-existe.pt"))
        assert model.status == STATUS_ERROR
        assert "no-existe.pt" in model.error

    def test_an_empty_file_is_not_weights(self, tmp_path, with_key):
        model = _model(_write(tmp_path, "best.pt", b""))
        assert model.status == STATUS_ERROR

    def test_a_section_without_path_says_so(self, with_key):
        model = _WeightsModel(_StubConfig({"type": "stub"}), _SLOT)
        model.load()
        assert model.status == STATUS_ERROR
        assert "path" in model.error

    def test_a_second_read_forgets_the_metadata_of_the_first(self, tmp_path, with_key):
        model = _WeightsModel(_StubConfig({"path": "x"}), _SLOT)
        model._read_weights(_write(tmp_path, "best.pt.enc",
                                   ew.pack(_WEIGHTS, _KEY, {"class_names": ["tornillo"]})))
        model._read_weights(_write(tmp_path, "otro.pt", _WEIGHTS))
        assert model._weights_metadata == {}


class TestMockModelReadsItsWeights:
    """El mock no necesita pesos, pero si su sección declara `path` los lee por el mismo
    camino: es lo que deja probar el cifrado de punta a punta sin ningún framework."""

    def _mock(self, path: str, **section) -> MockModel:
        section.setdefault("type", "mock")
        section.setdefault("path", path)
        model = MockModel(_StubConfig(section), _SLOT)
        model.load()
        return model

    def test_it_loads_protected_weights_and_uses_their_class_names(self, tmp_path, with_key):
        raw = ew.pack(_WEIGHTS, _KEY, {"class_names": ["tornillo", "tuerca", "arandela"]})
        model = self._mock(_write(tmp_path, "best.pt.enc", raw))
        assert model.status == STATUS_LOADED
        frame = np.full((200, 300, 3), 128, np.uint8)
        assert [d.class_name for d in model.predict(frame)] == ["tornillo", "tuerca",
                                                                "arandela"]

    def test_it_does_not_load_when_the_weights_do_not_open(self, tmp_path, without_key):
        model = self._mock(_write(tmp_path, "best.pt.enc", ew.pack(_WEIGHTS, _KEY)))
        assert model.status == STATUS_ERROR
        assert model.predict(np.full((200, 300, 3), 128, np.uint8)) == []

    def test_without_a_path_it_keeps_working_as_before(self, without_key):
        model = MockModel(_StubConfig({"type": "mock"}), _SLOT)
        model.load()
        assert model.status == STATUS_LOADED
