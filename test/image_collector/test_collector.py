"""
Tests del recolector de dataset: los dos modos de operación, los filtros, el buffer
circular y la guardia de disco.

El dataset se arma en `tmp_path`, así que todo corre sin cámara y sin hilos de la
app. Los tests del modo interval bajan `_MIN_INTERVAL_S` para no esperar el piso de
un segundo: lo que se prueba es la cadencia, no el piso.
"""

import json
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest


import system.image_collector.collector as ic
from system.image_collector.collector import (
    ImageCollector,
    _compute_phash,
    _get_free_space_gb,
    _hamming_distance,
    _write_image,
)
from system.image_collector.conditions import field_truthy_condition, min_confidence_condition

_STATS_KEYS = {
    "mode", "active", "running", "images", "saved", "skipped",
    "last_save_iso", "disk_free_gb", "disk_guard_active",
}

_FAST_INTERVAL_S = 0.02         # cadencia de los tests de intervalo
_WINDOW_INTERVAL_S = 0.3        # vuelta lo bastante larga como para meterle dos push
_TICKS_TO_WAIT = 5              # vueltas que se esperan para afirmar que algo NO pasó


class _MockConfig:
    """ConfigManager mínimo: `get` / `set` / `save` con clave punteada, sin archivo."""

    def __init__(self, dataset_path: Path, **overrides):
        self._values = {
            "system.paths.dataset": str(dataset_path),
            "image_collector.enabled": True,
            "image_collector.mode": "on_demand",
            "image_collector.max_images": 100,
            "image_collector.min_free_space_gb": 0,
            "image_collector.save_original": True,
            "image_collector.save_annotated": False,
            "image_collector.save_inference_json": True,
            "image_collector.dedup.enabled": False,
            "image_collector.interval_min_s": _FAST_INTERVAL_S,
            "image_collector.interval_max_s": _FAST_INTERVAL_S,
        }
        self._values.update(overrides)
        self.save_calls = 0

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self._values[key] = value

    def save(self) -> bool:
        self.save_calls += 1
        return True


class _FakeFreeSpace:
    """
    Espacio libre de mentira. Devuelve los valores en orden y repite el último.

    Sirve para las dos ramas de la guardia: borrar hasta recuperar espacio, y no
    tener nada que borrar.
    """

    def __init__(self, *free_gb: float):
        self.free_gb = list(free_gb)
        self.calls = 0

    def __call__(self, path: str) -> float:
        value = self.free_gb[min(self.calls, len(self.free_gb) - 1)]
        self.calls += 1
        return value


def _frame(seed: int, width_px: int = 64, height_px: int = 48) -> np.ndarray:
    """Frame BGR de ruido reproducible: dos seeds distintas son dos escenas distintas."""
    return np.random.default_rng(seed).integers(
        0, 255, (height_px, width_px, 3), dtype=np.uint8)


def _files(dataset_path: Path) -> list[str]:
    """Rutas relativas de todo lo que hay en el dataset, ordenadas."""
    return sorted(str(p.relative_to(dataset_path)).replace(os.sep, "/")
                  for p in dataset_path.rglob("*") if p.is_file())


def _pngs(dataset_path: Path, folder: str = "original") -> list[str]:
    return [p for p in _files(dataset_path) if f"/{folder}/" in p and p.endswith(".png")]


def _wait_until(predicate, timeout_s: float = 3.0) -> bool:
    """Espera a que se cumpla el predicado. False si venció el tiempo."""
    deadline_s = time.monotonic() + timeout_s
    while time.monotonic() < deadline_s:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def dataset_path(tmp_path) -> Path:
    return tmp_path / "dataset"


@pytest.fixture
def build_collector():
    """Fábrica de recolectores que los detiene al final, aunque el test falle."""
    created = []

    def build(config: _MockConfig, save_conditions=None) -> ImageCollector:
        collector = ImageCollector(config, save_conditions=save_conditions)
        created.append(collector)
        return collector

    yield build
    for collector in created:
        collector.stop()


