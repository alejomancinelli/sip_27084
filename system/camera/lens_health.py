"""
Detección de lente sucio — varianza del Laplaciano sobre el ROI de la cámara.

El polvo sobre el vidrio suaviza la escena. La varianza del Laplaciano mide cuánto
detalle de alta frecuencia tiene la imagen, así que un lente sucio la hace caer.

## La idea que hace que esto sea simple

    LA NITIDEZ ES UN TECHO, NO UN PROMEDIO.

Sin importar qué haya delante de la cámara, la varianza nunca puede superar lo que la
óptica permite: el contenido sólo la empuja hacia abajo —una cinta vacía es más lisa que
un tramo cargado—. Entonces no hace falta saber qué se está mirando, alcanza con el
máximo de una ventana larga:

    nitidez = MÁXIMO de la varianza medida en las últimas N horas
    alarma  = nitidez < umbral

Por eso `SharpnessWindow` vive acá y no del lado de quien cablea: la ventana no es un
detalle del cableado, es el método.

Hay un solo número que calibrar —la varianza con el vidrio limpio— y lo arma
`summarize_calibration()` con N muestras. Sin ese número el detector queda inerte y lo
declara con `STATE_UNAVAILABLE`, que no es lo mismo que decir que el lente está limpio.

**Todo eso es por cámara, así que el punto de entrada es `LensHealthMonitor`**, que
guarda una ventana y una referencia por `camera_slot`. Compartirlas entre cámaras no da un
error, da un equipo que nunca avisa: el máximo es un máximo, y un solo vidrio limpio
levantaría el de todas. Las funciones sueltas de acá abajo son los primitivos —puros y
testeables de a uno— sobre los que el monitor está armado.

El veredicto sale como estado propio; quien cablea lo traduce al bit de lente sucio de
`system/formats/camera_health.py`. Acá no se corre ningún bit.

Módulo puro: sin Qt, sin Modbus, sin ConfigManager, sin I/O.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import isfinite
from statistics import median, pstdev
from threading import Lock

import cv2
import numpy as np

# ── Veredicto de la óptica ───────────────────────────────────────────────────
STATE_OK = 0           # hay evidencia de que el vidrio está limpio
STATE_ALARM = 1        # la nitidez no llega al umbral: hay que limpiar
STATE_UNAVAILABLE = 2  # no se puede afirmar nada (sin calibrar, sin muestras o sin ventana)

# Techo del índice de nitidez publicado, en % de la varianza calibrada. Por encima de 100
# la imagen tiene más detalle que el día de la calibración, lo que es normal con otra
# carga delante; el techo sólo evita que un pico se vaya de escala.
SHARPNESS_MAX_PCT = 200

DEFAULT_ANALYSIS_WIDTH_PX = 960  # ancho al que se reduce el recorte antes de medir

# Condiciones de cámara con las que se calibró. Un cambio en cualquiera de ellas mueve la
# varianza sin que el vidrio se haya ensuciado.
CONDITION_KEYS = ("exposure_time_us", "gain", "rotation", "roi")

_ROI_KEYS = ("x_px", "y_px", "width_px", "height_px")

# Tolerancia al comparar exposición y ganancia: la cámara redondea lo que se le pide, así
# que una diferencia chica no es un cambio de condiciones.
_CONDITION_TOLERANCE_PCT = 2.0


# ── Región de análisis ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Region:
    """Rectángulo del frame sobre el que se mide, en píxeles del frame recibido."""

    x_px: int
    y_px: int
    width_px: int
    height_px: int
    from_roi: bool   # False = se midió el frame entero porque el ROI no está configurado

    def to_dict(self) -> dict:
        return {"x_px": self.x_px, "y_px": self.y_px,
                "width_px": self.width_px, "height_px": self.height_px}


def get_analysis_region(frame_bgr: np.ndarray | None, roi: dict | None) -> Region | None:
    """
    Región efectiva de análisis, o None si el frame no sirve para medir.

    Honra `enabled` y clampea contra las dimensiones del frame; un ancho o alto en 0 llega
    hasta el borde, igual que en el resto del sistema. Con el ROI apagado o de área nula
    devuelve el frame entero (`from_roi=False`): se sigue pudiendo medir, sólo que la
    región incluye estructura ajena a lo que se quiere vigilar.
    """
    if frame_bgr is None or getattr(frame_bgr, "ndim", 0) < 2:
        return None
    height_px, width_px = frame_bgr.shape[:2]
    if height_px <= 0 or width_px <= 0:
        return None

    roi = roi or {}
    if roi.get("enabled", False):
        # Clampeo: un ROI guardado con otra resolución de cámara no puede terminar en un
        # recorte vacío en silencio.
        x_px = max(0, min(int(roi.get("x_px", 0) or 0), width_px))
        y_px = max(0, min(int(roi.get("y_px", 0) or 0), height_px))
        roi_width_px = int(roi.get("width_px", 0) or 0) or (width_px - x_px)
        roi_height_px = int(roi.get("height_px", 0) or 0) or (height_px - y_px)
        roi_width_px = min(roi_width_px, width_px - x_px)
        roi_height_px = min(roi_height_px, height_px - y_px)
        if roi_width_px > 0 and roi_height_px > 0:
            return Region(x_px, y_px, roi_width_px, roi_height_px, True)

    return Region(0, 0, width_px, height_px, False)


# ── Medición ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Measurement:
    variance: float      # varianza del Laplaciano; sólo comparable contra la calibrada
    luma: float          # nivel de gris medio de la región, 0–255
    region: Region
    width_px: int        # tamaño al que se midió, ya reducido
    height_px: int


def measure(frame_bgr: np.ndarray | None, roi: dict | None, *,
            analysis_width_px: int = DEFAULT_ANALYSIS_WIDTH_PX) -> Measurement | None:
    """
    Varianza del Laplaciano y luminancia media de la región. None si el frame no sirve.

    Reduce a `analysis_width_px` antes de medir para que el costo no dependa de la
    resolución de la cámara. La varianza **cambia con la escala**, así que la referencia
    calibrada sólo vale mientras ese ancho no se toque: dos equipos que quieran comparar
    números tienen que medir con el mismo.
    """
    region = get_analysis_region(frame_bgr, roi)
    if region is None:
        return None

    crop = frame_bgr[region.y_px:region.y_px + region.height_px,
                     region.x_px:region.x_px + region.width_px]
    if crop.size == 0:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    if analysis_width_px and 0 < analysis_width_px < gray.shape[1]:
        scale = analysis_width_px / gray.shape[1]
        gray = cv2.resize(gray,
                          (int(analysis_width_px), max(1, int(round(gray.shape[0] * scale)))),
                          interpolation=cv2.INTER_AREA)

    laplacian = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    return Measurement(
        variance=float(laplacian.var()),
        luma=float(gray.mean()),
        region=region,
        width_px=int(gray.shape[1]),
        height_px=int(gray.shape[0]),
    )


def get_sharpness_pct(variance: float | None, reference_variance: float) -> int:
    """Nitidez como % de la varianza calibrada, clampeada a 0–SHARPNESS_MAX_PCT."""
    if variance is None or reference_variance <= 0:
        return 0
    return max(0, min(SHARPNESS_MAX_PCT,
                      int(round(100.0 * variance / reference_variance))))


# ── Ventana de nitidez ───────────────────────────────────────────────────────

class SharpnessWindow:
    """
    Máximo de la varianza medida en los últimos `window_s` segundos.

    Evicta por TIEMPO y no por cantidad: si la captura se muere la ventana se vacía sola y
    `get_max()` vuelve a None, en vez de congelarse con el último valor bueno y sostener
    un "limpio" que ya nadie está midiendo.

    `discard_expired()` se llama en **cada** ciclo, haya muestra o no: es lo que hace que
    una cámara caída se note. `is_complete()` dice si ya pasó una ventana entera desde el
    primer ciclo, que es lo que separa "todavía no puedo afirmarlo" de "está sucio".

    **Una ventana por cámara.** Es el primitivo de una sola óptica y no se comparte: ver
    `LensHealthMonitor`, que es lo que hay que usar con más de una cámara. Tampoco es
    thread-safe; el que da esa garantía es el monitor.
    """

    def __init__(self, window_s: float):
        self.window_s = float(window_s)
        self._samples: deque = deque()   # [(timestamp_s, variance)] en orden creciente
        self._started_s: float | None = None

    def discard_expired(self, now_s: float):
        """Envejece la ventana sin agregar muestra."""
        if self._started_s is None:
            self._started_s = now_s
        cutoff_s = now_s - self.window_s
        while self._samples and self._samples[0][0] <= cutoff_s:
            self._samples.popleft()

    def add(self, now_s: float, variance: float):
        """Agrega una muestra y envejece la ventana. Ignora NaN e infinito."""
        value = float(variance)
        if not isfinite(value):
            return
        self._samples.append((now_s, value))
        self.discard_expired(now_s)

    def get_count(self) -> int:
        return len(self._samples)

    def get_max(self) -> float | None:
        """Varianza más alta de la ventana, o None si está vacía."""
        if not self._samples:
            return None
        return max(value for _, value in self._samples)

    def is_complete(self, now_s: float) -> bool:
        """True cuando pasó una ventana entera desde el primer ciclo."""
        if self._started_s is None:
            return False
        return (now_s - self._started_s) >= self.window_s


# ── Estado ───────────────────────────────────────────────────────────────────

def get_state(max_variance: float | None, reference_variance: float,
              threshold_pct: float, window_complete: bool) -> int:
    """
    Veredicto de la óptica a partir del máximo de la ventana.

    El orden importa: la evidencia positiva no espera a que la ventana se cumpla, porque
    un solo frame nítido ya demuestra que el vidrio deja pasar el detalle. Lo que sí
    necesita la ventana entera es la afirmación contraria: sin haber mirado el período
    completo, una racha de imágenes lisas es una cinta vacía tanto como un lente sucio.
    """
    if reference_variance <= 0:
        return STATE_UNAVAILABLE     # sin calibrar: la función está inerte y lo declara
    if max_variance is not None and max_variance >= reference_variance * threshold_pct / 100.0:
        return STATE_OK
    if max_variance is None:
        return STATE_UNAVAILABLE     # sin muestras: la cámara no está entregando frames
    if not window_complete:
        return STATE_UNAVAILABLE     # todavía no se puede afirmar
    return STATE_ALARM


# ── Calibración ──────────────────────────────────────────────────────────────

def summarize_calibration(variances: list[float], lumas: list[float]) -> dict | None:
    """
    Referencia de lente limpio a partir de N muestras. None si no llegó ninguna.

    Mediana y no media: durante la calibración pasa material por delante, y un frame
    atípico no puede mover el número contra el que se va a comparar durante meses.
    `dispersion_pct` no invalida nada; es para que quien calibra vea si las muestras se
    parecen entre sí antes de aceptar el resultado.
    """
    if not variances:
        return None
    reference_variance = float(median(variances))
    dispersion_pct = 0.0
    if len(variances) > 1 and reference_variance > 0:
        dispersion_pct = 100.0 * float(pstdev(variances)) / reference_variance
    return {
        "variance": round(reference_variance, 2),
        "luma": round(float(median(lumas)), 2) if lumas else 0.0,
        "dispersion_pct": round(dispersion_pct, 1),
        "sample_count": len(variances),
    }


# ── Condiciones de la referencia (sólo aviso, no invalidan) ──────────────────

def get_current_conditions(acquisition: dict | None, rotation, roi: dict | None) -> dict:
    """
    Condiciones de cámara con las que se está midiendo ahora, para guardarlas junto a la
    referencia y compararlas después.
    """
    acquisition = acquisition or {}
    roi = roi or {}
    return {
        "exposure_time_us": int(acquisition.get("exposure_time_us", 0) or 0),
        "gain": float(acquisition.get("gain", 0.0) or 0.0),
        "rotation": rotation,
        "roi": {key: int(roi.get(key, 0) or 0) for key in _ROI_KEYS},
    }


def get_mismatch_reason(saved: dict | None, current: dict | None) -> str:
    """
    Texto del primer desajuste entre las condiciones calibradas y las actuales, o ''.

    Es un aviso y no una invalidación: la referencia se sigue usando. Bajar el detector a
    "no disponible" porque alguien tocó la ganancia dejaría al equipo sin vigilancia de la
    óptica justo cuando se estuvo metiendo mano, que es cuando más falta hace. Lo que
    corresponde es decir por qué el número puede haberse movido y que quien mira decida si
    recalibra.
    """
    saved, current = saved or {}, current or {}
    if not saved:
        return ""
    for key in CONDITION_KEYS:
        if key not in saved or key not in current:
            continue
        reference, actual = saved[key], current[key]
        if key == "roi":
            if _get_roi_key(reference) != _get_roi_key(actual):
                return f"cambió el ROI: {_format_roi(reference)} → {_format_roi(actual)}"
            continue
        if key in ("exposure_time_us", "gain"):
            try:
                tolerance = max(_CONDITION_TOLERANCE_PCT / 100.0 * abs(float(reference)), 1e-9)
                if abs(float(actual) - float(reference)) > tolerance:
                    return f"cambió {key}: {_format_number(reference)} → {_format_number(actual)}"
                continue
            except (TypeError, ValueError):
                pass
        if reference != actual:
            return f"cambió {key}: {reference} → {actual}"
    return ""


def _get_roi_key(roi) -> tuple:
    roi = roi if isinstance(roi, dict) else {}
    return tuple(int(roi.get(key, 0) or 0) for key in _ROI_KEYS)


def _format_roi(roi) -> str:
    x_px, y_px, width_px, height_px = _get_roi_key(roi)
    return f"{width_px}x{height_px}+{x_px}+{y_px}"


def _format_number(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


# ── N cámaras ────────────────────────────────────────────────────────────────

@dataclass
class _CameraState:
    """Todo lo que este detector sabe de UNA óptica."""

    window: SharpnessWindow
    reference_variance: float = 0.0          # 0 = sin calibrar
    last_measurement: Measurement | None = None


class LensHealthMonitor:
    """
    Salud de la óptica de N cámaras: una ventana y una referencia por slot.

    **Este es el punto de entrada con más de una cámara, y la razón es que compartir el
    estado falla en silencio.** Cada pieza de lo de arriba es por óptica:

      - La **ventana**: es un máximo, así que una sola cámara con el vidrio limpio
        levantaría el máximo de todas y la alarma no saltaría nunca. Nadie vería un error;
        el equipo diría "limpio" para siempre.
      - La **referencia**: la varianza con el vidrio limpio depende del lente, de la
        exposición y de lo que la cámara tenga enfrente. Un número compartido entre dos
        montajes distintos no compara nada.
      - Las **muestras de calibración**: mezclar las de dos cámaras da una mediana que no
        describe a ninguna.

    Por eso el estado se guarda por `camera_slot` y no hay forma de pedirlo sin decir de
    qué cámara se habla. El slot es la misma clave que en el resto del sistema.

    Thread-safe: un fork puede alimentarlo desde el hilo del ciclo de salud o desde varios
    hilos de inferencia —uno por pipeline— sin coordinarse. La medición, que es lo caro,
    corre FUERA del lock; adentro sólo queda tocar la ventana del slot.
    """

    def __init__(self, *, window_s: float, threshold_pct: float,
                 analysis_width_px: int = DEFAULT_ANALYSIS_WIDTH_PX):
        self.window_s = float(window_s)
        self.threshold_pct = float(threshold_pct)
        self.analysis_width_px = int(analysis_width_px)
        self._cameras: dict[str, _CameraState] = {}
        self._lock = Lock()

    # ── API pública ──────────────────────────────────────────────────────────

    def set_reference(self, camera_slot: str, reference_variance: float):
        """
        Varianza calibrada de ESE lente, la que sale de `summarize_calibration()`.

        0 o negativo = sin calibrar, que deja a esa cámara en `STATE_UNAVAILABLE` sin
        afectar a las demás: se calibra una por una y las que ya lo están siguen vigiladas.
        """
        with self._lock:
            self._get_camera(camera_slot).reference_variance = max(
                0.0, float(reference_variance or 0.0))

    def update(self, camera_slot: str, frame_bgr: np.ndarray | None, roi: dict | None, *,
               now_s: float) -> Measurement | None:
        """
        Mide el frame de esa cámara y lo agrega a SU ventana. Devuelve la medición, o None
        si el frame no servía.

        Envejece la ventana del slot aunque no haya medición, así que llamarlo por ciclo
        para cada cámara configurada alcanza: una cámara caída vacía su ventana sola y pasa
        a `STATE_UNAVAILABLE` en vez de quedarse con el último veredicto bueno.

        Se llama en el ciclo de salud y no por frame: medir cuesta milisegundos por cámara
        y la ventana es de horas, así que un intervalo de segundos ya la llena de sobra.

        Una excepción de OpenCV sobre un frame con un dtype inesperado se propaga: quien
        cablea la registra y sigue, como con cualquier otra medición que falla.
        """
        with self._lock:
            camera = self._get_camera(camera_slot)
            camera.window.discard_expired(now_s)

        # Fuera del lock: es la parte cara y no toca estado compartido.
        measurement = measure(frame_bgr, roi, analysis_width_px=self.analysis_width_px)

        if measurement is not None:
            with self._lock:
                camera.window.add(now_s, measurement.variance)
                camera.last_measurement = measurement
        return measurement

    def discard_expired(self, now_s: float):
        """
        Envejece las ventanas de TODAS las cámaras conocidas, sin medir.

        `update()` ya envejece la del slot que recibe. Esto es para el ciclo que corre
        aunque no haya frames que ofrecer: sin él, una cámara que dejó de entregar y a la
        que nadie llama más sostendría su último máximo para siempre.
        """
        with self._lock:
            for camera in self._cameras.values():
                camera.window.discard_expired(now_s)

    def get_state(self, camera_slot: str, *, now_s: float) -> int:
        """Veredicto de esa cámara. Una que nunca se midió es `STATE_UNAVAILABLE`."""
        with self._lock:
            camera = self._cameras.get(camera_slot)
            if camera is None:
                return STATE_UNAVAILABLE
            return get_state(camera.window.get_max(), camera.reference_variance,
                             self.threshold_pct, camera.window.is_complete(now_s))

    def get_status(self, camera_slot: str, *, now_s: float) -> dict:
        """
        Estado completo de esa cámara, con claves estables, para la UI y la telemetría.

        `variance` y `luma` son de la última medición que salió bien y pueden quedar
        rancias si la cámara se cayó; el que dice si se puede confiar es `state`, que sale
        de la ventana y sí se vacía sola.
        """
        with self._lock:
            camera = self._cameras.get(camera_slot)
            if camera is None:
                return self._build_empty_status()
            last = camera.last_measurement
            reference_variance = camera.reference_variance
            max_variance = camera.window.get_max()
            return {
                "state": get_state(max_variance, reference_variance, self.threshold_pct,
                                   camera.window.is_complete(now_s)),
                "sharpness_pct": (None if last is None
                                  else get_sharpness_pct(last.variance, reference_variance)),
                "sharpness_max_pct": get_sharpness_pct(max_variance, reference_variance),
                "variance": None if last is None else last.variance,
                "max_variance": max_variance,
                "luma": None if last is None else last.luma,
                "region": None if last is None else last.region.to_dict(),
                "reference_variance": reference_variance,
                "sample_count": camera.window.get_count(),
                "window_complete": camera.window.is_complete(now_s),
            }

    def get_slots(self) -> list[str]:
        """Cámaras de las que el monitor ya sabe algo, en el orden en que aparecieron."""
        with self._lock:
            return list(self._cameras)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _get_camera(self, camera_slot: str) -> _CameraState:
        """Estado de ese slot, creándolo la primera vez. Llamar con el lock tomado."""
        camera = self._cameras.get(camera_slot)
        if camera is None:
            camera = _CameraState(window=SharpnessWindow(self.window_s))
            self._cameras[camera_slot] = camera
        return camera

    def _build_empty_status(self) -> dict:
        """Estado de una cámara de la que todavía no se sabe nada: mismas claves."""
        return {
            "state": STATE_UNAVAILABLE,
            "sharpness_pct": None,
            "sharpness_max_pct": 0,
            "variance": None,
            "max_variance": None,
            "luma": None,
            "region": None,
            "reference_variance": 0.0,
            "sample_count": 0,
            "window_complete": False,
        }
