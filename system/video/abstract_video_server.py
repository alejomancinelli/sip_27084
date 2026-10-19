"""
Contrato de los servidores de video del sistema.

Módulo puro: sin Qt, sin sockets, sin GStreamer y sin cv2. Define el vocabulario
que comparten las implementaciones —`system/video/http_server.py` (MJPEG por HTTP)
y `system/video/rtsp_server.py` (RTSP con GStreamer)— para que quien empuja frames
no tenga que saber por qué transporte salen.

Qué fija el contrato:
  - **Un stream por cámara y modo.** `raw` es el frame de la cámara y `annotated`
    el que dibuja la inferencia. La identidad de la cámara es la clave de su slot
    en `cameras` (`camera_1`), no su `name`: el nombre es texto de UI y puede
    cambiar sin romper nada.
  - **El vocabulario de `status`**: `STATUS_DISABLED` cuando el config lo apaga,
    `STATUS_ACTIVE` cuando está sirviendo, `STATUS_ERROR` cuando quiso levantar y
    no pudo. Ningún servidor propaga errores desde `start()`: los deja en `status`.
  - **Gating por cliente.** `has_raw_clients` / `has_annotated_clients` dicen si
    alguien está mirando ese stream. Es lo que permite no dibujar anotaciones que
    nadie va a ver, que cuesta más que codificarlas, y lo que hace que un `push` a
    un stream vacío no cueste nada.
  - **`push_raw` / `push_annotated` son thread-safe** y se llaman desde los hilos
    de captura e inferencia.

Qué NO fija, a propósito: cómo se guarda y se codifica el frame. Los dos
servidores tienen un almacén con la misma forma pero distinto contenido —el de
HTTP guarda el JPEG ya codificado, porque ahí codifica el que empuja; el de RTSP
guarda el frame crudo, porque ahí codifica el pipeline de GStreamer—, y unificarlo
metería la codificación de uno en el camino del otro. El encoder es parte del
transporte, no algo que el transporte pida prestado.

Los servidores concretos no se conocen entre sí: los cablea `main.py`, y puede
tratarlos como una lista porque todos responden a esta interfaz.
"""

from abc import ABC, abstractmethod

import numpy as np

from system.config_manager import ConfigManager

# (slot de cámara, modo): identifica un stream en cualquiera de los servidores.
StreamKey = tuple[str, str]

MODE_RAW = "raw"
MODE_ANNOTATED = "annotated"
MODES = (MODE_RAW, MODE_ANNOTATED)

STATUS_DISABLED = "disabled"    # apagado en el config; no es una falla
STATUS_ACTIVE = "active"        # sirviendo
STATUS_ERROR = "error"          # quiso levantar y no pudo


class AbstractVideoServer(ABC):
    """
    Contrato del servidor que publica los streams de video hacia afuera.

    Una instancia por transporte, no por cámara: cada servidor sirve todos los
    slots de `cameras`. La subclase pone el transporte y el encoder; de acá salen
    el vocabulario de modos y de status, la lectura de los slots y el gating.

    `CONFIG_KEY` es la sub-sección de `video:` que le corresponde a ese transporte.
    """

    CONFIG_KEY = ""

    def __init__(self, config_manager: ConfigManager):
        self._config = config_manager
        self._status = STATUS_DISABLED
        self._slots: tuple[str, ...] = ()

    # ── Estado ───────────────────────────────────────────────────────────────

    @property
    def status(self) -> str:
        """Estado del servidor: uno de los `STATUS_*` de este módulo."""
        return self._status

    @abstractmethod
    def get_client_count(self) -> int:
        """Clientes conectados, sumando todos los streams."""

    # ── Publicación de frames ────────────────────────────────────────────────

    def push_raw(self, camera_slot: str, frame_bgr: np.ndarray | None):
        """Publica el frame crudo de una cámara. Llega al ritmo de la cámara."""
        self._push((camera_slot, MODE_RAW), frame_bgr)

    def push_annotated(self, camera_slot: str, frame_bgr: np.ndarray | None):
        """Publica el frame anotado de una cámara. Llega por ciclo de inferencia."""
        self._push((camera_slot, MODE_ANNOTATED), frame_bgr)

    def has_raw_clients(self, camera_slot: str) -> bool:
        """True si alguien está mirando el feed crudo de esa cámara."""
        return self._is_watched((camera_slot, MODE_RAW))

    def has_annotated_clients(self, camera_slot: str) -> bool:
        """
        True si alguien está mirando el feed anotado de esa cámara.

        Sirve para no dibujar las anotaciones cuando nadie las va a ver: eso cuesta
        más que codificar el frame, y el push no puede evitarlo por sí solo.
        """
        return self._is_watched((camera_slot, MODE_ANNOTATED))

    @abstractmethod
    def _push(self, key: StreamKey, frame_bgr: np.ndarray | None):
        """Entrega el frame al stream. Un stream sin clientes, o un frame None, no cuestan."""

    @abstractmethod
    def _is_watched(self, key: StreamKey) -> bool:
        """True si ese stream tiene al menos un cliente."""

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    @abstractmethod
    def start(self):
        """Levanta el servidor si está habilitado. No propaga errores: los deja en `status`."""

    @abstractmethod
    def stop(self):
        """Detiene el servidor, corta los streams abiertos y libera el puerto. Idempotente."""

    # ── Internos ─────────────────────────────────────────────────────────────

    def _read_camera_slots(self) -> tuple[str, ...]:
        """
        Slots de la sección `cameras`, en el orden del archivo.

        La clave del slot es la identidad de la cámara en todo el sistema, así que es
        lo que va en la ruta del stream. Van todos, habilitados o no: `enabled`
        cambia en caliente y la ruta no puede aparecer y desaparecer con eso.
        """
        cameras = self._config.get("cameras", {}) or {}
        return tuple(cameras)

    def _read_stream_names(self) -> dict:
        """
        Nombre público de cada slot en la ruta del stream, si el config lo renombra.

        Por defecto la ruta lleva la clave del slot, que es la identidad de la cámara en
        todo el sistema. `video.<transporte>.stream_names` la reemplaza **sólo en la ruta**:
        es para una instalación donde la URL ya está puesta en un NVR, un tablero o el
        navegador de alguien, y cambiarla rompe algo de afuera que este equipo no controla.

        Sólo afecta a la ruta. El slot sigue siendo el de siempre en los registros, en la
        telemetría y en el resto del sistema.
        """
        names = self._config.get(f"video.{self.CONFIG_KEY}.stream_names", {}) or {}
        return {str(slot): str(name) for slot, name in names.items() if str(name).strip()}
