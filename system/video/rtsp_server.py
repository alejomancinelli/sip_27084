"""
Servidor RTSP que publica los streams de video del sistema, con GStreamer.

Maquinaria genérica: no conoce cámaras ni inferencia. Recibe frames BGR ya armados
y los codifica.

Endpoints (uno por cámara y modo, igual que el servidor HTTP):
  rtsp://<ip>:<puerto>/camera_1/raw         feed de camera_1 sin procesar
  rtsp://<ip>:<puerto>/camera_1/annotated   feed de camera_1 con anotaciones
  rtsp://<ip>:<puerto>/camera_2/raw         ídem para cada slot de `cameras`

La ruta lleva la clave del slot (`camera_1`), no el `name` de la cámara: el nombre
es texto de UI y puede cambiar sin romper nada, el slot es la identidad.

Lo consume cualquier cliente RTSP: VLC, ffplay, un NVR, `cv2.VideoCapture`.

Es el camino de video de resolución completa —lo que se graba o se mira en un
NVR—; el stream liviano para una pestaña del navegador es el MJPEG de
`http_server`. Lo que cuesta acá es la codificación, y se regula con dos claves:
`codec` elige el encoder (y si es por CPU o por hardware) y `frame_width_px`
reduce la salida antes de codificar.

Sin clientes conectados a un stream no se codifica nada, y eso lo hace GStreamer
solo: arma el pipeline cuando alguien se conecta al endpoint y lo desarma cuando se
va el último. Ir y venir de la media es también de dónde sale el conteo por stream,
que es lo que contestan `has_raw_clients` y `has_annotated_clients` — con eso el
productor se ahorra armar un frame que nadie va a mirar.

Tres puntos del contrato que no se ven en las firmas:
  - **GLib no puede correr en el hilo de Qt.** El loop de GLib vive en un
    `threading.Thread` propio, con su propio `MainContext`; el resto de la app no
    lo ve.
  - Se publica **una instancia del servidor por interfaz** de
    `video.rtsp.net_interfaces`. Con la lista vacía se escucha en todas las
    interfaces; con nombres, lo que no está en la lista no ve el stream.
  - El `fps` del config es el ritmo del stream, y es el que manda: si la cámara
    entrega más rápido se descartan frames, y si deja de entregar se reenvía el
    último para que el reproductor no se quede sin saber si sigue vivo. Un `fps`
    más bajo que el de la cámara atrasa el stream respecto del tiempo real.

Sin GStreamer instalado el módulo degrada a no-op: expone la misma API, no levanta
nada y lo dice por `status`. El llamador no pregunta si está disponible.
"""

import socket
import threading
import time
from typing import NamedTuple

import numpy as np
import psutil

from system.config_manager import ConfigManager
from system.logger import logger
from system.video.abstract_video_server import (
    MODES,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_ERROR,
    AbstractVideoServer,
    StreamKey,
)

# Importaciones condicionales: GStreamer y sus bindings son librerías de sistema,
# no dependencias de pip, y en un equipo sin video no están.
try:
    import gi
    gi.require_version("Gst", "1.0")
    gi.require_version("GstRtspServer", "1.0")
    from gi.repository import Gst, GstRtspServer, GLib
    _GSTREAMER_AVAILABLE = True
    _IMPORT_ERROR = ""
except (ImportError, ValueError) as e:
    _GSTREAMER_AVAILABLE = False
    _IMPORT_ERROR = str(e)

_ALL_ADDRESSES = "0.0.0.0"      # escuchar en todas las interfaces
_LOOPBACK_PREFIX = "127."

_DEFAULT_PORT = 8554            # puerto RTSP por convención
_DEFAULT_FPS = 15
_DEFAULT_CODEC = "h264_sw"
_DEFAULT_FRAME_WIDTH_PX = 0     # 0 = resolución nativa

_NS_PER_S = 1_000_000_000       # GStreamer estampa los buffers en nanosegundos

_KEEPALIVE_S = 1.0              # sin frame nuevo se reenvía el último
_WAIT_STEP_S = 0.05             # troceo de la espera; acota la salida al detener
_START_TIMEOUT_S = 10.0         # espera al arranque del loop de GLib
_STOP_TIMEOUT_S = 5.0           # espera al hilo de GLib al detenerlo

# Nombres de los elementos que el pipeline expone por nombre: por ahí entran los
# frames y por ahí se fija el tamaño de salida.
_APPSRC_NAME = "source"
_SCALE_CAPS_NAME = "scale_caps"

