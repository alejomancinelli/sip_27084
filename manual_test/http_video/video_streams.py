"""
Prueba manual del servidor HTTP de video.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\http_video\\video_streams.py
    Linux:    .venv/bin/python manual_test/http_video/video_streams.py

Lee el config.yaml que tiene al lado (no el de la app) y levanta el servidor real.
No usa cámara ni inferencia: genera frames sintéticos de 1920×1200 al ritmo de una
cámara (`_CAMERA_FPS`) y frames anotados al ritmo de la inferencia
(`_INFERENCE_INTERVAL_S`), para cada slot de la sección `cameras`.

Como el sistema de verdad, sólo genera el frame de un stream si alguien lo está
mirando: es lo que hacen `has_raw_clients` y `has_annotated_clients`.

Qué mirar en el navegador:
    - Abrir un solo stream: en la consola sólo ese sube de 0 fps. Eso es el gating,
      y es lo que evita codificar cuatro streams cuando se mira uno.
    - El frame llega a 960 px de ancho aunque se genere a 1920: es `frame_width_px`.
    - Cerrar la pestaña: los fps de ese stream vuelven a 0 en menos de un segundo.
    - Abrir el mismo stream en dos pestañas: las dos ven el mismo frame y los fps
      publicados no se duplican.
    - Cada cámara tiene su propia ruta, con la clave del slot y no con el `name`.
    - `log_connections: false` calla las líneas de conectado/desconectado. Acá hay
      que reiniciar el script para que tome el cambio: el `_YamlConfig` de abajo lee
      el archivo una sola vez, mientras que ConfigManager lo relee.
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

from system.video.http_server import (  # noqa: E402
    HttpVideoServer,
    _MODE_ANNOTATED,
    _MODE_RAW,
)

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

_FRAME_WIDTH_PX = 1920         # resolución de origen, la que reduce el servidor
_FRAME_HEIGHT_PX = 1200
_CAMERA_FPS = 15               # ritmo al que se generan los frames crudos
_INFERENCE_INTERVAL_S = 1.0    # ritmo al que se generan los frames anotados
_STATUS_INTERVAL_S = 1.0       # cada cuánto se imprime el estado en consola
_BAR_WIDTH_PX = 120            # barra que se mueve, para ver el avance del stream


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


def _get_lan_address() -> str:
    """IP con la que el host sale a la red, para armar la URL que se abre desde otra PC."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("8.8.8.8", 80))   # elige la ruta; no envía tráfico
            return probe.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def _build_raw_frame(camera_slot: str, frame_index: int) -> np.ndarray:
    """Frame BGR con gradiente, barra móvil y hora, del tamaño de una cámara real."""
    frame_bgr = np.zeros((_FRAME_HEIGHT_PX, _FRAME_WIDTH_PX, 3), np.uint8)
    frame_bgr[:, :, 0] = np.linspace(40, 200, _FRAME_WIDTH_PX, dtype=np.uint8)[None, :]

    x_px = (frame_index * 20) % (_FRAME_WIDTH_PX - _BAR_WIDTH_PX)
    frame_bgr[:, x_px:x_px + _BAR_WIDTH_PX] = (255, 255, 255)
    cv2.putText(frame_bgr, f"{camera_slot} raw #{frame_index}", (40, 110),
                cv2.FONT_HERSHEY_SIMPLEX, 2.2, (0, 0, 0), 5)
    cv2.putText(frame_bgr, datetime.now().strftime("%H:%M:%S.%f")[:-3], (40, 200),
                cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 4)
    return frame_bgr


def _build_annotated_frame(camera_slot: str, cycle: int) -> np.ndarray:
    """Frame crudo con un recuadro y una etiqueta encima, como una salida de inferencia."""
    frame_bgr = _build_raw_frame(camera_slot, cycle)
    cv2.rectangle(frame_bgr, (300, 400), (900, 900), (0, 220, 0), 6)
    cv2.putText(frame_bgr, f"annotated ciclo {cycle}", (300, 380),
                cv2.FONT_HERSHEY_SIMPLEX, 1.8, (0, 220, 0), 4)
    return frame_bgr


def _print_status(server: HttpVideoServer, slots: list, published: Counter):
    """Qué streams se están mirando y cuántos frames se publicaron en el intervalo."""
    for slot in slots:
        print(
            f"  {slot}  "
            f"raw: {'mirando' if server.has_raw_clients(slot) else '  vacío'}, "
            f"{published[(slot, _MODE_RAW)]:2d} fps | "
            f"annotated: {'mirando' if server.has_annotated_clients(slot) else '  vacío'}, "
            f"{published[(slot, _MODE_ANNOTATED)]:2d} fps"
        )
    print(f"  total: {server.get_client_count()} clientes conectados")


def main() -> int:
    config = _YamlConfig(_CONFIG_PATH)
    slots = list(config.get("cameras", {}) or {})
    if not slots:
        print(f"{_CONFIG_PATH} no tiene ninguna cámara en 'cameras'.")
        return 2

    server = HttpVideoServer(config)
    server.start()
    if server.status != "active":
        print(f"El servidor no arrancó (estado '{server.status}'). Revisar {_CONFIG_PATH}.")
        return 1

    port = config.get("http_video.port")
    print("Servidor activo. Abrir en el navegador:")
    for host in ("localhost", _get_lan_address()):
        for slot in slots:
            print(f"  http://{host}:{port}/{slot}/{_MODE_RAW}")
            print(f"  http://{host}:{port}/{slot}/{_MODE_ANNOTATED}")
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
                    published[(slot, _MODE_RAW)] += 1

            if started_s >= next_inference_s:
                next_inference_s += _INFERENCE_INTERVAL_S
                cycle += 1
                for slot in slots:
                    if server.has_annotated_clients(slot):
                        server.push_annotated(slot, _build_annotated_frame(slot, cycle))
                        published[(slot, _MODE_ANNOTATED)] += 1

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
