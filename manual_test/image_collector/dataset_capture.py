"""
Prueba manual del recolector de dataset.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\image_collector\\dataset_capture.py
    Linux:    .venv/bin/python manual_test/image_collector/dataset_capture.py

No necesita cámara: genera escenas sintéticas y se las pasa al ImageCollector real,
con el ConfigManager real leyendo el config.yaml que tiene al lado (no el de la app).
El dataset queda donde diga `system.paths.dataset` de ese archivo.

En modo interval el script empuja frames de continuo con `push_frame()` y el
recolector decide cuándo guardar; en on_demand no empuja nada y cada guardado sale de
la tecla. La ventana muestra lo que se está por guardar: el original es la escena
limpia y el anotado es esa misma escena con el HUD dibujado encima.

El log se pone en DEBUG a propósito: los motivos de cada descarte —duplicado,
condición, disco— salen por ahí y son la mitad de lo que hay que mirar.

Teclas:
    q / ESC       salir (también sirve cerrar la ventana)
    s / espacio   guardar ahora (save_now), en cualquiera de los dos modos
    m             cambiar de modo (interval <-> on_demand)
    e             prender / apagar la recolección
    f             escena fija / cambiante — con la fija el dedup descarta
    c             confianza alta / baja — con la baja la save_condition descarta
    g             forzar / soltar la guardia de disco

Qué mirar:
    - Los intervalos. Cada guardado imprime cuánto pasó desde el anterior, y tiene
      que caer entre `interval_min_s` y `interval_max_s`. Poner los dos iguales y ver
      que el intervalo deja de variar: ese es el modo periódico.
    - Editar el config.yaml de al lado con la prueba corriendo: la ventana nueva se
      aplica en el guardado siguiente, sin reiniciar.
    - El tope. Con `max_images` chico la cuenta de imágenes se clava y el tamaño del
      dataset deja de crecer. En la carpeta, el guardado más viejo se va completo: su
      PNG original, su PNG anotado y su JSON.
    - El dedup. Con `f` la escena queda fija y los guardados siguientes se descartan
      por duplicado, con la distancia de Hamming en el log. El hash se calcula sobre la
      escena limpia, así que el reloj del HUD no lo afecta — eso es justamente lo que
      se está verificando.
    - La guardia de disco. `g` sube `min_free_space_gb` un poco por encima del espacio
      libre real: con imágenes en el buffer se borran las más viejas para hacer lugar y,
      cuando no queda nada que borrar, el guardado se rechaza. Soltarla con `g` otra vez.
      Hay que probarla con la escena cambiante: los filtros corren en orden y con la
      escena fija el dedup descarta el frame antes de que la guardia lo vea.
    - Las condiciones. Con `c` en confianza baja, `min_confidence_condition` descarta
      todo: el contador de descartados sube y no se escribe nada.
    - El modo on_demand. Con `m` el hilo se detiene (running pasa a 'no') y el intervalo
      deja de guardar; solo la tecla escribe, y escribe en el hilo del script. Es el
      modo de los proyectos que capturan con la luz encendida.
    - `set_active()` es la variante que persiste el cambio en el config; la tecla `e`
      lo cambia solo en memoria, para no reescribir este config.yaml sin sus comentarios.
"""

import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

# La raíz del repo es el primer ancestro que contiene `system/`, no un número fijo de
# niveles: así el script sobrevive a que lo muevan de carpeta.
_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "system").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene system/.")
sys.path.insert(0, str(_REPO_ROOT))

import system.image_collector as ic                                     # noqa: E402
from system.config_manager import ConfigManager                         # noqa: E402
from system.image_collector import ImageCollector                       # noqa: E402
from system.image_collector_conditions import min_confidence_condition  # noqa: E402
from system.logger import logger                                        # noqa: E402
from system.paths import resolve                                        # noqa: E402

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

_CAMERA_SLOT = "camera_1"
_WINDOW_ID = "dataset_capture"

_FRAME_WIDTH_PX = 640
_FRAME_HEIGHT_PX = 480
_SHAPES_PER_SCENE = 6           # figuras al azar: dos escenas no se parecen al hash

_LOOP_DELAY_MS = 30             # espera de waitKey; define la reacción a la tecla
_PUSH_INTERVAL_S = 0.2          # cada cuánto se empuja un frame en modo interval
_STATUS_INTERVAL_S = 1.0        # cada cuánto se imprime la línea de estado
_HEADER_EVERY_ROWS = 20

