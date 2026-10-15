"""
Tests de la fábrica de pipelines y del NullPipeline.

Sin modelos cargados ni framework: lo que se prueba es qué clase sale para cada `type`, no
qué detecta. Los tipos registrados se ponen con `monkeypatch` sobre el registro, así que el
archivo no depende de los pipelines que escriba cada fork.
"""

import numpy as np

from system.inference import pipeline_factory
from system.inference.abstract_pipeline import AbstractPipeline
from system.inference.null_pipeline import NullPipeline
from system.inference.pipeline_factory import (
    DEFAULT_PIPELINE, REGISTERED_PIPELINES, create_pipeline,
)

_SLOT = "pipeline_1"


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **values):
        self._values = values

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


def _config(pipeline_type: object = None) -> _MockConfig:
    if pipeline_type is None:
        return _MockConfig()
    return _MockConfig(**{f"inference.pipelines.{_SLOT}.type": pipeline_type})


class _DefaultPipeline(AbstractPipeline):
    model_slots = ("model_1",)

    def _run(self, frame_bgr, camera_slot):
        return []


class _PersonPipeline(AbstractPipeline):
    model_slots = ("person_detector",)

    def _run(self, frame_bgr, camera_slot):
        return []


def _register(monkeypatch):
    """Deja registrados `default` y `person` con clases propias del test."""
    monkeypatch.setitem(pipeline_factory._PIPELINE_CLASSES, DEFAULT_PIPELINE, _DefaultPipeline)
    monkeypatch.setitem(pipeline_factory._PIPELINE_CLASSES, "person", _PersonPipeline)


class TestPipelineFactory:
    def test_without_type_the_slot_runs_the_default(self, monkeypatch):
        _register(monkeypatch)
        assert isinstance(create_pipeline(_config(), _SLOT), _DefaultPipeline)

    def test_an_empty_type_runs_the_default(self, monkeypatch):
        """`type: ''` o `type: null` en el YAML es lo mismo que no declararlo."""
        _register(monkeypatch)
        assert isinstance(create_pipeline(_config(""), _SLOT), _DefaultPipeline)

    def test_a_registered_type_gives_its_class(self, monkeypatch):
        _register(monkeypatch)
        assert isinstance(create_pipeline(_config("person"), _SLOT), _PersonPipeline)

    def test_the_type_is_normalized(self, monkeypatch):
        _register(monkeypatch)
        assert isinstance(create_pipeline(_config("  PERSON "), _SLOT), _PersonPipeline)

    def test_the_pipeline_keeps_its_slot_as_identity(self, monkeypatch):
        _register(monkeypatch)
        assert create_pipeline(_config("person"), _SLOT).pipeline_slot == _SLOT

    def test_two_slots_of_the_same_type_are_two_instances(self, monkeypatch):
        """Así se reparten cámaras entre hilos: cada uno con sus propios pesos."""
        _register(monkeypatch)
        config = _MockConfig(**{"inference.pipelines.pipeline_1.type": "person",
                                "inference.pipelines.pipeline_2.type": "person"})
        first = create_pipeline(config, "pipeline_1")
        second = create_pipeline(config, "pipeline_2")
        assert first is not second
        assert (first.pipeline_slot, second.pipeline_slot) == ("pipeline_1", "pipeline_2")

    def test_the_default_is_registered(self):
        """Un config sin `type` tiene que funcionar en cualquier fork."""
        assert DEFAULT_PIPELINE in REGISTERED_PIPELINES

    def test_the_factory_does_not_load_the_models(self, monkeypatch):
        """Cargar pesos puede tardar minutos: lo hace el hilo del motor al arrancar."""
        _register(monkeypatch)
        assert create_pipeline(_config("person"), _SLOT).get_status()["models"] == {}


class TestUnregisteredType:
    """Un `type` mal escrito no puede caer en el default: correría un proceso que nadie eligió."""

    def test_it_gives_a_null_pipeline(self, monkeypatch):
        _register(monkeypatch)
        assert isinstance(create_pipeline(_config("persn"), _SLOT), NullPipeline)

    def test_the_null_pipeline_never_loads(self, monkeypatch):
        _register(monkeypatch)
        pipeline = create_pipeline(_config("persn"), _SLOT)
        pipeline.load()
        assert pipeline.is_loaded is False

    def test_the_null_pipeline_detects_nothing(self):
        pipeline = NullPipeline(_config(), _SLOT)
        assert pipeline.run(np.zeros((4, 4, 3), np.uint8), "camera_1") == []

    def test_the_null_pipeline_keeps_its_slot(self, monkeypatch):
        _register(monkeypatch)
        assert create_pipeline(_config("persn"), _SLOT).pipeline_slot == _SLOT
