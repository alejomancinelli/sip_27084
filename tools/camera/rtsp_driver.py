import os
import threading
import time
from queue import Empty, Queue
from urllib.parse import quote, urlsplit, urlunsplit

import cv2
import numpy as np

from system.logger import logger

from .abstract_driver import AbstractCameraDriver

# Esquema con el que se completa una `address` que trae sólo el host.
_URL_SCHEME = "rtsp://"

# Credenciales del stream: salen del entorno, no del config, que se versiona, se copia
# en la entrega y se ve desde la pantalla de configuración. Estas dos son las
# compartidas por todas las cámaras RTSP del equipo; una cámara con credenciales propias
# declara `credentials_env` y usa las de su sufijo (ver _resolve_credentials).
ENV_USER = "RTSP_USER"
ENV_PASSWORD = "RTSP_PASSWORD"

_QUEUE_MAXSIZE = 2        # cola corta: semántica always-fresh
_OPEN_TIMEOUT_MS = 5000   # espera al handshake RTSP
_READ_TIMEOUT_MS = 5000   # espera a un paquete de un stream ya abierto
_CONNECT_TIMEOUT_S = 10   # espera máxima a que el thread confirme el stream abierto
_CONNECT_POLL_S = 0.1
_JOIN_TIMEOUT_S = 5.0
_RETRY_DELAY_S = 5        # entre reintentos de apertura del stream
_PAUSE_POLL_S = 0.2       # cada cuánto revisa el thread si volvió la captura
_READ_FAIL_LIMIT = 10     # lecturas fallidas seguidas antes de reabrir el stream

# Crédito máximo del tope de entrega, en frames (ver _take_frame_credit).
#
# Cuatro sale de dos medidas contra una cámara real. Por abajo: exigir el período frame
# a frame entregaba 7.9 fps con `fps_limit: 15` sobre un stream de 15 —uno de cada dos—,
# porque los frames llegan una fracción de milisegundo antes de que se cumpla el
# período; hace falta crédito para absorber ese jitter. Por arriba: FFmpeg suelta su
# backlog de golpe cuando el stream sincroniza —medido: 20 frames con gaps de 0 a 7 ms,
# una sola vez—, y esos frames son viejos. Un crédito grande los dejaría pasar como si
# fueran frescos, que es justo lo contrario de la entrega always-fresh del contrato.
_BURST_CREDIT_FRAMES = 4.0


def _build_stream_url(address: str, credentials_env: str = "") -> str:
    """
    Arma la URL del stream a partir de `address`, con las credenciales del entorno.

    `address` puede traer la URL completa —`rtsp://host:554/Streaming/Channels/101`— o
    sólo el host, que se completa con el esquema. La ruta no se inventa: cambia con cada
    fabricante, y una ruta adivinada abre el stream equivocado o ninguno. Con `address`
    vacía devuelve texto vacío, que es lo que el driver reporta como error.
    """
    address = (address or "").strip()
    if not address:
        return ""
    if "://" not in address:
        address = f"{_URL_SCHEME}{address}"
    return _with_env_credentials(address, credentials_env)


def _with_env_credentials(url: str, credentials_env: str = "") -> str:
    """Agrega usuario y password del entorno; una URL que ya los trae queda igual."""
    parts = urlsplit(url)
    if parts.username is not None:
        return url

    user, password = _resolve_credentials(credentials_env)
    if not user:
        return url

    userinfo = quote(user, safe="")
    if password:
        userinfo = f"{userinfo}:{quote(password, safe='')}"
    return urlunsplit(parts._replace(netloc=f"{userinfo}@{parts.netloc}"))


