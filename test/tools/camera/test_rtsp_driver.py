"""
Tests del driver RTSP: la URL que se arma y el loop de captura.

No hay cámara ni red: se reemplaza `cv2.VideoCapture` por un doble y se corre
`_grab_loop` directamente. Lo que se verifica es lo que el driver decide —cómo compone
la URL, qué frame entrega, cuándo descarta sin decodificar y cuándo reabre el stream—;
decodificar H.264 es FFmpeg y no se puede afirmar sin una cámara.
"""

import os

import numpy as np
import pytest

from tools.camera import rtsp_driver
from tools.camera.rtsp_driver import RtspDriver

_STATUS_KEYS = {"connected", "capture_enabled", "temperature", "fps_estimated"}


class _Stream:
    """
    Doble del stream RTSP, compartido por todas las sesiones que abra el driver.

    Entrega `frames` frames por sesión —cada uno relleno con el número de lectura, para
    poder afirmar cuál llegó— y después falla, como una cámara que se queda muda. Los
    contadores son del stream y no de la sesión: los topes que cortan el loop tienen que
    valer en total, o un driver que reabre para siempre deja el test colgado en vez de
    hacerlo fallar.
    """

    def __init__(self, driver: RtspDriver, *, frames: int = 0, opens: bool = True,
                 stop_after_grabs: int = 1, stop_after_sessions: int = 0):
        self._driver = driver
        self._frames = frames
        self._stop_after_grabs = stop_after_grabs
        self._stop_after_sessions = stop_after_sessions
        self._session_grabs = 0
        self.opens = opens
        self.grabs = 0
        self.retrieves = 0
        self.sessions = 0
        self.released = 0
        self.open_calls: list = []

    def open_capture(self, url: str, api_preference: int = None, params: list = None):
        """Reemplaza a cv2.VideoCapture: cada llamada es una sesión nueva."""
        self.sessions += 1
        self._session_grabs = 0
        self.open_calls.append((url, api_preference, params))
        if self._stop_after_sessions and self.sessions >= self._stop_after_sessions:
            self._driver._grab_active = False
        return _Capture(self)

    def _grab(self) -> bool:
        self.grabs += 1
        self._session_grabs += 1
        if self.grabs >= self._stop_after_grabs:
            self._driver._grab_active = False
        return self._session_grabs <= self._frames


class _Capture:
    """Una sesión abierta contra el stream doble."""

    def __init__(self, stream: _Stream):
        self._stream = stream
        self.is_released = False
        self.props: dict = {}

    def isOpened(self) -> bool:  # noqa: N802 — firma de OpenCV
        return self._stream.opens

    def set(self, prop_id: int, value: float) -> bool:
        self.props[prop_id] = value
        return True

    def grab(self) -> bool:
        return self._stream._grab()

    def retrieve(self):
        self._stream.retrieves += 1
        return True, np.full((4, 4, 3), self._stream.grabs, dtype=np.uint8)

    def release(self):
        self._stream.released += 1
        self.is_released = True


@pytest.fixture(autouse=True)
def no_waits(monkeypatch):
    """El loop no tiene por qué esperar en los tests."""
    monkeypatch.setattr(rtsp_driver, "_RETRY_DELAY_S", 0)
    monkeypatch.setattr(rtsp_driver, "_PAUSE_POLL_S", 0)


@pytest.fixture(autouse=True)
def no_env_credentials(monkeypatch):
    """
    Las credenciales del entorno del equipo no pueden cambiar lo que afirma un test.

    Se limpian también las de sufijo: en el equipo de desarrollo hay un `.env` con las
    de la cámara del banco, y una de esas variables sueltas en el entorno haría pasar un
    test que debería fallar.
    """
    for name in list(os.environ):
        if name.startswith((rtsp_driver.ENV_USER, rtsp_driver.ENV_PASSWORD)):
            monkeypatch.delenv(name, raising=False)


def _driver(monkeypatch, *, address: str = "10.8.83.90/stream1", fps_limit: float = 0,
            **stream_kwargs) -> tuple[RtspDriver, _Stream]:
    """Un driver con el stream doble ya cableado, listo para correr `_grab_loop`."""
    driver = RtspDriver({"address": address, "acquisition": {"fps_limit": fps_limit}})
    stream = _Stream(driver, **stream_kwargs)
    monkeypatch.setattr(rtsp_driver.cv2, "VideoCapture", stream.open_capture)
    driver._grab_active = True
    return driver, stream