_MIN_CONFIDENCE_PCT = 50.0      # umbral de la save_condition
_CONFIDENCE_HIGH_PCT = 90.0     # pasa la condición
_CONFIDENCE_LOW_PCT = 10.0      # no la pasa
_GUARD_MARGIN_GB = 1.0          # cuánto se sube el mínimo por encima del libre real

_MB = 1024 ** 2

_QUIT_KEYS = (ord("q"), 27)     # q y ESC
_SAVE_KEYS = (ord("s"), 32)     # s y espacio
_MODE_KEY = ord("m")
_ACTIVE_KEY = ord("e")
_SCENE_KEY = ord("f")
_CONFIDENCE_KEY = ord("c")
_GUARD_KEY = ord("g")

_HEADER = (f"{'hora':8s}  {'modo':10s}  {'act':3s}  {'hilo':4s}  {'guardados':>9s}  "
           f"{'descart.':>8s}  {'imágenes':>8s}  {'dataset':>10s}  {'libre':>9s}  último")


class _Scene:
    """
    Escena sintética que hace de frame de cámara.

    Fija devuelve siempre el mismo frame, que es lo que hace visible al dedup;
    cambiante devuelve uno nuevo por llamada.
    """

    def __init__(self):
        self.is_frozen = False
        self._frame = self._build()

    def next_frame(self) -> np.ndarray:
        if not self.is_frozen:
            self._frame = self._build()
        return self._frame

    def _build(self) -> np.ndarray:
        rng = np.random.default_rng()
        frame = np.empty((_FRAME_HEIGHT_PX, _FRAME_WIDTH_PX, 3), np.uint8)
        frame[:, :] = rng.integers(0, 255, (3,), dtype=np.uint8)
        for _ in range(_SHAPES_PER_SCENE):
            center = (int(rng.integers(0, _FRAME_WIDTH_PX)),
                      int(rng.integers(0, _FRAME_HEIGHT_PX)))
            radius = int(rng.integers(20, 120))
            color = rng.integers(0, 255, (3,), dtype=np.uint8).tolist()
            cv2.circle(frame, center, radius, color, -1)
        return frame


class _State:
    """Lo que el bucle lleva entre vueltas y no vive en el recolector."""

    def __init__(self, min_free_gb: float):
        self.confidence_pct = _CONFIDENCE_HIGH_PCT
        self.is_guard_forced = False
        self.configured_min_free_gb = min_free_gb
        self.saved = 0
        self.previous_save_iso: str | None = None
        self.rows = 0


def _draw_hud(frame: np.ndarray, lines: list[str]) -> np.ndarray:
    """Copia del frame con el estado escrito encima: es lo que se guarda como anotado."""
    display = frame.copy()
    cv2.rectangle(display, (0, 0), (_FRAME_WIDTH_PX, 14 + 18 * len(lines)), (0, 0, 0), -1)
    for index, text in enumerate(lines):
        cv2.putText(display, text, (10, 22 + 18 * index), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return display


def _dataset_size_mb(dataset_path: Path) -> float:
    """Tamaño de todo lo que hay en el dataset, en MiB. 0 si la carpeta no existe."""
    try:
        return sum(p.stat().st_size for p in dataset_path.rglob("*") if p.is_file()) / _MB
    except OSError:
        return 0.0


def _seconds_since(timestamp_iso: str | None) -> float | None:
    """Segundos desde un timestamp ISO del recolector, o None si no hay ninguno."""
    if timestamp_iso is None:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(timestamp_iso)).total_seconds()
    except ValueError:
        return None


def _format_row(stats: dict, dataset_path: Path) -> str:
    """Una línea con el estado del recolector, alineada con `_HEADER`."""
    images = sum(stats["images"].values())
    since_s = _seconds_since(stats["last_save_iso"])
    return (
        f"{datetime.now():%H:%M:%S}  {stats['mode']:10s}  "
        f"{'sí' if stats['active'] else 'no':3s}  "
        f"{'sí' if stats['running'] else 'no':4s}  "
        f"{stats['saved']:9d}  {stats['skipped']:8d}  {images:8d}  "
        f"{_dataset_size_mb(dataset_path):7.1f} MB  "
        f"{stats['disk_free_gb']:6.1f} GB  "
        + ("nunca" if since_s is None else f"hace {since_s:.1f} s")
    )


