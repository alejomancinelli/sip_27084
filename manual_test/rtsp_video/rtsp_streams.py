"""
Prueba manual del servidor RTSP de video.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\rtsp_video\\rtsp_streams.py
    Linux:    .venv/bin/python manual_test/rtsp_video/rtsp_streams.py

Lee el config.yaml que tiene al lado (no el de la app) y levanta el servidor real.
No usa cámara ni inferencia: genera frames sintéticos de 1920×1200 al ritmo de una
cámara (`_CAMERA_FPS`) —gradiente, barra que se mueve, reloj y una banda de ruido—
y frames anotados al ritmo de la inferencia (`_INFERENCE_INTERVAL_S`), para cada
slot de la sección `cameras`.

Como el sistema de verdad, sólo genera el frame de un stream si alguien lo está
mirando: es lo que hacen `has_raw_clients` y `has_annotated_clients`.

Qué hace falta en el equipo, y es lo que esta prueba verifica antes que nada:

    Jetson / Ubuntu:
        sudo apt install python3-gi gir1.2-gst-rtsp-server-1.0 \\
            gstreamer1.0-plugins-base gstreamer1.0-plugins-good \\
            gstreamer1.0-plugins-ugly gstreamer1.0-libav
      Los bindings salen de apt, no de pip, así que el venv tiene que haberse
      creado con `--system-site-packages`: sin eso `import gi` no los ve.

    Windows: GStreamer y gst-rtsp-server existen para Windows; lo que no hay es un
      `pip install` que meta `gi` en este intérprete. Los caminos son MSYS2 —que
      trae su propio Python, donde no entran los wheels cp310 de pypylon y
      stapipy— o compilar PyGObject con gvsbuild. Ninguno vale la pena para una
      plataforma donde no hay cámaras conectadas: acá el RTSP queda no-op, el
      módulo lo dice y la app sigue andando, y para mirar video en la PC de
      desarrollo está el MJPEG de `http_server`.

Con qué mirarlo (la URL exacta la imprime el script):

    ffplay -fflags nobuffer -flags low_delay rtsp://<ip>:8654/camera_1/raw
    vlc rtsp://<ip>:8654/camera_1/raw
    cv2.VideoCapture("rtsp://<ip>:8654/camera_1/raw")

Qué mirar:
    - El reloj del frame contra el reloj de la consola: eso es la latencia real de
      codificar, transportar y decodificar. VLC bufferea ~1 s de entrada; ffplay
      con `nobuffer` muestra bastante menos.
    - Abrir un solo stream: en la consola sólo ese sube de 0 fps. Eso es el gating,
      y es lo que evita generar y codificar cuatro streams cuando se mira uno.
    - Conectarse *antes* de que haya frames (arrancar el reproductor primero, o con
      `cameras` recién agregadas): el stream espera al primer frame en lugar de
      quedarse colgado.
    - `frame_width_px` en 1280: el reproductor informa 1280×800 aunque el frame se
      genere a 1920×1200, y baja el uso de CPU.
    - `codec: mjpeg` arranca en cualquier equipo con los plugins base; los `_hw`
      solo en la Jetson.
    - `net_interfaces` con el nombre de una interfaz: la URL de esa interfaz
      responde y la de las otras no. El script imprime las interfaces del equipo
      con su IP, que son los nombres que van en esa clave.
    - Cerrar el reproductor: el pipeline se desarma y el uso de CPU vuelve a cero.
      La sesión que se cuenta acá tarda unos segundos más en vencer.
    - Ctrl+C corta los streams abiertos y libera el puerto.
"""

import socket
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import psutil
import yaml

# La raíz del repo es el primer ancestro que contiene `system/`, no un número fijo de
# niveles: así el script sobrevive a que lo muevan de carpeta.
_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "system").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene system/.")
sys.path.insert(0, str(_REPO_ROOT))

from system.video.abstract_video_server import MODE_ANNOTATED, MODE_RAW  # noqa: E402
from system.video.rtsp_server import RtspVideoServer  # noqa: E402

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

_FRAME_WIDTH_PX = 1920         # resolución de origen, la que escala el servidor
_FRAME_HEIGHT_PX = 1200
_CAMERA_FPS = 15               # ritmo al que se generan los frames crudos
_INFERENCE_INTERVAL_S = 1.0    # ritmo al que se generan los frames anotados
_STATUS_INTERVAL_S = 1.0       # cada cuánto se imprime el estado en consola
_BAR_WIDTH_PX = 120            # barra que se mueve, para ver el avance del stream
_NOISE_WIDTH_PX = 400          # ancho de la banda de ruido
_NOISE_MAX = 60                # amplitud del ruido 0–255