class TestStreamUrl:
    def test_a_full_url_is_used_as_is(self):
        url = "rtsp://10.8.83.90:554/Streaming/Channels/101"
        assert RtspDriver({"address": url})._url == url

    def test_a_bare_host_gets_the_scheme(self):
        assert RtspDriver({"address": "10.8.83.90"})._url == "rtsp://10.8.83.90"

    def test_a_host_with_port_and_path_gets_the_scheme(self):
        driver = RtspDriver({"address": "10.8.83.90:554/stream1"})
        assert driver._url == "rtsp://10.8.83.90:554/stream1"

    def test_surrounding_whitespace_is_dropped(self):
        assert RtspDriver({"address": "  10.8.83.90  "})._url == "rtsp://10.8.83.90"

    @pytest.mark.parametrize("address", ["", "   ", None])
    def test_without_address_there_is_no_url(self, address):
        assert RtspDriver({"address": address})._url == ""


class TestCredentials:
    """No van al config: se versiona y se comparte. Ver ENV_USER / ENV_PASSWORD."""

    def test_they_come_from_the_environment(self, monkeypatch):
        monkeypatch.setenv(rtsp_driver.ENV_USER, "operador")
        monkeypatch.setenv(rtsp_driver.ENV_PASSWORD, "secreta")
        driver = RtspDriver({"address": "10.8.83.90/stream1"})
        assert driver._url == "rtsp://operador:secreta@10.8.83.90/stream1"

    def test_a_user_without_password_is_still_applied(self, monkeypatch):
        monkeypatch.setenv(rtsp_driver.ENV_USER, "operador")
        assert RtspDriver({"address": "10.8.83.90"})._url == "rtsp://operador@10.8.83.90"

    def test_characters_that_break_the_url_are_escaped(self, monkeypatch):
        """Un `@` o un `/` en la password partiría la URL en otro host y otra ruta."""
        monkeypatch.setenv(rtsp_driver.ENV_USER, "admin")
        monkeypatch.setenv(rtsp_driver.ENV_PASSWORD, "a@b/c")
        driver = RtspDriver({"address": "10.8.83.90"})
        assert driver._url == "rtsp://admin:a%40b%2Fc@10.8.83.90"

    def test_the_address_wins_over_the_environment(self, monkeypatch):
        """La cámara que necesita otras credenciales las lleva en su `address`."""
        monkeypatch.setenv(rtsp_driver.ENV_USER, "operador")
        url = "rtsp://otro:clave@10.8.83.90/stream1"
        assert RtspDriver({"address": url})._url == url

    def test_the_password_never_reaches_the_log(self, monkeypatch):
        monkeypatch.setenv(rtsp_driver.ENV_USER, "operador")
        monkeypatch.setenv(rtsp_driver.ENV_PASSWORD, "secreta")
        safe_url = RtspDriver({"address": "10.8.83.90/stream1"})._safe_url
        assert "secreta" not in safe_url
        assert safe_url == "rtsp://operador:***@10.8.83.90/stream1"


