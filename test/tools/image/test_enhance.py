"""Tests del ajuste de imagen: qué aclara, qué no toca, cuándo no hay nada que ajustar y
cómo se reparte por cámara cuando entra al preprocessor.

Se verifica el efecto sobre los píxeles y que el frame de entrada nunca se modifique: el
mismo array lo comparten la captura, el modelo y el dataset.
"""

import numpy as np
import pytest

from tools.image.enhance import (BRIGHTNESS_FACTOR_MAX, CLAHE_CLIP_MAX, GAMMA_MAX, GAMMA_MIN,
                                 apply_clahe, apply_gamma, build_display_adjust,
                                 build_image_adjust)


def _frame(level: int = 60) -> np.ndarray:
    return np.full((40, 60, 3), level, np.uint8)


def _gradient() -> np.ndarray:
    """Frame con una zona oscura y una clara: es donde CLAHE hace algo."""
    frame = np.zeros((40, 60, 3), np.uint8)
    frame[:, :30] = 30
    frame[:, 30:] = 220
    return frame


class TestGamma:
    def test_a_gamma_below_one_brightens(self):
        assert apply_gamma(_frame(60), 0.5).mean() > 60

    def test_a_gamma_above_one_darkens(self):
        assert apply_gamma(_frame(60), 2.0).mean() < 60

    def test_it_does_not_clip_the_extremes(self):
        """Negro sigue negro y blanco sigue blanco: la curva no recorta rango."""
        frame = np.array([[[0, 0, 0], [255, 255, 255]]], np.uint8)
        adjusted = apply_gamma(frame, 0.5)
        assert (tuple(adjusted[0, 0]), tuple(adjusted[0, 1])) == ((0, 0, 0), (255, 255, 255))

    @pytest.mark.parametrize("gamma", [1.0, 1.005, GAMMA_MIN / 2, GAMMA_MAX * 2])
    def test_a_neutral_or_out_of_range_gamma_changes_nothing(self, gamma):
        assert np.array_equal(apply_gamma(_frame(), gamma), _frame())

    def test_the_input_frame_is_never_touched(self):
        frame = _frame(60)
        apply_gamma(frame, 0.5)
        assert np.array_equal(frame, _frame(60))

    def test_it_returns_a_new_array_even_with_no_change(self):
        frame = _frame()
        assert apply_gamma(frame, 1.0) is not frame


class TestClahe:
    def test_it_changes_a_frame_with_dark_and_bright_zones(self):
        assert not np.array_equal(apply_clahe(_gradient(), 2.0), _gradient())

    def test_it_keeps_the_hue(self):
        """Ecualiza la luminancia: un gris no puede salir con tinte."""
        adjusted = apply_clahe(_frame(60), 4.0)
        assert adjusted[:, :, 0].mean() == pytest.approx(adjusted[:, :, 2].mean(), abs=2)

    def test_a_clip_of_zero_changes_nothing(self):
        assert np.array_equal(apply_clahe(_frame(), 0.0), _frame())

    def test_the_clip_saturates(self):
        """Un clip enorme no puede reventar: se acota al máximo del módulo."""
        assert apply_clahe(_gradient(), CLAHE_CLIP_MAX * 100).shape == _gradient().shape

    def test_it_works_on_a_gray_frame(self):
        gray = np.full((40, 60), 60, np.uint8)
        assert apply_clahe(gray, 2.0).shape == (40, 60)

    def test_the_input_frame_is_never_touched(self):
        frame = _gradient()
        apply_clahe(frame, 3.0)
        assert np.array_equal(frame, _gradient())


class TestBuildDisplayAdjust:
    def test_nothing_to_adjust_returns_none(self):
        """El llamador se saltea la llamada en vez de copiar el frame para dejarlo igual."""
        assert build_display_adjust(gamma=1.0, clahe_clip=0.0) is None

    @pytest.mark.parametrize("gamma", [GAMMA_MIN / 2, GAMMA_MAX * 2])
    def test_an_out_of_range_gamma_alone_returns_none(self, gamma):
        assert build_display_adjust(gamma=gamma) is None

    def test_gamma_alone_builds_an_adjustment(self):
        adjust = build_display_adjust(gamma=0.5)
        assert adjust is not None and adjust(_frame(60)).mean() > 60

    def test_clahe_alone_builds_an_adjustment(self):
        adjust = build_display_adjust(clahe_clip=2.0)
        assert adjust is not None
        assert not np.array_equal(adjust(_gradient()), _gradient())

    def test_the_two_stack(self):
        gamma_only = build_display_adjust(gamma=0.5)(_gradient())
        both = build_display_adjust(gamma=0.5, clahe_clip=3.0)(_gradient())
        assert not np.array_equal(gamma_only, both)

    def test_the_adjustment_returns_a_new_array(self):
        frame = _frame()
        adjusted = build_display_adjust(gamma=0.5)(frame)
        assert adjusted is not frame
        assert np.array_equal(frame, _frame())

    def test_an_empty_frame_comes_back_untouched(self):
        empty = np.zeros((0, 0, 3), np.uint8)
        assert build_display_adjust(gamma=0.5)(empty).size == 0

    def test_it_names_itself_with_its_parameters(self):
        """El aviso de quien lo aplica tiene que decir con qué estaba configurado."""
        assert "0.5" in build_display_adjust(gamma=0.5).__name__