@pytest.fixture
def fast_interval(monkeypatch):
    """Baja el piso del intervalo para que una vuelta dure milisegundos."""
    monkeypatch.setattr(ic, "_MIN_INTERVAL_S", _FAST_INTERVAL_S)


class TestPerceptualHash:
    def test_same_frame_hashes_identical(self):
        assert _hamming_distance(_compute_phash(_frame(1)), _compute_phash(_frame(1))) == 0

    def test_different_scenes_are_far_apart(self):
        """Sin esto el dedup descartaría escenas distintas."""
        distance = _hamming_distance(_compute_phash(_frame(1)), _compute_phash(_frame(2)))
        assert distance > 10

    def test_brightness_drift_stays_close(self):
        """El hash tiene que aguantar la deriva de iluminación, que no es cambio de escena."""
        frame = _frame(3)
        brighter = np.clip(frame.astype(np.int16) + 12, 0, 255).astype(np.uint8)
        distance = _hamming_distance(_compute_phash(frame), _compute_phash(brighter))
        assert distance <= 5

    def test_accepts_grayscale_frame(self):
        """Un frame de una sola banda no pasa por cvtColor."""
        gray = _frame(4)[:, :, 0]
        assert isinstance(_compute_phash(gray), int)

    def test_hash_fits_in_64_bits(self):
        assert 0 <= _compute_phash(_frame(5)) < 2 ** 64


class TestWriteImage:
    def test_creates_missing_folders(self, tmp_path):
        path = tmp_path / "a" / "b" / "img.png"
        assert _write_image(str(path), _frame(1), []) is True
        assert path.is_file()

    def test_writes_path_with_accents(self, tmp_path):
        """
        Es el motivo de usar imencode: cv2.imwrite pasa la ruta por la codificación
        ANSI del sistema y en Windows falla en silencio con acentos.
        """
        path = tmp_path / "acentuación" / "cámara_ñ.png"
        assert _write_image(str(path), _frame(1), []) is True
        decoded = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
        assert decoded is not None

    def test_returns_false_when_encoding_fails(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ic.cv2, "imencode", lambda *args: (False, None))
        path = tmp_path / "img.png"
        assert _write_image(str(path), _frame(1), []) is False
        assert not path.exists()


class TestFreeSpace:
    def test_measures_through_missing_folders(self, tmp_path):
        """El dataset no existe hasta el primer guardado; la medición sube al padre."""
        free_gb = _get_free_space_gb(str(tmp_path / "no" / "existe" / "todavia"))
        assert free_gb is not None and free_gb > 0