class TestPerCameraCredentials:
    """
    `credentials_env` nombra al secreto, no a la cámara: dos cámaras con la misma cuenta
    apuntan al mismo sufijo, y una que cambia de slot no obliga a renombrar la variable.
    """

    def test_the_camera_uses_the_variables_of_its_suffix(self, monkeypatch):
        monkeypatch.setenv(f"{rtsp_driver.ENV_USER}_CAM2", "operador2")
        monkeypatch.setenv(f"{rtsp_driver.ENV_PASSWORD}_CAM2", "secreta2")
        driver = RtspDriver({"address": "10.8.83.91/stream1", "credentials_env": "CAM2"})
        assert driver._url == "rtsp://operador2:secreta2@10.8.83.91/stream1"

    def test_the_suffix_is_uppercased(self, monkeypatch):
        """El config puede traer `cam2` y la variable llamarse RTSP_USER_CAM2."""
        monkeypatch.setenv(f"{rtsp_driver.ENV_USER}_CAM2", "operador2")
        driver = RtspDriver({"address": "10.8.83.91", "credentials_env": "  cam2  "})
        assert driver._url == "rtsp://operador2@10.8.83.91"

    def test_two_cameras_can_share_one_secret(self, monkeypatch):
        monkeypatch.setenv(f"{rtsp_driver.ENV_USER}_BELT", "operador")
        monkeypatch.setenv(f"{rtsp_driver.ENV_PASSWORD}_BELT", "secreta")
        first = RtspDriver({"address": "10.8.83.91", "credentials_env": "BELT"})
        second = RtspDriver({"address": "10.8.83.92", "credentials_env": "BELT"})
        assert first._url == "rtsp://operador:secreta@10.8.83.91"
        assert second._url == "rtsp://operador:secreta@10.8.83.92"

    def test_a_missing_variable_does_not_fall_back_to_the_shared_account(self, monkeypatch):
        """
        Prestarle a una cámara las credenciales de otra da un 401 que culpa a lo que no
        es, o peor, una sesión con una cuenta que nadie eligió.
        """
        monkeypatch.setenv(rtsp_driver.ENV_USER, "compartido")
        monkeypatch.setenv(rtsp_driver.ENV_PASSWORD, "compartida")
        driver = RtspDriver({"address": "10.8.83.91/stream1", "credentials_env": "CAM2"})
        assert driver._url == "rtsp://10.8.83.91/stream1"

    def test_the_missing_variable_is_named_in_the_log(self, caplog):
        """Sin el nombre, el que lee el log no sabe qué definir."""
        with caplog.at_level("ERROR"):
            RtspDriver({"address": "10.8.83.91", "credentials_env": "CAM2"})
        assert f"{rtsp_driver.ENV_USER}_CAM2" in caplog.text

    def test_the_password_of_the_suffix_never_reaches_the_log(self, monkeypatch, caplog):
        monkeypatch.setenv(f"{rtsp_driver.ENV_USER}_CAM2", "operador2")
        monkeypatch.setenv(f"{rtsp_driver.ENV_PASSWORD}_CAM2", "secreta2")
        with caplog.at_level("DEBUG"):
            driver = RtspDriver({"address": "10.8.83.91", "credentials_env": "CAM2"})

        assert "secreta2" not in caplog.text
        assert "secreta2" not in driver.stream_url
        assert f"{rtsp_driver.ENV_PASSWORD}_CAM2" in caplog.text

    def test_the_address_wins_over_the_suffix(self, monkeypatch):
        monkeypatch.setenv(f"{rtsp_driver.ENV_USER}_CAM2", "operador2")
        url = "rtsp://otro:clave@10.8.83.91/stream1"
        assert RtspDriver({"address": url, "credentials_env": "CAM2"})._url == url

    @pytest.mark.parametrize("credentials_env", ["", "   ", None])
    def test_without_the_key_the_shared_account_applies(self, monkeypatch, credentials_env):
        monkeypatch.setenv(rtsp_driver.ENV_USER, "compartido")
        driver = RtspDriver({"address": "10.8.83.91", "credentials_env": credentials_env})
        assert driver._url == "rtsp://compartido@10.8.83.91"


class TestWithoutAddress:
    def test_it_never_connects(self):
        driver = RtspDriver({"address": ""})
        assert driver.connect() is False
        assert driver.is_connected is False
        assert driver.get_frame() is None

    def test_the_status_carries_the_reason(self):
        status = RtspDriver({"address": ""}).get_status()
        assert _STATUS_KEYS <= set(status)
        assert "address" in status["error"]


class TestStatus:
    def test_the_stable_keys_are_there_without_hardware(self):
        assert _STATUS_KEYS <= set(RtspDriver({"address": "10.8.83.90"}).get_status())

    def test_temperature_stays_at_zero(self):
        """Por el stream la cámara no publica sensores: un 0.0 es 'sin lectura'."""
        assert RtspDriver({"address": "10.8.83.90"}).get_status()["temperature"] == 0.0


