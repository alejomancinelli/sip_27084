"""
Tests de la pestaña de proceso: de dónde salen sus campos y que `load()`/`save()` no
deformen lo que hay en el config.

A diferencia del resto de los tests de `ui/`, éste sí construye widgets, así que necesita
un `QApplication` sin pantalla —plataforma `offscreen`—. Se prueba acá porque el modo en
que estas pestañas fallan es silencioso y caro: abrir el panel y guardar sin tocar nada
tiene que dejar el `config.yaml` igual, y cuando no lo deja, lo que se pierde es la
configuración del proceso que alguien calibró en planta.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.views.config.process_tab import ProcessTab                      # noqa: E402

_SLOT = "camera_1"
_OTHER_SLOT = "camera_2"
_BELT_ROI = {"x_px": 425, "y_px": 0, "width_px": 1445, "height_px": 1515}


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get` punteado y `set`, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            "cameras": {_SLOT: {"name": "Cinta"}},
            f"cameras.{_SLOT}.name": "Cinta",
            "inference.models": {"segmenter": {}},
            "inference.models.segmenter.class_names": ["desmenuzado", "pellet"],
            "process.belt_roi_px": {_SLOT: dict(_BELT_ROI)},
            "process.dark_background_threshold": {_SLOT: {"desmenuzado": 60}},
            "process.rolling_window_s": 3600,
        }
        self._values.update(overrides)
        self.written: dict = {}

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self.written[key] = value


def _tab(**overrides) -> tuple[ProcessTab, _MockConfig]:
    config = _MockConfig(**overrides)
    tab = ProcessTab(config)
    tab.load()
    return tab, config


# ── De dónde salen los campos ────────────────────────────────────────────────

class TestFields:
    def test_it_shows_one_threshold_per_declared_class(self):
        """Las clases se leen del modelo: una lista propia acá podría discrepar."""
        tab, _ = _tab()
        assert list(tab._forms[_SLOT]._threshold_spins) == ["desmenuzado", "pellet"]

    def test_classes_of_every_model_are_covered(self):
        tab, _ = _tab(**{
            "inference.models": {"segmenter": {}, "classifier": {}},
            "inference.models.classifier.class_names": ["polvo"],
        })
        assert "polvo" in tab._forms[_SLOT]._threshold_spins

    def test_a_class_declared_twice_appears_once(self):
        """El umbral es por cámara y por clase: dos modelos con la misma clase, una fila."""
        tab, _ = _tab(**{
            "inference.models": {"segmenter": {}, "other": {}},
            "inference.models.other.class_names": ["pellet"],
        })
        assert list(tab._forms[_SLOT]._threshold_spins).count("pellet") == 1

    def test_one_sub_tab_per_camera(self):
        tab, _ = _tab(**{
            "cameras": {_SLOT: {"name": "Cinta"}, _OTHER_SLOT: {"name": "Cinta 2"}},
        })
        assert set(tab._forms) == {_SLOT, _OTHER_SLOT}

    def test_without_cameras_it_still_builds(self):
        """La ventana de las medias no es por cámara: la pestaña sigue sirviendo."""
        tab, _ = _tab(**{"cameras": {}})
        assert tab._forms == {}


# ── Carga ────────────────────────────────────────────────────────────────────

class TestLoad:
    def test_it_loads_the_belt_rectangle(self):
        tab, _ = _tab()
        spins = tab._forms[_SLOT]._roi_spins
        assert {key: spin.value() for key, spin in spins.items()} == _BELT_ROI

    def test_it_loads_the_threshold_of_each_class(self):
        tab, _ = _tab()
        spins = tab._forms[_SLOT]._threshold_spins
        assert (spins["desmenuzado"].value(), spins["pellet"].value()) == (60, 0)

    def test_it_loads_the_averaging_window(self):
        tab, _ = _tab()
        assert tab._window_spin.value() == 3600

    def test_a_camera_without_a_rectangle_loads_zeros(self):
        tab, _ = _tab(**{"process.belt_roi_px": {}})
        assert all(spin.value() == 0 for spin in tab._forms[_SLOT]._roi_spins.values())

    def test_loading_twice_does_not_accumulate(self):
        """Es lo que hace el botón de descartar: la misma función, sin estado."""
        tab, _ = _tab()
        tab.load()
        assert tab._forms[_SLOT]._roi_spins["x_px"].value() == _BELT_ROI["x_px"]


# ── Guardado ─────────────────────────────────────────────────────────────────

class TestSave:
    def test_saving_untouched_writes_back_the_same_values(self):
        """Abrir el panel y guardar no puede mover lo que alguien calibró en planta."""
        tab, config = _tab()
        tab.save()
        assert config.written[f"process.belt_roi_px.{_SLOT}.width_px"] == 1445
        assert config.written[
            f"process.dark_background_threshold.{_SLOT}.desmenuzado"] == 60
        assert config.written["process.rolling_window_s"] == 3600

    def test_an_edited_rectangle_reaches_the_config(self):
        tab, config = _tab()
        tab._forms[_SLOT]._roi_spins["x_px"].setValue(500)
        tab.save()
        assert config.written[f"process.belt_roi_px.{_SLOT}.x_px"] == 500

    def test_a_class_without_a_threshold_is_written_as_zero(self):
        """Cero es «sin refinar», que es un valor y no una ausencia."""
        tab, config = _tab()
        tab.save()
        assert config.written[f"process.dark_background_threshold.{_SLOT}.pellet"] == 0

    def test_it_does_not_persist_the_file(self):
        """El contrato: `save()` deja los valores en el ConfigManager y nada más."""
        tab, config = _tab()
        tab.save()
        assert not hasattr(config, "saved")


# ── Recarga desde el diálogo ─────────────────────────────────────────────────

class TestBeltRoiRefresh:
    def test_it_rereads_only_the_geometry(self):
        """Vuelve del diálogo, donde lo único que cambió fue el rectángulo."""
        tab, config = _tab()
        tab._forms[_SLOT]._threshold_spins["desmenuzado"].setValue(99)
        config._values["process.belt_roi_px"] = {_SLOT: {**_BELT_ROI, "x_px": 700}}
        tab.reload_roi(_SLOT)
        form = tab._forms[_SLOT]
        assert form._roi_spins["x_px"].value() == 700
        assert tab._forms[_SLOT]._threshold_spins["desmenuzado"].value() == 99

    def test_an_unknown_camera_is_ignored(self):
        tab, _ = _tab()
        tab.reload_roi("camera_9")

    def test_the_request_carries_where_to_save_it(self):
        """La pestaña es dueña de su sección: manda la clave, no la deduce quien abre."""
        tab, _ = _tab()
        asked = []
        tab.open_roi_requested.connect(lambda *args: asked.append(args))
        tab._forms[_SLOT]._build_belt_roi_box()
        tab.open_roi_requested.emit(
            _SLOT, f"process.belt_roi_px.{_SLOT}", "process_belt_title")
        assert asked == [(_SLOT, f"process.belt_roi_px.{_SLOT}", "process_belt_title")]