class TestOnDemandMode:
    def test_start_does_not_launch_a_thread(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(dataset_path))
        collector.start()
        assert collector.is_running is False

    def test_save_now_writes_the_three_variants(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(
            dataset_path, **{"image_collector.save_annotated": True}))
        assert collector.save_now("camera_1", _frame(1), annotated_bgr=_frame(2),
                                  inference={"ok": True}) is True

        files = _files(dataset_path)
        assert len(files) == 3
        stems = {Path(f).stem for f in files}
        assert len(stems) == 1, "las tres variantes comparten el nombre de archivo"
        stem = stems.pop()
        assert stem.startswith("camera_1_")
        date = os.listdir(dataset_path)[0]
        assert files == sorted([
            f"{date}/annotated/camera_1/{stem}.json",
            f"{date}/annotated/camera_1/{stem}.png",
            f"{date}/original/camera_1/{stem}.png",
        ])

    def test_push_frame_saves_nothing_without_a_thread(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(dataset_path))
        collector.push_frame("camera_1", _frame(1))
        assert _files(dataset_path) == []

    def test_disabled_collection_saves_nothing(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(
            dataset_path, **{"image_collector.enabled": False}))
        assert collector.save_now("camera_1", _frame(1)) is False
        assert _files(dataset_path) == []

    def test_nothing_to_save_returns_false(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(dataset_path))
        assert collector.save_now("camera_1", None) is False
        assert _files(dataset_path) == []

    def test_annotated_only_still_writes(self, dataset_path, build_collector):
        """El frame crudo puede no estar; el anotado y el JSON alcanzan para guardar."""
        collector = build_collector(_MockConfig(dataset_path, **{
            "image_collector.save_original": False,
            "image_collector.save_annotated": True,
        }))
        assert collector.save_now("camera_1", _frame(1), annotated_bgr=_frame(2),
                                  inference={"ok": True}) is True
        assert _pngs(dataset_path, "original") == []
        assert len(_pngs(dataset_path, "annotated")) == 1

    def test_json_carries_the_stable_keys(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(dataset_path))
        collector.save_now("camera_1", _frame(1), inference={"confidence_pct": 90})

        json_path = next(dataset_path.rglob("*.json"))
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert set(payload) == {"schema_version", "camera_slot", "timestamp_iso", "inference"}
        assert payload["camera_slot"] == "camera_1"
        assert payload["inference"] == {"confidence_pct": 90}

    def test_inference_is_copied_at_the_call(self, dataset_path, build_collector):
        """El llamador puede reusar su dict; lo que se guarda es lo que había."""
        collector = build_collector(_MockConfig(dataset_path))
        inference = {"confidence_pct": 90}
        collector.save_now("camera_1", _frame(1), inference=inference)
        inference["confidence_pct"] = 0

        payload = json.loads(next(dataset_path.rglob("*.json")).read_text(encoding="utf-8"))
        assert payload["inference"]["confidence_pct"] == 90

    def test_unknown_mode_falls_back_to_on_demand(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(
            dataset_path, **{"image_collector.mode": "periodico"}))
        assert collector.mode == "on_demand"
        collector.start()
        assert collector.is_running is False

    def test_set_active_persists_to_config(self, dataset_path, build_collector):
        config = _MockConfig(dataset_path, **{"image_collector.enabled": False})
        collector = build_collector(config)
        assert collector.is_active is False
        assert collector.set_active(True) is True
        assert collector.is_active is True
        assert config.save_calls == 1


class TestSaveConditions:
    def test_condition_not_met_skips_the_frame(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(dataset_path),
                                    save_conditions=[min_confidence_condition(75)])
        assert collector.save_now("camera_1", _frame(1),
                                  inference={"confidence_pct": 10}) is False
        assert _files(dataset_path) == []
        assert collector.get_stats()["skipped"] == 1

    def test_every_condition_has_to_pass(self, dataset_path, build_collector):
        collector = build_collector(
            _MockConfig(dataset_path),
            save_conditions=[min_confidence_condition(75), field_truthy_condition("alarm")])
        assert collector.save_now("camera_1", _frame(1),
                                  inference={"confidence_pct": 90}) is False
        assert collector.save_now("camera_1", _frame(2),
                                  inference={"confidence_pct": 90, "alarm": True}) is True

    def test_condition_that_raises_skips_the_frame(self, dataset_path, build_collector):
        def broken(inference: dict) -> bool:
            raise RuntimeError("boom")

        collector = build_collector(_MockConfig(dataset_path), save_conditions=[broken])
        assert collector.save_now("camera_1", _frame(1), inference={"ok": True}) is False
        assert _files(dataset_path) == []

    def test_conditions_see_an_empty_dict_without_inference(self, dataset_path, build_collector):
        """Sin inferencia la condición se evalúa igual, no se saltea."""
        seen = []
        collector = build_collector(
            _MockConfig(dataset_path),
            save_conditions=[lambda inference: seen.append(inference) or True])
        collector.save_now("camera_1", _frame(1))
        assert seen == [{}]