def _resolve_credentials(credentials_env: str) -> tuple[str, str]:
    """
    Devuelve el par (usuario, password) del entorno para esta cámara.

    Con `credentials_env` la cámara usa sus propias variables —`RTSP_USER_<sufijo>` y
    `RTSP_PASSWORD_<sufijo>`— y **no** cae a las compartidas si faltan: prestarle a una
    cámara las credenciales de otra da un 401 que culpa a lo que no es, o peor, una
    sesión abierta con una cuenta que nadie eligió. Faltando, se avisa qué variable se
    buscó y se sigue sin credenciales, que es lo que el log ya explica.

    Sin `credentials_env` valen las compartidas, que es el caso normal: una cuenta de
    servicio para todas las cámaras del equipo.
    """
    if not credentials_env:
        return os.environ.get(ENV_USER, "").strip(), os.environ.get(ENV_PASSWORD, "")

    user_var = f"{ENV_USER}_{credentials_env}"
    password_var = f"{ENV_PASSWORD}_{credentials_env}"
    user = os.environ.get(user_var, "").strip()
    if not user:
        logger.error(
            f"[RtspDriver] {user_var} no está definida en el entorno. La cámara que "
            f"declara `credentials_env: {credentials_env}` va a intentar sin credenciales."
        )
        return "", ""

    logger.info(f"[RtspDriver] Credenciales de {user_var} / {password_var}.")
    return user, os.environ.get(password_var, "")


