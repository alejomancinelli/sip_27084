import os
import time

import cv2
import numpy as np

from system.logger import logger
from system.paths import resolve

from .abstract_driver import AbstractCameraDriver

# Extensiones que se toman de una carpeta; el resto de los archivos se ignora.
_VIDEO_EXTENSIONS = (".mp4", ".avi", ".mkv", ".mov")

_DEFAULT_FPS = 15.0       # sin fps en el contenedor ni `fps_limit`
_MAX_CLIP_FPS = 240.0     # por encima, el contenedor informa un timebase y no un fps

# Atraso, en frames, que se recupera leyendo y descartando. Más que esto —un consumidor que
# estuvo parado, una captura que se rehabilitó— se busca con seek: leer de a uno decodifica
# todo lo del medio.
_MAX_SKIP_FRAMES = 30

# Fracción de frame que se suma antes de truncar el índice del reloj: justo en el instante
# de un frame, el redondeo binario del tiempo da 7.9999 y truncar entregaría el anterior.
_FRAME_INDEX_EPSILON = 1e-6


class VideoFileDriver(AbstractCameraDriver):
    """
    Implementación de AbstractCameraDriver que reproduce video grabado como si fuera una
    cámara en vivo: es lo que deja probar el sistema entero con imagen real de la planta
    y sin la cámara.

    Claves de config_cam que usa:
        address : ruta a un video, o a una carpeta de videos; relativa a la raíz de la
                  instalación
        acquisition:
            fps_limit : tope de frames entregados por segundo; 0 = el fps del video
        rotation : opcional — la aplica la base

    Una carpeta es una playlist: se reproducen sus videos (.mp4 .avi .mkv .mov) en orden
    alfabético y al terminar el último vuelve a empezar. Un archivo solo es una playlist
    de uno. La lista se arma en cada `connect()`, así que un video agregado a la carpeta
    entra en la próxima reconexión.

    **El ritmo lo pone el reloj, no el consumidor.** El frame que se entrega es el que
    corresponde al tiempo transcurrido desde que arrancó el video, y los del medio se
    descartan. Un consumidor lento pierde frames igual que con una cámara en vivo —la
    entrega always-fresh del contrato— y el video no se estira: un minuto de video dura un
    minuto aunque la inferencia no llegue. Con la captura deshabilitada el reloj sigue
    corriendo, como una cámara que filmó mientras nadie miraba.

    `is_synthetic` va en True: la imagen es real pero no es la línea en este momento, y
    quien publica el estado de la cámara tiene que poder decirlo. Exposición y ganancia se
    ignoran, y `temperature` queda en 0.0: un archivo no tiene sensor.

    Los frames son los del archivo: si el video ya salió rotado, la cámara lleva
    `rotation: null` o se rota dos veces.
    """

    is_synthetic = True

    def __init__(self, config_cam: dict):
        super().__init__(config_cam)
        self._address = str(config_cam.get("address", "") or "").strip()
        self._fps_limit = float(config_cam.get("acquisition", {}).get("fps_limit") or 0.0)
        self._min_delivery_period_s = 1.0 / self._fps_limit if self._fps_limit > 0 else 0.0
        self.is_connected = False

        self._playlist: list[str] = []
        self._clip_index = 0
        self._capture = None
        self._clip_fps = _DEFAULT_FPS
        # Instante en que se mostró —o se habría mostrado— el frame 0 del video actual.
        self._clip_start_s = 0.0
        self._next_frame_index = 0
        self._last_delivery_s = 0.0
        self._error = ""

    # ── AbstractCameraDriver interface ────────────────────────────────────────

    def connect(self) -> bool:
        self._playlist = self._build_playlist()
        if not self._playlist:
            logger.error(f"[VideoFileDriver] {self._error}")
            return False

        if not self._open_from(0):
            logger.error(f"[VideoFileDriver] {self._error}")
            return False

        self._error = ""
        self.is_connected = True
        logger.info(
            f"[VideoFileDriver] Reproduciendo {len(self._playlist)} video(s) de "
            f"{self._address} en loop."
        )
        return True

    def disconnect(self):
        self._close_capture()
        self.is_connected = False

    def get_frame(self, timeout_ms: int = 500) -> np.ndarray | None:
        if not self._capture_enabled or not self.is_connected:
            return None

        due_s = max(self._clip_start_s + self._next_frame_index / self._clip_fps,
                    self._last_delivery_s + self._min_delivery_period_s)
        pending_s = due_s - time.monotonic()
        if pending_s > timeout_ms / 1000.0:
            return None
        if pending_s > 0:
            time.sleep(pending_s)

        frame = self._read_current_frame()
        if frame is None:
            return None
        self._last_delivery_s = time.monotonic()
        return self._deliver(frame)

    def get_status(self) -> dict:
        status = {
            "connected": self.is_connected,
            "capture_enabled": self._capture_enabled,
            "temperature": 0.0,
            "fps_estimated": self._measured_fps(),
        }
        if self._error:
            status["error"] = self._error
        return status

    # ── Playlist ─────────────────────────────────────────────────────────────

    def _build_playlist(self) -> list[str]:
        """Los videos de `address`, en orden; vacía con `_error` explicando por qué."""
        if not self._address:
            self._error = "Cámara de video sin `address`"
            return []

        path = resolve(self._address, "")
        if os.path.isfile(path):
            return [path]
        if not os.path.isdir(path):
            self._error = f"No existe el video ni la carpeta {path}"
            return []

        clips = [
            os.path.join(path, name) for name in sorted(os.listdir(path))
            if os.path.splitext(name)[1].lower() in _VIDEO_EXTENSIONS
            and os.path.isfile(os.path.join(path, name))
        ]
        if not clips:
            self._error = (f"La carpeta {path} no tiene videos "
                           f"({' '.join(_VIDEO_EXTENSIONS)})")
        return clips

    def _open_from(self, start_index: int) -> bool:
        """Abre el primer video que abra, dando como mucho una vuelta a la lista."""
        for offset in range(len(self._playlist)):
            if self._open_clip((start_index + offset) % len(self._playlist)):
                return True
        self._error = "Ningún video de la lista se pudo abrir"
        return False

    def _open_clip(self, index: int) -> bool:
        self._close_capture()
        path = self._playlist[index]
        capture = cv2.VideoCapture(path)
        if not capture.isOpened():
            capture.release()
            logger.warning(f"[VideoFileDriver] {path} no abrió: se saltea.")
            return False

        self._capture = capture
        self._clip_index = index
        self._clip_fps = self._read_clip_fps(capture, path)
        self._clip_start_s = time.monotonic()
        self._next_frame_index = 0
        logger.debug(f"[VideoFileDriver] {path} desde el principio, a {self._clip_fps:g} fps.")
        return True

    def _read_clip_fps(self, capture: cv2.VideoCapture, path: str) -> float:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if 0.0 < fps <= _MAX_CLIP_FPS:
            return fps
        fallback_fps = self._fps_limit or _DEFAULT_FPS
        logger.warning(f"[VideoFileDriver] {path} no informa un fps válido ({fps:g}): "
                       f"se reproduce a {fallback_fps:g}.")
        return fallback_fps

    def _close_capture(self):
        if self._capture is None:
            return
        try:
            self._capture.release()
        except Exception as e:
            logger.debug(f"[VideoFileDriver] Error cerrando el video: {e}")
        self._capture = None

    # ── Lectura ──────────────────────────────────────────────────────────────

    def _read_current_frame(self) -> np.ndarray | None:
        """
        El frame que corresponde al reloj; al final del video, el primero del siguiente.

        Sin ningún video que abra, la cámara pasa a desconectada y la reconexión vuelve a
        armar la lista.
        """
        elapsed_s = time.monotonic() - self._clip_start_s
        clock_index = int(elapsed_s * self._clip_fps + _FRAME_INDEX_EPSILON)
        frame = self._read_at(max(self._next_frame_index, clock_index))
        if frame is not None:
            return frame

        if not self._open_from(self._clip_index + 1):
            logger.error(f"[VideoFileDriver] {self._error}")
            self.is_connected = False
            return None
        return self._read_at(0)

    def _read_at(self, frame_index: int) -> np.ndarray | None:
        """Lee el frame `frame_index` del video actual; None pasado el final."""
        skip_count = frame_index - self._next_frame_index
        if skip_count > _MAX_SKIP_FRAMES:
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        else:
            for _ in range(skip_count):
                if not self._capture.grab():
                    return None

        is_read, frame = self._capture.read()
        if not is_read or frame is None:
            return None
        self._next_frame_index = frame_index + 1
        return frame
