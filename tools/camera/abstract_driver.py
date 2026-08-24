import time
from abc import ABC, abstractmethod
from collections import deque

import cv2
import numpy as np

# Valores que acepta la clave `rotation` de cada cámara en config.yaml.
_ROTATE_MAP = {
    "90cw":  cv2.ROTATE_90_CLOCKWISE,
    "90ccw": cv2.ROTATE_90_COUNTERCLOCKWISE,
    "180":   cv2.ROTATE_180,
}

_FPS_WINDOW = 30   # frames sobre los que se promedia fps_estimated


class AbstractCameraDriver(ABC):
    """
    Contrato de todo driver de cámara: ciclo de vida, entrega de frames
    always-fresh y estado de salud para telemetría.

    Las implementaciones devuelven cada frame por `_deliver()`, que aplica la
    rotación configurada y marca la llegada para medir el fps real. Ningún driver
    conoce el mapa de rotaciones de OpenCV ni repite el cálculo de fps.
    """

    # Marcadores de la implementación concreta: el consumidor los lee sin saber
    # qué driver le tocó. Cada driver al que apliquen los pisa en True.
    is_synthetic = False
    is_config_error = False

    def __init__(self, config_cam: dict | None = None):
        # Sesión abierta con el hardware. Lo sostiene cada implementación; quien
        # captura lo lee para saber si tiene que llamar a connect().
        self.is_connected = False

        config = config_cam or {}
        self._rotation = config.get("rotation")
        # `enabled: false` deja la cámara sin capturar sin borrarla del config.
        self._capture_enabled = bool(config.get("enabled", True))
        self._frame_stamps: deque = deque(maxlen=_FPS_WINDOW)

    @abstractmethod
    def connect(self) -> bool:
        """Intenta inicializar la conexión por red o bus con la cámara."""

    @abstractmethod
    def disconnect(self):
        """Libera los recursos crudos del host."""

    @abstractmethod
    def get_frame(self, timeout_ms: int = 500) -> np.ndarray | None:
        """
        Devuelve el último frame validado, tipo always-fresh, o None.

        Con la captura deshabilitada devuelve None sin tocar el hardware.
        """

    @abstractmethod
    def get_status(self) -> dict:
        """
        Devuelve la salud de la cámara con claves estables:

            connected       : bool — hay sesión abierta con el hardware
            capture_enabled : bool — se están pidiendo frames
            temperature     : °C, 0.0 cuando no hay lectura
            fps_estimated   : fps medido sobre los frames entregados

        Un driver que no puede operar agrega `error` con el motivo. `connected` y
        `capture_enabled` son independientes: una cámara conectada y deshabilitada
        no es lo mismo que una cámara caída, y el consumidor tiene que poder
        distinguirlas.
        """

    # ── Captura habilitada ───────────────────────────────────────────────────

    @property
    def is_capture_enabled(self) -> bool:
        return self._capture_enabled

    def set_capture_enabled(self, enabled: bool):
        """
        Habilita o corta la captura sin cerrar la conexión con la cámara.

        Deshabilitada, `get_frame()` devuelve None y el status lo informa. Los
        drivers que pueden además cortar el grab en el hardware lo hacen en
        `_apply_capture_enabled()`, que es donde se libera ancho de banda del bus.
        """
        if enabled == self._capture_enabled:
            return
        self._capture_enabled = enabled
        self._apply_capture_enabled()

    def _apply_capture_enabled(self):
        """
        Hook para cortar o retomar la captura en el hardware.

        La base ya garantiza que no salgan frames; esto es solo la optimización de
        no moverlos por el bus. Un driver que no pueda hacerlo deja el hook vacío.
        """

    # ── Entrega de frames ────────────────────────────────────────────────────

    def _deliver(self, frame: np.ndarray) -> np.ndarray:
        """Marca el frame para el cálculo de fps y le aplica la rotación configurada."""
        self._frame_stamps.append(time.time())
        return self._rotate_frame(frame)

    def _measured_fps(self) -> float:
        """fps real medido sobre las marcas de los últimos frames entregados."""
        if len(self._frame_stamps) < 2:
            return 0.0
        span_s = self._frame_stamps[-1] - self._frame_stamps[0]
        if span_s <= 0:
            return 0.0
        return round((len(self._frame_stamps) - 1) / span_s, 1)

    def _rotate_frame(self, frame: np.ndarray) -> np.ndarray:
        """Aplica la rotación configurada; sin `rotation` válida devuelve el frame tal cual."""
        rotate_code = _ROTATE_MAP.get(self._rotation)
        if rotate_code is None:
            return frame
        return cv2.rotate(frame, rotate_code)
