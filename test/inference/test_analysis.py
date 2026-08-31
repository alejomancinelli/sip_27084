"""Tests de las agregaciones sobre detecciones y de la agregación de un ciclo de varios
resultados: promedio, resumen con desvío y elección del frame representativo.

Módulo puro: sin config, sin Qt y sin frames.
"""

import pytest

from system.inference import analysis
from system.inference.result import Detection, InferenceResult


def _detection(class_name: str, confidence_pct: float = 90.0, area_px: int = 100) -> Detection:
    return Detection(class_index=0, class_name=class_name, confidence_pct=confidence_pct,
                     bbox_px=(0, 0, 10, 10), area_px=area_px)


def _result(metrics: dict, *, is_valid: bool = True) -> InferenceResult:
    return InferenceResult(camera_slot="camera_1", metrics=metrics, is_valid=is_valid)


class TestFilterByConfidence:
    def test_it_keeps_what_reaches_the_threshold(self):
        detections = [_detection("a", 90.0), _detection("b", 40.0)]
        assert analysis.filter_by_confidence(detections, 50.0) == [detections[0]]

    def test_the_threshold_is_inclusive(self):
        assert len(analysis.filter_by_confidence([_detection("a", 50.0)], 50.0)) == 1

    def test_without_detections_it_returns_empty(self):
        assert analysis.filter_by_confidence([], 50.0) == []


class TestAverageConfidence:
    def test_it_averages_the_scores(self):
        assert analysis.average_confidence_pct(
            [_detection("a", 90.0), _detection("b", 70.0)]) == 80.0

    def test_without_detections_it_is_zero(self):
        assert analysis.average_confidence_pct([]) == 0.0


class TestCountAndArea:
    def test_it_counts_by_class(self):
        detections = [_detection("grain"), _detection("grain"), _detection("stone")]
        assert analysis.count_by_class(detections) == {"grain": 2, "stone": 1}

    def test_it_adds_up_the_area_by_class(self):
        detections = [_detection("grain", area_px=100), _detection("grain", area_px=50)]
        assert analysis.area_by_class(detections) == {"grain": 150}

    def test_empty_input_gives_empty_maps(self):
        assert (analysis.count_by_class([]), analysis.area_by_class([])) == ({}, {})


class TestClassFractions:
    def test_it_splits_by_count(self):
        detections = [_detection("grain"), _detection("grain"), _detection("stone"),
                      _detection("stone")]
        assert analysis.class_fractions_pct(detections) == {"grain": 50.0, "stone": 50.0}

    def test_it_splits_by_area(self):
        detections = [_detection("grain", area_px=300), _detection("stone", area_px=100)]
        assert analysis.class_fractions_pct(detections, by_area=True) == {"grain": 75.0,
                                                                         "stone": 25.0}

    def test_without_detections_there_are_no_classes_to_report(self):
        """Un dict vacío y no ceros: no hay clases de las que hablar."""
        assert analysis.class_fractions_pct([]) == {}


class TestCoverage:
    def test_it_measures_the_analyzed_area(self):
        assert analysis.coverage_pct([_detection("a", area_px=250)], 1000) == 25.0

    def test_it_saturates_at_one_hundred(self):
        """Con máscaras superpuestas la suma puede pasarse del área real."""
        assert analysis.coverage_pct([_detection("a", area_px=2000)], 1000) == 100.0

    def test_an_invalid_area_gives_zero(self):
        assert analysis.coverage_pct([_detection("a")], 0) == 0.0


class TestSummarizeMetrics:
    """El ciclo de N frames de un proyecto que responde con varias imágenes por medición:
    la media sola no dice si los N coincidieron."""

    def test_it_reports_mean_std_and_range(self):
        results = [_result({"load_pct": 40.0}), _result({"load_pct": 60.0})]
        summary = analysis.summarize_metrics(results)
        assert summary == {"sample_count": 2, "load_pct_mean": 50.0, "load_pct_std": 10.0,
                           "load_pct_min": 40.0, "load_pct_max": 60.0}

    def test_the_output_is_flat(self):
        """Va a los fields de la telemetría y a los registros: un dict anidado no entra."""
        summary = analysis.summarize_metrics([_result({"a": 1, "b": 2})])
        assert all(not isinstance(value, dict) for value in summary.values())

    def test_a_single_sample_has_no_dispersion(self):
        summary = analysis.summarize_metrics([_result({"load_pct": 40.0})])
        assert (summary["load_pct_std"], summary["sample_count"]) == (0.0, 1)

    def test_the_sample_count_shows_a_short_cycle(self):
        """Si el ciclo pedía 10 y llegaron 3, el número tiene que estar a la vista."""
        results = [_result({"load_pct": 40.0}) for _ in range(3)]
        assert analysis.summarize_metrics(results)["sample_count"] == 3

    def test_invalid_results_are_left_out(self):
        results = [_result({"load_pct": 40.0}), _result({"load_pct": 1000.0}, is_valid=False)]
        summary = analysis.summarize_metrics(results)
        assert (summary["load_pct_mean"], summary["sample_count"]) == (40.0, 1)

    def test_it_ignores_what_cannot_be_summarized(self):
        summary = analysis.summarize_metrics([_result({"alarm": True, "label": "ok"})])
        assert summary == {"sample_count": 1}

    def test_without_results_only_the_count(self):
        assert analysis.summarize_metrics([]) == {"sample_count": 0}

    def test_it_accepts_a_generator(self):
        results = (_result({"load_pct": value}) for value in (40.0, 60.0))
        assert analysis.summarize_metrics(results)["sample_count"] == 2


