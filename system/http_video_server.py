"""
Servidor HTTP que publica los streams MJPEG de video del sistema.

Maquinaria genérica: no conoce cámaras ni inferencia. Recibe frames BGR ya
armados y los sirve.

Endpoints (uno por cámara y modo):
  GET /                     -> página de información con los links
  GET /camera_1/raw         -> feed de camera_1 sin procesar
  GET /camera_1/annotated   -> feed de camera_1 con anotaciones de inferencia
  GET /camera_2/raw         -> ídem para cada slot de la sección `cameras`

La ruta lleva la clave del slot (`camera_1`), no el `name` de la cámara: el nombre
es texto de UI y puede cambiar sin romper nada, el slot es la identidad.

Cualquier cliente MJPEG los consume: el navegador abriendo la URL,
`cv2.VideoCapture("http://<ip>:<puerto>/camera_1/raw")`, VLC, ffmpeg.

Tres cosas cuidan el CPU y el ancho de banda, y las tres importan porque el feed
crudo llega al ritmo de la cámara y la codificación corre en el hilo que empuja:
  - Sin clientes en un stream no se codifica nada: `push_*` sale enseguida.
  - Cada frame se reduce a `http_video.frame_width_px` antes de codificar.
  - Cada cliente recibe como máximo un frame cada `_SEND_PERIOD_S`.

Es video de visualización, no de medición: lo que se mide se calcula sobre el
frame completo, antes de pasar por acá.

Soporta varios clientes por stream: cada conexión corre en su propio hilo y todas
despiertan con el mismo frame.
"""

import select
import socket
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

import cv2
import numpy as np

from system.config_manager import ConfigManager
from system.logger import logger

# (slot de cámara, modo): identifica un stream en todo el módulo.
_StreamKey = tuple[str, str]

_HOST = "0.0.0.0"
_INFO_PATH = "/"
_MODE_RAW = "raw"
_MODE_ANNOTATED = "annotated"
_MODES = (_MODE_RAW, _MODE_ANNOTATED)
_BOUNDARY = "frame"             # separador multipart del stream MJPEG

_DEFAULT_PORT = 8091
_DEFAULT_JPEG_QUALITY = 80      # 0–100, escala de cv2.IMWRITE_JPEG_QUALITY
_DEFAULT_FRAME_WIDTH_PX = 960   # 0 = resolución nativa

_SEND_PERIOD_S = 0.05           # tope de 20 fps por cliente, y período de chequeo de corte
_WRITE_TIMEOUT_S = 2.0          # escritura trabada más allá de esto => cliente caído
_KEEPALIVE_S = 5.0              # reenvío del último frame si el stream quedó quieto
_STOP_TIMEOUT_S = 5.0           # espera al hilo del servidor al detenerlo
_SNDBUF_BYTES = 64 * 1024       # chico a propósito: el write se traba antes

# Un cliente que dejó de renovar su lease ya no puede estar escribiendo: se le
# venció el timeout de escritura y la vuelta de loop que lo renueva.
_LEASE_TTL_S = _WRITE_TIMEOUT_S + _SEND_PERIOD_S + 3.0


# ── Helpers de red y de imagen ───────────────────────────────────────────────

def _is_client_gone(sock: socket.socket) -> bool:
    """
    True si el socket ya avisó que el cliente se fue.

    Camino rápido, no garantía: `select` con timeout 0 es un muestreo. El respaldo
    es que la escritura falle o se trabe más de `_WRITE_TIMEOUT_S`.
    """
    try:
        _readable, _, _ = select.select([sock], [], [], 0)
        if not _readable:
            return False
        return sock.recv(4096) == b""
    except (OSError, ValueError):
        return True


