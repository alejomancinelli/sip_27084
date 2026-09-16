"""Tests de la media móvil por ventana de tiempo: evicción, drenaje sin muestras y el
porcentaje de ventana medida que valida la media.

Módulo puro: el reloj entra por parámetro, así que una ventana de una hora se prueba sin
esperar una hora.
"""

from system.inference.rolling import RollingMean, has_material, ratio_pct

_WINDOW_S = 60.0


class TestMean:
    def test_without_samples_it_returns_none(self):
        """None y no 0: nunca haber medido no es haber medido cero."""
        assert RollingMean(_WINDOW_S).mean() is None

    def test_it_averages_what_is_inside_the_window(self):
        window = RollingMean(_WINDOW_S)
        window.add(0.0, 10.0)
        window.add(1.0, 20.0)
        assert window.mean() == 15.0

    def test_a_sample_older_than_the_window_is_evicted(self):
        window = RollingMean(_WINDOW_S)
        window.add(0.0, 10.0)
        window.add(_WINDOW_S + 1.0, 20.0)
        assert (window.mean(), window.count()) == (20.0, 1)

    def test_the_edge_of_the_window_is_exclusive(self):
        window = RollingMean(_WINDOW_S)
        window.add(0.0, 10.0)
        window.add(_WINDOW_S, 20.0)
        assert window.count() == 1


class TestTick:
    def test_ticking_drains_the_window_without_new_samples(self):
        """Una inferencia caída vacía la ventana en vez de congelar el último promedio."""
        window = RollingMean(_WINDOW_S)
        window.add(0.0, 10.0)
        window.tick(_WINDOW_S + 1.0)
        assert window.mean() is None

    def test_ticking_inside_the_window_keeps_the_samples(self):
        window = RollingMean(_WINDOW_S)
        window.add(0.0, 10.0)
        window.tick(_WINDOW_S / 2)
        assert window.mean() == 10.0


class TestFillPct:
    def test_a_full_window_reports_one_hundred(self):
        window = RollingMean(10.0)
        for second in range(10):
            window.add(float(second), 1.0)
        assert window.fill_pct(expected_hz=1.0) == 100

    def test_a_half_measured_window_reports_half(self):
        window = RollingMean(10.0)
        for second in range(5):
            window.add(float(second), 1.0)
        assert window.fill_pct(expected_hz=1.0) == 50

    def test_it_is_capped_at_one_hundred(self):
        """Midiendo más rápido de lo esperado la ventana no puede estar más que llena."""
        window = RollingMean(10.0)
        for i in range(30):
            window.add(i * 0.1, 1.0)
        assert window.fill_pct(expected_hz=1.0) == 100

    def test_without_an_expected_rate_it_reports_zero(self):
        assert RollingMean(10.0).fill_pct(expected_hz=0.0) == 0


class TestHasMaterial:
    def test_an_empty_belt_has_no_composition(self):
        assert has_material({"pellet": 0.0, "desmenuzado": 0.0}) is False

    def test_any_detected_class_counts_as_material(self):
        assert has_material({"pellet": 0.0, "desmenuzado": 0.4}) is True

    def test_without_classes_there_is_no_material(self):
        assert has_material({}) is False


class TestRatioPct:
    def test_it_rounds_to_an_integer_percentage(self):
        assert ratio_pct(1, 3) == 33

    def test_without_a_total_it_returns_zero(self):
        assert ratio_pct(5, 0) == 0

    def test_it_is_capped_at_one_hundred(self):
        assert ratio_pct(7, 5) == 100