class TestDedup:
    def test_duplicate_frame_is_skipped(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(
            dataset_path, **{"image_collector.dedup.enabled": True}))
        assert collector.save_now("camera_1", _frame(1)) is True
        assert collector.save_now("camera_1", _frame(1)) is False
        assert len(_pngs(dataset_path)) == 1

    def test_different_scene_is_saved(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(
            dataset_path, **{"image_collector.dedup.enabled": True}))
        assert collector.save_now("camera_1", _frame(1)) is True
        assert collector.save_now("camera_1", _frame(2)) is True

    def test_disabled_dedup_saves_the_duplicate(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(dataset_path))
        assert collector.save_now("camera_1", _frame(1)) is True
        assert collector.save_now("camera_1", _frame(1)) is True

    def test_history_is_per_camera(self, dataset_path, build_collector):
        """El mismo frame en otra cámara es otro dato, no un duplicado."""
        collector = build_collector(_MockConfig(
            dataset_path, **{"image_collector.dedup.enabled": True}))
        assert collector.save_now("camera_1", _frame(1)) is True
        assert collector.save_now("camera_2", _frame(1)) is True

    def test_history_length_bounds_the_comparison(self, dataset_path, build_collector):
        """Con historial 1 solo se compara contra el último guardado."""
        collector = build_collector(_MockConfig(dataset_path, **{
            "image_collector.dedup.enabled": True,
            "image_collector.dedup.history_len": 1,
        }))
        collector.save_now("camera_1", _frame(1))
        collector.save_now("camera_1", _frame(2))
        assert collector.save_now("camera_1", _frame(1)) is True


class TestCircularBuffer:
    def test_oldest_save_is_evicted_at_the_cap(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(dataset_path, **{
            "image_collector.max_images": 2,
            "image_collector.save_annotated": True,
        }))
        for seed in range(4):
            collector.save_now("camera_1", _frame(seed), annotated_bgr=_frame(seed + 10),
                               inference={"i": seed})

        assert len(_pngs(dataset_path, "original")) == 2
        assert collector.get_stats()["images"] == {"camera_1": 2}

    def test_eviction_removes_the_three_variants(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(dataset_path, **{
            "image_collector.max_images": 1,
            "image_collector.save_annotated": True,
        }))
        collector.save_now("camera_1", _frame(1), annotated_bgr=_frame(2),
                           inference={"i": 1})
        first_stem = Path(_pngs(dataset_path, "original")[0]).stem
        collector.save_now("camera_1", _frame(3), annotated_bgr=_frame(4),
                           inference={"i": 2})

        assert not any(first_stem in name for name in _files(dataset_path))
        assert len(_files(dataset_path)) == 3

    def test_cap_is_per_camera(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(
            dataset_path, **{"image_collector.max_images": 1}))
        collector.save_now("camera_1", _frame(1))
        collector.save_now("camera_2", _frame(2))
        assert collector.get_stats()["images"] == {"camera_1": 1, "camera_2": 1}

    def test_indexes_what_was_already_on_disk(self, dataset_path, build_collector):
        """
        En on_demand no hay hilo que indexe al arrancar: sin el indexado perezoso, el
        tope no se aplicaría a lo que dejó la corrida anterior.
        """
        old = dataset_path / "2020-01-01" / "original" / "camera_1"
        old.mkdir(parents=True)
        for name in ("camera_1_20200101_120000000000.png",
                     "camera_1_20200101_130000000000.png"):
            _write_image(str(old / name), _frame(1), [])

        collector = build_collector(_MockConfig(
            dataset_path, **{"image_collector.max_images": 2}))
        collector.save_now("camera_1", _frame(9))

        remaining = _pngs(dataset_path, "original")
        assert len(remaining) == 2
        assert not any("20200101_120000" in name for name in remaining)


