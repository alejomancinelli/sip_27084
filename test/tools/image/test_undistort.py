"""Tests de la corrección de lente: qué cámara se corrige, qué pasa con una calibración
que no sirve, y que los mapas se calculen una sola vez.

La calibración de los tests es sintética: lo que se verifica es el andamio —despacho por
cámara, validación, caché— y no la exactitud de OpenCV.
"""

import numpy as np
import pytest

from tools.image.undistort import build_undistorter

_SLOT = "camera_1"
_OTHER_SLOT = "camera_2"

# Calibración con barril notable, para que corregir cambie píxeles de verdad.
_CALIBRATION = {"camera_matrix": [[300.0, 0.0, 60.0], [0.0, 300.0, 40.0], [0.0, 0.0, 1.0]],
                "dist_coeffs": [-0.4, 0.15, 0.0, 0.0, 0.0]}


def _frame() -> np.ndarray:
    """Frame con estructura: una imagen plana se ve igual corregida o no."""
    frame = np.zeros((80, 120, 3), np.uint8)
    frame[::8, :] = 255
    frame[:, ::8] = 255
    return frame


class TestBuildUndistorter:
    def test_without_calibration_there_is_nothing_to_build(self):
        assert build_undistorter({}) is None
        assert build_undistorter(None) is None

    @pytest.mark.parametrize("calibration", [
        {},                                                        # sección vacía
        {"camera_matrix": [], "dist_coeffs": []},                   # el default del template
        {"camera_matrix": _CALIBRATION["camera_matrix"]},           # sin coeficientes
        {"dist_coeffs": _CALIBRATION["dist_coeffs"]},               # sin matriz
        {"camera_matrix": [[1.0, 2.0], [3.0, 4.0]],
         "dist_coeffs": _CALIBRATION["dist_coeffs"]},               # matriz de 2x2
        {"camera_matrix": _CALIBRATION["camera_matrix"],
         "dist_coeffs": [0.1, 0.2]},                                # 2 coeficientes
        {"camera_matrix": "3x3", "dist_coeffs": "nada"},            # texto en el YAML
        "no es un mapa",
    ])
    def test_an_unusable_calibration_is_ignored(self, calibration):
        """Corregir con una matriz inventada desplazaría cada medición sin avisar."""
        assert build_undistorter({_SLOT: calibration}) is None

    def test_it_corrects_the_declared_camera(self):
        undistort = build_undistorter({_SLOT: _CALIBRATION})
        assert not np.array_equal(undistort(_frame(), _SLOT), _frame())

    def test_it_keeps_the_frame_size(self):
        undistort = build_undistorter({_SLOT: _CALIBRATION})
        assert undistort(_frame(), _SLOT).shape == _frame().shape

    def test_a_camera_without_calibration_passes_through(self):
        """La misma función sirve para un pipeline con cámaras calibradas y sin calibrar."""
        undistort = build_undistorter({_SLOT: _CALIBRATION})
        frame = _frame()
        assert undistort(frame, _OTHER_SLOT) is frame

    def test_each_camera_uses_its_own_calibration(self):
        soft = {**_CALIBRATION, "dist_coeffs": [-0.05, 0.0, 0.0, 0.0, 0.0]}
        undistort = build_undistorter({_SLOT: _CALIBRATION, _OTHER_SLOT: soft})
        assert not np.array_equal(undistort(_frame(), _SLOT), undistort(_frame(), _OTHER_SLOT))

    def test_the_input_frame_is_never_touched(self):
        undistort = build_undistorter({_SLOT: _CALIBRATION})
        frame = _frame()
        undistort(frame, _SLOT)
        assert np.array_equal(frame, _frame())

    def test_an_empty_frame_comes_back_untouched(self):
        undistort = build_undistorter({_SLOT: _CALIBRATION})
        empty = np.zeros((0, 0, 3), np.uint8)
        assert undistort(empty, _SLOT).size == 0

    def test_two_frames_of_the_same_size_reuse_the_maps(self):
        """Recalcular los mapas por frame cuesta más que la corrección misma."""
        calls = []
        import tools.image.undistort as ud
        original = ud._build_maps
        try:
            ud._build_maps = lambda *args: calls.append(1) or original(*args)
            undistort = build_undistorter({_SLOT: _CALIBRATION})
            undistort(_frame(), _SLOT)
            undistort(_frame(), _SLOT)
        finally:
            ud._build_maps = original
        assert calls == [1]

    def test_a_different_frame_size_rebuilds_them(self):
        undistort = build_undistorter({_SLOT: _CALIBRATION})
        undistort(_frame(), _SLOT)
        small = np.zeros((40, 60, 3), np.uint8)
        assert undistort(small, _SLOT).shape == small.shape

    def test_keep_field_changes_how_much_is_kept(self):
        """keep_field elige entre recortar los bordes o conservarlos: no son la misma imagen."""
        cropped = build_undistorter({_SLOT: _CALIBRATION}, keep_field=0.0)(_frame(), _SLOT)
        full = build_undistorter({_SLOT: _CALIBRATION}, keep_field=1.0)(_frame(), _SLOT)
        assert not np.array_equal(cropped, full)

    @pytest.mark.parametrize("keep_field", [-1.0, 2.0])
    def test_keep_field_saturates(self, keep_field):
        undistort = build_undistorter({_SLOT: _CALIBRATION}, keep_field=keep_field)
        assert undistort(_frame(), _SLOT).shape == _frame().shape

    def test_it_names_the_cameras_it_corrects(self):
        assert _SLOT in build_undistorter({_SLOT: _CALIBRATION}).__name__
