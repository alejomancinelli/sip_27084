"""Tests de la detección de lente sucio: región de análisis, medición sobre frames
sintéticos, ventana de nitidez, veredicto y aviso de condiciones cambiadas.

Los frames son sintéticos y el "lente sucio" se simula con un desenfoque gaussiano: lo
que se verifica es el andamio —clampeo del ROI, eviction por tiempo, orden de las reglas
del estado— y no la exactitud del Laplaciano de OpenCV. La única propiedad que se le pide
al operador es la dirección: desenfocar baja la varianza.
"""

import cv2
import numpy as np
import pytest

from system.camera.lens_health import (
    CONDITION_KEYS,
    DEFAULT_ANALYSIS_WIDTH_PX,
    SHARPNESS_MAX_PCT,
    STATE_ALARM,
    STATE_OK,
    STATE_UNAVAILABLE,
    LensHealthMonitor,
    Region,
    SharpnessWindow,
    get_analysis_region,
    get_current_conditions,
    get_mismatch_reason,
    get_sharpness_pct,
    get_state,
    measure,
    summarize_calibration,
)

_WIDTH_PX = 320
_HEIGHT_PX = 240


def _sharp_frame(width_px: int = _WIDTH_PX, height_px: int = _HEIGHT_PX) -> np.ndarray:
    """Frame con detalle de alta frecuencia: un damero de 8 px."""
    frame = np.zeros((height_px, width_px, 3), np.uint8)
    frame[::8, :] = 255
    frame[:, ::8] = 255
    return frame


def _blurred_frame(sigma: float = 4.0) -> np.ndarray:
    """El mismo frame visto a través de un vidrio sucio."""
    return cv2.GaussianBlur(_sharp_frame(), (0, 0), sigma)


def _roi(x_px: int = 0, y_px: int = 0, width_px: int = 0, height_px: int = 0,
         enabled: bool = True) -> dict:
    return {"enabled": enabled, "x_px": x_px, "y_px": y_px,
            "width_px": width_px, "height_px": height_px}


class TestGetAnalysisRegion:
    def test_without_roi_the_region_is_the_whole_frame(self):
        region = get_analysis_region(_sharp_frame(), None)
        assert region == Region(0, 0, _WIDTH_PX, _HEIGHT_PX, False)

    def test_a_disabled_roi_is_ignored(self):
        """El rectángulo puede estar guardado y ser válido: manda `enabled`."""
        region = get_analysis_region(_sharp_frame(), _roi(10, 10, 50, 50, enabled=False))
        assert region.from_roi is False
        assert (region.width_px, region.height_px) == (_WIDTH_PX, _HEIGHT_PX)

    def test_an_enabled_roi_is_honoured(self):
        region = get_analysis_region(_sharp_frame(), _roi(10, 20, 50, 60))
        assert region == Region(10, 20, 50, 60, True)

    def test_zero_size_reaches_the_edge(self):
        """Misma convención que el resto del sistema: 0 = hasta el borde."""
        region = get_analysis_region(_sharp_frame(), _roi(40, 30))
        assert region == Region(40, 30, _WIDTH_PX - 40, _HEIGHT_PX - 30, True)

    def test_a_roi_saved_with_another_resolution_is_clamped(self):
        """No puede producir un recorte vacío ni salirse del frame en silencio."""
        region = get_analysis_region(_sharp_frame(), _roi(100, 100, 9000, 9000))
        assert region.x_px + region.width_px <= _WIDTH_PX
        assert region.y_px + region.height_px <= _HEIGHT_PX
        assert region.width_px > 0 and region.height_px > 0

    def test_a_roi_fully_outside_the_frame_falls_back_to_the_whole_frame(self):
        region = get_analysis_region(_sharp_frame(), _roi(_WIDTH_PX, _HEIGHT_PX, 10, 10))
        assert region.from_roi is False

    @pytest.mark.parametrize("frame", [
        None,
        np.zeros((0, 0, 3), np.uint8),   # frame vacío
        np.zeros(10, np.uint8),          # 1-D: no es una imagen
    ])
    def test_a_frame_that_cannot_be_measured_gives_none(self, frame):
        assert get_analysis_region(frame, None) is None

    def test_to_dict_uses_the_same_keys_as_the_config(self):
        assert set(Region(1, 2, 3, 4, True).to_dict()) == {"x_px", "y_px",
                                                           "width_px", "height_px"}