# Cola de cada pipeline, de appsrc hacia el payloader RTP. El payloader se llama
# pay0 en todos: es el nombre con el que gst-rtsp-server encuentra la salida.
#
# Los pipelines `_hw` son de Jetson (nvv4l2*) y no existen en un x86; los `_sw`
# codifican por CPU y andan en cualquier equipo con los plugins instalados.
_CODEC_PIPELINES: dict[str, str] = {
    "h264_sw": (
        "! videoconvert ! video/x-raw,format=I420 "
        "! x264enc speed-preset=ultrafast tune=zerolatency "
        "! rtph264pay config-interval=1 name=pay0 pt=96"
    ),
    "h264_hw": (
        "! videoconvert ! video/x-raw,format=I420 "
        "! nvv4l2h264enc "
        "! rtph264pay config-interval=1 name=pay0 pt=96"
    ),
    "h265_sw": (
        "! videoconvert ! video/x-raw,format=I420 "
        "! x265enc speed-preset=ultrafast tune=zerolatency "
        "! rtph265pay config-interval=1 name=pay0 pt=96"
    ),
    "h265_hw": (
        "! videoconvert ! video/x-raw,format=NV12 "
        "! nvv4l2h265enc "
        "! rtph265pay config-interval=1 name=pay0 pt=96"
    ),
    "mjpeg": (
        "! videoconvert ! video/x-raw,format=I420 "
        "! jpegenc "
        "! rtpjpegpay name=pay0 pt=26"
    ),
}


# ── Helpers de pipeline, red y escala ────────────────────────────────────────

def _mount_path(key: StreamKey) -> str:
    """Ruta del stream en el servidor RTSP: '/camera_1/raw'."""
    return f"/{key[0]}/{key[1]}"


def _build_launch_string(codec: str) -> str:
    """
    Pipeline del stream: appsrc → escalado → codec → payloader RTP.

    El capsfilter del escalado nace en ANY, o sea sin escalar: el tamaño de salida
    lo fija quien empuja los frames, cuando conoce el del primero. Un codec que no
    está en `_CODEC_PIPELINES` cae en el default y se avisa; un stream en otro
    formato al menos se ve, uno que no arranca no.
    """
    codec_segment = _CODEC_PIPELINES.get(codec)
    if codec_segment is None:
        logger.warning(
            f"[RtspVideo] Codec '{codec}' desconocido; se usa {_DEFAULT_CODEC}. "
            f"Opciones: {', '.join(_CODEC_PIPELINES)}."
        )
        codec_segment = _CODEC_PIPELINES[_DEFAULT_CODEC]
    return (
        f"appsrc name={_APPSRC_NAME} is-live=true block=true format=GST_FORMAT_TIME "
        f"! videoscale ! capsfilter name={_SCALE_CAPS_NAME} "
        f"{codec_segment}"
    )


def _compute_scaled_size(width_px: int, height_px: int,
                         target_width_px: int) -> tuple[int, int] | None:
    """
    (ancho, alto) de salida para `target_width_px`, o None si no hay que escalar.

    Conserva la relación de aspecto y nunca amplía. Los dos lados salen pares: el
    submuestreo de croma de I420 —lo que consumen H.264 y H.265— no acepta un lado
    impar.
    """
    if target_width_px <= 0 or width_px <= target_width_px:
        return None
    scaled_width_px = target_width_px - target_width_px % 2
    scaled_height_px = round(height_px * scaled_width_px / width_px)
    return max(2, scaled_width_px), max(2, scaled_height_px - scaled_height_px % 2)


def _first_ipv4(addrs: list) -> str | None:
    """Primera dirección IPv4 de una interfaz de psutil, o None si no tiene."""
    for addr in addrs:
        if addr.family == socket.AF_INET:
            return addr.address
    return None


def _resolve_addresses(interfaces: list) -> list[str]:
    """
    Direcciones en las que hay que escuchar para publicar por `interfaces`.

    Sin interfaces configuradas se escucha en todas con un solo servidor. Con
    nombres se devuelve una dirección por interfaz, y cada una lleva su propio
    servidor: lo que no está en la lista no ve el stream. Una interfaz que el
    equipo no tiene, o que no tiene IPv4, se avisa y se saltea — los nombres no se
    cross-portean ('eth0' en Jetson, 'Ethernet' en Windows).
    """
    if not interfaces:
        return [_ALL_ADDRESSES]

    addrs_by_iface = psutil.net_if_addrs()
    addresses = []
    for iface in interfaces:
        address = _first_ipv4(addrs_by_iface.get(iface, []))
        if address is None:
            logger.warning(
                f"[RtspVideo] La interfaz '{iface}' no existe en este equipo o no "
                f"tiene IPv4; no se publica por ahí."
            )
        elif address not in addresses:
            addresses.append(address)
    return addresses


