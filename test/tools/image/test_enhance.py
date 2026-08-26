"""Tests del ajuste de visualización: qué aclara, qué no toca y cuándo no hay nada que
ajustar.

Se verifica el efecto sobre los píxeles y que el frame de entrada nunca se modifique: el
mismo array lo comparten la captura, el modelo y el dataset.
"""

import numpy as np
import pytest

from tools.image.enhance import (CLAHE_CLIP_MAX, GAMMA_MAX, GAMMA_MIN, apply_clahe,
                                 apply_gamma, build_display_adjust)


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
