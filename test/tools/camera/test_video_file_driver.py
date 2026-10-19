"""
Tests del driver de video grabado: la playlist, el loop y el ritmo por reloj.

Los videos son de verdad —unos pocos frames MJPG en `tmp_path`, que OpenCV escribe sin
codecs del sistema— y el reloj es un doble: lo que se verifica es qué frame entrega el
driver según el tiempo transcurrido, sin esperar ese tiempo.
"""

import os

import cv2
import numpy as np
import pytest

from tools.camera import video_file_driver
from tools.camera.video_file_driver import VideoFileDriver

_STATUS_KEYS = {"connected", "capture_enabled", "temperature", "fps_estimated"}

_CLIP_FPS = 10.0
_FRAME_SHAPE = (48, 64, 3)
_LEVEL_STEP = 20         # cada frame es gris uniforme: (número de frame + 1) × este paso
_LEVEL_TOLERANCE = 4     # lo que mueve la compresión MJPG sobre un gris uniforme


class _Clock:
    """Doble del módulo `time` del driver: `sleep()` avanza el reloj en vez de esperar."""

    def __init__(self):
        self.now_s = 1000.0
        self.sleeps_s: list[float] = []

    def monotonic(self) -> float:
        return self.now_s

    def sleep(self, seconds: float):
        self.sleeps_s.append(seconds)
        self.now_s += seconds

    def advance(self, seconds: float):
        self.now_s += seconds


def _write_clip(path: str, frame_count: int, *, first_level: int = _LEVEL_STEP,
                fps: float = _CLIP_FPS) -> str:
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), fps,
                             (_FRAME_SHAPE[1], _FRAME_SHAPE[0]))
    for i in range(frame_count):
        writer.write(np.full(_FRAME_SHAPE, first_level + i * _LEVEL_STEP, np.uint8))
    writer.release()
    return path


def _level_of(frame: np.ndarray) -> int:
    return int(round(float(frame.mean())))


def _frame_number(frame: np.ndarray) -> int:
    """Qué frame del clip es, a partir de su nivel de gris."""
    return round(_level_of(frame) / _LEVEL_STEP) - 1


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(video_file_driver, "time", fake)
    return fake


def _connected(address: str, **extra) -> VideoFileDriver:
    driver = VideoFileDriver({"address": address, **extra})
    assert driver.connect()
    return driver


class TestPlaylist:
    def test_a_single_file_plays(self, tmp_path, clock):
        driver = _connected(_write_clip(str(tmp_path / "clip.avi"), 3))
        assert _frame_number(driver.get_frame()) == 0

    def test_a_folder_plays_its_videos_in_alphabetical_order(self, tmp_path, clock):
        _write_clip(str(tmp_path / "b.avi"), 2, first_level=200)
        _write_clip(str(tmp_path / "a.avi"), 2, first_level=40)
        driver = _connected(str(tmp_path))

        levels = []
        for _ in range(4):
            levels.append(_level_of(driver.get_frame()))
        assert levels == pytest.approx([40, 60, 200, 220], abs=_LEVEL_TOLERANCE)

    def test_files_that_are_not_videos_are_ignored(self, tmp_path, clock):
        (tmp_path / "notes.txt").write_text("no es un video")
        _write_clip(str(tmp_path / "clip.avi"), 2)
        driver = _connected(str(tmp_path))
        assert driver.get_frame() is not None

    def test_a_relative_address_is_anchored_to_the_installation(self, tmp_path, monkeypatch,
                                                                 clock):
        monkeypatch.setattr("system.paths.DATA_DIR", str(tmp_path))
        os.mkdir(tmp_path / "replay")
        _write_clip(str(tmp_path / "replay" / "clip.avi"), 2)
        assert _connected("replay/clip.avi").get_frame() is not None

    def test_a_video_added_to_the_folder_enters_on_the_next_connect(self, tmp_path, clock):
        _write_clip(str(tmp_path / "a.avi"), 1, first_level=40)
        driver = _connected(str(tmp_path))
        _write_clip(str(tmp_path / "b.avi"), 1, first_level=200)

        driver.disconnect()
        assert driver.connect()
        levels = [_level_of(driver.get_frame()) for _ in range(2)]
        assert levels == pytest.approx([40, 200], abs=_LEVEL_TOLERANCE)


