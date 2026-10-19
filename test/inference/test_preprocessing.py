"""
Tests del preprocessor del motor: la cadena que arma el frame de referencia.

Los dos builders se reemplazan por dobles que dejan rastro: lo que se prueba es la
composición —qué entra, en qué orden y cuándo no hay nada—, no la corrección ni el ajuste
de brillo, que tienen sus propios tests. Un caso va sin dobles, para ver que la clave del
config llega de verdad al builder.
"""

import numpy as np

from system.inference import preprocessing
from system.inference.preprocessing import build_preprocessor


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = dict(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


def _with_doubles(monkeypatch, *, with_undistort: bool, with_adjust: bool) -> list:
    calls: list = []

    def undistort(frame, camera_slot):
        calls.append(("undistort", camera_slot))
        return frame + 1

    def adjust(frame, camera_slot):
        calls.append(("adjust", camera_slot))
        return frame * 2

    monkeypatch.setattr(preprocessing, "build_undistorter",
                        lambda calibration: undistort if with_undistort else None)
    monkeypatch.setattr(preprocessing, "build_image_adjust",
                        lambda adjust_by_camera: adjust if with_adjust else None)
    return calls


_ONE_CAMERA = _MockConfig(cameras={"camera_1": {}})


class TestChain:
    def test_with_both_the_geometry_goes_first(self, monkeypatch):
        """El contraste local ecualiza por celdas: tiene que correr sobre el frame corregido."""
        calls = _with_doubles(monkeypatch, with_undistort=True, with_adjust=True)
        frame = build_preprocessor(_ONE_CAMERA)(np.zeros((2, 2), np.int32), "camera_1")
        assert calls == [("undistort", "camera_1"), ("adjust", "camera_1")]
        assert (frame == 2).all()

    def test_with_both_the_name_says_what_runs(self, monkeypatch):
        _with_doubles(monkeypatch, with_undistort=True, with_adjust=True)
        assert build_preprocessor(_ONE_CAMERA).__name__ == "undistort + adjust"

    def test_with_only_the_lens_it_is_the_undistorter_itself(self, monkeypatch):
        """Con uno solo no se envuelve: el motor no paga una llamada de más por frame."""
        _with_doubles(monkeypatch, with_undistort=True, with_adjust=False)
        assert build_preprocessor(_ONE_CAMERA).__name__ == "undistort"

    def test_with_only_the_adjustment_it_is_the_adjustment_itself(self, monkeypatch):
        _with_doubles(monkeypatch, with_undistort=False, with_adjust=True)
        assert build_preprocessor(_ONE_CAMERA).__name__ == "adjust"

    def test_with_neither_there_is_no_preprocessor(self, monkeypatch):
        """Sin nada declarado el motor mide el frame de la cámara y no gasta la pasada."""
        _with_doubles(monkeypatch, with_undistort=False, with_adjust=False)
        assert build_preprocessor(_ONE_CAMERA) is None


class TestConfig:
    def test_the_adjustment_is_read_from_each_camera(self):
        """Sin dobles: la clave del config llega al builder y ajusta sólo esa cámara."""
        config = _MockConfig(**{
            "cameras": {"camera_1": {}, "camera_2": {}},
            "cameras.camera_1.image_adjust": {"brightness_factor": 2.0, "clahe_clip": 0.0},
        })
        preprocessor = build_preprocessor(config)
        frame = np.full((4, 4, 3), 60, np.uint8)
        assert preprocessor(frame, "camera_1").mean() > 60
        assert preprocessor(frame, "camera_2") is frame

    def test_without_cameras_there_is_no_preprocessor(self):
        assert build_preprocessor(_MockConfig()) is None