def _report_save(stats: dict, state: _State):
    """Avisa de cada guardado nuevo y de cuánto pasó desde el anterior."""
    elapsed = ""
    if state.previous_save_iso is not None:
        try:
            previous = datetime.fromisoformat(state.previous_save_iso)
            current = datetime.fromisoformat(stats["last_save_iso"])
            elapsed = f" — {(current - previous).total_seconds():.1f} s desde el anterior"
        except (TypeError, ValueError):
            elapsed = ""
    print(f">> GUARDADO #{stats['saved']} ({stats['images'].get(_CAMERA_SLOT, 0)} "
          f"en disco){elapsed}")
    state.previous_save_iso = stats["last_save_iso"]
    state.saved = stats["saved"]


def _toggle_mode(config: ConfigManager, collector: ImageCollector):
    """Cambia el modo y reinicia el recolector: el modo se lee en `start()`."""
    new_mode = (ic._MODE_ON_DEMAND if collector.mode == ic._MODE_INTERVAL
                else ic._MODE_INTERVAL)
    collector.stop()
    config.set("image_collector.mode", new_mode)
    collector.start()
    print(f"-- modo: {new_mode} --")


def _toggle_guard(config: ConfigManager, collector: ImageCollector, state: _State):
    """
    Sube el mínimo de espacio libre por encima del real, o lo devuelve al del config.

    Es la forma de probar la guardia sin llenar el disco de verdad.
    """
    if state.is_guard_forced:
        config.set("image_collector.min_free_space_gb", state.configured_min_free_gb)
        state.is_guard_forced = False
        print(f"-- guardia de disco: mínimo {state.configured_min_free_gb} GB (config) --")
        return
    forced_gb = round(collector.get_stats()["disk_free_gb"] + _GUARD_MARGIN_GB, 1)
    config.set("image_collector.min_free_space_gb", forced_gb)
    state.is_guard_forced = True
    print(f"-- guardia de disco FORZADA: mínimo {forced_gb} GB --")


def _handle_key(key: int, config: ConfigManager, collector: ImageCollector,
                state: _State, scene: _Scene, frame: np.ndarray,
                annotated: np.ndarray) -> bool:
    """Aplica la tecla. Devuelve False cuando hay que terminar."""
    if key in _QUIT_KEYS:
        return False
    if key in _SAVE_KEYS:
        saved = collector.save_now(_CAMERA_SLOT, frame, annotated_bgr=annotated,
                                   inference=_inference(state))
        print(f"-- save_now: {'guardado' if saved else 'no se guardó'} --")
    elif key == _MODE_KEY:
        _toggle_mode(config, collector)
    elif key == _ACTIVE_KEY:
        # Solo en memoria: set_active() reescribiría este config.yaml sin comentarios.
        config.set("image_collector.enabled", not collector.is_active)
        print(f"-- recolección: {'activa' if collector.is_active else 'apagada'} --")
    elif key == _SCENE_KEY:
        scene.is_frozen = not scene.is_frozen
        print(f"-- escena: {'fija' if scene.is_frozen else 'cambiante'} --")
    elif key == _CONFIDENCE_KEY:
        state.confidence_pct = (_CONFIDENCE_LOW_PCT
                                if state.confidence_pct == _CONFIDENCE_HIGH_PCT
                                else _CONFIDENCE_HIGH_PCT)
        print(f"-- confianza: {state.confidence_pct:.0f} % "
              f"(umbral {_MIN_CONFIDENCE_PCT:.0f} %) --")
    elif key == _GUARD_KEY:
        _toggle_guard(config, collector, state)
    return True


def _inference(state: _State) -> dict:
    """Dict de inferencia de mentira: lo que miran la save_condition y el JSON."""
    return {"confidence_pct": state.confidence_pct, "source": "dataset_capture"}


def _hud_lines(stats: dict, state: _State, scene: _Scene) -> list[str]:
    return [
        f"modo {stats['mode']} | hilo {'si' if stats['running'] else 'no'} | "
        f"recoleccion {'activa' if stats['active'] else 'apagada'}",
        f"guardados {stats['saved']} | descartados {stats['skipped']} | "
        f"en disco {sum(stats['images'].values())}",
        f"escena {'fija' if scene.is_frozen else 'cambiante'} | "
        f"confianza {state.confidence_pct:.0f}% | "
        f"guardia {'forzada' if state.is_guard_forced else 'config'}",
        f"{datetime.now():%H:%M:%S}  q salir  s guardar  m modo  e activa  "
        f"f escena  c confianza  g guardia",
    ]