def _downscale(frame_bgr: np.ndarray, frame_width_px: int) -> np.ndarray:
    """Reduce el frame a `frame_width_px` de ancho conservando la relación de aspecto."""
    if frame_width_px <= 0:
        return frame_bgr
    _height_px, _width_px = frame_bgr.shape[:2]
    if _width_px <= frame_width_px:
        return frame_bgr
    _target_height_px = max(1, round(_height_px * frame_width_px / _width_px))
    return cv2.resize(frame_bgr, (frame_width_px, _target_height_px),
                      interpolation=cv2.INTER_AREA)


def _build_part_header(jpeg_size: int) -> bytes:
    """Arma la cabecera multipart que precede a cada JPEG del stream."""
    return (
        f"--{_BOUNDARY}\r\n"
        f"Content-Type: image/jpeg\r\n"
        f"Content-Length: {jpeg_size}\r\n\r\n"
    ).encode()


def _build_info_html(slots: tuple[str, ...]) -> bytes:
    """Página con un link por stream disponible."""
    _links = "\n".join(
        f'<a href="/{_slot}/{_mode}">/{_slot}/{_mode}</a>'
        for _slot in slots for _mode in _MODES
    ) or "<p>No hay cámaras configuradas.</p>"
    return f"""\
<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Streams de video</title>
<style>body{{font-family:sans-serif;padding:2em}}a{{display:block;margin:.5em 0}}</style>
</head><body>
<h2>Streams MJPEG</h2>
<p>raw = frame de cámara; annotated = frame con anotaciones de inferencia.</p>
{_links}
</body></html>
""".encode()


# ── Estado compartido entre el productor y los clientes ──────────────────────

class _Subscribers:
    """
    Cuenta los clientes MJPEG conectados a cada stream.

    Cada cliente renueva su lease en cada vuelta de su loop. Un lease vencido deja
    de contar solo: una conexión semiabierta no puede quedar habilitando la
    codificación para siempre.
    """

    def __init__(self):
        self._leases: dict[_StreamKey, dict[int, float]] = {}
        self._lock = threading.Lock()

    def touch(self, key: _StreamKey, token: int):
        """Registra un cliente nuevo o renueva el lease de uno existente."""
        with self._lock:
            self._leases.setdefault(key, {})[token] = time.monotonic()

    def unregister(self, key: _StreamKey, token: int) -> int:
        """Da de baja el lease. Devuelve los clientes que quedan en `key`."""
        with self._lock:
            _leases = self._leases.get(key)
            if _leases is None:
                return 0
            _leases.pop(token, None)
            if not _leases:
                del self._leases[key]
                return 0
            return self._count_locked(key)

    def count(self, key: _StreamKey) -> int:
        with self._lock:
            return self._count_locked(key)

    def count_all(self) -> int:
        with self._lock:
            return sum(self._count_locked(_key) for _key in list(self._leases))

    def _count_locked(self, key: _StreamKey) -> int:
        _leases = self._leases.get(key)
        if not _leases:
            return 0
        _cutoff_s = time.monotonic() - _LEASE_TTL_S
        return sum(1 for _seen_s in _leases.values() if _seen_s >= _cutoff_s)