class _YamlConfig:
    """Lee claves punteadas del config.yaml de al lado, como hará ConfigManager."""

    def __init__(self, path: Path):
        self._values = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def get(self, key: str, default: object = None) -> object:
        node = self._values
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _print_interfaces():
    """Interfaces del equipo con su IPv4: son los nombres que van en `net_interfaces`."""
    print("Interfaces de este equipo:")
    for iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET:
                print(f"  {iface:<24} {addr.address}")


def _build_raw_frame(camera_slot: str, frame_index: int) -> np.ndarray:
    """Frame BGR con gradiente, barra móvil, reloj y ruido, del tamaño de una cámara."""
    frame_bgr = np.zeros((_FRAME_HEIGHT_PX, _FRAME_WIDTH_PX, 3), np.uint8)
    frame_bgr[:, :, 0] = np.linspace(40, 200, _FRAME_WIDTH_PX, dtype=np.uint8)[None, :]

    x_px = (frame_index * 20) % (_FRAME_WIDTH_PX - _BAR_WIDTH_PX)
    frame_bgr[:, x_px:x_px + _BAR_WIDTH_PX] = (255, 255, 255)

    # Banda de ruido: un stream congelado se nota enseguida, incluso sin mirar el reloj.
    noise = np.random.randint(0, _NOISE_MAX,
                              (_FRAME_HEIGHT_PX, _NOISE_WIDTH_PX, 3), dtype=np.uint8)
    frame_bgr[:, :_NOISE_WIDTH_PX] = noise

    cv2.putText(frame_bgr, f"{camera_slot} raw #{frame_index}", (40, 110),
                cv2.FONT_HERSHEY_SIMPLEX, 2.2, (0, 0, 0), 5)
    cv2.putText(frame_bgr, datetime.now().strftime("%H:%M:%S.%f")[:-3], (40, 200),
                cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 4)
    return frame_bgr


def _build_annotated_frame(camera_slot: str, cycle: int) -> np.ndarray:
    """Frame crudo con un recuadro y una etiqueta encima, como una salida de inferencia."""
    frame_bgr = _build_raw_frame(camera_slot, cycle)
    cv2.rectangle(frame_bgr, (600, 400), (1200, 900), (0, 220, 0), 6)
    cv2.putText(frame_bgr, f"annotated ciclo {cycle}", (600, 380),
                cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 220, 0), 4)
    return frame_bgr


def _print_status(server: RtspVideoServer, slots: list, published: Counter):
    """Qué streams se están mirando y cuántos frames se publicaron en el intervalo."""
    for slot in slots:
        print(
            f"  {slot}  "
            f"raw: {'mirando' if server.has_raw_clients(slot) else '  vacío'}, "
            f"{published[(slot, MODE_RAW)]:2d} fps | "
            f"annotated: {'mirando' if server.has_annotated_clients(slot) else '  vacío'}, "
            f"{published[(slot, MODE_ANNOTATED)]:2d} fps"
        )
    print(f"  total: {server.get_client_count()} sesiones RTSP")


def main() -> int:
    config = _YamlConfig(_CONFIG_PATH)
    slots = list(config.get("cameras", {}) or {})
    if not slots:
        print(f"{_CONFIG_PATH} no tiene ninguna cámara en 'cameras'.")
        return 2

    _print_interfaces()
    server = RtspVideoServer(config)
    server.start()
    if server.status != "active":
        print(f"El servidor no arrancó (estado '{server.status}'). El log de arriba dice "
              f"por qué: revisar GStreamer, {_CONFIG_PATH} y el puerto.")
        return 1

    print("Servidor activo. Abrir con ffplay, VLC o un NVR:")
    for url in server.get_stream_urls():
        print(f"  {url}")
    print(f"Generando {_CAMERA_FPS} fps por cámara, sólo para los streams que alguien "
          f"esté mirando. Ctrl+C para terminar.")

    published = Counter()
    frame_period_s = 1.0 / _CAMERA_FPS
    frame_index = 0
    cycle = 0
    next_inference_s = time.monotonic()
    next_status_s = time.monotonic() + _STATUS_INTERVAL_S

    try:
        while True:
            started_s = time.monotonic()
            frame_index += 1

            for slot in slots:
                if server.has_raw_clients(slot):
                    server.push_raw(slot, _build_raw_frame(slot, frame_index))
                    published[(slot, MODE_RAW)] += 1

            if started_s >= next_inference_s:
                next_inference_s += _INFERENCE_INTERVAL_S
                cycle += 1
                for slot in slots:
                    if server.has_annotated_clients(slot):
                        server.push_annotated(slot, _build_annotated_frame(slot, cycle))
                        published[(slot, MODE_ANNOTATED)] += 1

            if started_s >= next_status_s:
                next_status_s += _STATUS_INTERVAL_S
                _print_status(server, slots, published)
                published.clear()

            time.sleep(max(0.0, frame_period_s - (time.monotonic() - started_s)))
    except KeyboardInterrupt:
        print()
    finally:
        server.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
