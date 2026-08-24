"""Tests de las condiciones de guardado del recolector de dataset."""

import inspect
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from system import image_collector_conditions as conditions
from system.image_collector_conditions import (
    field_at_least_condition,
    field_truthy_condition,
    min_confidence_condition,
)

# Factories con casos propios en este archivo. Agregar una condición al módulo
# implica agregarla acá: TestModuleSurface falla hasta que eso pase.
_COVERED_FACTORIES = {
    "field_at_least_condition",
    "field_truthy_condition",
    "min_confidence_condition",
}

# Un predicado ya armado por cada factory, para los tests que valen para todas.
_PREDICATES = (
    field_at_least_condition("confidence_pct", 75.0),
    field_truthy_condition("alarm"),
    min_confidence_condition(75.0),
)


def _public_factories() -> set[str]:
    """Nombres de las factories que el módulo publica, sin lo que importa de afuera."""
    return {
        name for name, obj in vars(conditions).items()
        if not name.startswith("_")
        and inspect.isfunction(obj)
        and obj.__module__ == conditions.__name__
    }


class TestModuleSurface:
    def test_every_factory_has_its_own_cases(self):
        """
        Guardia de cobertura: una condición sin tests es una condición que nadie
        probó, y el recolector la corre por frame.
        """
        assert _public_factories() == _COVERED_FACTORIES


class TestPredicateContract:
    """Vale para todas: el recolector las trata a todas igual."""

    @pytest.mark.parametrize("predicate", _PREDICATES, ids=lambda p: p.__name__)
    def test_returns_a_real_bool(self, predicate):
        """La anotación dice bool; con un np.bool_ el JSON del dataset no serializa."""
        assert isinstance(predicate({}), bool)

    @pytest.mark.parametrize("predicate", _PREDICATES, ids=lambda p: p.__name__)
    def test_empty_inference_does_not_raise(self, predicate):
        """Un dict vacío llega solo: el recolector guarda frames sin inferencia."""
        assert predicate({}) is False

    @pytest.mark.parametrize("predicate", _PREDICATES, ids=lambda p: p.__name__)
    def test_does_not_touch_the_inference(self, predicate):
        """Un predicado con efectos rompe al siguiente de la lista."""
        inference = {"confidence_pct": 90.0, "alarm": True}
        predicate(inference)
        assert inference == {"confidence_pct": 90.0, "alarm": True}

    @pytest.mark.parametrize("predicate", _PREDICATES, ids=lambda p: p.__name__)
    def test_name_carries_the_values_it_was_built_with(self, predicate):
        """El log del recolector nombra la condición que descartó el frame."""
        assert predicate.__name__ not in ("check", "<lambda>", "")
        assert "(" in predicate.__name__


class TestFieldAtLeast:
    @pytest.mark.parametrize("value, expected", [
        (80.0, True),
        (75.0, True),        # el umbral entra: es "al menos", no "más que"
        (74.9, False),
        (0, False),
    ])
    def test_compares_against_the_minimum(self, value, expected):
        condition = field_at_least_condition("confidence_pct", 75.0)
        assert condition({"confidence_pct": value}) is expected

    def test_missing_field_counts_as_zero(self):
        assert field_at_least_condition("confidence_pct", 75.0)({"other": 99}) is False

    def test_zero_minimum_lets_everything_through(self):
        assert field_at_least_condition("confidence_pct", 0)({}) is True

    @pytest.mark.parametrize("value", [None, "alta", [], {}])
    def test_non_numeric_value_does_not_meet_the_minimum(self, value):
        """
        La inferencia puede dejar el campo sin calcular. El recolector cuenta a la
        condición que levanta excepción como frame descartado igual, pero lo avisa
        con un warning por frame: esto no es una falla, es un frame que no aplica.
        """
        condition = field_at_least_condition("confidence_pct", 75.0)
        assert condition({"confidence_pct": value}) is False

    def test_numpy_scalar_is_accepted(self):
        """La inferencia devuelve escalares de numpy, no floats de Python."""
        condition = field_at_least_condition("confidence_pct", 75.0)
        assert condition({"confidence_pct": np.float32(80)}) is True
        assert condition({"confidence_pct": np.float32(10)}) is False


class TestFieldTruthy:
    @pytest.mark.parametrize("value", [True, 1, 0.5, "si", [0]])
    def test_truthy_values_pass(self, value):
        assert field_truthy_condition("alarm")({"alarm": value}) is True

    @pytest.mark.parametrize("value", [False, 0, 0.0, "", None, [], {}])
    def test_falsy_values_do_not_pass(self, value):
        assert field_truthy_condition("alarm")({"alarm": value}) is False

    def test_missing_field_does_not_pass(self):
        assert field_truthy_condition("alarm")({"confidence_pct": 99}) is False


class TestMinConfidence:
    def test_reads_the_confidence_field(self):
        condition = min_confidence_condition(75.0)
        assert condition({"confidence_pct": 90.0}) is True
        assert condition({"confidence_pct": 50.0}) is False

    def test_only_the_confidence_pct_key_counts(self):
        """
        Es la única factory que asume un nombre de campo. Si la inferencia publica
        la confianza con otra clave, esto falla y hay que elegir: renombrar la clave
        o usar field_at_least_condition con el nombre real.
        """
        assert min_confidence_condition(75.0)({"confidence": 90.0}) is False