def _mask_credentials(url: str) -> str:
    """Tapa la password para que la URL pueda ir al log y a la consola."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    host = parts.netloc.split("@", 1)[1]
    return urlunsplit(parts._replace(netloc=f"{parts.username}:***@{host}"))


class RtspDriver(AbstractCameraDriver):
    """
    Implementación de AbstractCameraDriver para cámaras IP que publican RTSP, leído
    con el backend FFmpeg de OpenCV.

    Claves de config_cam que usa:
        address         : URL del stream, o sólo el host si lo publica en la raíz
        credentials_env : opcional — sufijo de las variables de entorno de esta cámara
        acquisition:
            fps_limit : tope de frames entregados por segundo; 0 = todos los que lleguen
        rotation        : opcional — la aplica la base

    `exposure_time_us` y `gain` no se aplican y se ignoran sin avisar: una cámara IP no
    expone nodos de adquisición por el stream. Exposición, ganancia, resolución y fps
    real se configuran en el servidor web de la cámara, así que `fps_limit` acá es un
    tope de entrega y no el ritmo del sensor.

    Las credenciales salen del entorno, no del config, y se buscan en este orden:

      1. Las que ya trae `address` — mandan sobre todo lo demás y no se consulta nada.
      2. `credentials_env: CAM2` → RTSP_USER_CAM2 / RTSP_PASSWORD_CAM2, para la cámara
         con cuenta propia. Si esa variable falta NO se cae a las compartidas: se avisa
         cuál se buscó y se intenta sin credenciales.
      3. Las compartidas RTSP_USER / RTSP_PASSWORD, que es el caso normal.
      4. Ninguna, para la cámara que no pide autenticación.

    El sufijo nombra al secreto y no a la cámara: dos cámaras con la misma cuenta
    apuntan al mismo, y una que cambia de slot no obliga a renombrar nada en el equipo.
    La URL nunca se loguea con la password.

    El stream lo drena un thread propio con una cola de dos frames: FFmpeg acumula los
    paquetes que nadie saca, así que leer al ritmo del llamador devuelve imágenes cada
    vez más viejas. El thread lee siempre y descarta lo que sobra, que es la entrega
    always-fresh del contrato, y es también quien reabre el stream cuando se corta:
    `connect()` sólo espera la primera apertura y después informa el estado.

    El transporte lo elige FFmpeg y no se puede fijar por captura. Si la imagen llega
    con artefactos de pérdida de paquetes, se fuerza TCP con
    OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp en el entorno del proceso.
    """

    def __init__(self, config_cam: dict):
        super().__init__(config_cam)
        self._address = config_cam.get("address", "")
        # En mayúsculas porque así se escriben las variables de entorno: el config puede
        # traer `cam1` y la variable llamarse RTSP_USER_CAM1 sin que sea un error.
        credentials_env = str(config_cam.get("credentials_env", "") or "").strip().upper()
        self._url = _build_stream_url(self._address, credentials_env)
        self._safe_url = _mask_credentials(self._url) or "(sin address)"
        self.is_connected = False

        fps_limit = float(config_cam.get("acquisition", {}).get("fps_limit") or 0.0)
        self._min_frame_period_s = 1.0 / fps_limit if fps_limit > 0 else 0.0

        self._img_queue: Queue = Queue(maxsize=_QUEUE_MAXSIZE)
        self._capture = None
        self._grab_active = False
        self._grab_thread: threading.Thread | None = None
        # Crédito del tope de entrega, en frames, y cuándo se rellenó por última vez.
        # Arranca en uno: el primer frame después de conectar sale en cuanto llega.
        self._frame_credit = 1.0
        self._credit_refill_s = 0.0

    @property
    def stream_url(self) -> str:
        """URL efectiva del stream, con la password tapada: es la que se puede mostrar."""
        return self._safe_url

    # ── AbstractCameraDriver interface ────────────────────────────────────────

    def connect(self) -> bool:
        if not self._url:
            logger.error("[RtspDriver] Cámara sin `address`: no hay stream que abrir.")
            return False

        # El thread reabre el stream por su cuenta: no levantar un segundo si ya hay uno
        # vivo, y no bajarlo cuando la primera apertura no sale — es la máquina de
        # reintentos, y matarla dejaría la reconexión en manos de que el llamador vuelva
        # a llamar. Acá `connect()` pasa a informar el estado.
        if self._grab_thread and self._grab_thread.is_alive():
            return self.is_connected

        self._grab_active = True
        self._grab_thread = threading.Thread(
            target=self._grab_loop, daemon=True, name=f"RtspDriver-{self._address}"
        )
        self._grab_thread.start()

        deadline = time.time() + _CONNECT_TIMEOUT_S
        while not self.is_connected and time.time() < deadline:
            time.sleep(_CONNECT_POLL_S)

        if not self.is_connected:
            logger.error(
                f"[RtspDriver] {self._safe_url} no abrió en {_CONNECT_TIMEOUT_S} s. "
                f"El thread sigue reintentando."
            )
            return False

        return True

    def disconnect(self):
        self._grab_active = False
        if self._grab_thread and self._grab_thread.is_alive():
            self._grab_thread.join(timeout=_JOIN_TIMEOUT_S)
        self._grab_thread = None
        self._drain_queue()
        self.is_connected = False
        logger.info(f"[RtspDriver] {self._safe_url} desconectada.")

    def get_frame(self, timeout_ms: int = 500) -> np.ndarray | None:
        if not self._capture_enabled or not self.is_connected:
            return None
        try:
            return self._deliver(self._img_queue.get(timeout=timeout_ms / 1000.0))
        except Empty:
            return None
        except Exception as e:
            logger.warning(f"[RtspDriver {self._safe_url}] Error en get_frame: {e}")
            return None

    def get_status(self) -> dict:
        """
        Estado para telemetría. `temperature` queda en 0.0 siempre: por el stream la
        cámara no publica ningún sensor, y no hay lectura que reportar.
        """
        status = {
            "connected": self.is_connected,
            "capture_enabled": self._capture_enabled,
            "temperature": 0.0,
            "fps_estimated": self._measured_fps(),
        }
        if not self._url:
            status["error"] = "Cámara RTSP sin `address`"
        return status

    def _apply_capture_enabled(self):
        # Se vacía la cola para que al rehabilitar no salga un frame viejo.
        self._drain_queue()

        # El stream lo cierra el propio thread de captura, no éste: soltar la captura
        # mientras el thread está adentro de un read() es una carrera contra FFmpeg. La
        # lectura expira en _READ_TIMEOUT_MS, así que ve la bandera dentro de ese plazo.
        logger.info(
            f"[RtspDriver] {self._safe_url}: captura "
            f"{'habilitada' if self._capture_enabled else 'deshabilitada'}."
        )

    # ── Loop de captura ──────────────────────────────────────────────────────

    def _grab_loop(self):
        """Thread de fondo: abre el stream, lo drena y lo reabre ante un corte."""
        read_fails = 0

        while self._grab_active:
            # Deshabilitada, la sesión RTSP se cierra y la cámara deja de mandar: con
            # varias cámaras sobre el mismo enlace, el ancho de banda es el recurso
            # escaso. No es un filtro de frames.
            #
            # `is_connected` no baja por esto: una cámara conectada y deshabilitada no es
            # una cámara caída, y el contrato las separa a propósito. Si al rehabilitarla
            # el stream no vuelve a abrir, ahí sí baja.
            if not self._capture_enabled:
                self._close_capture()
                time.sleep(_PAUSE_POLL_S)
                continue

            if self._capture is None:
                if not self._open_capture():
                    time.sleep(_RETRY_DELAY_S)
                    continue
                read_fails = 0

            if not self._capture.grab():
                read_fails += 1
                if read_fails >= _READ_FAIL_LIMIT:
                    logger.warning(
                        f"[RtspDriver] {self._safe_url} dejó de mandar frames: "
                        f"se reabre el stream."
                    )
                    self.is_connected = False
                    self._close_capture()
                continue

            read_fails = 0
            # El frame que sobra se descarta sin decodificarlo: decodificar H.264 es lo
            # más caro del driver, y `grab()` deja el paquete leído sin pagarlo.
            now_s = time.time()
            if not self._take_frame_credit(now_s):
                continue

            is_retrieved, frame = self._capture.retrieve()
            if not is_retrieved or frame is None:
                continue

            self._enqueue(frame)

        self._close_capture()

    def _open_capture(self) -> bool:
        capture = cv2.VideoCapture(
            self._url,
            cv2.CAP_FFMPEG,
            [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, _OPEN_TIMEOUT_MS,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC, _READ_TIMEOUT_MS,
            ],
        )
        if not capture.isOpened():
            capture.release()
            self.is_connected = False
            # FFmpeg devuelve lo mismo para las tres causas, así que se nombran las tres:
            # el que lee el log es el que va a ir a probar cuál era.
            logger.warning(
                f"[RtspDriver] {self._safe_url} no abrió: host inalcanzable, ruta de stream "
                f"equivocada o credenciales rechazadas. Reintento en {_RETRY_DELAY_S} s."
            )
            return False

        # Buffer mínimo, hasta donde el backend lo respete: sin esto, un consumidor que
        # se atrasa deja una fila de frames viejos por delante del actual.
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        self._capture = capture
        self.is_connected = True
        logger.info(f"[RtspDriver] {self._safe_url} capturando.")
        return True

    def _close_capture(self):
        """
        Suelta la sesión RTSP. Silenciosa: puede estar ya cerrada.

        No toca `is_connected`: quien cierra dice si es un corte —la cámara se quedó
        muda— o una pausa pedida, que no es lo mismo para el que lee el status.
        """
        if self._capture is None:
            return
        try:
            self._capture.release()
        except Exception as e:
            logger.debug(f"[RtspDriver] Error cerrando {self._safe_url}: {e}")
        self._capture = None

    # ── Cola de frames ───────────────────────────────────────────────────────

    def _take_frame_credit(self, now_s: float) -> bool:
        """
        Gasta un crédito del tope de entrega; False si no queda y el frame se descarta.

        Es un token bucket: el crédito se rellena a `fps_limit` por segundo, cada frame
        entregado gasta uno, y no se acumula más allá de `_BURST_CREDIT_FRAMES`. Así una
        cámara que promedia por debajo del tope no pierde ningún frame aunque llegue a
        tirones, un stream sostenidamente más rápido que el tope queda limitado al tope, y
        una pausa no deja un backlog para recuperar: el crédito tiene techo.

        Comparar contra un período fijo frame a frame no sirve: las ráfagas de FFmpeg
        pasan el tope aunque el promedio esté por debajo, y cada frame de esas ráfagas se
        descartaba sin recuperar nada. Ver _BURST_CREDIT_FRAMES.
        """
        if self._min_frame_period_s <= 0:
            return True

        if self._credit_refill_s > 0.0:
            elapsed_s = max(0.0, now_s - self._credit_refill_s)
            self._frame_credit = min(
                _BURST_CREDIT_FRAMES,
                self._frame_credit + elapsed_s / self._min_frame_period_s,
            )
        self._credit_refill_s = now_s

        if self._frame_credit < 1.0:
            return False
        self._frame_credit -= 1.0
        return True

    def _enqueue(self, frame: np.ndarray):
        """Encola descartando el más viejo, así el consumidor lento recibe el último."""
        if self._img_queue.full():
            try:
                self._img_queue.get_nowait()
            except Empty:
                pass
        self._img_queue.put_nowait(frame)

    def _drain_queue(self):
        while not self._img_queue.empty():
            try:
                self._img_queue.get_nowait()
            except Empty:
                break