class TestMeasure:
    def test_a_blurred_frame_measures_lower_than_a_sharp_one(self):
        """Es la única propiedad del método de la que depende todo lo demás."""
        sharp = measure(_sharp_frame(), None)
        blurred = measure(_blurred_frame(), None)
        assert blurred.variance < sharp.variance

    def test_a_frame_that_cannot_be_measured_gives_none(self):
        assert measure(None, None) is None

    def test_the_region_travels_with_the_measurement(self):
        measurement = measure(_sharp_frame(), _roi(10, 20, 50, 60))
        assert measurement.region == Region(10, 20, 50, 60, True)

    def test_luma_is_the_mean_gray_level(self):
        frame = np.full((40, 40, 3), 128, np.uint8)
        assert measure(frame, None).luma == pytest.approx(128.0, abs=1.0)

    def test_a_frame_narrower_than_the_analysis_width_is_not_scaled_up(self):
        """Agrandar no agrega detalle y cambiaría la escala de la varianza."""
        measurement = measure(_sharp_frame(), None, analysis_width_px=DEFAULT_ANALYSIS_WIDTH_PX)
        assert measurement.width_px == _WIDTH_PX

    def test_a_wider_frame_is_reduced_to_the_analysis_width(self):
        frame = _sharp_frame(width_px=1200, height_px=600)
        measurement = measure(frame, None, analysis_width_px=300)
        assert measurement.width_px == 300
        assert measurement.height_px == 150   # conserva la relación de aspecto

    def test_a_grayscale_frame_is_accepted(self):
        """El driver puede entregar mono: no hay canal que convertir."""
        gray = cv2.cvtColor(_sharp_frame(), cv2.COLOR_BGR2GRAY)
        assert measure(gray, None) is not None

    def test_measuring_only_a_dirty_corner_sees_it(self):
        """El ROI es lo que hace que la suciedad local no se diluya en el frame entero."""
        frame = _sharp_frame()
        frame[:60, :60] = cv2.GaussianBlur(frame[:60, :60], (0, 0), 4.0)
        dirty_corner = measure(frame, _roi(0, 0, 60, 60))
        clean_corner = measure(frame, _roi(_WIDTH_PX - 60, _HEIGHT_PX - 60, 60, 60))
        assert dirty_corner.variance < clean_corner.variance


class TestGetSharpnessPct:
    def test_the_calibrated_variance_is_one_hundred_percent(self):
        assert get_sharpness_pct(400.0, 400.0) == 100

    def test_half_the_calibrated_variance_is_half(self):
        assert get_sharpness_pct(200.0, 400.0) == 50

    def test_a_peak_is_capped_instead_of_going_off_scale(self):
        assert get_sharpness_pct(40000.0, 400.0) == SHARPNESS_MAX_PCT

    @pytest.mark.parametrize("variance, reference", [
        (None, 400.0),    # sin muestra
        (400.0, 0.0),     # sin calibrar
        (400.0, -1.0),    # referencia corrupta
    ])
    def test_without_something_to_compare_it_is_zero(self, variance, reference):
        assert get_sharpness_pct(variance, reference) == 0


