"""
Hilo de captura de una cámara: entrega frames y telemetría por señales de Qt.

Una instancia por cámara. Lee su slot de la sección `cameras` del config, le pide
el driver a la fábrica y desde ahí solo habla con el contrato de
AbstractCameraDriver: no sabe qué marca le tocó ni cómo se conecta.

Maquinaria genérica: reutilizable entre proyectos.

De qué se ocupa:
  - Conectar y reconectar solo. Un driver caído se reintenta cada
    `_RECONNECT_DELAY_MS` hasta que vuelve, sin que nadie lo pida.
  - Publicar cada frame por `frame_ready` y la salud de la cámara por
    `status_updated`.
  - Parar limpio: `requestInterruption()` + `wait()` cortan el bucle y liberan el
    driver.

Cuatro puntos del contrato que no se ven en las firmas:
  - La sección de la cámara se lee una vez, al construir el hilo, que es donde se
    arma el driver. Cambiar marca, dirección o parámetros de adquisición exige
    rehacer el hilo.
  - El bucle pide frames de continuo y el ritmo lo pone la cámara: es lo que
    corresponde en free-run, donde `fps_limit` regula y el driver descarta lo que
    sobra. Si el driver pasa a capturar por trigger, cada `get_frame()` es un
    disparo y este hilo dispararía tan rápido como el driver acepte: el ritmo y el
    orden de los disparos son de quien orqueste la captura, no de acá.
  - El status que viaja en `status_updated` es el del driver tal cual: acá no se
    completa ni se recalcula ninguna clave, `fps_estimated` incluido.
  - Una cámara mal configurada (NullDriver) no se reintenta a ciegas: el error
    sale una sola vez al log y viaja en cada status.
"""

import time

from PySide6.QtCore import QThread, Signal

from system.config_manager import ConfigManager
from system.logger import logger
from tools.camera.camera_factory import create_camera

_FRAME_TIMEOUT_MS = 500      # espera máxima al driver por cada frame
_STATUS_INTERVAL_S = 2.0     # período de la telemetría de `status_updated`
_RECONNECT_DELAY_MS = 5000   # entre reintentos de conexión
_ERROR_DELAY_MS = 1000       # pausa después de una excepción del driver
_IDLE_SLEEP_MS = 10          # sin frame disponible: evita el busy-loop
_DISABLED_POLL_MS = 200      # con la captura deshabilitada no hay nada que pedir
_SLEEP_STEP_MS = 200         # troceo de las esperas largas