class TestDiskGuard:
    def test_refuses_to_save_with_nothing_to_evict(self, dataset_path, build_collector,
                                                   monkeypatch):
        monkeypatch.setattr(ic, "_get_free_space_gb", _FakeFreeSpace(1.0))
        collector = build_collector(_MockConfig(
            dataset_path, **{"image_collector.min_free_space_gb": 5}))
        assert collector.save_now("camera_1", _frame(1)) is False
        assert _files(dataset_path) == []

    def test_evicts_until_there_is_room(self, dataset_path, build_collector, monkeypatch):
        config = _MockConfig(dataset_path, **{"image_collector.min_free_space_gb": 5})
        collector = build_collector(config)
        for seed in range(3):
            collector.save_now("camera_1", _frame(seed))
        assert len(_pngs(dataset_path)) == 3

        # Dos mediciones por debajo del mínimo: borra dos y la tercera ya entra.
        monkeypatch.setattr(ic, "_get_free_space_gb", _FakeFreeSpace(1.0, 1.0, 50.0))
        assert collector.save_now("camera_1", _frame(9)) is True
        assert len(_pngs(dataset_path)) == 2

    def test_stats_report_the_guard(self, dataset_path, build_collector, monkeypatch):
        monkeypatch.setattr(ic, "_get_free_space_gb", _FakeFreeSpace(1.0))
        collector = build_collector(_MockConfig(
            dataset_path, **{"image_collector.min_free_space_gb": 5}))
        stats = collector.get_stats()
        assert stats["disk_free_gb"] == 1.0
        assert stats["disk_guard_active"] is True

    def test_unmeasurable_disk_does_not_block_saving(self, dataset_path, build_collector,
                                                    monkeypatch):
        """Sin medición no se puede afirmar que falte espacio: se guarda y se avisa."""
        monkeypatch.setattr(ic, "_get_free_space_gb", lambda path: None)
        collector = build_collector(_MockConfig(
            dataset_path, **{"image_collector.min_free_space_gb": 5}))
        assert collector.save_now("camera_1", _frame(1)) is True
        assert collector.get_stats()["disk_free_gb"] == -1.0


class TestIntervalMode:
    @pytest.fixture
    def config(self, dataset_path) -> _MockConfig:
        return _MockConfig(dataset_path, **{"image_collector.mode": "interval"})

    def test_start_and_stop_are_idempotent(self, config, build_collector, fast_interval):
        collector = build_collector(config)
        collector.start()
        collector.start()
        assert collector.is_running is True
        collector.stop()
        collector.stop()
        assert collector.is_running is False

    def test_saves_one_image_per_camera_per_tick(self, dataset_path, config,
                                                build_collector, fast_interval):
        collector = build_collector(config)
        collector.start()
        collector.push_frame("camera_1", _frame(1))
        collector.push_frame("camera_2", _frame(2))

        assert _wait_until(lambda: collector.get_stats()["saved"] == 2)
        assert collector.get_stats()["images"] == {"camera_1": 1, "camera_2": 1}

    def test_a_pushed_frame_is_saved_once(self, config, build_collector, fast_interval):
        """
        El frame se consume: una cámara que dejó de pushear no se guarda de nuevo en
        cada vuelta.
        """
        collector = build_collector(config)
        collector.start()
        collector.push_frame("camera_1", _frame(1))
        assert _wait_until(lambda: collector.get_stats()["saved"] == 1)

        time.sleep(_FAST_INTERVAL_S * _TICKS_TO_WAIT)
        assert collector.get_stats()["saved"] == 1

    def test_last_push_wins(self, dataset_path, config, build_collector, fast_interval):
        """
        Dos push en la misma vuelta dejan un solo guardado, el del último frame: es lo
        que mantiene juntos el frame, su anotado y su JSON.
        """
        # Ventana holgada para que los dos push entren seguro en la misma vuelta, y
        # corta para no frenar la suite.
        config.set("image_collector.interval_min_s", _WINDOW_INTERVAL_S)
        config.set("image_collector.interval_max_s", _WINDOW_INTERVAL_S)
        collector = build_collector(config)
        collector.start()
        collector.push_frame("camera_1", _frame(1))
        collector.push_frame("camera_1", _frame(2))

        assert _wait_until(lambda: collector.get_stats()["saved"] == 1)
        saved_png = next(dataset_path.rglob("*.png"))
        decoded = cv2.imdecode(np.fromfile(str(saved_png), np.uint8), cv2.IMREAD_COLOR)
        assert np.array_equal(decoded, _frame(2))

    def test_inactive_collection_pushes_nothing(self, dataset_path, config,
                                               build_collector, fast_interval):
        config.set("image_collector.enabled", False)
        collector = build_collector(config)
        collector.start()
        collector.push_frame("camera_1", _frame(1))

        time.sleep(_FAST_INTERVAL_S * _TICKS_TO_WAIT)
        assert _files(dataset_path) == []

    def test_stop_discards_the_cached_frame(self, dataset_path, config, build_collector):
        """Con un intervalo largo el frame queda en caché; stop() no lo escribe."""
        config.set("image_collector.interval_min_s", 30)
        config.set("image_collector.interval_max_s", 30)
        collector = build_collector(config)
        collector.start()
        collector.push_frame("camera_1", _frame(1))
        collector.stop()

        assert _files(dataset_path) == []
        assert collector.is_running is False

    def test_save_now_still_writes_immediately(self, dataset_path, config, build_collector):
        """El guardado a pedido no espera la vuelta del hilo."""
        config.set("image_collector.interval_min_s", 30)
        config.set("image_collector.interval_max_s", 30)
        collector = build_collector(config)
        collector.start()
        assert collector.save_now("camera_1", _frame(1)) is True
        assert len(_pngs(dataset_path)) == 1

    def test_filters_apply_in_interval_mode(self, dataset_path, config, build_collector,
                                           fast_interval):
        collector = build_collector(config, save_conditions=[min_confidence_condition(75)])
        collector.start()
        collector.push_frame("camera_1", _frame(1), inference={"confidence_pct": 10})

        assert _wait_until(lambda: collector.get_stats()["skipped"] == 1)
        assert _files(dataset_path) == []