class TestSharpnessWindow:
    def test_an_empty_window_has_no_maximum(self):
        assert SharpnessWindow(60.0).get_max() is None

    def test_it_keeps_the_highest_sample(self):
        window = SharpnessWindow(60.0)
        for offset_s, variance in enumerate([100.0, 900.0, 300.0]):
            window.add(offset_s, variance)
        assert window.get_max() == 900.0
        assert window.get_count() == 3

    def test_samples_older_than_the_window_are_dropped(self):
        window = SharpnessWindow(60.0)
        window.add(0.0, 900.0)
        window.add(70.0, 100.0)
        assert window.get_max() == 100.0

    def test_it_empties_itself_when_the_capture_dies(self):
        """Sin esto el último valor bueno sostendría un 'limpio' que nadie está midiendo."""
        window = SharpnessWindow(60.0)
        window.add(0.0, 900.0)
        window.discard_expired(200.0)
        assert window.get_max() is None
        assert window.get_count() == 0

    @pytest.mark.parametrize("variance", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_sample_is_ignored(self, variance):
        window = SharpnessWindow(60.0)
        window.add(0.0, variance)
        assert window.get_count() == 0

    def test_the_window_is_not_complete_before_it_started(self):
        assert SharpnessWindow(60.0).is_complete(1000.0) is False

    def test_the_window_completes_one_period_after_the_first_cycle(self):
        window = SharpnessWindow(60.0)
        window.discard_expired(1000.0)
        assert window.is_complete(1059.0) is False
        assert window.is_complete(1060.0) is True

    def test_the_clock_starts_on_the_first_cycle_even_without_samples(self):
        """Una cámara que nunca entregó un frame tiene que llegar a poder afirmarlo."""
        window = SharpnessWindow(60.0)
        window.discard_expired(0.0)
        assert window.is_complete(60.0) is True


class TestGetState:
    def test_without_calibration_it_is_unavailable(self):
        """La función está inerte y lo declara; no dice que el lente esté limpio."""
        assert get_state(900.0, 0.0, 50.0, True) == STATE_UNAVAILABLE

    def test_enough_sharpness_is_ok(self):
        assert get_state(300.0, 400.0, 50.0, True) == STATE_OK

    def test_the_threshold_is_inclusive(self):
        assert get_state(200.0, 400.0, 50.0, True) == STATE_OK

    def test_positive_evidence_does_not_wait_for_the_window(self):
        """Un solo frame nítido ya demuestra que el vidrio deja pasar el detalle."""
        assert get_state(300.0, 400.0, 50.0, False) == STATE_OK

    def test_a_low_maximum_before_the_window_completes_is_unavailable(self):
        """Todavía puede ser una racha de imágenes lisas y no un lente sucio."""
        assert get_state(10.0, 400.0, 50.0, False) == STATE_UNAVAILABLE

    def test_a_low_maximum_over_the_whole_window_is_the_alarm(self):
        assert get_state(10.0, 400.0, 50.0, True) == STATE_ALARM

    def test_without_samples_it_is_unavailable_and_not_the_alarm(self):
        """Una cámara caída no es un lente sucio: la salud de la captura la reporta otro."""
        assert get_state(None, 400.0, 50.0, True) == STATE_UNAVAILABLE

    def test_an_uncalibrated_detector_never_raises_the_alarm(self):
        for max_variance in (None, 0.0, 10.0, 900.0):
            assert get_state(max_variance, 0.0, 50.0, True) == STATE_UNAVAILABLE


class TestSummarizeCalibration:
    def test_without_samples_there_is_nothing_to_summarize(self):
        assert summarize_calibration([], []) is None

    def test_it_takes_the_median_and_not_the_mean(self):
        """Un frame atípico no puede mover el número contra el que se compara por meses."""
        summary = summarize_calibration([100.0, 100.0, 100.0, 100.0, 9000.0], [])
        assert summary["variance"] == 100.0

    def test_it_reports_how_many_samples_it_used(self):
        assert summarize_calibration([100.0, 200.0], [10.0, 20.0])["sample_count"] == 2

    def test_consistent_samples_show_no_dispersion(self):
        assert summarize_calibration([400.0] * 5, [])["dispersion_pct"] == 0.0

    def test_scattered_samples_show_it(self):
        summary = summarize_calibration([100.0, 400.0, 900.0], [])
        assert summary["dispersion_pct"] > 50.0

    def test_a_single_sample_has_no_dispersion_to_report(self):
        assert summarize_calibration([400.0], [])["dispersion_pct"] == 0.0

    def test_without_lumas_it_still_summarizes_the_variance(self):
        summary = summarize_calibration([400.0], [])
        assert summary["variance"] == 400.0 and summary["luma"] == 0.0

    def test_the_summary_feeds_the_reference_back(self):
        """Lo que sale de acá es lo que después entra como `reference_variance`."""
        summary = summarize_calibration([400.0] * 3, [128.0] * 3)
        assert get_state(400.0, summary["variance"], 50.0, True) == STATE_OK


class TestGetCurrentConditions:
    def test_it_reports_every_condition_that_moves_the_variance(self):
        conditions = get_current_conditions({"exposure_time_us": 25000, "gain": 1.5},
                                            "90cw", _roi(1, 2, 3, 4))
        assert set(conditions) == set(CONDITION_KEYS)
        assert conditions["exposure_time_us"] == 25000
        assert conditions["gain"] == 1.5
        assert conditions["rotation"] == "90cw"
        assert conditions["roi"] == {"x_px": 1, "y_px": 2, "width_px": 3, "height_px": 4}

    def test_an_empty_camera_gives_a_complete_dict(self):
        """Nunca levanta: se calibra igual y lo que se guarda son ceros."""
        conditions = get_current_conditions(None, None, None)
        assert set(conditions) == set(CONDITION_KEYS)
        assert conditions["exposure_time_us"] == 0

    def test_null_values_from_the_config_become_zero(self):
        conditions = get_current_conditions({"exposure_time_us": None, "gain": None},
                                            None, None)
        assert conditions["exposure_time_us"] == 0 and conditions["gain"] == 0.0


class TestGetMismatchReason:
    def _conditions(self, **overrides) -> dict:
        base = get_current_conditions({"exposure_time_us": 25000, "gain": 1.0},
                                      None, _roi(0, 0, 100, 100))
        base.update(overrides)
        return base

    def test_the_same_conditions_have_nothing_to_report(self):
        assert get_mismatch_reason(self._conditions(), self._conditions()) == ""

    def test_without_saved_conditions_there_is_nothing_to_compare(self):
        """Una referencia vieja, calibrada antes de que se guardaran las condiciones."""
        assert get_mismatch_reason(None, self._conditions()) == ""
        assert get_mismatch_reason({}, self._conditions()) == ""

    def test_a_changed_exposure_is_named(self):
        reason = get_mismatch_reason(self._conditions(),
                                     self._conditions(exposure_time_us=40000))
        assert "exposure_time_us" in reason and "25000" in reason and "40000" in reason

    def test_the_camera_rounding_the_exposure_is_not_a_change(self):
        assert get_mismatch_reason(self._conditions(),
                                   self._conditions(exposure_time_us=25100)) == ""

    def test_a_changed_gain_is_named(self):
        assert "gain" in get_mismatch_reason(self._conditions(), self._conditions(gain=4.0))

    def test_a_changed_rotation_is_named(self):
        reason = get_mismatch_reason(self._conditions(), self._conditions(rotation="180"))
        assert "rotation" in reason

    def test_a_changed_roi_is_named_with_both_rectangles(self):
        moved = self._conditions()["roi"] | {"x_px": 50}
        reason = get_mismatch_reason(self._conditions(), self._conditions(roi=moved))
        assert "ROI" in reason and "100x100+0+0" in reason and "100x100+50+0" in reason

    def test_a_condition_missing_on_either_side_is_skipped(self):
        """Comparar contra lo que no se guardó daría un aviso permanente y falso."""
        saved = self._conditions()
        del saved["rotation"]
        assert get_mismatch_reason(saved, self._conditions(rotation="180")) == ""

    def test_a_non_numeric_value_still_compares_as_a_change(self):
        reason = get_mismatch_reason(self._conditions(), self._conditions(gain="alta"))
        assert "gain" in reason

    def test_it_reports_the_first_mismatch_and_not_a_list(self):
        """Es un aviso para quien mira, no un informe: alcanza con el primero."""
        reason = get_mismatch_reason(self._conditions(),
                                     self._conditions(exposure_time_us=40000, gain=4.0))
        assert reason.count("cambió") == 1


class TestPureModule:
    def test_it_only_imports_the_standard_library_and_opencv(self):
        """
        El módulo se tiene que poder copiar a otro proyecto sin abrir ningún otro: nada
        de Qt, de ConfigManager ni del mapa de registros.
        """
        import ast
        import pathlib

        import system.camera.lens_health as module

        tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        assert imported <= {"__future__", "collections", "dataclasses", "math",
                            "statistics", "threading", "cv2", "numpy"}


class TestLensHealthMonitor:
    """Lo que se prueba acá es que el estado NO se comparta entre cámaras: es la falla
    que no da error, sólo un equipo que deja de avisar."""

    def _monitor(self, window_s: float = 60.0) -> LensHealthMonitor:
        return LensHealthMonitor(window_s=window_s, threshold_pct=50.0)

    def test_an_unknown_camera_is_unavailable(self):
        assert self._monitor().get_state("camera_9", now_s=0.0) == STATE_UNAVAILABLE

    def test_an_unknown_camera_still_gives_the_full_set_of_keys(self):
        """La UI y la telemetría no pueden tener que preguntar si la cámara ya midió."""
        monitor = self._monitor()
        known = monitor.get_status("camera_1", now_s=0.0)
        monitor.update("camera_1", _sharp_frame(), None, now_s=0.0)
        assert set(known) == set(monitor.get_status("camera_1", now_s=0.0))

    def test_each_camera_keeps_its_own_window(self):
        """El máximo de una cámara limpia no puede tapar el de una sucia: si las dos
        ventanas fueran la misma, camera_2 saldría OK y nadie limpiaría el vidrio."""
        monitor = self._monitor()
        monitor.set_reference("camera_1", 400.0)
        monitor.set_reference("camera_2", 400.0)
        for now_s in range(0, 70, 10):
            monitor.update("camera_1", _sharp_frame(), None, now_s=now_s)
            monitor.update("camera_2", _blurred_frame(), None, now_s=now_s)
        assert monitor.get_state("camera_1", now_s=70.0) == STATE_OK
        assert monitor.get_state("camera_2", now_s=70.0) == STATE_ALARM

    def test_each_camera_keeps_its_own_reference(self):
        """Dos ópticas distintas tienen dos varianzas de vidrio limpio distintas."""
        monitor = self._monitor()
        monitor.set_reference("camera_1", 1.0)          # lente de poco detalle
        monitor.set_reference("camera_2", 1e9)          # referencia inalcanzable
        for now_s in range(0, 70, 10):
            for camera_slot in ("camera_1", "camera_2"):
                monitor.update(camera_slot, _sharp_frame(), None, now_s=now_s)
        assert monitor.get_state("camera_1", now_s=70.0) == STATE_OK
        assert monitor.get_state("camera_2", now_s=70.0) == STATE_ALARM

    def test_calibrating_one_camera_does_not_touch_the_others(self):
        """Se calibra de a una y las que ya estaban siguen vigiladas."""
        monitor = self._monitor()
        monitor.set_reference("camera_1", 400.0)
        monitor.update("camera_1", _sharp_frame(), None, now_s=0.0)
        monitor.update("camera_2", _sharp_frame(), None, now_s=0.0)
        assert monitor.get_state("camera_1", now_s=0.0) == STATE_OK
        assert monitor.get_state("camera_2", now_s=0.0) == STATE_UNAVAILABLE

    def test_each_camera_gets_its_own_roi(self):
        monitor = self._monitor()
        monitor.update("camera_1", _sharp_frame(), _roi(0, 0, 60, 60), now_s=0.0)
        monitor.update("camera_2", _sharp_frame(), _roi(10, 10, 80, 90), now_s=0.0)
        assert monitor.get_status("camera_1", now_s=0.0)["region"]["width_px"] == 60
        assert monitor.get_status("camera_2", now_s=0.0)["region"]["width_px"] == 80

    def test_one_camera_going_down_does_not_take_the_others_with_it(self):
        monitor = self._monitor()
        monitor.set_reference("camera_1", 400.0)
        monitor.set_reference("camera_2", 400.0)
        for now_s in range(0, 200, 10):
            monitor.update("camera_1", _sharp_frame(), None, now_s=now_s)
            monitor.update("camera_2", None, None, now_s=now_s)   # sin frames
        assert monitor.get_state("camera_1", now_s=200.0) == STATE_OK
        assert monitor.get_state("camera_2", now_s=200.0) == STATE_UNAVAILABLE

    def test_a_dead_camera_empties_its_window_even_without_frames(self):
        """`update` con un frame None tiene que envejecer igual; si no, el último
        veredicto bueno se congelaría para siempre."""
        monitor = self._monitor()
        monitor.set_reference("camera_1", 400.0)
        monitor.update("camera_1", _sharp_frame(), None, now_s=0.0)
        assert monitor.get_state("camera_1", now_s=0.0) == STATE_OK
        monitor.update("camera_1", None, None, now_s=500.0)
        assert monitor.get_state("camera_1", now_s=500.0) == STATE_UNAVAILABLE

    def test_discard_expired_ages_every_camera(self):
        """Para el ciclo que corre aunque no haya frames que ofrecer."""
        monitor = self._monitor()
        for camera_slot in ("camera_1", "camera_2"):
            monitor.set_reference(camera_slot, 400.0)
            monitor.update(camera_slot, _sharp_frame(), None, now_s=0.0)
        monitor.discard_expired(500.0)
        for camera_slot in ("camera_1", "camera_2"):
            assert monitor.get_status(camera_slot, now_s=500.0)["sample_count"] == 0

    def test_it_remembers_which_cameras_it_has_seen(self):
        monitor = self._monitor()
        monitor.update("camera_2", _sharp_frame(), None, now_s=0.0)
        monitor.set_reference("camera_1", 400.0)
        assert monitor.get_slots() == ["camera_2", "camera_1"]

    def test_status_reports_sharpness_against_that_cameras_reference(self):
        monitor = self._monitor()
        monitor.set_reference("camera_1", 400.0)
        monitor.update("camera_1", _sharp_frame(), None, now_s=0.0)
        status = monitor.get_status("camera_1", now_s=0.0)
        assert status["reference_variance"] == 400.0
        assert status["sharpness_pct"] == get_sharpness_pct(status["variance"], 400.0)

    def test_an_uncalibrated_reference_is_clamped_to_zero(self):
        monitor = self._monitor()
        monitor.set_reference("camera_1", None)
        monitor.set_reference("camera_2", -5.0)
        for camera_slot in ("camera_1", "camera_2"):
            assert monitor.get_status(camera_slot, now_s=0.0)["reference_variance"] == 0.0

    def test_update_returns_the_measurement_it_took(self):
        monitor = self._monitor()
        assert monitor.update("camera_1", _sharp_frame(), None, now_s=0.0) is not None
        assert monitor.update("camera_1", None, None, now_s=0.0) is None

    def test_concurrent_cameras_do_not_corrupt_it(self):
        """Un fork puede alimentarlo desde varios hilos de inferencia, uno por pipeline."""
        import threading

        monitor = self._monitor(window_s=1000.0)
        slots = [f"camera_{i}" for i in range(1, 5)]
        errors = []

        def feed(camera_slot: str):
            try:
                for now_s in range(40):
                    monitor.update(camera_slot, _sharp_frame(), None, now_s=float(now_s))
                    monitor.discard_expired(float(now_s))
                    monitor.get_status(camera_slot, now_s=float(now_s))
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=feed, args=(slot,)) for slot in slots]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert sorted(monitor.get_slots()) == slots
        for camera_slot in slots:
            assert monitor.get_status(camera_slot, now_s=39.0)["sample_count"] == 40
