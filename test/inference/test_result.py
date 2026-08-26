"""Tests de la forma del resultado: completado del área y el centroide, traslado al
espacio del frame de cámara y serialización.
"""

import numpy as np
import pytest

from system.inference.result import Detection, InferenceResult, offset_detections


def _detection(**overrides) -> Detection:
    fields = {"class_index": 0, "class_name": "grain", "confidence_pct": 90.0,
              "bbox_px": (10, 20, 30, 40)}
    fields.update(overrides)
    return Detection(**fields)


class TestDetection:
    def test_the_area_comes_from_the_bbox_when_it_is_not_given(self):
        assert _detection().area_px == 400

    def test_a_given_area_is_respected(self):
        """Con máscara el área es la de la forma, no la de la caja."""
        assert _detection(area_px=137).area_px == 137

    def test_the_centroid_comes_from_the_bbox_when_it_is_not_given(self):
        assert _detection().centroid_px == (20, 30)

    def test_a_given_centroid_is_respected(self):
        assert _detection(centroid_px=(11, 12)).centroid_px == (11, 12)

    def test_a_degenerate_bbox_has_no_area(self):
        assert _detection(bbox_px=(10, 10, 10, 10)).area_px == 0

    def test_the_mask_does_not_go_to_the_dict(self):
        detection = _detection(mask=np.ones((20, 20), np.uint8))
        assert detection.to_dict()["has_mask"] is True
        assert "mask" not in detection.to_dict()

    def test_the_dict_carries_plain_types(self):
        payload = _detection().to_dict()
        assert payload["bbox_px"] == [10, 20, 30, 40]
        assert payload["centroid_px"] == [20, 30]


class TestOffsetDetections:
    def test_it_moves_the_bbox_and_the_centroid(self):
        detection = _detection()
        offset_detections([detection], 100, 200)
        assert detection.bbox_px == (110, 220, 130, 240)
        assert detection.centroid_px == (120, 230)

    def test_the_area_does_not_change(self):
        detection = _detection()
        offset_detections([detection], 100, 200)
        assert detection.area_px == 400

    def test_a_zero_offset_changes_nothing(self):
        detection = _detection()
        offset_detections([detection], 0, 0)
        assert detection.bbox_px == (10, 20, 30, 40)


class TestInferenceResult:
    def test_the_count_follows_the_detections(self):
        result = InferenceResult(camera_slot="camera_1", detections=[_detection(), _detection()])
        assert result.detection_count == 2

    def test_a_fresh_result_is_valid(self):
        assert InferenceResult(camera_slot="camera_1").is_valid is True

    def test_the_dict_is_serializable_without_frames(self):
        import json
        result = InferenceResult(
            camera_slot="camera_1", pipeline_slot="pipeline_1",
            detections=[_detection()], metrics={"count": 1},
            source_bgr=np.zeros((4, 4, 3), np.uint8),
            annotated_bgr=np.zeros((4, 4, 3), np.uint8))
        payload = result.to_dict()
        assert "source_bgr" not in payload and "annotated_bgr" not in payload
        assert json.dumps(payload)

    def test_a_fresh_result_has_no_labels(self):
        assert InferenceResult(camera_slot="camera_1").labels == {}

    def test_the_labels_travel_in_the_dict(self):
        """El veredicto de frame llega al JSON del dataset y a la telemetría como texto."""
        result = InferenceResult(camera_slot="camera_1", labels={"belt": "full"})
        assert result.to_dict()["labels"] == {"belt": "full"}

    def test_the_labels_of_the_dict_are_a_copy(self):
        result = InferenceResult(camera_slot="camera_1", labels={"belt": "full"})
        result.to_dict()["labels"]["belt"] = "empty"
        assert result.labels == {"belt": "full"}

    def test_the_confidence_key_is_the_one_the_save_conditions_read(self):
        """`image_collector.conditions` filtra por 'confidence_pct': el nombre es contrato."""
        assert "confidence_pct" in InferenceResult(camera_slot="camera_1").to_dict()