class TestIntervalSchedule:
    def test_equal_bounds_give_a_fixed_period(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(dataset_path, **{
            "image_collector.interval_min_s": 30,
            "image_collector.interval_max_s": 30,
        }))
        assert collector._next_interval_s() == pytest.approx(30.0)

    def test_bounds_out_of_order_are_swapped(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(dataset_path, **{
            "image_collector.interval_min_s": 600,
            "image_collector.interval_max_s": 60,
        }))
        assert 60.0 <= collector._next_interval_s() <= 600.0

    def test_interval_has_a_floor(self, dataset_path, build_collector):
        """Un intervalo de 0 haría un guardado por vuelta del hilo."""
        collector = build_collector(_MockConfig(dataset_path, **{
            "image_collector.interval_min_s": 0,
            "image_collector.interval_max_s": 0,
        }))
        assert collector._next_interval_s() >= ic._MIN_INTERVAL_S

    def test_interval_is_read_fresh_every_time(self, dataset_path, build_collector):
        """Mover la ventana en el config no exige reiniciar el recolector."""
        config = _MockConfig(dataset_path, **{
            "image_collector.interval_min_s": 10,
            "image_collector.interval_max_s": 10,
        })
        collector = build_collector(config)
        assert collector._next_interval_s() == pytest.approx(10.0)
        config.set("image_collector.interval_min_s", 20)
        config.set("image_collector.interval_max_s", 20)
        assert collector._next_interval_s() == pytest.approx(20.0)


class TestStats:
    def test_has_the_stable_keys(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(dataset_path))
        assert set(collector.get_stats()) == _STATS_KEYS

    def test_counters_start_at_zero(self, dataset_path, build_collector):
        stats = build_collector(_MockConfig(dataset_path)).get_stats()
        assert (stats["saved"], stats["skipped"]) == (0, 0)
        assert stats["images"] == {}
        assert stats["last_save_iso"] is None

    def test_last_save_is_stamped(self, dataset_path, build_collector):
        collector = build_collector(_MockConfig(dataset_path))
        collector.save_now("camera_1", _frame(1))
        stats = collector.get_stats()
        assert stats["saved"] == 1
        assert stats["last_save_iso"] is not None

    def test_reports_mode_and_running(self, dataset_path, build_collector, fast_interval):
        config = _MockConfig(dataset_path, **{"image_collector.mode": "interval"})
        collector = build_collector(config)
        collector.start()
        stats = collector.get_stats()
        assert (stats["mode"], stats["running"], stats["active"]) == ("interval", True, True)
