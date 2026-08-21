import threading

import numpy as np

from system.logger import logger

from .abstract_driver import AbstractCameraDriver

try:
    from pypylon import pylon, genicam
    _PYPYLON_AVAILABLE = True
except ImportError:
    _PYPYLON_AVAILABLE = False

# Clase de dispositivo GenICam con la que pypylon expone las GigE de Basler.
_DEVICE_CLASS_GIGE = "BaslerGigE"

_DEFAULT_EXPOSURE_TIME_US = 15000.0
_DEFAULT_GAIN = 1.5
_DEFAULT_FPS_LIMIT = 15.0

# TlFactory y EnumerateDevices de pypylon no son thread-safe cuando varios hilos
# los llaman a la vez durante la inicialización GigE. Este lock serializa los
# connect() para evitar corrupción de heap en C++.
_pylon_connect_lock = threading.Lock()


class BaslerDriver(AbstractCameraDriver):
    """
    Implementación de AbstractCameraDriver para cámaras Basler GigE vía pypylon.

    Claves de config_cam que usa:
        address     : IP con la que se busca la cámara en la enumeración GigE
        acquisition:
            exposure_time_us : float
            gain             : float
            fps_limit        : float
        rotation    : opcional — la aplica la base
    """

    def __init__(self, config_cam: dict):
        super().__init__(config_cam)
        self._address = config_cam.get("address")
        self._acquisition = config_cam.get("acquisition", {})
        self._camera = None
        self._converter = None
        # Anti-spam del warning de temperatura (ver get_status).
        self._temp_warned = False
        self.is_connected = False

    # ── AbstractCameraDriver interface ────────────────────────────────────────

    def connect(self) -> bool:
        if not _PYPYLON_AVAILABLE:
            logger.error("El módulo nativo pypylon no está instalado. No se puede abrir la Basler.")
            return False

        with _pylon_connect_lock:
            return self._connect_locked()

    def disconnect(self):
        try:
            if self._camera and self._camera.IsGrabbing():
                self._camera.StopGrabbing()
            if self._camera and self._camera.IsOpen():
                self._camera.Close()
        except Exception as e:
            logger.error(f"Error cerrando la Basler {self._address}: {e}")
        finally:
            # Nullificar las referencias C++ antes de que el GC de Python corra
            # los destructores — evita el "FATAL: exception not rethrown" al salir.
            self._camera = None
            self._converter = None
            self.is_connected = False

    def get_frame(self, timeout_ms: int = 500) -> np.ndarray | None:
        if not self._capture_enabled:
            return None
        if not self.is_connected or not self._camera or not self._camera.IsGrabbing():
            return None

        try:
            grab_result = self._camera.RetrieveResult(
                timeout_ms, pylon.TimeoutHandling_ThrowException
            )
            bgr_image = None
            if grab_result.GrabSucceeded():
                # `converted` debe mantenerse vivo hasta que .copy() termine.
                # GetArray() devuelve una vista de memoria C++ del PylonImage;
                # si el objeto se destruye antes del copy, el heap se corrompe.
                converted = self._converter.Convert(grab_result)
                bgr_image = converted.GetArray().copy()
                del converted

            grab_result.Release()
            return self._deliver(bgr_image) if bgr_image is not None else None
        except genicam.GenericException as e:
            # Timeout o desconexión física: se marca para que el thread de captura
            # dispare su bloque de reconexión en la próxima iteración.
            logger.warning(f"[Basler {self._address}] GenericException en get_frame: {e}")
            self.is_connected = False
            return None
        except Exception as e:
            logger.warning(f"[Basler {self._address}] Error en get_frame: {e}")
            self.is_connected = False
            return None

    def get_status(self) -> dict:
        """Estado para telemetría. Un 0.0 en `temperature` significa sin lectura."""
        status = {
            "connected": self.is_connected,
            "capture_enabled": self._capture_enabled,
            "temperature": 0.0,
            "fps_estimated": self._measured_fps(),
        }
        if self.is_connected and self._camera:
            try:
                status["temperature"] = float(self._camera.DeviceTemperature.GetValue())
                self._temp_warned = False
            except Exception as e:
                # Sin el latch esto inunda el log.
                if not self._temp_warned:
                    logger.warning(
                        f"[Basler {self._address}] No se pudo leer DeviceTemperature: {e}. "
                        f"La temperatura queda en 0 (sin lectura)."
                    )
                    self._temp_warned = True
        return status

    # ── Conexión ─────────────────────────────────────────────────────────────

    def _connect_locked(self) -> bool:
        # Liberar la cámara anterior antes de crear otra: sin esto cada reconexión
        # filtra el handle C++ de pypylon.
        if self._camera is not None:
            try:
                if self._camera.IsGrabbing():
                    self._camera.StopGrabbing()
                if self._camera.IsOpen():
                    self._camera.Close()
            except Exception:
                pass
            self._camera = None
            self._converter = None

        try:
            tl_factory = pylon.TlFactory.GetInstance()

            matched_device = None
            for info in tl_factory.EnumerateDevices():
                if (info.GetDeviceClass() == _DEVICE_CLASS_GIGE
                        and info.GetIpAddress() == self._address):
                    matched_device = info
                    break

            if matched_device is None:
                logger.error(f"No ruteable: no hay hardware Basler en la dirección {self._address}")
                return False

            self._camera = pylon.InstantCamera(tl_factory.CreateDevice(matched_device))

            self._converter = pylon.ImageFormatConverter()
            self._converter.OutputPixelFormat = pylon.PixelType_BGR8packed
            self._converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned

            self._camera.Open()
            self._apply_camera_params()
            # Con la captura deshabilitada se abre la cámara pero no se pide stream:
            # la sesión queda lista y el ancho de banda del bus libre.
            if self._capture_enabled:
                self._start_grabbing()

            self.is_connected = True
            # Puede ser otra cámara física, así que se permite volver a avisar si
            # la temperatura sigue sin leerse.
            self._temp_warned = False
            logger.info(f"Basler {self._address} conectada y capturando.")
            return True

        except Exception as e:
            logger.error(f"Excepción grave conectando al stream Basler {self._address}: {e}")
            self.is_connected = False
            return False

    def _start_grabbing(self):
        self._camera.StartGrabbing(
            pylon.GrabStrategy_LatestImageOnly, pylon.GrabLoop_ProvidedByUser
        )

    def _apply_capture_enabled(self):
        # Cortar el grab libera el ancho de banda GigE, que con varias cámaras sobre
        # una sola NIC es el recurso escaso. La sesión con la cámara no se cierra.
        if not self.is_connected or not self._camera:
            return
        try:
            if self._capture_enabled and not self._camera.IsGrabbing():
                self._start_grabbing()
                logger.info(f"Basler {self._address}: captura habilitada.")
            elif not self._capture_enabled and self._camera.IsGrabbing():
                self._camera.StopGrabbing()
                logger.info(f"Basler {self._address}: captura deshabilitada.")
        except Exception as e:
            logger.warning(f"[Basler {self._address}] No se pudo cambiar la captura: {e}")

    def _apply_camera_params(self):
        def set_node(node_name: str, value: str | float | bool):
            try:
                node = getattr(self._camera, node_name)
                if genicam.IsWritable(node):
                    node.SetValue(value)
            except Exception as e:
                logger.warning(
                    f"No se pudo escribir {node_name} en la Basler {self._address}: {e}"
                )

        exposure_time_us = float(
            self._acquisition.get("exposure_time_us", _DEFAULT_EXPOSURE_TIME_US)
        )
        gain = float(self._acquisition.get("gain", _DEFAULT_GAIN))
        fps_limit = float(self._acquisition.get("fps_limit", _DEFAULT_FPS_LIMIT))

        set_node("ExposureAuto", "Off")
        set_node("ExposureTime", exposure_time_us)
        set_node("GainAuto", "Off")
        set_node("Gain", gain)
        set_node("AcquisitionFrameRateEnable", True)
        set_node("AcquisitionFrameRate", fps_limit)