def _print_intro(config: ConfigManager, dataset_path: Path):
    print(f"Config:  {_CONFIG_PATH}")
    print(f"Dataset: {dataset_path}")
    print(f"Modo:    {config.get('image_collector.mode')} | "
          f"intervalo {config.get('image_collector.interval_min_s')}–"
          f"{config.get('image_collector.interval_max_s')} s | "
          f"tope {config.get('image_collector.max_images')} imágenes/cámara")
    print(f"Filtros: dedup {'on' if config.get('image_collector.dedup.enabled') else 'off'} | "
          f"condición min_confidence({_MIN_CONFIDENCE_PCT:.0f})")
    print("\nTeclas: q salir | s guardar ahora | m modo | e recolección | "
          "f escena | c confianza | g guardia\n")


def _reload_if_changed(config: ConfigManager, last_mtime_s: float) -> float:
    """Relee el config si el archivo cambió: el recolector lo toma en el guardado siguiente."""
    try:
        mtime_s = _CONFIG_PATH.stat().st_mtime
    except OSError:
        return last_mtime_s
    if mtime_s != last_mtime_s:
        config.load()
        print(f"-- {_CONFIG_PATH.name} recargado (se pierden los cambios de las teclas) --")
    return mtime_s


def _is_window_closed() -> bool:
    try:
        return cv2.getWindowProperty(_WINDOW_ID, cv2.WND_PROP_VISIBLE) < 1
    except cv2.error:
        return True


def main() -> int:
    if not _CONFIG_PATH.exists():
        print(f"Falta {_CONFIG_PATH}.")
        return 2

    # Los motivos de descarte del recolector salen en DEBUG, y son la mitad de lo que
    # esta prueba tiene que mostrar.
    logger.setLevel(logging.DEBUG)

    config = ConfigManager(str(_CONFIG_PATH))
    collector = ImageCollector(
        config, save_conditions=[min_confidence_condition(_MIN_CONFIDENCE_PCT)])
    dataset_path = Path(resolve(config.get("system.paths.dataset", None), "./data/dataset"))
    state = _State(float(config.get("image_collector.min_free_space_gb", 5)))
    scene = _Scene()

    _print_intro(config, dataset_path)
    collector.start()

    config_mtime_s = _CONFIG_PATH.stat().st_mtime
    last_push_s = 0.0
    last_status_s = 0.0
    try:
        while True:
            frame = scene.next_frame()
            stats = collector.get_stats()
            annotated = _draw_hud(frame, _hud_lines(stats, state, scene))
            cv2.imshow(_WINDOW_ID, annotated)

            now_s = time.monotonic()
            # En on_demand no se empuja nada: ahí el guardado sale de la tecla.
            if stats["mode"] == ic._MODE_INTERVAL and now_s - last_push_s >= _PUSH_INTERVAL_S:
                collector.push_frame(_CAMERA_SLOT, frame, annotated_bgr=annotated,
                                     inference=_inference(state))
                last_push_s = now_s

            if stats["saved"] != state.saved:
                _report_save(stats, state)

            if now_s - last_status_s >= _STATUS_INTERVAL_S:
                if state.rows % _HEADER_EVERY_ROWS == 0:
                    print(_HEADER)
                print(_format_row(stats, dataset_path))
                state.rows += 1
                last_status_s = now_s
                config_mtime_s = _reload_if_changed(config, config_mtime_s)

            key = cv2.waitKey(_LOOP_DELAY_MS) & 0xFF
            if key != 255 and not _handle_key(key, config, collector, state, scene,
                                              frame, annotated):
                break
            if _is_window_closed():
                break
    except KeyboardInterrupt:
        print()
    finally:
        collector.stop()
        cv2.destroyAllWindows()

    stats = collector.get_stats()
    print(f"\nResumen: {stats['saved']} guardados, {stats['skipped']} descartados, "
          f"{sum(stats['images'].values())} en disco, "
          f"{_dataset_size_mb(dataset_path):.1f} MB en {dataset_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