def _list_host_addresses() -> list[str]:
    """Direcciones IPv4 del equipo sin loopback: con cuáles se lo puede alcanzar."""
    addresses = []
    for addrs in psutil.net_if_addrs().values():
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith(_LOOPBACK_PREFIX):
                addresses.append(addr.address)
    return addresses


def _build_stream_urls(addresses: list, port: int, slots: tuple) -> list[str]:
    """
    URLs que hay que abrir para ver cada stream, una por dirección y stream.

    Escuchando en todas las interfaces se listan las direcciones reales del equipo:
    `0.0.0.0` no sirve para pegar en un reproductor.
    """
    hosts = addresses
    if _ALL_ADDRESSES in addresses:
        hosts = _list_host_addresses() or [_ALL_ADDRESSES]
    return [
        f"rtsp://{host}:{port}/{slot}/{mode}"
        for host in hosts for slot in slots for mode in MODES
    ]


# ── Estado compartido entre el productor y los pipelines ─────────────────────

class _FrameStore:
    """
    Almacén thread-safe del último frame de cada stream, con espera por secuencia.

    El contador es lo que hace correcta la espera: el pipeline pide "algo más nuevo
    que la secuencia N", así que un frame que llega entre su chequeo y su espera lo
    ve igual y no se duerme.

    Un solo Condition para todos los streams: despertar de más cuesta comparar dos
    enteros, y así el almacén no necesita saber qué streams existen.
    """

    def __init__(self):
        self._frames: dict[StreamKey, np.ndarray] = {}
        self._seqs: dict[StreamKey, int] = {}
        self._is_closed = False
        self._cond = threading.Condition()

    @property
    def is_closed(self) -> bool:
        with self._cond:
            return self._is_closed

    def update(self, key: StreamKey, frame_bgr: np.ndarray):
        """Guarda el frame como el último del stream y despierta a quien esperaba."""
        with self._cond:
            self._frames[key] = frame_bgr
            self._seqs[key] = self._seqs.get(key, 0) + 1
            self._cond.notify_all()

    def get(self, key: StreamKey) -> tuple[np.ndarray | None, int]:
        """Último frame del stream y su número de secuencia."""
        with self._cond:
            return self._frames.get(key), self._seqs.get(key, 0)

    def wait_for_change(self, key: StreamKey, last_seq: int, timeout_s: float):
        """Duerme hasta que el stream tenga algo más nuevo que `last_seq`, o venza el timeout."""
        with self._cond:
            if self._seqs.get(key, 0) == last_seq and not self._is_closed:
                self._cond.wait(timeout_s)

    def clear(self, key: StreamKey):
        """Descarta el último frame del stream: sin clientes no hay para quién guardarlo."""
        with self._cond:
            self._frames.pop(key, None)
            self._seqs[key] = self._seqs.get(key, 0) + 1
            self._cond.notify_all()

    def close(self):
        """Despierta a todos los pipelines para que terminen: el servidor se detiene."""
        with self._cond:
            self._is_closed = True
            self._cond.notify_all()


class _Watchers:
    """
    Cuenta las medias vivas de cada stream, que es cuánta gente lo está mirando.

    GStreamer crea la media cuando llega el primer cliente a un endpoint y la
    desarma cuando se va el último, así que con contar altas y bajas alcanza para
    saber si alguien mira ese stream. Es un contador y no un booleano porque un
    cliente que reconecta puede crear la media nueva antes de que se desarme la
    vieja, y ahí el conteo pasa por 2 sin que el stream se haya quedado sin nadie.

    El contador es del servidor, no de una interfaz: el mismo stream publicado por
    dos interfaces tiene una media en cada una y las dos cuentan.
    """

    def __init__(self):
        self._counts: dict[StreamKey, int] = {}
        self._lock = threading.Lock()

    def register(self, key: StreamKey) -> int:
        """Suma una media al stream. Devuelve cuántas quedan."""
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1
            return self._counts[key]

    def unregister(self, key: StreamKey) -> int:
        """Da de baja una media del stream. Devuelve cuántas quedan."""
        with self._lock:
            remaining = max(0, self._counts.get(key, 0) - 1)
            self._counts[key] = remaining
            return remaining

    def count(self, key: StreamKey) -> int:
        with self._lock:
            return self._counts.get(key, 0)


