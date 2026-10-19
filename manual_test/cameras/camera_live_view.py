"""
Visor en vivo para probar los drivers de cámara contra hardware real.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\cameras\\camera_live_view.py [nombre]
    Linux:    .venv/bin/python manual_test/cameras/camera_live_view.py [nombre]

Ejecutar `.\\manual_test\\cameras\\camera_live_view.py` deja que Windows elija el
intérprete por asociación de archivo, que puede no tener consola (no se ve nada) y no
es el del venv, así que los SDK de cámara no están. `py` tampoco sirve: toma el Python
del sistema, sin pypylon ni stapipy.

Lee el config.yaml que tiene al lado (no el de la app) y arma el driver con la misma
fábrica que usa el sistema, así se prueba el camino real y no una instancia a mano.
Sin argumento toma la primera cámara del archivo.

Las credenciales de una cámara RTSP no están en ese archivo: salen del `.env` de la raíz
del repo, que este script carga por su cuenta porque no pasa por `main.py`.

Con ventana (OpenCV con highgui) — teclas:
    q / ESC   salir (también sirve cerrar la ventana)
    e         habilitar / deshabilitar la captura

El frame se muestra tal como lo devuelve el driver: la ventana se encoge para que
entre en pantalla, pero la imagen no se toca.

Sin ventana (OpenCV headless, o Linux sin DISPLAY) — se detecta solo, no hay flag:
    - Hace el mismo ciclo contra el driver —get_frame, reconexión, estado— e imprime,
      además, el tamaño del frame y los fps realmente entregados.
    - Levanta el `HttpVideoServer` del sistema para poder mirar: el frame crudo del
      driver sale como MJPEG en
          http://<ip del equipo>:8192/<nombre>/raw
      (la URL exacta se imprime al arrancar). Se abre desde el navegador de otra PC de
      la LAN. El puerto es `_PREVIEW_PORT`, distinto del 8091 de la app y del 8191 de
      `manual_test/http_video/`, así corren juntos. Si el equipo tiene firewall (ufw)
      hay que abrirlo: `sudo ufw allow 8192/tcp`.
    - Como en el sistema, el frame sólo se codifica mientras alguien mira el stream.
    - La tecla `e` no existe: se sale con Ctrl+C, que desconecta el driver y apaga el
      servidor antes de terminar.
"""

import os
import socket
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import yaml

# La raíz del repo es el primer ancestro que contiene `tools/`, no un número fijo de
# niveles: así el script sobrevive a que lo muevan de carpeta.
_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "tools").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene tools/.")
sys.path.insert(0, str(_REPO_ROOT))

from system.env import load_env_file  # noqa: E402
from system.video.abstract_video_server import MODE_RAW, STATUS_ACTIVE  # noqa: E402
from system.video.http_server import HttpVideoServer  # noqa: E402
from tools.camera.abstract_driver import AbstractCameraDriver  # noqa: E402
from tools.camera.camera_factory import create_camera  # noqa: E402

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

_WINDOW_ID = "camera_live_view"
_DISPLAY_MAX_WIDTH_PX = 1280   # ancho máximo de la ventana, no del frame
_FRAME_TIMEOUT_MS = 1000
_STATUS_INTERVAL_S = 1.0       # cada cuánto se imprime el estado en consola
_RECONNECT_AFTER_MISSES = 30   # frames nulos seguidos antes de reintentar connect()
_QUIT_KEYS = (ord("q"), 27)    # q y ESC
_TOGGLE_CAPTURE_KEY = ord("e")

_PREVIEW_PORT = 8192           # vista previa HTTP del modo sin ventana
_PREVIEW_JPEG_QUALITY = 80     # 0–100
_PREVIEW_FRAME_WIDTH_PX = 1280  # ancho al que el servidor reduce el frame antes de codificar


def _load_cameras() -> dict:
    """Devuelve la sección `cameras` de la config de prueba."""
    config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return config.get("cameras") or {}