class TestUnplayableAddress:
    @pytest.mark.parametrize("address", ["", "missing.avi"])
    def test_an_address_without_video_does_not_connect(self, tmp_path, address):
        driver = VideoFileDriver({"address": str(tmp_path / address) if address else ""})
        assert driver.connect() is False
        assert driver.is_connected is False
        assert driver.get_status()["error"]

    def test_a_folder_without_videos_does_not_connect(self, tmp_path):
        (tmp_path / "notes.txt").write_text("no es un video")
        driver = VideoFileDriver({"address": str(tmp_path)})
        assert driver.connect() is False
        assert "no tiene videos" in driver.get_status()["error"]

    def test_a_file_that_does_not_open_is_skipped(self, tmp_path, clock):
        (tmp_path / "a_broken.mp4").write_bytes(b"esto no es un mp4")
        _write_clip(str(tmp_path / "b.avi"), 2, first_level=200)
        driver = _connected(str(tmp_path))
        assert _level_of(driver.get_frame()) == pytest.approx(200, abs=_LEVEL_TOLERANCE)

    def test_a_list_where_nothing_opens_does_not_connect(self, tmp_path):
        (tmp_path / "broken.mp4").write_bytes(b"esto no es un mp4")
        driver = VideoFileDriver({"address": str(tmp_path)})
        assert driver.connect() is False
        assert driver.get_status()["error"]

    def test_a_successful_connect_clears_the_error(self, tmp_path, clock):
        driver = VideoFileDriver({"address": str(tmp_path / "clip.avi")})
        assert driver.connect() is False
        _write_clip(str(tmp_path / "clip.avi"), 2)
        assert driver.connect()
        assert "error" not in driver.get_status()


class TestLoop:
    def test_the_video_starts_again_after_the_last_frame(self, tmp_path, clock):
        driver = _connected(_write_clip(str(tmp_path / "clip.avi"), 3))
        numbers = [_frame_number(driver.get_frame()) for _ in range(7)]
        assert numbers == [0, 1, 2, 0, 1, 2, 0]

    def test_the_playlist_starts_again_after_the_last_video(self, tmp_path, clock):
        _write_clip(str(tmp_path / "a.avi"), 1, first_level=40)
        _write_clip(str(tmp_path / "b.avi"), 1, first_level=200)
        driver = _connected(str(tmp_path))
        levels = [_level_of(driver.get_frame()) for _ in range(4)]
        assert levels == pytest.approx([40, 200, 40, 200], abs=_LEVEL_TOLERANCE)