class _TimedFrame(NamedTuple):
    """Frame listo para empujar al pipeline, con su marca de tiempo en nanosegundos."""

    frame_bgr: np.ndarray
    pts_ns: int
    duration_ns: int


class _FramePacer:
    """
    Elige y estampa el frame que sigue en un stream, al ritmo declarado.

    Acá está toda la política de ritmo, sin Qt y sin GStreamer, y por eso se puede
    probar sin levantar un pipeline.

    Una instancia por media de GStreamer, no por cliente: los clientes de un mismo
    endpoint comparten la media y, con ella, el ritmo.
    """

    def __init__(self, store: _FrameStore, key: StreamKey, fps: int):
        self._store = store
        self._key = key
        self._duration_ns = int(_NS_PER_S / fps)
        self._period_s = 1.0 / fps
        self._last_seq = 0               # 0 = todavía no se empujó nada de este stream
        self._pushed = 0
        self._last_push_s: float | None = None

    def get_next(self) -> _TimedFrame | None:
        """
        Próximo frame a empujar, ya estampado. Bloquea hasta que haya uno.

        Devuelve None cuando el almacén se cerró: el stream terminó y el pipeline
        tiene que mandar EOS. Eso es lo que evita que un pipeline que arrancó antes
        del primer frame quede esperando para siempre uno que nadie va a empujar.

        Tres cosas las decide el ritmo declarado y no el de la cámara:
          - No sale más de un frame por período: una cámara más rápida que el `fps`
            no acelera el stream, se le descartan frames.
          - Sin frame nuevo, cada `_KEEPALIVE_S` se reenvía el último: un pipeline
            sin datos deja al reproductor sin saber si el stream sigue vivo.
          - El PTS lo lleva el contador de frames, no el reloj: es lo que declara el
            caps y lo que espera un reproductor de framerate fijo.
        """
        while not self._store.is_closed:
            frame_bgr, seq = self._store.get(self._key)
            if frame_bgr is not None and (seq != self._last_seq or self._is_keepalive_due()):
                self._last_seq = seq
                return self._stamp(frame_bgr)
            self._store.wait_for_change(self._key, seq, timeout_s=_WAIT_STEP_S)
        return None

    def _is_keepalive_due(self) -> bool:
        return (self._last_push_s is not None
                and time.monotonic() - self._last_push_s >= _KEEPALIVE_S)

    def _stamp(self, frame_bgr: np.ndarray) -> _TimedFrame:
        """Espera lo que falte del período y le pone al frame el PTS que sigue."""
        if self._last_push_s is not None:
            time.sleep(max(0.0, self._period_s - (time.monotonic() - self._last_push_s)))
        self._last_push_s = time.monotonic()
        pts_ns = self._pushed * self._duration_ns
        self._pushed += 1
        return _TimedFrame(frame_bgr, pts_ns, self._duration_ns)


# ── Pipelines de GStreamer ───────────────────────────────────────────────────