class _PreviewConfig:
    """Config mínima del servidor de vista previa: claves punteadas sobre un dict."""

    def __init__(self, values: dict):
        self._values = values

    def get(self, key: str, default: object = None) -> object:
        node = self._values
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _get_lan_address() -> str:
    """IP con la que el equipo sale a la red, para armar la URL que se abre desde otra PC."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("8.8.8.8", 80))   # elige la ruta; no envía tráfico
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def _open_window(title: str) -> bool:
    """
    Crea la ventana de OpenCV. False si no hay highgui o no hay pantalla donde abrirla.

    Un OpenCV headless levanta cv2.error al crear la ventana; uno con Qt o GTK y sin
    DISPLAY puede abortar el proceso en vez de levantar nada, así que en Linux se mira
    el entorno antes de intentarlo.
    """
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return False
    try:
        cv2.namedWindow(_WINDOW_ID, cv2.WINDOW_NORMAL)
        cv2.setWindowTitle(_WINDOW_ID, title)
    except cv2.error:
        return False
    return True


def _close_window():
    """Cierra las ventanas; sin highgui no hay nada que cerrar y no levanta nada."""
    try:
        cv2.destroyAllWindows()
    except cv2.error:
        pass


def _resize_window(frame: np.ndarray):
    """Ajusta la ventana al frame, encogiéndola si es más ancho que la pantalla."""
    height_px, width_px = frame.shape[:2]
    scale = min(1.0, _DISPLAY_MAX_WIDTH_PX / width_px)
    cv2.resizeWindow(_WINDOW_ID, int(width_px * scale), int(height_px * scale))


def _print_status(driver: AbstractCameraDriver, extra: str = ""):
    status = driver.get_status()
    print(
        f"  conectada {status.get('connected')} | "
        f"captura {'ON ' if status.get('capture_enabled') else 'OFF'} | "
        f"fps {status.get('fps_estimated', 0.0):5.1f} | "
        f"temperatura {status.get('temperature', 0.0):5.1f} °C"
        f"{extra}"
    )


def _fetch_frame(driver: AbstractCameraDriver, misses: int) -> tuple[np.ndarray | None, int]:
    """
    Pide un frame y lleva la cuenta de los nulos seguidos.

    Reintenta connect() al llegar a `_RECONNECT_AFTER_MISSES`. Devuelve el frame —o None—
    y los nulos seguidos que lleva.
    """
    frame = driver.get_frame(timeout_ms=_FRAME_TIMEOUT_MS)
    if frame is not None:
        return frame, 0

    # Sin captura no llegan frames por diseño: no es una falla que reconectar.
    if not driver.is_capture_enabled:
        return None, misses
    misses += 1
    if misses >= _RECONNECT_AFTER_MISSES:
        print(f"Sin frames desde hace {misses} intentos: reintentando connect()...")
        driver.connect()
        misses = 0
    return None, misses


def _show_live_feed(driver: AbstractCameraDriver):
    """Muestra el feed hasta que el usuario cierre. La ventana ya tiene que estar abierta."""
    is_window_sized = False
    misses = 0
    last_status_s = time.time()

    while True:
        frame, misses = _fetch_frame(driver, misses)

        if frame is not None:
            if not is_window_sized:
                _resize_window(frame)
                is_window_sized = True
            cv2.imshow(_WINDOW_ID, frame)

        if time.time() - last_status_s >= _STATUS_INTERVAL_S:
            _print_status(driver)
            last_status_s = time.time()

        key = cv2.waitKey(1) & 0xFF
        if key in _QUIT_KEYS:
            break
        if key == _TOGGLE_CAPTURE_KEY:
            driver.set_capture_enabled(not driver.is_capture_enabled)
            print(f"Captura -> {'habilitada' if driver.is_capture_enabled else 'deshabilitada'}")

        # La X de la ventana también termina el script.
        if cv2.getWindowProperty(_WINDOW_ID, cv2.WND_PROP_VISIBLE) < 1:
            break


def _start_preview(name: str) -> HttpVideoServer | None:
    """Levanta el stream MJPEG del frame crudo y dice dónde abrirlo. None si no arrancó."""
    server = HttpVideoServer(_PreviewConfig({
        "cameras": {name: {}},
        "video": {"http": {
            "enabled": True,
            "port": _PREVIEW_PORT,
            "jpeg_quality": _PREVIEW_JPEG_QUALITY,
            "frame_width_px": _PREVIEW_FRAME_WIDTH_PX,
            "log_connections": True,
        }},
    }))
    server.start()
    if server.status != STATUS_ACTIVE:
        print(f"La vista previa no arrancó (estado '{server.status}'): puerto {_PREVIEW_PORT} "
              f"ocupado. Se sigue sin ella.")
        return None
    print(f"Vista previa en http://{_get_lan_address()}:{_PREVIEW_PORT}/{name}/{MODE_RAW}")
    return server


def _show_headless_feed(driver: AbstractCameraDriver, name: str):
    """
    Corre el mismo ciclo que la ventana, sin ventana: imprime el estado, el tamaño del
    frame y los fps entregados, y publica el frame crudo por HTTP para mirarlo.
    """
    server = _start_preview(name)
    misses = 0
    frame_shape = None
    interval_frames = 0
    interval_start_s = time.monotonic()

    try:
        while True:
            frame, misses = _fetch_frame(driver, misses)

            if frame is not None:
                frame_shape = frame.shape
                interval_frames += 1
                if server is not None and server.has_raw_clients(name):
                    server.push_raw(name, frame)

            elapsed_s = time.monotonic() - interval_start_s
            if elapsed_s >= _STATUS_INTERVAL_S:
                size = (f"{frame_shape[1]}x{frame_shape[0]}x{frame_shape[2]}"
                        if frame_shape is not None else "sin frames")
                clients = server.get_client_count() if server is not None else 0
                _print_status(
                    driver,
                    f" | frame {size} | entregados {interval_frames / elapsed_s:5.1f} fps"
                    f" | clientes {clients}",
                )
                interval_frames = 0
                interval_start_s = time.monotonic()
    finally:
        if server is not None:
            server.stop()


def main() -> int:
    # Antes de armar el driver: la cámara RTSP resuelve sus credenciales al construirse, y
    # este script no pasa por `main.py`, que es donde la app carga el archivo.
    load_env_file()

    cameras = _load_cameras()
    if not cameras:
        print(f"{_CONFIG_PATH} no tiene ninguna cámara en 'cameras'.")
        return 2

    name = sys.argv[1] if len(sys.argv) > 1 else next(iter(cameras))
    if name not in cameras:
        print(f"'{name}' no está en {_CONFIG_PATH}. Disponibles: {', '.join(cameras)}.")
        return 2

    driver = create_camera(cameras[name])
    print(f"Cámara '{name}' -> {type(driver).__name__}")
    if driver.is_synthetic:
        print("Driver sintético: los frames no vienen de una cámara real.")

    if not driver.connect():
        status = driver.get_status()
        if driver.is_config_error:
            print(f"Config inválida: {status.get('error')}")
        else:
            print(f"No se pudo conectar. Estado: {status}")
        return 1

    if not driver.is_capture_enabled:
        print("Captura deshabilitada por config (`enabled: false`): 'e' la habilita.")

    has_window = _open_window(f"{name} - {type(driver).__name__}")
    if not has_window:
        print("OpenCV sin ventana disponible (headless o sin pantalla): modo sin ventana.")

    try:
        if has_window:
            _show_live_feed(driver)
        else:
            _show_headless_feed(driver, name)
    except KeyboardInterrupt:
        print()
    finally:
        driver.disconnect()
        if has_window:
            _close_window()

    return 0


if __name__ == "__main__":
    sys.exit(main())
