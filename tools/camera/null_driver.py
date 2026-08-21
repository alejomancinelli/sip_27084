import numpy as np

from .abstract_driver import AbstractCameraDriver


class NullDriver(AbstractCameraDriver):
    """
    Driver de falla de configuración: lo devuelve la fábrica cuando la cámara no
    está correctamente parametrizada (campo 'driver' ausente, vacío o sin
    registrar en el core).
    """

    # Marca de fallo permanente — reintentar connect() no cambia nada.
    is_config_error = True

    def __init__(self, config_cam: dict | None = None, reason: str = "Cámara sin configurar"):
        super().__init__(config_cam)
        self._config_cam = config_cam or {}
        self._reason = reason
        self.is_connected = False

    def connect(self) -> bool:
        return False

    def disconnect(self):
        self.is_connected = False

    def get_frame(self, timeout_ms: int = 500) -> np.ndarray | None:
        return None

    def get_status(self) -> dict:
        """
        Estado para telemetría: todo en cero o False, más `error` con el motivo del
        fallo de configuración. Nunca captura, sin importar la clave `enabled`.
        """
        return {
            "connected": False,
            "capture_enabled": False,
            "temperature": 0.0,
            "fps_estimated": 0.0,
            "error": self._reason,
        }