if _GSTREAMER_AVAILABLE:

    class _AppsrcPusher:
        """
        Empuja al appsrc de una media los frames que le da su `_FramePacer`.

        Una instancia por media: el appsrc y el capsfilter son de esa media, y el
        formato se declara con el primer frame que se empuja, así que las
        dimensiones no se configuran, se detectan.

        Se engancha a `need-data` al construirse, y la conexión de la señal es la
        que la mantiene viva mientras exista el appsrc.
        """

        def __init__(self, appsrc, scale_caps, pacer: _FramePacer,
                     key: StreamKey, fps: int, frame_width_px: int):
            self._appsrc = appsrc
            self._scale_caps = scale_caps
            self._pacer = pacer
            self._key = key
            self._fps = fps
            self._frame_width_px = frame_width_px
            self._declared_shape: tuple | None = None
            appsrc.connect("need-data", self._on_need_data)

        def _on_need_data(self, appsrc, _length):
            timed = self._pacer.get_next()
            if timed is None:
                appsrc.emit("end-of-stream")
                return

            # El formato se vuelve a declarar si cambió el tamaño del frame: un buffer
            # que no mide lo que dice el caps le baja el pipeline al encoder.
            if timed.frame_bgr.shape != self._declared_shape:
                self._declare_format(timed.frame_bgr)
                self._declared_shape = timed.frame_bgr.shape

            buffer = Gst.Buffer.new_wrapped(timed.frame_bgr.tobytes())
            buffer.pts = buffer.dts = timed.pts_ns
            buffer.duration = timed.duration_ns
            result = appsrc.emit("push-buffer", buffer)
            if result != Gst.FlowReturn.OK:
                # FLUSHING y EOS son el pipeline desarmándose: el cliente se fue o
                # se detuvo el servidor. Cualquier otro código sí es una falla.
                message = f"[RtspVideo] {_mount_path(self._key)}: push-buffer devolvió {result}."
                if result in (Gst.FlowReturn.FLUSHING, Gst.FlowReturn.EOS):
                    logger.debug(message)
                else:
                    logger.warning(message)

        def _declare_format(self, frame_bgr: np.ndarray):
            """Declara el formato de entrada y, si hace falta, el tamaño de salida."""
            height_px, width_px = frame_bgr.shape[:2]
            self._appsrc.set_property("caps", Gst.Caps.from_string(
                f"video/x-raw,format=BGR,width={width_px},height={height_px},"
                f"framerate={self._fps}/1"
            ))

            scaled = _compute_scaled_size(width_px, height_px, self._frame_width_px)
            if scaled is None:
                logger.info(f"[RtspVideo] {_mount_path(self._key)}: {width_px}x{height_px} "
                            f"nativo a {self._fps} fps.")
                return
            self._scale_caps.set_property("caps", Gst.Caps.from_string(
                f"video/x-raw,width={scaled[0]},height={scaled[1]}"
            ))
            logger.info(f"[RtspVideo] {_mount_path(self._key)}: {width_px}x{height_px} → "
                        f"{scaled[0]}x{scaled[1]} a {self._fps} fps.")


    class _StreamFactory(GstRtspServer.RTSPMediaFactory):
        """
        Fábrica de la media de un stream.

        Compartida: los clientes del mismo endpoint reciben la misma media, así que
        se codifica una sola vez para todos. GStreamer la construye cuando llega el
        primer cliente y la desarma cuando se va el último, que es lo que hace que
        un stream que nadie mira no cueste nada.

        El codec, el `fps` y el ancho de salida se leen al construir la media, no al
        arrancar la app: reconectar el cliente alcanza para tomar el cambio.

        Es también donde se lleva la cuenta de quién mira: acá se sabe que entró el
        primer cliente y que se fue el último, y nadie más lo sabe.
        """

        def __init__(self, key: StreamKey, store: _FrameStore, watchers: _Watchers,
                     config_manager: ConfigManager, **props):
            super().__init__(**props)
            self._key = key
            self._store = store
            self._watchers = watchers
            self._config = config_manager
            self.set_shared(True)

        def do_create_element(self, url):
            codec = self._config.get("video.rtsp.codec", _DEFAULT_CODEC)
            try:
                return Gst.parse_launch(_build_launch_string(codec))
            except GLib.Error as e:
                # Un plugin que falta (x264enc en un equipo pelado, nvv4l2h264enc
                # fuera de la Jetson) se ve acá, cuando llega el primer cliente.
                logger.error(
                    f"[RtspVideo] {_mount_path(self._key)}: no se pudo armar el pipeline "
                    f"con codec '{codec}': {e}. ¿Faltan plugins de GStreamer?"
                )
                return None

        def do_configure(self, rtsp_media):
            element = rtsp_media.get_element()
            fps = self._config.get("video.rtsp.fps", _DEFAULT_FPS) or _DEFAULT_FPS
            # El pusher no se guarda: queda vivo por la conexión de señal del appsrc,
            # y muere con la media.
            _AppsrcPusher(
                element.get_child_by_name(_APPSRC_NAME),
                element.get_child_by_name(_SCALE_CAPS_NAME),
                _FramePacer(self._store, self._key, fps),
                self._key,
                fps,
                self._config.get("video.rtsp.frame_width_px", _DEFAULT_FRAME_WIDTH_PX),
            )
            # El alta va antes de que el pipeline pida datos: si no, el productor
            # todavía estaría descartando los frames de este stream.
            watching = self._watchers.register(self._key)
            self._log_event(f"Stream {_mount_path(self._key)} abierto "
                            f"— {watching} mirando.")
            rtsp_media.connect("unprepared", self._on_unprepared)

        def _on_unprepared(self, _rtsp_media):
            remaining = self._watchers.unregister(self._key)
            if remaining == 0:
                self._store.clear(self._key)
            self._log_event(f"Stream {_mount_path(self._key)} cerrado "
                            f"— quedan {remaining} mirando.")

        def _log_event(self, message: str):
            """
            Registra que un stream se abrió o se cerró, si el config lo pide.

            La opción se lee en el momento: con un cliente que reconecta solo, estas
            líneas tapan el resto del log, y apagarlas no tiene que exigir reiniciar.

            Con la media compartida las dos líneas marcan el primer cliente que
            entra y el último que sale, no cada cliente.
            """
            if self._config.get("video.rtsp.log_connections", True):
                logger.info(f"[RtspVideo] {message}")