class TestPacing:
    def test_frames_come_out_at_the_video_fps(self, tmp_path, clock):
        driver = _connected(_write_clip(str(tmp_path / "clip.avi"), 5))
        driver.get_frame()
        clock.sleeps_s.clear()

        driver.get_frame()
        assert clock.sleeps_s == [pytest.approx(1 / _CLIP_FPS)]

    def test_a_late_consumer_gets_the_frame_of_now_and_loses_the_ones_between(
            self, tmp_path, clock):
        driver = _connected(_write_clip(str(tmp_path / "clip.avi"), 10))
        driver.get_frame()

        clock.advance(5 / _CLIP_FPS)
        assert _frame_number(driver.get_frame()) == 5

    def test_a_long_delay_is_recovered_by_seeking(self, tmp_path, monkeypatch, clock):
        monkeypatch.setattr(video_file_driver, "_MAX_SKIP_FRAMES", 2)
        driver = _connected(_write_clip(str(tmp_path / "clip.avi"), 10))
        driver.get_frame()

        clock.advance(8 / _CLIP_FPS)
        assert _frame_number(driver.get_frame()) == 8

    def test_a_delay_past_the_end_moves_on_to_the_next_video(self, tmp_path, clock):
        _write_clip(str(tmp_path / "a.avi"), 3, first_level=40)
        _write_clip(str(tmp_path / "b.avi"), 3, first_level=200)
        driver = _connected(str(tmp_path))
        driver.get_frame()

        clock.advance(60.0)
        assert _level_of(driver.get_frame()) == pytest.approx(200, abs=_LEVEL_TOLERANCE)

    def test_fps_limit_below_the_video_fps_spaces_the_deliveries(self, tmp_path, clock):
        driver = _connected(_write_clip(str(tmp_path / "clip.avi"), 10),
                            acquisition={"fps_limit": _CLIP_FPS / 4})
        driver.get_frame()

        second = driver.get_frame()
        assert clock.sleeps_s[-1] == pytest.approx(4 / _CLIP_FPS)
        assert _frame_number(second) == 4

    def test_a_frame_not_due_within_the_timeout_returns_none(self, tmp_path, clock):
        driver = _connected(_write_clip(str(tmp_path / "clip.avi"), 5))
        driver.get_frame()

        timeout_ms = int(1000 / _CLIP_FPS / 2)
        assert driver.get_frame(timeout_ms=timeout_ms) is None
        assert clock.sleeps_s == []

    def test_an_fps_the_container_does_not_report_falls_back_to_fps_limit(
            self, tmp_path, monkeypatch, clock):
        monkeypatch.setattr(video_file_driver, "_MAX_CLIP_FPS", _CLIP_FPS / 2)
        driver = _connected(_write_clip(str(tmp_path / "clip.avi"), 5),
                            acquisition={"fps_limit": 4})
        driver.get_frame()
        driver.get_frame()
        assert clock.sleeps_s[-1] == pytest.approx(1 / 4)


class TestCaptureEnabled:
    def test_disabled_capture_yields_no_frames_while_still_connected(self, tmp_path, clock):
        driver = _connected(_write_clip(str(tmp_path / "clip.avi"), 3))
        driver.set_capture_enabled(False)
        assert driver.get_frame() is None
        assert driver.get_status()["connected"] is True
        assert driver.get_status()["capture_enabled"] is False

    def test_the_clock_keeps_running_while_capture_is_disabled(self, tmp_path, clock):
        driver = _connected(_write_clip(str(tmp_path / "clip.avi"), 10))
        driver.get_frame()
        driver.set_capture_enabled(False)

        clock.advance(6 / _CLIP_FPS)
        driver.set_capture_enabled(True)
        assert _frame_number(driver.get_frame()) == 6

    def test_a_disconnected_driver_yields_no_frames(self, tmp_path, clock):
        driver = _connected(_write_clip(str(tmp_path / "clip.avi"), 3))
        driver.disconnect()
        assert driver.get_frame() is None
        assert driver.get_status()["connected"] is False


class TestStatus:
    def test_status_has_the_stable_keys(self, tmp_path, clock):
        status = _connected(_write_clip(str(tmp_path / "clip.avi"), 2)).get_status()
        assert _STATUS_KEYS <= set(status)
        assert status["temperature"] == 0.0

    def test_recorded_video_is_marked_as_synthetic(self):
        """Imagen real, pero no la línea ahora: el estado que llega al PLC lo tiene que decir."""
        assert VideoFileDriver({}).is_synthetic is True

    def test_the_driver_builds_without_an_address(self):
        """La fábrica lo construye con la config que haya; el error aparece al conectar."""
        assert VideoFileDriver({}).is_config_error is False


class TestRotation:
    def test_rotation_comes_from_the_base_class(self, tmp_path, clock):
        clip = _write_clip(str(tmp_path / "clip.avi"), 2)
        rotated = _connected(clip, rotation="90cw").get_frame()
        assert rotated.shape[:2] == (_FRAME_SHAPE[1], _FRAME_SHAPE[0])