class _FrameStore:
    """
    Almacén thread-safe del último JPEG de cada stream, con espera por secuencia.

    El contador es lo que hace correcta la espera: el cliente pide "algo más nuevo
    que la secuencia N", así que un frame que llega entre su chequeo y su espera lo
    ve igual y no se duerme.

    Un solo Condition para todos los streams: despertar de más cuesta comparar dos
    enteros, y así el almacén no necesita saber qué streams existen.
    """

    def __init__(self):
        self._jpegs: dict[_StreamKey, bytes] = {}
        self._seqs: dict[_StreamKey, int] = {}
        self._is_closed = False
        self._cond = threading.Condition()

    @property
    def is_closed(self) -> bool:
        with self._cond:
            return self._is_closed

    def update(self, key: _StreamKey, frame_bgr: np.ndarray, *,
               jpeg_quality: int = _DEFAULT_JPEG_QUALITY,
               frame_width_px: int = _DEFAULT_FRAME_WIDTH_PX):
        """Codifica el frame reducido y despierta a los clientes que esperan."""
        _ok, _buf = cv2.imencode(".jpg", _downscale(frame_bgr, frame_width_px),
                                 [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        if not _ok:
            logger.warning(f"[HttpVideo] No se pudo codificar el frame de /{key[0]}/{key[1]}.")
            return
        _jpeg = _buf.tobytes()
        with self._cond:
            self._jpegs[key] = _jpeg
            self._seqs[key] = self._seqs.get(key, 0) + 1
            self._cond.notify_all()

    def get(self, key: _StreamKey) -> tuple[bytes | None, int]:
        """Último JPEG del stream y su número de secuencia."""
        with self._cond:
            return self._jpegs.get(key), self._seqs.get(key, 0)

    def wait_for_change(self, key: _StreamKey, last_seq: int, timeout_s: float):
        """Duerme hasta que el stream tenga algo más nuevo que `last_seq`, o venza el timeout."""
        with self._cond:
            if self._seqs.get(key, 0) == last_seq and not self._is_closed:
                self._cond.wait(timeout_s)

    def clear(self, key: _StreamKey):
        """Descarta el último JPEG del stream: sin clientes no hay para quién guardarlo."""
        with self._cond:
            self._jpegs.pop(key, None)
            self._seqs[key] = self._seqs.get(key, 0) + 1

    def close(self):
        """Despierta a todos los clientes para que corten: el servidor se está deteniendo."""
        with self._cond:
            self._is_closed = True
            self._cond.notify_all()


# ── Servidor HTTP ────────────────────────────────────────────────────────────

class _StreamingHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args):
        logger.debug(f"[HttpVideo] {self.address_string()} — {fmt % args}")

    def do_GET(self):
        if self.path == _INFO_PATH:
            self._send_info()
            return
        _parts = self.path.strip("/").split("/")
        if len(_parts) == 2 and _parts[0] in self.server.slots and _parts[1] in _MODES:
            self._send_stream((_parts[0], _parts[1]))
        else:
            self.send_error(404)

    def _send_info(self):
        _html = self.server.info_html
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_html)))
        self.end_headers()
        self.wfile.write(_html)

    def _send_stream(self, key: _StreamKey):
        _token = threading.get_ident()
        _subs: _Subscribers = self.server.subscribers
        _store: _FrameStore = self.server.frame_store

        self.connection.settimeout(_WRITE_TIMEOUT_S)
        try:
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, _SNDBUF_BYTES)
        except OSError:
            pass    # no todos los SO lo permiten; sin esto la caída se detecta más tarde

        _subs.touch(key, _token)
        self.server.log_client_event(
            f"Cliente conectado a /{key[0]}/{key[1]} ({self.address_string()}) "
            f"— {_subs.count(key)} en este stream."
        )
        try:
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={_BOUNDARY}")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self._pump_frames(key, _token)
        except OSError:
            pass    # el cliente cerró
        finally:
            # Sin esto el handler queda esperando el request siguiente del keep-alive
            # y el hilo no muere hasta que el cliente cierre el socket.
            self.close_connection = True
            _remaining = _subs.unregister(key, _token)
            if _remaining == 0:
                _store.clear(key)
            self.server.log_client_event(
                f"Cliente desconectado de /{key[0]}/{key[1]} "
                f"— quedan {_remaining} en este stream."
            )

    def _pump_frames(self, key: _StreamKey, token: int):
        """Envía frames hasta que el cliente corta o el servidor se detiene."""
        _subs: _Subscribers = self.server.subscribers
        _store: _FrameStore = self.server.frame_store
        _last_seq = -1
        _last_write_s = 0.0

        while not _store.is_closed and not _is_client_gone(self.connection):
            _subs.touch(key, token)
            _jpeg, _seq = _store.get(key)
            _now_s = time.monotonic()
            if _jpeg and (_seq != _last_seq or _now_s - _last_write_s >= _KEEPALIVE_S):
                self.wfile.write(_build_part_header(len(_jpeg)) + _jpeg + b"\r\n")
                self.wfile.flush()
                _last_seq, _last_write_s = _seq, _now_s
                time.sleep(_SEND_PERIOD_S)      # tope de envío; un frame más nuevo espera
            else:
                _store.wait_for_change(key, _seq, timeout_s=_SEND_PERIOD_S)


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Un hilo daemon por cliente conectado. Lleva el estado compartido de los streams."""

    daemon_threads = True

    # HTTPServer trae allow_reuse_address = 1. En Linux eso sólo saltea el TIME_WAIT,
    # pero en Windows SO_REUSEADDR permite que un SEGUNDO proceso vivo tome el mismo
    # puerto: las dos instancias "arrancan bien" y qué cliente le toca a cada una
    # queda indefinido. Prefiere fallar al arrancar y decir que el puerto está en uso.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, address: tuple[str, int], frame_store: _FrameStore,
                 subscribers: _Subscribers, slots: tuple[str, ...],
                 config_manager: ConfigManager):
        self.frame_store = frame_store
        self.subscribers = subscribers
        self.slots = slots
        self.info_html = _build_info_html(slots)
        self._config = config_manager
        super().__init__(address, _StreamingHandler)

    def log_client_event(self, message: str):
        """
        Registra que un cliente entró o salió, si el config lo pide.

        La opción se lee en el momento: con varias pestañas reconectando, estas
        líneas tapan el resto del log, y apagarlas no tiene que exigir reiniciar.
        """
        if self._config.get("http_video.log_connections", True):
            logger.info(f"[HttpVideo] {message}")

    def handle_error(self, request: socket.socket, client_address: tuple):
        """
        Manda los errores de conexión al logger en vez de a stderr.

        La base de socketserver imprime el traceback completo, y un cliente que
        corta de golpe no es una falla del servidor.
        """
        _error = sys.exc_info()[1]
        if isinstance(_error, (ConnectionError, TimeoutError)):
            logger.debug(f"[HttpVideo] {client_address[0]} cortó la conexión: {_error}")
            return
        logger.exception(f"[HttpVideo] Error atendiendo a {client_address[0]}")


class HttpVideoServer:
    """
    Publica los streams MJPEG de video por HTTP, en un hilo daemon propio.

    Un stream por cámara y modo: `raw` es el frame de la cámara y `annotated` el
    que dibuja la inferencia. Los slots salen de la sección `cameras` del config.

    `push_raw` y `push_annotated` son thread-safe y están pensados para llamarse
    desde los hilos de captura e inferencia: si nadie está mirando ese stream
    salen sin codificar nada.
    """

    def __init__(self, config_manager: ConfigManager):
        self._config = config_manager
        self._store = _FrameStore()
        self._subs = _Subscribers()
        self._server: _ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._is_active = False
        self._status = "disabled"

    @property
    def status(self) -> str:
        """Estado del servidor: 'disabled' | 'active' | 'error'."""
        return self._status

    # ── Publicación de frames ────────────────────────────────────────────────

    def push_raw(self, camera_slot: str, frame_bgr: np.ndarray | None):
        """Publica el frame crudo de una cámara. Llega al ritmo de la cámara."""
        self._push((camera_slot, _MODE_RAW), frame_bgr)

    def push_annotated(self, camera_slot: str, frame_bgr: np.ndarray | None):
        """Publica el frame anotado de una cámara. Llega por ciclo de inferencia."""
        self._push((camera_slot, _MODE_ANNOTATED), frame_bgr)

    def has_raw_clients(self, camera_slot: str) -> bool:
        """True si alguien está mirando el feed crudo de esa cámara."""
        return self._subs.count((camera_slot, _MODE_RAW)) > 0

    def has_annotated_clients(self, camera_slot: str) -> bool:
        """
        True si alguien está mirando el feed anotado de esa cámara.

        Sirve para no dibujar las anotaciones cuando nadie las va a ver: eso cuesta
        más que codificar el JPEG, y el push no puede evitarlo por sí solo.
        """
        return self._subs.count((camera_slot, _MODE_ANNOTATED)) > 0

    def get_client_count(self) -> int:
        """Clientes conectados, sumando todos los streams."""
        return self._subs.count_all()

    def _push(self, key: _StreamKey, frame_bgr: np.ndarray | None):
        if not self._is_active or frame_bgr is None:
            return
        # El chequeo de clientes va antes de leer el config: esto corre por frame de
        # cámara, así que sin nadie mirando tiene que salir lo más barato posible.
        if self._subs.count(key) == 0:
            return
        _jpeg_quality = self._config.get("http_video.jpeg_quality", _DEFAULT_JPEG_QUALITY)
        _frame_width_px = self._config.get("http_video.frame_width_px",
                                           _DEFAULT_FRAME_WIDTH_PX)
        self._store.update(key, frame_bgr,
                           jpeg_quality=_jpeg_quality, frame_width_px=_frame_width_px)

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    def start(self):
        """Levanta el servidor si está habilitado. No propaga errores: los deja en `status`."""
        if not self._config.get("http_video.enabled", False):
            logger.info("[HttpVideo] Deshabilitado en configuración.")
            self._status = "disabled"
            return

        _slots = self._read_camera_slots()
        if not _slots:
            logger.warning("[HttpVideo] No hay cámaras en el config: no hay streams que servir.")

        # Estado por corrida: los hilos de la corrida anterior siguen apuntando a los
        # objetos viejos, así que no interfieren con esta.
        self._store = _FrameStore()
        self._subs = _Subscribers()

        _port = self._config.get("http_video.port", _DEFAULT_PORT)
        try:
            _server = _ThreadingHTTPServer((_HOST, _port), self._store, self._subs,
                                           _slots, self._config)
        except OSError as e:
            self._status = "error"
            logger.error(f"[HttpVideo] No se pudo iniciar en puerto {_port}: {e}")
            return

        self._server = _server
        self._thread = threading.Thread(
            target=_server.serve_forever,
            daemon=True,
            name="HttpVideo",
        )
        self._thread.start()
        self._is_active = True
        self._status = "active"
        logger.info(f"[HttpVideo] Servidor activo en http://{_HOST}:{_port}")
        for _slot in _slots:
            logger.info(f"[HttpVideo]   /{_slot}/{_MODE_RAW} y /{_slot}/{_MODE_ANNOTATED}")

    def stop(self):
        """Detiene el servidor, corta los streams abiertos y libera el puerto. Idempotente."""
        self._is_active = False
        if self._server is None:
            return

        # shutdown() no toca las conexiones ya abiertas: sin despertar a esos hilos
        # se quedarían mandando keepalives después de detener el servidor.
        self._store.close()

        # Y sin server_close() el socket de escucha queda abierto hasta que muere el
        # proceso, así que el próximo arranque falla con "puerto en uso".
        self._server.shutdown()
        self._server.server_close()
        self._server = None

        if self._thread is not None:
            self._thread.join(timeout=_STOP_TIMEOUT_S)
            self._thread = None

        self._status = "disabled"
        logger.info("[HttpVideo] Servidor detenido.")

    def _read_camera_slots(self) -> tuple[str, ...]:
        """
        Slots de la sección `cameras`, en el orden del archivo.

        La clave del slot es la identidad de la cámara en todo el sistema, así que es
        lo que va en la ruta. Van todos, habilitados o no: `enabled` cambia en
        caliente y la ruta del stream no puede aparecer y desaparecer con eso.
        """
        _cameras = self._config.get("cameras", {}) or {}
        return tuple(_cameras)
