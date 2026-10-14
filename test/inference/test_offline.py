"""
Tests del runner offline: los pipelines del config corridos sobre una imagen suelta.

Corre de punta a punta con el `Pipeline` de la cinta y el `MockModel`, que es determinista
y no necesita framework: lo que se prueba es que el runner arma lo mismo que la app —qué
pipelines, qué cámaras, preprocessor y hooks— y no el modelo. Sin Qt corriendo, sin GPU y
sin disco.
"""

import copy

import numpy as np

from system.inference.offline import OfflineRunner
from system.inference.result import REASON_NO_MODEL

_DETECTION_COUNT = 3


class _MockConfig:
    """ConfigManager mínimo sobre un dict anidado: `get` resuelve la clave punteada."""

    def __init__(self, values: dict):
        self._values = values

    def get(self, key: str, default: object = None) -> object:
        node = self._values
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return copy.deepcopy(node)


def _config(*, pipelines: dict | None = None, cameras: dict | None = None,
            process: dict | None = None) -> _MockConfig:
    return _MockConfig({
        "cameras": cameras if cameras is not None else {"camera_1": {}},
        "process": process or {},
        "inference": {
            # Sin hora ni panel: dibujan el instante y el tiempo de inferencia, y dos
            # anotados de la misma imagen tienen que poder compararse.
            "overlay": {"draw_timestamp": False, "draw_summary": False},
            "pipelines": pipelines if pipelines is not None else {
                "pipeline_1": {"enabled": True, "cameras": []}},
            "models": {"segmenter": {
                "type": "mock", "min_confidence_pct": 0,
                "params": {"detection_count": _DETECTION_COUNT}}},
        },
    })


def _frame(level: int = 100) -> np.ndarray:
    return np.full((120, 160, 3), level, np.uint8)


def _loaded(config: _MockConfig, **kwargs) -> OfflineRunner:
    runner = OfflineRunner(config, **kwargs)
    runner.load()
    return runner


class TestPipelines:
    def test_an_image_goes_through_the_pipeline_like_in_the_app(self):
        [result] = _loaded(_config()).run_image(_frame(), "camera_1")
        assert result.is_valid
        assert result.detection_count == _DETECTION_COUNT
        assert (result.camera_slot, result.pipeline_slot) == ("camera_1", "pipeline_1")

    def test_the_overlay_is_always_drawn(self):
        """Sin gate: el anotado es lo que se viene a mirar."""
        [result] = _loaded(_config()).run_image(_frame(), "camera_1")
        assert result.annotated_bgr is not None
        assert not np.array_equal(result.annotated_bgr, result.source_bgr)

    def test_the_mock_model_marks_the_result_as_synthetic(self):
        [result] = _loaded(_config()).run_image(_frame(), "camera_1")
        assert result.is_synthetic is True

    def test_there_is_one_result_per_pipeline_that_takes_the_camera(self):
        pipelines = {"pipeline_1": {"enabled": True, "cameras": []},
                     "pipeline_2": {"enabled": True, "cameras": []}}
        results = _loaded(_config(pipelines=pipelines)).run_image(_frame(), "camera_1")
        assert [result.pipeline_slot for result in results] == ["pipeline_1", "pipeline_2"]

    def test_a_disabled_pipeline_is_not_built(self):
        pipelines = {"pipeline_1": {"enabled": True, "cameras": []},
                     "pipeline_2": {"enabled": False, "cameras": []}}
        runner = _loaded(_config(pipelines=pipelines))
        assert runner.pipeline_slots == ("pipeline_1",)
        assert len(runner.run_image(_frame(), "camera_1")) == 1

    def test_a_pipeline_only_takes_its_own_cameras(self):
        pipelines = {"pipeline_1": {"enabled": True, "cameras": ["camera_1"]},
                     "pipeline_2": {"enabled": True, "cameras": ["camera_2"]}}
        results = _loaded(_config(pipelines=pipelines)).run_image(_frame(), "camera_2")
        assert [result.pipeline_slot for result in results] == ["pipeline_2"]

    def test_a_camera_no_pipeline_takes_gives_no_results(self):
        pipelines = {"pipeline_1": {"enabled": True, "cameras": ["camera_1"]}}
        assert _loaded(_config(pipelines=pipelines)).run_image(_frame(), "camera_9") == []


class TestLoading:
    def test_without_loading_the_result_says_there_is_no_model(self):
        [result] = OfflineRunner(_config()).run_image(_frame(), "camera_1")
        assert result.invalid_reason == REASON_NO_MODEL

    def test_unloading_leaves_the_pipelines_without_model(self):
        runner = _loaded(_config())
        runner.unload()
        [result] = runner.run_image(_frame(), "camera_1")
        assert result.invalid_reason == REASON_NO_MODEL

    def test_the_status_reports_each_pipeline_and_its_models(self):
        status = _loaded(_config()).get_status()
        assert list(status) == ["pipeline_1"]
        assert status["pipeline_1"]["loaded"] is True
        assert list(status["pipeline_1"]["models"]) == ["segmenter"]


class TestPreprocessor:
    _ADJUSTED_CAMERA = {"camera_1": {"image_adjust": {"brightness_factor": 2.0}}}

    def test_the_camera_preprocessor_is_applied(self):
        [result] = _loaded(_config(cameras=self._ADJUSTED_CAMERA)).run_image(_frame(), "camera_1")
        assert result.source_bgr.mean() == 200

    def test_an_image_that_is_already_a_reference_frame_skips_it(self):
        """Las del dataset ya vienen ajustadas: otra pasada duplicaría el brillo."""
        runner = _loaded(_config(cameras=self._ADJUSTED_CAMERA), apply_preprocessor=False)
        [result] = runner.run_image(_frame(), "camera_1")
        assert result.source_bgr.mean() == 100


class TestHooks:
    def test_the_project_analyzer_computes_the_metrics(self):
        [result] = _loaded(_config()).run_image(_frame(), "camera_1")
        assert {"pct_carga", "pct_pellet_norm", "pct_desmenuzado_norm"} <= set(result.metrics)

    def test_the_project_annotator_draws_its_references(self):
        """El rectángulo de cinta de `process:` sale en el anotado, igual que en la app."""
        plain = _loaded(_config()).run_image(_frame(), "camera_1")[0].annotated_bgr
        belt_roi_px = {"camera_1": {"x_px": 10, "y_px": 10, "width_px": 60, "height_px": 60}}
        with_belt = _loaded(_config(process={"belt_roi_px": belt_roi_px})
                            ).run_image(_frame(), "camera_1")[0].annotated_bgr
        assert not np.array_equal(plain, with_belt)
