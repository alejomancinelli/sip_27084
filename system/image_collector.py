"""
Recolección del dataset de imágenes en disco.

Maquinaria genérica: no conoce cámaras, inferencia ni protocolo. Recibe frames BGR
ya armados y el dict de inferencia que les corresponde, y los escribe.

Dos modos de operación, según `image_collector.mode`, porque los proyectos capturan
de dos maneras distintas:

    interval    El recolector decide cuándo guardar. El productor solo deja el último
                frame de cada cámara con `push_frame()`; un hilo propio guarda uno por
                cámara cada `interval_min_s`–`interval_max_s` segundos, al azar dentro
                de esa ventana. Configurar los dos valores iguales da un guardado
                periódico. Es el modo de los proyectos que infieren de continuo: el
                intervalo al azar junta un dataset variado sin sesgarlo al ritmo del
                proceso.

    on_demand   El llamador decide cuándo guardar, con `save_now()`. No se levanta
                ningún hilo. Es el modo de los proyectos que infieren cada varios
                minutos —con la iluminación encendida solo durante la captura—, donde
                guardar por reloj traería imágenes a oscuras.

                    interval   push_frame(frame) ──► último frame por cámara
                                                          │ vencido el intervalo
                                                          ▼
                                                     hilo propio ──► disco

                    on_demand  save_now(frame) ─────────────────────► disco
                                                        (en el hilo del llamador)

Layout de cada guardado. La imagen anotada conserva el mismo nombre de archivo que
la original — la carpeta es lo que las distingue:

    {dataset}/{fecha}/original/{camera_slot}/{camera_slot}_{ts}.png    frame de cámara
    {dataset}/{fecha}/annotated/{camera_slot}/{camera_slot}_{ts}.png   frame con anotaciones
    {dataset}/{fecha}/annotated/{camera_slot}/{camera_slot}_{ts}.json  metadata de inferencia

La carpeta lleva la clave del slot (`camera_1`), no el `name` de la cámara: el
nombre es texto de UI y puede cambiar sin romper nada, el slot es la identidad.

Tres filtros deciden qué llega a disco, en este orden, en los dos modos:
  - `save_conditions`: predicados sobre el dict de inferencia (ver
    image_collector_conditions). Lista vacía = pasa todo.
  - dedup por hash perceptual: descarta el frame casi idéntico a uno de los últimos
    guardados de esa cámara.
  - guardia de disco: por debajo de `min_free_space_gb` se hace lugar borrando lo más
    viejo y, si no alcanza, no se guarda.

Además cada cámara tiene su buffer circular de `max_images` guardados: al llegar al
tope se borra el más viejo —los dos PNG y el JSON— antes de escribir el nuevo.

Cuatro puntos del contrato que no se ven en las firmas:
  - `save_now()` escribe en el hilo del llamador y recién vuelve cuando el archivo
    está en disco. Es lo que permite pedir la captura justo mientras la luz está
    encendida, y es la razón de no llamarla por frame en un proyecto continuo: para
    eso está el modo interval.
  - En modo interval el frame se consume: cada vuelta guarda a lo sumo uno por cámara
    y una cámara que dejó de pushear no se guarda dos veces. El último push gana, así
    que conviene conectar un solo productor por cámara — el de la inferencia si se
    quieren el frame anotado y el JSON, el de la captura si alcanza el frame crudo.
  - `image_collector.enabled` se lee en cada guardado, así que prender y apagar la
    recolección no exige reiniciar. El modo, en cambio, se lee en `start()`: cambiarlo
    exige `stop()` + `start()`.
  - Las escrituras están serializadas entre sí, sin importar de qué hilo vengan, y
    `get_stats()` nunca espera a una: devuelve la última foto publicada.

Sin Qt: quien quiera mostrar las estadísticas en una UI las pide con `get_stats()`
y las publica desde donde corresponda.
"""

import json
import os
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import cv2
import numpy as np
import psutil

from system.config_manager import ConfigManager
from system.logger import logger
from system.paths import resolve