class CaptureThread(QThread):
    """
    Captura los frames de una cámara por polling al driver, en su propio hilo.

    `frame_ready` sale al ritmo de la cámara, así que lo que se conecte del otro
    lado tiene que ser barato o descartar: cualquier demora ahí frena la captura.
    """

    frame_ready = Signal(object, str)     # (frame BGR, slot) — al ritmo de la cámara
    status_updated = Signal(object, str)  # (dict de get_status(), slot) — cada _STATUS_INTERVAL_S

    def __init__(self, config_manager: ConfigManager, camera_slot: str):
        super().__init__()
        self.camera_slot = camera_slot
        self._driver = create_camera(config_manager.get(f"cameras.{camera_slot}", {}) or {})
        # NullDriver: la config está mal y reintentar connect() no cambia nada. El
        # motivo lo publica el propio driver en su status.
        self._config_error: str | None = (
            self._driver.get_status().get("error") if self._driver.is_config_error else None
        )
        self._last_status: dict = self._driver.get_status()
        self._last_status_time_s = time.time()
        # Pedido de prender o apagar la captura, pendiente de aplicar. None = nada que
        # hacer. Lo escribe el hilo que llama a `set_capture_enabled` y lo consume este
        # hilo: asignar una referencia es atómico bajo el GIL, así que no necesita lock.
        self._capture_wanted: bool | None = None

    # ── API pública ──────────────────────────────────────────────────────────

    @property
    def is_synthetic(self) -> bool:
        """True si los frames los genera un driver sintético y no una cámara real."""
        return self._driver.is_synthetic

    @property
    def config_error(self) -> str | None:
        """Motivo por el que la cámara no puede operar, o None si la config sirve."""
        return self._config_error

    def set_capture_enabled(self, enabled: bool):
        """
        Pide prender o apagar la captura sin cerrar la conexión. Barata y thread-safe.

        No toca el driver: dos de los drivers cortan el grab en el SDK, y hacerlo desde
        otro hilo mientras éste está adentro de `get_frame()` es una carrera contra la
        librería del fabricante. Lo aplica este hilo en su próxima vuelta.
        """
        self._capture_wanted = bool(enabled)

    def get_status(self) -> dict:
        """
        Última telemetría leída, con las claves de AbstractCameraDriver.get_status().

        Para quien llegue tarde a `status_updated` —una ventana que se abre después
        de arrancar la captura— y no quiera esperar hasta el próximo período.
        """
        return dict(self._last_status)

    def run(self):
        logger.info(f"[{self.camera_slot}] Hilo de captura iniciado.")
        config_error_logged = False

        while not self.isInterruptionRequested():
            # ── Conexión y reconexión ────────────────────────────────────────
            if not self._driver.is_connected:
                self._emit_status()

                if self._config_error:
                    if not config_error_logged:
                        logger.error(
                            f"[{self.camera_slot}] {self._config_error}. Sin captura hasta "
                            f"corregir 'cameras.{self.camera_slot}.driver' en config.yaml."
                        )
                        config_error_logged = True
                    self._sleep_interruptible(_RECONNECT_DELAY_MS)
                    continue

                if not self._driver.connect():
                    logger.warning(
                        f"[{self.camera_slot}] Conexión fallida. "
                        f"Reintento en {_RECONNECT_DELAY_MS // 1000} s."
                    )
                    self._sleep_interruptible(_RECONNECT_DELAY_MS)
                    continue

            self._apply_capture_wanted()

            # ── Captura de frame ─────────────────────────────────────────────
            try:
                frame = self._driver.get_frame(timeout_ms=_FRAME_TIMEOUT_MS)
            except Exception as e:
                # Falla inesperada del driver: desconexión física, caída del bus.
                # disconnect() libera lo que haya quedado abierto y deja
                # `is_connected` en False, así que la próxima vuelta reconecta.
                logger.warning(f"[{self.camera_slot}] Excepción en get_frame: {e}. Se reconecta.")
                self._driver.disconnect()
                self._sleep_interruptible(_ERROR_DELAY_MS)
                continue

            if frame is not None:
                self.frame_ready.emit(frame, self.camera_slot)
            else:
                self.msleep(self._idle_sleep_ms())

            # ── Telemetría ───────────────────────────────────────────────────
            if (time.time() - self._last_status_time_s) > _STATUS_INTERVAL_S:
                self._emit_status()

        logger.info(f"[{self.camera_slot}] Liberando el driver y cerrando el hilo.")
        self._driver.disconnect()

    # ── Internos ─────────────────────────────────────────────────────────────

    def _emit_status(self):
        """Lee la salud del driver y la publica. Un driver que falla acá no baja el hilo."""
        self._last_status_time_s = time.time()
        try:
            status = self._driver.get_status()
        except Exception as e:
            logger.warning(f"[{self.camera_slot}] Excepción en get_status: {e}. No se publica.")
            return
        self._last_status = status
        self.status_updated.emit(status, self.camera_slot)

    def _apply_capture_wanted(self):
        """Aplica en el driver el último pedido de captura, si hay alguno pendiente."""
        wanted, self._capture_wanted = self._capture_wanted, None
        if wanted is None:
            return
        try:
            self._driver.set_capture_enabled(wanted)
        except Exception as e:
            logger.warning(f"[{self.camera_slot}] No se pudo cambiar la captura: {e}.")

    def _idle_sleep_ms(self) -> int:
        """Pausa entre pedidos cuando el driver no entrega frame."""
        # Con la captura deshabilitada get_frame() vuelve enseguida y sin tocar el
        # hardware: preguntar cada 10 ms sería quemar CPU para nada.
        if not self._last_status.get("capture_enabled", True):
            return _DISABLED_POLL_MS
        return _IDLE_SLEEP_MS

    def _sleep_interruptible(self, total_ms: int):
        """
        Espera troceada, que corta apenas se pide la interrupción.

        Un msleep() monolítico sobrevive al wait() del cierre, y Qt aborta el
        proceso al destruir un QThread que todavía está corriendo.
        """
        waited_ms = 0
        while waited_ms < total_ms and not self.isInterruptionRequested():
            step_ms = min(_SLEEP_STEP_MS, total_ms - waited_ms)
            self.msleep(step_ms)
            waited_ms += step_ms
