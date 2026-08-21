import time

import cv2
import numpy as np

from .abstract_driver import AbstractCameraDriver

_DEFAULT_FPS_LIMIT = 15

# Geometría del frame sintético.
_FRAME_WIDTH_PX  = 1920
_FRAME_HEIGHT_PX = 1200

# Banda central que simula el flujo de material sobre la cinta.
_FLOW_X_MIN_PX  = 800    # borde izquierdo con jitter en cero
_FLOW_X_MAX_PX  = 1200   # borde derecho con jitter en cero
_FLOW_JITTER_PX = 150    # ensanchamiento aleatorio por frame, ±px
_FLOW_GRAY      = 150    # nivel de gris de la banda 0–255

_DUST_NOISE_MAX = 30        # amplitud del ruido que simula polvillo 0–255
_BASE_TEMPERATURE_C = 40.0  # temperatura de referencia del status sintético


class MockDriver(AbstractCameraDriver):
    """
    Driver sintético: genera frames con una banda central de ancho variable que
    imita el flujo de material sobre la cinta, sin hardware de por medio. Permite
    levantar la app y ejercitar el pipeline completo.

    Entrega frames al ritmo de `fps_limit`; la geometría y el ruido salen de las
    constantes del módulo.
    """

    # Los frames no vienen de una cámara real; quien consuma el status debe poder
    # distinguirlo para no reportar números sintéticos como medidos.
    is_synthetic = True

    def __init__(self, config_cam: dict):
        super().__init__(config_cam)
        self.is_connected = False

        # `or` cubre ausente, None y 0: los tres rompen la división de abajo.
        fps_limit = config_cam.get("acquisition", {}).get("fps_limit") or _DEFAULT_FPS_LIMIT
        self._fps_limit = float(fps_limit)
        self._frame_time_s = 1.0 / self._fps_limit
        self._last_frame_time_s = time.time()

    def connect(self) -> bool:
        self.is_connected = True
        return True

    def disconnect(self):
        self.is_connected = False

    def get_frame(self, timeout_ms: int = 500) -> np.ndarray | None:
        if not self._capture_enabled or not self.is_connected:
            return None

        # Ritmo limitado al fps configurado. Si el próximo frame no entra en el
        # timeout, devuelve None como haría una cámara real.
        pending_s = self._frame_time_s - (time.time() - self._last_frame_time_s)
        if pending_s > timeout_ms / 1000.0:
            return None
        if pending_s > 0:
            time.sleep(pending_s)

        self._last_frame_time_s = time.time()
        return self._deliver(self._build_frame())

    def get_status(self) -> dict:
        """
        Estado sintético para telemetría. `temperature` es inventada — ver
        `is_synthetic`; `fps_estimated` sí es el medido sobre los frames entregados.
        """
        return {
            "connected": self.is_connected,
            "capture_enabled": self._capture_enabled,
            "temperature": _BASE_TEMPERATURE_C + np.random.randn(),
            "fps_estimated": self._measured_fps(),
        }

    def _build_frame(self) -> np.ndarray:
        frame = np.zeros((_FRAME_HEIGHT_PX, _FRAME_WIDTH_PX, 3), dtype=np.uint8)

        jitter_px = np.random.randint(-_FLOW_JITTER_PX, _FLOW_JITTER_PX)
        cv2.rectangle(
            frame,
            (_FLOW_X_MIN_PX - jitter_px, 0),
            (_FLOW_X_MAX_PX + jitter_px, _FRAME_HEIGHT_PX),
            (_FLOW_GRAY, _FLOW_GRAY, _FLOW_GRAY),
            -1,
        )

        # Ruido uniforme: simula polvillo en suspensión.
        noise = np.random.randint(0, _DUST_NOISE_MAX, frame.shape, dtype=np.uint8)
        return cv2.add(frame, noise)