class TestBuildImageAdjust:
    """El ajuste de medición por cámara, con la firma del `preprocessor` del motor."""

    @pytest.mark.parametrize("adjust_by_camera", [
        {},
        None,
        {"camera_1": {}},
        {"camera_1": {"brightness_factor": 1.0, "clahe_clip": 0.0}},
        {"camera_1": "no es un dict"},
    ])
    def test_nothing_to_adjust_returns_none(self, adjust_by_camera):
        """Sin ajuste el motor no gasta la pasada: el preprocessor no existe."""
        assert build_image_adjust(adjust_by_camera) is None

    @pytest.mark.parametrize("factor", [0.3, 1.8, 4.6])
    def test_the_brightness_is_the_linear_factor_of_the_equipment_it_reproduces(self, factor):
        """
        Píxel a píxel la cuenta del equipo que se reproduce: multiplicar en float32 y
        truncar a uint8. Otra curva le daría al modelo imágenes que no vio al entrenar.
        """
        frame = np.arange(256, dtype=np.uint8).reshape(16, 16, 1).repeat(3, axis=2)
        expected = np.clip(frame.astype(np.float32) * factor, 0, 255).astype(np.uint8)
        adjust = build_image_adjust({"camera_1": {"brightness_factor": factor}})
        assert np.array_equal(adjust(frame, "camera_1"), expected)

    def test_above_one_brightens_and_saturates(self):
        """Más número es más brillo, y lo que pasa de 255 queda blanco, como en el equipo."""
        adjust = build_image_adjust({"camera_1": {"brightness_factor": 4.0}})
        assert adjust(_frame(20), "camera_1").mean() == 80
        assert adjust(_frame(100), "camera_1").mean() == 255

    def test_below_one_darkens(self):
        adjust = build_image_adjust({"camera_1": {"brightness_factor": 0.5}})
        assert adjust(_frame(60), "camera_1").mean() == 30

    def test_an_out_of_range_factor_is_saturated_not_ignored(self):
        """Es un número copiado de otro equipo: ignorarlo dejaría al modelo sin su ajuste."""
        adjust = build_image_adjust({"camera_1": {"brightness_factor": 9.0}})
        assert adjust(_frame(20), "camera_1").mean() == 20 * BRIGHTNESS_FACTOR_MAX

    def test_each_camera_gets_its_own_parameters(self):
        """La luz es de cada montaje: una cámara oscura no arrastra a la de al lado."""
        adjust = build_image_adjust({"camera_1": {"brightness_factor": 2.0},
                                     "camera_2": {"brightness_factor": 0.5}})
        assert adjust(_frame(60), "camera_1").mean() > 60
        assert adjust(_frame(60), "camera_2").mean() < 60

    def test_a_camera_without_adjustment_passes_the_same_array(self):
        """Ni copia ni cambio: el frame de referencia de esa cámara sigue siendo el suyo."""
        adjust = build_image_adjust({"camera_1": {"brightness_factor": 2.0}})
        frame = _frame(60)
        assert adjust(frame, "camera_2") is frame

    def test_the_local_contrast_runs_on_the_brightened_frame(self):
        """El CLAHE ecualiza la imagen como la ve el modelo, ya aclarada."""
        both = build_image_adjust({"camera_1": {"brightness_factor": 2.0, "clahe_clip": 2.0}})
        factor_only = build_image_adjust({"camera_1": {"brightness_factor": 2.0}})
        expected = apply_clahe(factor_only(_gradient(), "camera_1"), 2.0)
        assert np.array_equal(both(_gradient(), "camera_1"), expected)

    def test_the_camera_frame_is_never_touched(self):
        """El mismo array lo usan la vista cruda y la nitidez de la óptica."""
        frame = _frame(60)
        build_image_adjust({"camera_1": {"brightness_factor": 2.0}})(frame, "camera_1")
        assert np.array_equal(frame, _frame(60))

    def test_it_names_the_cameras_it_adjusts(self):
        """Es lo que dice el log de arranque: qué cámaras miden sobre un frame ajustado."""
        adjust = build_image_adjust({"camera_2": {"brightness_factor": 2.0},
                                     "camera_1": {"clahe_clip": 2.0}, "camera_3": {}})
        assert adjust.__name__ == "image_adjust(camera_1, camera_2)"