_MODE_ON_DEMAND = "on_demand"
_MODE_INTERVAL = "interval"
_MODES = (_MODE_ON_DEMAND, _MODE_INTERVAL)
# El modo sin hilo es el default: un modo mal escrito en el config no puede terminar
# guardando por reloj en un proyecto que captura con luz.
_DEFAULT_MODE = _MODE_ON_DEMAND

_DEFAULT_DATASET_PATH = "./data/dataset"
_DEFAULT_MAX_IMAGES = 10000               # por cámara
_DEFAULT_MIN_FREE_SPACE_GB = 5.0
_DEFAULT_DEDUP_HISTORY_LEN = 5            # guardados contra los que se compara el hash
_DEFAULT_HAMMING_THRESHOLD = 5            # 0–64 bits de diferencia; menos = más estricto
_DEFAULT_INTERVAL_MIN_S = 60.0
_DEFAULT_INTERVAL_MAX_S = 600.0
_MIN_INTERVAL_S = 1.0                     # piso: con 0 el hilo guardaría en cada vuelta

_DIR_ORIGINAL = "original"
_DIR_ANNOTATED = "annotated"
_DATE_FORMAT = "%Y-%m-%d"
_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S%f"     # ordena cronológicamente por nombre de archivo

_JSON_SCHEMA_VERSION = "1.0"
# 0–9. Bajo a propósito: el PNG es sin pérdida en cualquier nivel, así que comprimir
# más cuesta CPU del hilo que escribe y no cambia la imagen del dataset.
_PNG_COMPRESSION = 1

_GB = 1024 ** 3

_TICK_S = 0.5                             # tajada de espera; período de reacción a stop()
_STOP_TIMEOUT_S = 5.0                     # espera al hilo al detenerlo
# Estos avisos salen desde el camino que corre por frame: sin techo, un disco lleno
# escribe una línea por captura.
_WARN_PERIOD_S = 10.0
# Tope de borrados en un mismo guardado: hacer lugar no puede convertirse en un
# barrido del dataset entero con la captura esperando.
_MAX_EVICTIONS_PER_SAVE = 32

# Hash perceptual: la DCT se calcula sobre la miniatura y se queda con el bloque de
# bajas frecuencias, que es lo que sobrevive al ruido del sensor.
_PHASH_SIZE_PX = 32
_PHASH_BLOCK = 8


# ── Hash perceptual ──────────────────────────────────────────────────────────