class TestGrabLoop:
    def test_the_frame_reaches_the_consumer(self, monkeypatch):
        driver, _ = _driver(monkeypatch, frames=1, stop_after_grabs=2)
        driver._grab_loop()

        assert driver._img_queue.get_nowait().shape == (4, 4, 3)

    def test_the_stream_is_opened_with_ffmpeg_and_timeouts(self, monkeypatch):
        """Sin timeouts, un host que no contesta deja el thread colgado para siempre."""
        driver, stream = _driver(monkeypatch, frames=0, stop_after_grabs=1)
        driver._grab_loop()

        url, api_preference, params = stream.open_calls[0]
        assert url == driver._url
        assert api_preference == rtsp_driver.cv2.CAP_FFMPEG
        assert rtsp_driver.cv2.CAP_PROP_OPEN_TIMEOUT_MSEC in params
        assert rtsp_driver.cv2.CAP_PROP_READ_TIMEOUT_MSEC in params

    def test_a_stream_that_does_not_open_leaves_the_driver_disconnected(self, monkeypatch):
        driver, stream = _driver(monkeypatch, opens=False, stop_after_sessions=1)
        driver._grab_loop()

        assert driver.is_connected is False
        assert stream.released == 1, "una captura que no abre igual se suelta"

    def test_only_the_newest_frames_survive_a_slow_consumer(self, monkeypatch):
        """Always-fresh: la cola corta descarta el viejo en vez de acumular atraso."""
        driver, _ = _driver(monkeypatch, frames=6, stop_after_grabs=7)
        driver._grab_loop()

        assert int(driver._img_queue.get_nowait()[0, 0, 0]) >= 5
        assert driver._img_queue.qsize() == 1

    def test_a_camera_that_goes_quiet_is_reported_as_a_drop(self, monkeypatch):
        """Ésta sí es una caída, y el status tiene que decirlo mientras se reabre."""
        driver, stream = _driver(
            monkeypatch, frames=0, stop_after_grabs=rtsp_driver._READ_FAIL_LIMIT
        )
        driver._grab_loop()

        assert stream.sessions == 1
        assert driver.get_status()["connected"] is False

    def test_the_stream_reopens_after_the_camera_goes_quiet(self, monkeypatch):
        """Un corte que FFmpeg no reporta como cierre: se ve por las lecturas vacías."""
        driver, stream = _driver(
            monkeypatch, frames=0, stop_after_grabs=rtsp_driver._READ_FAIL_LIMIT + 1
        )
        driver._grab_loop()

        assert stream.released >= 1
        assert stream.sessions >= 2, "no reabrió el stream"


class TestFpsLimit:
    def test_without_a_limit_every_frame_is_decoded(self, monkeypatch):
        driver, stream = _driver(monkeypatch, fps_limit=0, frames=4, stop_after_grabs=5)
        driver._grab_loop()
        assert stream.retrieves == 4

    def test_the_extra_frame_is_dropped_before_decoding(self, monkeypatch):
        """Decodificar H.264 para tirar el frame es el gasto que este tope evita."""
        driver, stream = _driver(monkeypatch, fps_limit=0.01, frames=4, stop_after_grabs=5)
        driver._grab_loop()

        # Un tope de un frame cada 100 s: pasa el del crédito inicial y el resto se
        # descarta sin decodificar.
        assert stream.grabs == 5
        assert stream.retrieves == 1

    def test_a_missing_fps_limit_is_not_a_division_by_zero(self):
        driver = RtspDriver({"address": "10.8.83.90", "acquisition": {}})
        assert driver._min_frame_period_s == 0.0
        assert driver._take_frame_credit(0.0) is True