class TestPickRepresentative:
    """El promedio no tiene imagen: cuando N frames dan un solo número, hay que elegir
    cuál de los N se guarda y se muestra."""

    def test_it_picks_the_one_closest_to_the_mean(self):
        results = [_result({"load_pct": 10.0}), _result({"load_pct": 50.0}),
                   _result({"load_pct": 90.0})]
        assert analysis.pick_representative(results) is results[1]

    def test_a_big_scale_key_does_not_outweigh_a_small_one(self):
        """La distancia se mide en desvíos: un conteo no puede tapar a un porcentaje."""
        results = [_result({"count": 1000, "load_pct": 10.0}),
                   _result({"count": 1010, "load_pct": 50.0}),
                   _result({"count": 1020, "load_pct": 90.0})]
        assert analysis.pick_representative(results) is results[1]

    def test_it_can_be_told_which_keys_matter(self):
        results = [_result({"load_pct": 10.0, "noise": 900.0}),
                   _result({"load_pct": 50.0, "noise": 0.0}),
                   _result({"load_pct": 90.0, "noise": 950.0})]
        assert analysis.pick_representative(results, ["load_pct"]) is results[1]

    def test_invalid_results_are_never_picked(self):
        results = [_result({"load_pct": 50.0}, is_valid=False), _result({"load_pct": 90.0})]
        assert analysis.pick_representative(results) is results[1]

    def test_without_valid_results_there_is_nothing_to_pick(self):
        assert analysis.pick_representative([_result({"a": 1}, is_valid=False)]) is None
        assert analysis.pick_representative([]) is None

    def test_with_no_dispersion_the_first_one_serves(self):
        """Todos midieron lo mismo: cualquiera representa el ciclo."""
        results = [_result({"load_pct": 50.0}), _result({"load_pct": 50.0})]
        assert analysis.pick_representative(results) is results[0]

    def test_results_without_metrics_still_give_one(self):
        results = [_result({}), _result({})]
        assert analysis.pick_representative(results) is results[0]

    def test_a_key_missing_in_one_result_is_not_used(self):
        results = [_result({"load_pct": 10.0}), _result({"load_pct": 50.0, "extra": 1.0}),
                   _result({"load_pct": 90.0})]
        assert analysis.pick_representative(results) is results[1]

    def test_it_returns_the_result_itself_not_a_copy(self):
        """Lo que se guarda es su frame anotado y sus detecciones, no sólo sus números."""
        results = [_result({"load_pct": 50.0}), _result({"load_pct": 10.0})]
        assert analysis.pick_representative(results) in results


class TestAverageMetrics:
    def test_it_averages_key_by_key(self):
        results = [_result({"count": 10, "coverage_pct": 50.0}),
                   _result({"count": 20, "coverage_pct": 60.0})]
        assert analysis.average_metrics(results) == {"count": 15.0, "coverage_pct": 55.0}

    def test_a_key_missing_in_one_result_averages_over_the_rest(self):
        results = [_result({"count": 10, "extra": 4}), _result({"count": 20})]
        assert analysis.average_metrics(results) == {"count": 15.0, "extra": 4.0}

    def test_it_ignores_what_cannot_be_averaged(self):
        assert analysis.average_metrics([_result({"alarm": True, "label": "ok"})]) == {}

    def test_invalid_results_are_left_out(self):
        """Promediar un frame oscuro con uno bueno da un número que no midió nada."""
        results = [_result({"count": 10}), _result({"count": 1000}, is_valid=False)]
        assert analysis.average_metrics(results) == {"count": 10.0}

    def test_it_can_be_told_to_average_everything(self):
        results = [_result({"count": 10}), _result({"count": 20}, is_valid=False)]
        assert analysis.average_metrics(results, valid_only=False) == {"count": 15.0}

    def test_without_results_it_is_empty(self):
        assert analysis.average_metrics([]) == {}


class TestSummaryKeys:
    """Qué claves inventa `summarize_metrics`, para que el consumidor las separe."""

    def test_the_sample_count_is_a_summary_key(self):
        assert analysis.is_summary_key(analysis.SAMPLE_COUNT_KEY)

    @pytest.mark.parametrize("suffix", analysis.SUMMARY_SUFFIXES)
    def test_every_statistic_is_recognised(self, suffix):
        assert analysis.is_summary_key(f"load_pct{suffix}")

    def test_a_process_metric_is_not(self):
        assert not analysis.is_summary_key("load_pct")
        assert not analysis.is_summary_key("camera_1_class_1_pct")

    def test_everything_summarize_metrics_adds_is_recognised(self):
        """Evita que agregar un estadístico nuevo deje de reconocerse y vuelva a llenar
        el log del cableado con avisos de métricas sin fila."""
        summary = analysis.summarize_metrics([_result({"load_pct": 10.0}),
                                              _result({"load_pct": 20.0})])
        derived = set(summary) - {"load_pct"}
        assert derived
        assert all(analysis.is_summary_key(key) for key in derived)
