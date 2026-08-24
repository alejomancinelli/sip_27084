"""Tests del contrato de los servidores de video: qué tiene que implementar un
transporte nuevo y qué garantiza la base a quien empuja frames.

Los servidores concretos se prueban en su propio archivo; acá se verifica lo que
los dos comparten, que es lo que `main.py` va a dar por cierto para tratarlos como
una lista.
"""

import numpy as np
import pytest

from system.video.abstract_video_server import (
    MODE_ANNOTATED,
    MODE_RAW,
    MODES,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_ERROR,
    AbstractVideoServer,
    StreamKey,
)
from system.video.http_server import HttpVideoServer
from system.video.rtsp_server import RtspVideoServer

_IMPLEMENTATIONS = (HttpVideoServer, RtspVideoServer)


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **values):
        self._values = values

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _MinimalServer(AbstractVideoServer):
    """Lo mínimo que hay que escribir para sumar un transporte nuevo."""

    def __init__(self, config_manager, watched: tuple = ()):
        super().__init__(config_manager)
        self._watched = watched
        self.pushed: list = []

    def get_client_count(self) -> int:
        return len(self._watched)

    def start(self):
        self._status = STATUS_ACTIVE

    def stop(self):
        self._status = STATUS_DISABLED

    def _push(self, key: StreamKey, frame_bgr: np.ndarray | None):
        self.pushed.append((key, frame_bgr))

    def _is_watched(self, key: StreamKey) -> bool:
        return key in self._watched


def _frame() -> np.ndarray:
    return np.zeros((4, 4, 3), np.uint8)


class TestContract:
    @pytest.mark.parametrize("server_class", _IMPLEMENTATIONS)
    def test_every_server_implements_the_abstraction(self, server_class):
        assert issubclass(server_class, AbstractVideoServer)

    def test_the_abstraction_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            AbstractVideoServer(_MockConfig())

    def test_a_server_without_the_whole_contract_cannot_be_instantiated(self):
        """Falta `_is_watched`: sin él nadie puede saber si vale la pena empujar."""

        class _Incomplete(AbstractVideoServer):
            def get_client_count(self) -> int:
                return 0

            def start(self):
                pass

            def stop(self):
                pass

            def _push(self, key: StreamKey, frame_bgr: np.ndarray | None):
                pass

        with pytest.raises(TypeError):
            _Incomplete(_MockConfig())


class TestStatus:
    def test_a_server_starts_disabled(self):
        assert _MinimalServer(_MockConfig()).status == STATUS_DISABLED

    def test_the_status_vocabulary_has_no_repeated_values(self):
        """Son estados excluyentes: dos con el mismo texto serían indistinguibles."""
        assert len({STATUS_DISABLED, STATUS_ACTIVE, STATUS_ERROR}) == 3

    def test_the_lifecycle_moves_the_status(self):
        server = _MinimalServer(_MockConfig())
        server.start()
        assert server.status == STATUS_ACTIVE
        server.stop()
        assert server.status == STATUS_DISABLED


class TestModes:
    def test_there_is_one_stream_per_camera_and_mode(self):
        assert MODES == (MODE_RAW, MODE_ANNOTATED)

    def test_the_modes_are_the_text_that_goes_in_the_path(self):
        """Las rutas y las URLs se arman con estos valores: cambiarlos las cambia."""
        assert (MODE_RAW, MODE_ANNOTATED) == ("raw", "annotated")


class TestPush:
    def test_push_raw_carries_the_slot_and_the_raw_mode(self):
        server = _MinimalServer(_MockConfig())
        frame_bgr = _frame()
        server.push_raw("camera_1", frame_bgr)
        assert server.pushed == [(("camera_1", MODE_RAW), frame_bgr)]

    def test_push_annotated_carries_the_slot_and_the_annotated_mode(self):
        server = _MinimalServer(_MockConfig())
        frame_bgr = _frame()
        server.push_annotated("camera_2", frame_bgr)
        assert server.pushed == [(("camera_2", MODE_ANNOTATED), frame_bgr)]

    def test_a_none_frame_reaches_the_implementation(self):
        """Descartarlo es de la implementación: la base no decide por ella."""
        server = _MinimalServer(_MockConfig())
        server.push_raw("camera_1", None)
        assert server.pushed == [(("camera_1", MODE_RAW), None)]


class TestClientPredicates:
    def test_the_predicates_ask_for_the_stream_of_that_mode(self):
        server = _MinimalServer(_MockConfig(), watched=(("camera_1", MODE_RAW),
                                                        ("camera_2", MODE_ANNOTATED)))
        assert server.has_raw_clients("camera_1") is True
        assert server.has_annotated_clients("camera_1") is False
        assert server.has_raw_clients("camera_2") is False
        assert server.has_annotated_clients("camera_2") is True

    def test_an_unknown_slot_has_no_clients(self):
        server = _MinimalServer(_MockConfig(), watched=(("camera_1", MODE_RAW),))
        assert server.has_raw_clients("camera_9") is False


class TestCameraSlots:
    def test_the_slots_keep_the_config_order(self):
        server = _MinimalServer(_MockConfig(cameras={"camera_2": {}, "camera_1": {}}))
        assert server._read_camera_slots() == ("camera_2", "camera_1")

    def test_the_slots_include_disabled_cameras(self):
        """`enabled` cambia en caliente: la ruta no puede aparecer y desaparecer."""
        server = _MinimalServer(_MockConfig(cameras={"camera_1": {"enabled": False}}))
        assert server._read_camera_slots() == ("camera_1",)

    def test_no_cameras_section_is_not_an_error(self):
        assert _MinimalServer(_MockConfig())._read_camera_slots() == ()

    def test_an_empty_cameras_section_is_not_an_error(self):
        """`cameras:` sin nada abajo se lee como None, no como {}."""
        assert _MinimalServer(_MockConfig(cameras=None))._read_camera_slots() == ()