# ── Clase pública ────────────────────────────────────────────────────────────

class RtspVideoServer(AbstractVideoServer):
    """
    Publica los streams RTSP de video, en un hilo propio con su loop de GLib.

    Implementa `AbstractVideoServer`: de ahí salen los modos, el vocabulario de
    `status` y el gating por cliente. Acá está el transporte —una instancia del
    servidor RTSP por cada interfaz de `video.rtsp.net_interfaces`— y la
    codificación, que la hace el pipeline de GStreamer y no el que empuja.

    El hilo es un `threading.Thread` y no un `QThread` a propósito: el loop de GLib
    y el event loop de Qt no pueden compartir hilo.

    Uso:
        server = RtspVideoServer(config_manager)
        server.start()
        capture.frame_ready.connect(lambda frame, slot: server.push_raw(slot, frame))
        server.stop()
    """

    CONFIG_KEY = "rtsp"

    def __init__(self, config_manager: ConfigManager):
        super().__init__(config_manager)
        self._store = _FrameStore()
        self._watchers = _Watchers()
        self._servers: list = []
        self._addresses: list[str] = []
        self._glib_loop = None
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._is_active = False

    # ── Estado ───────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """True si el hilo del loop de GLib está vivo."""
        return self._thread is not None and self._thread.is_alive()

    def get_client_count(self) -> int:
        """
        Sesiones RTSP activas, sumando todas las interfaces por las que se publica.

        Es la cuenta de GStreamer, y es gruesa: una sesión sobrevive unos segundos
        a que el cliente desaparezca —vence por timeout— y un mismo cliente puede
        tener sesión en más de un endpoint. Sirve para telemetría; para decidir si
        vale la pena armar un frame están `has_raw_clients` y `has_annotated_clients`.
        """
        total = 0
        for server in self._servers:
            try:
                total += server.get_session_pool().get_n_sessions()
            except Exception as e:
                logger.debug(f"[RtspVideo] No se pudo contar las sesiones: {e}")
        return total

    def get_stream_urls(self) -> list[str]:
        """URLs de los streams publicados. Vacía si el servidor no está activo."""
        if not self._is_active:
            return []
        port = self._config.get("video.rtsp.port", _DEFAULT_PORT)
        return _build_stream_urls(self._addresses, port, self._slots)

    # ── Publicación de frames ────────────────────────────────────────────────

    def _is_watched(self, key: StreamKey) -> bool:
        return self._watchers.count(key) > 0

    def _push(self, key: StreamKey, frame_bgr: np.ndarray | None):
        if not self._is_active or frame_bgr is None:
            return
        # Guardar es barato —no copia ni codifica— pero un stream que nadie mira no
        # tiene por qué quedarse con un frame de 1920x1200 retenido para siempre.
        if not self._is_watched(key):
            return
        self._store.update(key, frame_bgr)

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    def start(self):
        """Levanta el servidor si está habilitado. No propaga errores: los deja en `status`."""
        if not self._config.get("video.rtsp.enabled", False):
            logger.info("[RtspVideo] Deshabilitado en configuración.")
            self._status = STATUS_DISABLED
            return

        if not _GSTREAMER_AVAILABLE:
            logger.error(
                f"[RtspVideo] Habilitado en configuración pero GStreamer no está "
                f"disponible ({_IMPORT_ERROR}). Sin streams RTSP."
            )
            self._status = STATUS_ERROR
            return

        self._slots = self._read_camera_slots()
        if not self._slots:
            logger.warning("[RtspVideo] No hay cámaras en el config: no hay streams que servir.")

        self._addresses = _resolve_addresses(
            self._config.get("video.rtsp.net_interfaces", []) or []
        )
        if not self._addresses:
            logger.error(
                "[RtspVideo] Ninguna interfaz de 'video.rtsp.net_interfaces' existe en "
                "este equipo. Sin streams RTSP."
            )
            self._status = STATUS_ERROR
            return

        # Estado por corrida: los pipelines de la corrida anterior siguen apuntando
        # al almacén viejo, que quedó cerrado, así que no interfieren con esta.
        self._store = _FrameStore()
        self._watchers = _Watchers()
        self._ready_event.clear()

        self._thread = threading.Thread(target=self._run_glib_loop, daemon=True,
                                        name="RtspVideo")
        self._thread.start()

        if not self._ready_event.wait(timeout=_START_TIMEOUT_S):
            logger.error("[RtspVideo] Timeout esperando el arranque del loop de GLib.")
            self._status = STATUS_ERROR
            return

        self._is_active = self._status == STATUS_ACTIVE

    def stop(self):
        """Detiene el servidor, corta los streams abiertos y libera el puerto. Idempotente."""
        self._is_active = False
        if self._thread is None:
            return

        # Primero el almacén: sin despertarlos, los pipelines que están esperando un
        # frame nuevo no llegan a terminar y el loop de GLib no baja.
        self._store.close()

        if self._glib_loop is not None and self._glib_loop.is_running():
            self._glib_loop.quit()

        self._thread.join(timeout=_STOP_TIMEOUT_S)
        if self._thread.is_alive():
            logger.warning("[RtspVideo] El hilo de GLib no terminó en "
                           f"{_STOP_TIMEOUT_S:.0f} s.")
        self._thread = None
        self._glib_loop = None
        self._servers = []
        # Las medias se desarman con el loop, pero no está garantizado que cada una
        # avise: con el contador viejo, `has_*_clients` seguiría diciendo que alguien
        # mira un servidor detenido.
        self._watchers = _Watchers()
        self._status = STATUS_DISABLED
        logger.info("[RtspVideo] Servidor detenido.")

    # ── Internos ─────────────────────────────────────────────────────────────

    def _run_glib_loop(self):
        """
        Arma los servidores y corre el loop de GLib hasta que se pida detenerlo.

        Todo el trabajo va dentro de un `MainContext` propio de este hilo: el que
        está por default es global, y compartirlo con Qt cuelga a los dos.
        """
        Gst.init(None)
        context = GLib.MainContext.new()
        context.push_thread_default()
        try:
            self._glib_loop = GLib.MainLoop.new(context, False)
            port = self._config.get("video.rtsp.port", _DEFAULT_PORT)
            self._servers = [self._create_server(address, port) for address in self._addresses]
            self._status = STATUS_ACTIVE
            self._log_urls(port)
            self._ready_event.set()
            self._glib_loop.run()
        except Exception as e:
            self._status = STATUS_ERROR
            self._servers = []
            logger.error(f"[RtspVideo] No se pudo publicar: {e}")
            self._ready_event.set()
        finally:
            context.pop_thread_default()

    def _create_server(self, address: str, port: int):
        """
        Servidor RTSP escuchando en una dirección, con un endpoint por stream.

        `attach` no levanta excepción cuando la dirección no se puede tomar:
        devuelve 0. Sin mirarlo, un puerto ocupado o una IP que ya no está en el
        equipo darían un servidor "activo" que nadie puede abrir.
        """
        server = GstRtspServer.RTSPServer()
        server.set_address(address)
        server.set_service(str(port))

        mounts = server.get_mount_points()
        for slot in self._slots:
            for mode in MODES:
                key = (slot, mode)
                mounts.add_factory(
                    _mount_path(key),
                    _StreamFactory(key, self._store, self._watchers, self._config),
                )

        if server.attach(None) == 0:
            raise OSError(f"no se pudo escuchar en {address}:{port} "
                          f"(¿puerto en uso o dirección inexistente?)")
        return server

    def _log_urls(self, port: int):
        """Deja en el log la URL de cada stream, que es lo que se pega en el reproductor."""
        bound = ", ".join(self._addresses)
        logger.info(f"[RtspVideo] Servidor activo en {bound} puerto {port}:")
        for url in _build_stream_urls(self._addresses, port, self._slots):
            logger.info(f"[RtspVideo]   {url}")