def _compute_phash(frame: np.ndarray) -> int:
    """
    Hash perceptual de 64 bits basado en DCT, empaquetado en un entero.

    Resiste el ruido del sensor y las derivas leves de iluminación; cambia cuando
    cambia la escena.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    small = cv2.resize(gray, (_PHASH_SIZE_PX, _PHASH_SIZE_PX),
                       interpolation=cv2.INTER_AREA).astype(np.float32)
    block = cv2.dct(small)[:_PHASH_BLOCK, :_PHASH_BLOCK]
    # La mediana se calcula sin el coeficiente DC ([0,0]): es el brillo medio de la
    # imagen y arrastraría la comparación de todos los demás bits.
    median = np.median(np.delete(block.flatten(), 0))

    bits = 0
    for bit in (block > median).flatten():
        bits = (bits << 1) | int(bit)
    return bits


def _hamming_distance(hash_a: int, hash_b: int) -> int:
    """Bits en los que difieren dos hashes perceptuales."""
    return bin(hash_a ^ hash_b).count("1")


# ── Helpers de disco ─────────────────────────────────────────────────────────

def _write_image(path: str, image: np.ndarray, params: list[int]) -> bool:
    """
    Escribe una imagen creando la carpeta si falta. False si no se pudo.

    imencode y escritura binaria en vez de cv2.imwrite: imwrite pasa la ruta por la
    codificación ANSI del sistema y en Windows devuelve False en silencio si el
    nombre del cliente o del proyecto tiene acentos.
    """
    extension = os.path.splitext(path)[1] or ".png"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ok, buf = cv2.imencode(extension, image, params)
        if not ok:
            logger.warning(f"[ImageCollector] No se pudo codificar {path}.")
            return False
        with open(path, "wb") as f:
            f.write(buf)
        return True
    except (OSError, cv2.error) as e:
        logger.error(f"[ImageCollector] Error escribiendo {path}: {e}")
        return False


def _get_free_space_gb(path: str) -> float | None:
    """
    GiB libres en la partición de `path`, o None si no se pudo medir.

    Sube por los padres hasta encontrar uno que exista: antes del primer guardado la
    carpeta del dataset todavía no está creada.
    """
    candidate = os.path.abspath(path)
    while not os.path.exists(candidate):
        parent = os.path.dirname(candidate)
        if parent == candidate:
            return None
        candidate = parent
    try:
        return psutil.disk_usage(candidate).free / _GB
    except OSError as e:
        logger.debug(f"[ImageCollector] No se pudo medir el espacio libre de {candidate}: {e}")
        return None


@dataclass
class _SaveJob:
    """
    Una captura completa y ya copiada: lo que se guarda como una sola unidad.

    Es también la entrada de la caché del modo interval, así que los dos modos
    escriben por el mismo camino.
    """

    camera_slot: str
    captured_at: datetime
    frame_bgr: np.ndarray | None
    annotated_bgr: np.ndarray | None
    inference: dict | None


class ImageCollector:
    """
    Escribe el dataset de imágenes en disco, por intervalo o a pedido.

    En modo interval levanta un hilo daemon propio y guarda lo que le hayan dejado
    con `push_frame()`. En modo on_demand no levanta nada y guarda cuando el llamador
    lo pide con `save_now()`, en el hilo del llamador. Las dos entradas son
    thread-safe y las escrituras se serializan entre sí.

    Lee su sección `image_collector` del config en el momento de usarla, así que
    prender la recolección o mover los topes no exige reiniciar; el modo se fija en
    `start()`.
    """

    def __init__(self, config_manager: ConfigManager,
                 save_conditions: list[Callable[[dict], bool]] | None = None):
        self._config = config_manager
        self._save_conditions = list(save_conditions or [])

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Último frame de cada cámara, esperando la vuelta del hilo (modo interval).
        self._latest: dict[str, _SaveJob] = {}
        self._latest_lock = threading.Lock()

        # Serializa las escrituras y protege lo que va con ellas: el buffer circular
        # de cada cámara y su historial de hashes. En on_demand pueden escribir dos
        # hilos de inferencia a la vez, así que no basta con el dueño único.
        self._write_lock = threading.Lock()
        self._file_buffers: dict[str, deque] = {}
        self._recent_hashes: dict[str, deque] = {}
        self._buffers_ready = False

        # Lo que publica get_stats(): se toca con su propio lock para que consultar el
        # estado no espere a una escritura.
        self._counters = {"saved": 0, "skipped": 0}
        self._image_counts: dict[str, int] = {}
        self._last_save_iso: str | None = None
        self._last_warn_s: dict[str, float] = {}
        self._stats_lock = threading.Lock()

    # ── API pública ──────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        """Modo configurado: 'interval' o 'on_demand'. Un valor desconocido cae en on_demand."""
        configured = str(self._config.get("image_collector.mode", _DEFAULT_MODE) or "")
        mode = configured.strip().lower()
        if mode not in _MODES:
            self._warn_throttled(
                "mode",
                f"Modo '{configured}' desconocido; se usa '{_DEFAULT_MODE}'. "
                f"Válidos: {', '.join(_MODES)}."
            )
            return _DEFAULT_MODE
        return mode

    @property
    def is_active(self) -> bool:
        """True si la recolección está habilitada en el config."""
        return bool(self._config.get("image_collector.enabled", False))

    @property
    def is_running(self) -> bool:
        """True si el hilo de intervalo está vivo. Siempre False en modo on_demand."""
        return self._thread is not None and self._thread.is_alive()

    def set_active(self, active: bool) -> bool:
        """
        Prende o apaga la recolección y lo persiste en el config.

        Devuelve False si el config no se pudo guardar; el cambio queda igual en
        memoria y tiene efecto en el guardado siguiente.
        """
        self._config.set("image_collector.enabled", active)
        logger.info(f"[ImageCollector] Recolección {'habilitada' if active else 'deshabilitada'}.")
        return self._config.save()

    def start(self):
        """
        Levanta el hilo de intervalo si el modo lo pide. Idempotente.

        En on_demand no hay hilo que levantar y la llamada solo lo deja asentado en el
        log. Arranca aunque `enabled` esté en false, porque se prende en caliente.
        """
        if self.is_running:
            return
        if self.mode != _MODE_INTERVAL:
            logger.info(
                "[ImageCollector] Modo on_demand: sin hilo propio, "
                "cada guardado lo pide el llamador con save_now()."
            )
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ImageCollector")
        self._thread.start()

    def stop(self):
        """Detiene el hilo de intervalo y descarta los frames en caché. Idempotente."""
        self._stop_event.set()
        with self._latest_lock:
            self._latest.clear()
        if self._thread is None:
            return
        self._thread.join(timeout=_STOP_TIMEOUT_S)
        if self._thread.is_alive():
            logger.warning("[ImageCollector] El hilo no terminó en el tiempo esperado.")
        self._thread = None

    def push_frame(self, camera_slot: str, frame_bgr: np.ndarray | None, *,
                   annotated_bgr: np.ndarray | None = None,
                   inference: dict | None = None):
        """
        Deja el último frame de esa cámara para que el hilo lo guarde. Modo interval.

        Thread-safe y barata: copia los frames y vuelve, sin tocar el disco. Cada
        llamada reemplaza a la anterior de esa cámara, así que el frame anotado y el
        JSON son siempre los de la captura que se está guardando, nunca los de una
        anterior — por eso conviene un solo productor por cámara.

        No guarda nada por sí sola: sin hilo de intervalo corriendo el frame se
        descarta. Para pedir un guardado puntual está `save_now()`.
        """
        if not self.is_active:
            return
        if not self.is_running:
            self._warn_throttled(
                "push_without_thread",
                "Llegó un push_frame sin hilo de intervalo corriendo: el frame se "
                "descarta. En modo on_demand el guardado se pide con save_now()."
            )
            return
        job = self._build_job(camera_slot, frame_bgr, annotated_bgr, inference)
        if job is None:
            return
        with self._latest_lock:
            self._latest[camera_slot] = job

    def save_now(self, camera_slot: str, frame_bgr: np.ndarray | None, *,
                 annotated_bgr: np.ndarray | None = None,
                 inference: dict | None = None) -> bool:
        """
        Guarda esa captura ahora y devuelve si algo llegó a disco. Modo on_demand.

        Escribe en el hilo del llamador: cuando vuelve, el archivo está en disco. Eso
        es lo que permite pedir la captura mientras la luz está encendida, y también
        lo que la hace inadecuada para llamarla por frame en un proyecto que infiere
        de continuo — ahí el modo interval no le cuesta nada al hilo de captura.

        `frame_bgr` es el frame de cámara y `annotated_bgr` el mismo frame con las
        anotaciones dibujadas: van en la misma llamada para que original/ y annotated/
        correspondan siempre a la misma captura. Los frames se copian acá; el dict de
        inferencia se guarda como vino, así que el llamador no lo modifica después.

        Devuelve False si la recolección está apagada, no vino nada que guardar, algún
        filtro descartó el frame o ninguna escritura tuvo éxito. Funciona en los dos
        modos: en interval es el guardado puntual que no espera la vuelta del hilo.
        """
        if not self.is_active:
            return False
        job = self._build_job(camera_slot, frame_bgr, annotated_bgr, inference)
        if job is None:
            return False
        return self._write(job)

    def get_stats(self) -> dict:
        """
        Estado de la recolección con claves estables:
          mode              : 'interval' | 'on_demand'
          active            : bool — `image_collector.enabled`
          running           : bool — hilo de intervalo vivo
          images            : {camera_slot: guardados en disco dentro del tope}
          saved / skipped   : guardados y descartados por filtro desde que se creó
          last_save_iso     : timestamp del último guardado, o None si todavía no hubo
          disk_free_gb      : GiB libres en la partición del dataset; -1.0 si no se midió
          disk_guard_active : True si el espacio libre está por debajo del mínimo

        `images` es la última foto publicada por quien escribió, no un conteo de
        archivos: leer el disco en cada consulta no vale lo que cuesta. No espera a la
        escritura en curso.
        """
        free_gb = _get_free_space_gb(self._get_dataset_path())
        min_free_gb = float(self._config.get("image_collector.min_free_space_gb",
                                             _DEFAULT_MIN_FREE_SPACE_GB))
        with self._stats_lock:
            counters = dict(self._counters)
            images = dict(self._image_counts)
            last_save_iso = self._last_save_iso
        return {
            "mode": self.mode,
            "active": self.is_active,
            "running": self.is_running,
            "images": images,
            "saved": counters["saved"],
            "skipped": counters["skipped"],
            "last_save_iso": last_save_iso,
            "disk_free_gb": -1.0 if free_gb is None else round(free_gb, 2),
            "disk_guard_active": free_gb is not None and free_gb < min_free_gb,
        }

    # ── Hilo de intervalo ────────────────────────────────────────────────────

    def _run(self):
        with self._write_lock:
            self._ensure_buffers_ready()
        logger.info(
            f"[ImageCollector] Modo interval. Dataset en {self._get_dataset_path()}; "
            f"recolección {'habilitada' if self.is_active else 'deshabilitada'}."
        )

        deadline_s = time.monotonic() + self._next_interval_s()
        while not self._stop_event.is_set():
            remaining_s = deadline_s - time.monotonic()
            if remaining_s > 0:
                # En tajadas para que stop() no espere el intervalo entero.
                if self._stop_event.wait(min(remaining_s, _TICK_S)):
                    break
                continue
            deadline_s = time.monotonic() + self._next_interval_s()
            self._save_pending()

        logger.info("[ImageCollector] Hilo de intervalo detenido.")

    def _next_interval_s(self) -> float:
        """
        Segundos hasta el próximo guardado, al azar dentro de la ventana configurada.

        El azar es lo que hace variado al dataset: un período fijo lo sincroniza con el
        ritmo del proceso y termina fotografiando siempre el mismo instante. Con
        `interval_min_s` igual a `interval_max_s` el guardado queda periódico.
        """
        min_s = float(self._config.get("image_collector.interval_min_s",
                                       _DEFAULT_INTERVAL_MIN_S))
        max_s = float(self._config.get("image_collector.interval_max_s",
                                       _DEFAULT_INTERVAL_MAX_S))
        if max_s < min_s:
            min_s, max_s = max_s, min_s
        return random.uniform(max(_MIN_INTERVAL_S, min_s), max(_MIN_INTERVAL_S, max_s))

    def _save_pending(self):
        """Guarda el último frame de cada cámara que haya pusheado algo desde la vuelta anterior."""
        with self._latest_lock:
            jobs = list(self._latest.values())
            self._latest.clear()

        if not jobs:
            logger.debug("[ImageCollector] Sin frames nuevos en esta vuelta.")
            return
        for job in jobs:
            try:
                self._write(job)
            except Exception as e:
                # Un guardado que falla no se lleva puesto el hilo ni a las otras cámaras.
                logger.error(f"[ImageCollector] Error guardando {job.camera_slot}: {e}")

    # ── Guardado ─────────────────────────────────────────────────────────────

    def _write(self, job: _SaveJob) -> bool:
        """
        Aplica los filtros y escribe lo que corresponda. True si algo llegó a disco.

        Único camino de escritura de los dos modos. El lock lo serializa todo: el
        buffer circular, el historial de hashes y la guardia de disco solo tienen
        sentido si no hay dos escrituras pisándose.
        """
        with self._write_lock:
            self._ensure_buffers_ready()

            if not self._should_save(job):
                self._count("skipped")
                return False

            save_original = bool(self._config.get("image_collector.save_original", True))
            save_annotated = bool(self._config.get("image_collector.save_annotated", False))
            save_json = bool(self._config.get("image_collector.save_inference_json", True))

            write_original = save_original and job.frame_bgr is not None
            write_annotated = save_annotated and job.annotated_bgr is not None
            write_json = save_json and bool(job.inference)
            if save_annotated and job.annotated_bgr is None:
                self._warn_throttled(
                    "no_annotated",
                    f"'save_annotated' está activo pero {job.camera_slot} no manda "
                    f"frame anotado.")
            if not (write_original or write_annotated or write_json):
                self._count("skipped")
                return False

            # El hash se calcula sobre el frame de cámara: mide el cambio de escena, no
            # el de las anotaciones dibujadas encima.
            frame_hash = self._compute_frame_hash(
                job.camera_slot,
                job.frame_bgr if job.frame_bgr is not None else job.annotated_bgr)
            if frame_hash is not None and self._is_duplicate(job.camera_slot, frame_hash):
                self._count("skipped")
                return False

            dataset_path = self._get_dataset_path()
            buf = self._file_buffers.setdefault(job.camera_slot, deque())
            if not self._make_room(job.camera_slot, buf, dataset_path):
                self._count("skipped")
                return False

            timestamp = job.captured_at.strftime(_TIMESTAMP_FORMAT)
            filename = f"{job.camera_slot}_{timestamp}"
            date_path = os.path.join(dataset_path, job.captured_at.strftime(_DATE_FORMAT))
            dir_annotated = os.path.join(date_path, _DIR_ANNOTATED, job.camera_slot)
            # La entrada del buffer apunta siempre a la ranura de original/, exista o no
            # ese archivo: es la clave con la que _evict_oldest reconstruye las tres rutas.
            canonical_path = os.path.join(date_path, _DIR_ORIGINAL, job.camera_slot,
                                          f"{filename}.png")

            params = [cv2.IMWRITE_PNG_COMPRESSION, _PNG_COMPRESSION]
            saved_any = False
            if write_original:
                saved_any |= _write_image(canonical_path, job.frame_bgr, params)
            if write_annotated:
                saved_any |= _write_image(os.path.join(dir_annotated, f"{filename}.png"),
                                          job.annotated_bgr, params)
            if write_json:
                saved_any |= self._write_inference_json(
                    os.path.join(dir_annotated, f"{filename}.json"), job)

            if not saved_any:
                self._count("skipped")
                return False

            buf.append(canonical_path)
            if frame_hash is not None:
                self._get_hash_history(job.camera_slot).append(frame_hash)
            self._count("saved")
            self._publish_save(job.captured_at)
            logger.debug(f"[ImageCollector] Guardado: {canonical_path}")
            return True

    def _ensure_buffers_ready(self):
        """
        Indexa el dataset la primera vez que se va a escribir. Se llama con el lock.

        En on_demand no hay hilo que lo haga al arrancar, y sin esto el tope de
        `max_images` no se aplicaría a lo que ya está en disco.
        """
        if self._buffers_ready:
            return
        self._file_buffers = self._rebuild_buffers(self._get_dataset_path())
        self._buffers_ready = True
        self._publish_image_counts()

    # ── Filtros ──────────────────────────────────────────────────────────────

    def _should_save(self, job: _SaveJob) -> bool:
        """
        True cuando todas las `save_conditions` se cumplen. Sin condiciones, siempre.

        Una condición que levanta excepción cuenta como no cumplida: es mejor perder
        el frame que llenar el dataset con lo que no se pudo filtrar.
        """
        inference = job.inference or {}
        for condition in self._save_conditions:
            name = getattr(condition, "__name__", repr(condition))
            try:
                if not condition(inference):
                    logger.debug(
                        f"[ImageCollector] {job.camera_slot} descartado por {name}.")
                    return False
            except Exception as e:
                logger.warning(
                    f"[ImageCollector] La condición {name} falló para {job.camera_slot}: "
                    f"{e}. Se descarta el frame."
                )
                return False
        return True

    def _compute_frame_hash(self, camera_slot: str, frame_bgr: np.ndarray | None) -> int | None:
        """Hash perceptual del frame, o None si el dedup está apagado o el cálculo falló."""
        if frame_bgr is None or not self._config.get("image_collector.dedup.enabled", False):
            return None
        try:
            return _compute_phash(frame_bgr)
        except cv2.error as e:
            logger.warning(f"[ImageCollector] No se pudo hashear {camera_slot}: {e}")
            return None

    def _is_duplicate(self, camera_slot: str, frame_hash: int) -> bool:
        """True si el frame es casi igual a alguno de los últimos guardados de esa cámara."""
        threshold = int(self._config.get("image_collector.dedup.hamming_threshold",
                                         _DEFAULT_HAMMING_THRESHOLD))
        for previous in self._get_hash_history(camera_slot):
            distance = _hamming_distance(frame_hash, previous)
            if distance <= threshold:
                logger.debug(
                    f"[ImageCollector] {camera_slot} descartado por duplicado "
                    f"(distancia {distance} ≤ {threshold})."
                )
                return True
        return False

    def _get_hash_history(self, camera_slot: str) -> deque:
        """Hashes de los últimos guardados de la cámara, con el largo que pide el config."""
        history_len = max(1, int(self._config.get("image_collector.dedup.history_len",
                                                  _DEFAULT_DEDUP_HISTORY_LEN)))
        history = self._recent_hashes.get(camera_slot)
        if history is None or history.maxlen != history_len:
            history = deque(history or (), maxlen=history_len)
            self._recent_hashes[camera_slot] = history
        return history

    # ── Buffer circular y espacio en disco ───────────────────────────────────

    def _rebuild_buffers(self, dataset_path: str) -> dict[str, deque]:
        """
        Reconstruye el buffer circular de cada cámara con lo que ya hay en disco.

        Cada guardado cuenta una vez, sin importar cuántas de sus tres variantes
        existan, y las entradas apuntan a la ranura de original/. El orden sale de
        ordenar los nombres: el timestamp del nombre es de ancho fijo, así que
        alfabético y cronológico son lo mismo.

        Si el config bajó `max_images` desde la corrida anterior, el exceso se borra acá.
        """
        max_images = int(self._config.get("image_collector.max_images", _DEFAULT_MAX_IMAGES))
        # camera_slot -> {nombre sin extensión: ruta canónica en original/}
        found: dict[str, dict[str, str]] = {}
        for root, _, files in os.walk(dataset_path):
            if os.path.basename(os.path.dirname(root)) not in (_DIR_ORIGINAL, _DIR_ANNOTATED):
                continue
            camera_slot = os.path.basename(root)
            date_path = os.path.dirname(os.path.dirname(root))
            for filename in files:
                stem, extension = os.path.splitext(filename)
                if extension not in (".png", ".json"):
                    continue
                canonical = os.path.join(date_path, _DIR_ORIGINAL, camera_slot, f"{stem}.png")
                found.setdefault(camera_slot, {}).setdefault(stem, canonical)

        buffers: dict[str, deque] = {}
        for camera_slot, path_by_stem in found.items():
            buf = deque(path_by_stem[stem] for stem in sorted(path_by_stem))
            while len(buf) > max_images and self._evict_oldest(buf):
                pass
            buffers[camera_slot] = buf

        if buffers:
            logger.info(
                "[ImageCollector] Dataset en disco: "
                + ", ".join(f"{slot}={len(buf)}/{max_images}"
                            for slot, buf in buffers.items())
            )
        return buffers

    def _make_room(self, camera_slot: str, buf: deque, dataset_path: str) -> bool:
        """
        Hace lugar para un guardado nuevo borrando lo más viejo de esa cámara.

        Respeta el tope de `max_images` y la guardia de espacio libre. Devuelve False
        si el disco sigue por debajo del mínimo y ya no queda nada que borrar: en ese
        caso el frame no se guarda.
        """
        max_images = int(self._config.get("image_collector.max_images", _DEFAULT_MAX_IMAGES))
        min_free_gb = float(self._config.get("image_collector.min_free_space_gb",
                                             _DEFAULT_MIN_FREE_SPACE_GB))
        evictions = 0
        while len(buf) >= max_images and evictions < _MAX_EVICTIONS_PER_SAVE:
            if not self._evict_oldest(buf):
                break
            evictions += 1

        free_gb = _get_free_space_gb(dataset_path)
        while free_gb is not None and free_gb < min_free_gb:
            if not buf or evictions >= _MAX_EVICTIONS_PER_SAVE or not self._evict_oldest(buf):
                self._warn_throttled(
                    "disk",
                    f"Quedan {free_gb:.2f} GB libres, por debajo del mínimo de "
                    f"{min_free_gb:.2f} GB: no se guarda {camera_slot}."
                )
                return False
            evictions += 1
            free_gb = _get_free_space_gb(dataset_path)
        return True

    def _evict_oldest(self, buf: deque) -> bool:
        """
        Borra el guardado más viejo de un buffer: los dos PNG y el JSON.

        Devuelve False si algún borrado falló; en ese caso la entrada vuelve al buffer
        para no perderle el rastro al archivo que quedó en disco.
        """
        oldest = buf.popleft()
        # oldest = {dataset}/{fecha}/original/{camera_slot}/{archivo}.png
        filename = os.path.basename(oldest)
        camera_slot = os.path.basename(os.path.dirname(oldest))
        date_path = os.path.dirname(os.path.dirname(os.path.dirname(oldest)))
        json_name = f"{os.path.splitext(filename)[0]}.json"

        paths = (
            os.path.join(date_path, _DIR_ORIGINAL, camera_slot, filename),
            os.path.join(date_path, _DIR_ANNOTATED, camera_slot, filename),
            os.path.join(date_path, _DIR_ANNOTATED, camera_slot, json_name),
        )
        for path in paths:
            if not os.path.isfile(path):
                continue
            try:
                os.remove(path)
                logger.debug(f"[ImageCollector] Borrado: {path}")
            except OSError as e:
                logger.warning(f"[ImageCollector] No se pudo borrar {path}: {e}")
                buf.appendleft(oldest)
                return False
        return True

    # ── Internos ─────────────────────────────────────────────────────────────

    def _build_job(self, camera_slot: str, frame_bgr: np.ndarray | None,
                   annotated_bgr: np.ndarray | None, inference: dict | None) -> _SaveJob | None:
        """
        Copia la captura en un job propio, o None si no vino nada que guardar.

        Los frames se copian acá y no en la escritura: el productor reutiliza sus
        buffers en cuanto vuelve de la llamada.
        """
        if frame_bgr is None and annotated_bgr is None and not inference:
            return None
        return _SaveJob(
            camera_slot=camera_slot,
            captured_at=datetime.now(),
            frame_bgr=None if frame_bgr is None else frame_bgr.copy(),
            annotated_bgr=None if annotated_bgr is None else annotated_bgr.copy(),
            inference=dict(inference) if inference else None,
        )

    def _get_dataset_path(self) -> str:
        """Ruta absoluta del dataset; las relativas se anclan a la raíz del repo."""
        return resolve(self._config.get("system.paths.dataset", None), _DEFAULT_DATASET_PATH)

    def _write_inference_json(self, json_path: str, job: _SaveJob) -> bool:
        """Escribe la metadata de inferencia al lado del frame anotado. False si no se pudo."""
        payload = {
            "schema_version": _JSON_SCHEMA_VERSION,
            "camera_slot": job.camera_slot,
            "timestamp_iso": job.captured_at.isoformat(),
            "inference": job.inference,
        }
        try:
            os.makedirs(os.path.dirname(json_path), exist_ok=True)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            return True
        except (OSError, TypeError) as e:
            logger.warning(f"[ImageCollector] No se pudo escribir {json_path}: {e}")
            return False

    def _publish_save(self, captured_at: datetime):
        """Publica el conteo por cámara y la marca del último guardado para `get_stats()`."""
        counts = {slot: len(buf) for slot, buf in self._file_buffers.items()}
        timestamp_iso = captured_at.isoformat()
        with self._stats_lock:
            self._image_counts = counts
            self._last_save_iso = timestamp_iso

    def _publish_image_counts(self):
        """Deja la foto de los buffers donde `get_stats()` la pueda leer sin esperar."""
        counts = {slot: len(buf) for slot, buf in self._file_buffers.items()}
        with self._stats_lock:
            self._image_counts = counts

    def _count(self, key: str):
        with self._stats_lock:
            self._counters[key] += 1

    def _warn_throttled(self, key: str, message: str):
        """Avisa como máximo una vez cada `_WARN_PERIOD_S` por clave."""
        now_s = time.monotonic()
        with self._stats_lock:
            last_s = self._last_warn_s.get(key, 0.0)
            if now_s - last_s < _WARN_PERIOD_S:
                return
            self._last_warn_s[key] = now_s
        logger.warning(f"[ImageCollector] {message}")