class TestFrameCredit:
    """
    El tope de entrega es un token bucket, y el reloj entra por parámetro: la secuencia
    se afirma sin esperar de verdad.

    `camera_fps` es el ritmo al que llegan los frames; `fps_limit`, el tope pedido.
    """

    @staticmethod
    def _count_kept(*, fps_limit: float, camera_fps: float, frames: int = 30) -> int:
        driver = RtspDriver({"address": "10.8.83.90", "acquisition": {"fps_limit": fps_limit}})
        now_s = 1000.0
        kept = 0
        for _ in range(frames):
            if driver._take_frame_credit(now_s):
                kept += 1
            now_s += 1.0 / camera_fps
        return kept

    def test_a_camera_below_the_cap_loses_no_frame(self):
        """
        La regresión medida contra la cámara: mandaba 14.3 fps con el tope en 15 y el
        driver entregaba 12.3, porque el jitter la ponía por encima del tope a ratos.
        """
        assert self._count_kept(fps_limit=15, camera_fps=14.3, frames=60) == 60

    def test_a_cap_equal_to_the_camera_rate_keeps_every_frame(self):
        assert self._count_kept(fps_limit=15, camera_fps=15, frames=60) == 60

    def test_jitter_within_the_credit_costs_no_frame(self):
        """Un keyframe tarda más que un P-frame: el espaciado real no es parejo."""
        driver = RtspDriver({"address": "10.8.83.90", "acquisition": {"fps_limit": 15}})
        now_s = 1000.0
        kept = 0
        for index in range(60):
            if driver._take_frame_credit(now_s):
                kept += 1
            # Promedian 70 ms —14.3 fps, por debajo del tope— pero de a tirones.
            now_s += 0.040 if index % 2 else 0.100

        assert kept == 60

    def test_a_cap_below_the_camera_rate_throttles(self):
        """El tope sigue siendo un tope: un tercio de los frames de una cámara a 15 fps."""
        # Un crédito inicial y uno cada tres frames: 10 de 30.
        assert self._count_kept(fps_limit=5, camera_fps=15) == 10

    def test_without_a_cap_every_frame_passes(self):
        assert self._count_kept(fps_limit=0, camera_fps=15) == 30

    def test_a_pause_does_not_leave_a_backlog_to_catch_up(self):
        """
        Los frames que no salieron durante una pausa no se recuperan: el crédito tiene
        techo, así que al volver el stream salen cuatro y después vuelve a valer el tope.
        """
        driver = RtspDriver({"address": "10.8.83.90", "acquisition": {"fps_limit": 15}})
        assert driver._take_frame_credit(1000.0) is True

        # Diez segundos sin frames —150 frames de crédito si no tuviera techo— y vuelven
        # todos juntos.
        kept = sum(driver._take_frame_credit(1010.0) for _ in range(20))

        assert kept == int(rtsp_driver._BURST_CREDIT_FRAMES)


class TestCaptureEnabled:
    def test_config_can_start_with_capture_disabled(self):
        driver = RtspDriver({"address": "10.8.83.90", "enabled": False})
        assert driver.is_capture_enabled is False
        assert driver.get_frame() is None

    def test_a_disabled_camera_does_not_even_open_the_stream(self, monkeypatch):
        driver, stream = _driver(monkeypatch, frames=4, stop_after_grabs=5)
        driver.set_capture_enabled(False)
        monkeypatch.setattr(rtsp_driver.time, "sleep", lambda _s: _stop(driver))

        driver._grab_loop()

        assert stream.sessions == 0
        assert driver.get_frame() is None

    def test_disabling_it_closes_the_session_without_reporting_a_drop(self, monkeypatch):
        """
        Cerrar la sesión hace que la cámara deje de mandar y libera el enlace, pero una
        cámara conectada y deshabilitada no es una cámara caída: el contrato las separa,
        y quien mira el status tiene que poder distinguirlas.
        """
        driver, stream = _driver(monkeypatch, frames=4, stop_after_grabs=5)
        assert driver._open_capture() is True

        driver.set_capture_enabled(False)
        monkeypatch.setattr(rtsp_driver.time, "sleep", lambda _s: _stop(driver))
        driver._grab_loop()

        assert stream.released == 1
        status = driver.get_status()
        assert status["connected"] is True
        assert status["capture_enabled"] is False
        assert driver.get_frame() is None

    def test_re_enabling_does_not_deliver_a_stale_frame(self, monkeypatch):
        """El frame que quedó en la cola es de antes de la pausa: ya no dice nada."""
        driver, _ = _driver(monkeypatch, frames=1, stop_after_grabs=2)
        driver._grab_loop()
        assert driver._img_queue.qsize() == 1

        driver.set_capture_enabled(False)
        assert driver._img_queue.qsize() == 0


def _stop(driver: RtspDriver):
    """Corta el loop desde la espera de la pausa, para que el test termine."""
    driver._grab_active = False
