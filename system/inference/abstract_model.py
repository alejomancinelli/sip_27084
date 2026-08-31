"""
Contrato de un modelo de inferencia.

Maquinaria genérica: reutilizable entre proyectos. Este módulo no importa ningún
framework —ni torch, ni tensorflow, ni onnxruntime—: define qué tiene que ofrecer un
modelo para que el pipeline y el motor no sepan con qué se entrenó.

Ciclo de vida, en este orden y desde un solo hilo:

    load()                        carga los pesos y deja el resultado en `status`
    predict(frame_bgr, previous)  devuelve las detecciones de ese frame
    unload()                      libera memoria y VRAM; idempotente

Qué fija el contrato:
  - **`load()` no propaga errores.** Un framework que falta o unos pesos que no están
    dejan el modelo en STATUS_ERROR con el motivo, y `predict()` devuelve una lista
    vacía. La app arranca igual y el motivo viaja en `get_status()`.
  - **La tarea es de la implementación, no del archivo de pesos.** Cada clase concreta
    declara en `task` qué produce —clasificación, detección, segmentación—, porque de eso
    depende cómo se decodifica la salida. El archivo no se olfatea: adivinar la tarea es
    justamente lo que hace que un modelo mal elegido devuelva números creíbles en vez de
    fallar. Cuando el runtime sí sabe qué exportó —un `.engine` de ultralytics trae la
    tarea en su metadata—, `load()` lo confronta con `_verify_task()` y un desacuerdo
    deja el modelo en error antes del primer frame, no adentro del postproceso.
  - **Coordenadas en el espacio del frame recibido.** Si la implementación redimensiona
    para que entre en la VRAM —`max_side_px`—, mapea las detecciones de vuelta antes de
    devolverlas: quien llama nunca ve la escala interna.
  - **El umbral de confianza lo aplica el modelo**, no el llamador: una detección por
    debajo de `min_confidence_pct` no sale de `predict()`. Es el umbral de descarte por
    detección, y en un proyecto que cuenta cientos de objetos va bajo a propósito; la
    confianza del resultado completo la promedia `analysis` y la juzga el motor.
  - **Los nombres de clase salen del config**, indexados por la clase que devuelve el
    framework. Ningún nombre de clase aparece como identificador en el código.
  - **`predict()` no dibuja nada.** El overlay es de `overlay.py`.
  - **`predict()` recibe las detecciones de la etapa anterior** y decide si recorta por
    ahí (detección → clasificación, detección → segmentación) o si las ignora y mira el
    frame completo. Ignorarlas es lo que hace que dos etapas corran independientes.
  - **`predict()` no sabe de qué cámara es el frame**, y es a propósito: el modelo es uno
    por slot y lo comparten todas las cámaras del pipeline, así que nada calibrado por
    montaje —una escala de píxel, un filtro de tamaño, una clase que sale de la medida—
    puede resolverse acá. Eso va en el `classifier` del motor, que sí recibe el slot.

Cada modelo lee su sección `inference.models.<slot>` del config.yaml. El slot es su
identidad: es el nombre con el que el pipeline lo pide y con el que aparece en
`stage_times_ms` del resultado.

Agregar un modelo es implementar esta interfaz y sumar una línea a `model_factory`.
Ningún otro archivo se toca.
"""

from abc import ABC, abstractmethod

import numpy as np

from system.config_manager import ConfigManager
from system.logger import logger
from system.paths import resolve

from .result import Detection

# Vocabulario de `status`, para que la UI y los tests no repitan los literales.
STATUS_UNLOADED = "unloaded"    # todavía no se llamó a load(), o ya se descargó
STATUS_LOADED = "loaded"        # listo para inferir
STATUS_ERROR = "error"          # quiso cargar y no pudo; el motivo está en `error`

# Vocabulario de `task`: qué produce el modelo, que es lo que decide cómo se decodifica
# su salida. Es de la implementación concreta, no del config.
TASK_CLASSIFICATION = "classification"   # un veredicto del frame, sin forma ni posición
TASK_DETECTION = "detection"             # bbox por instancia
TASK_SEGMENTATION = "segmentation"       # bbox y máscara por instancia
TASK_KEYPOINTS = "keypoints"             # bbox y puntos por instancia
TASKS = (TASK_CLASSIFICATION, TASK_DETECTION, TASK_SEGMENTATION, TASK_KEYPOINTS)

# Nombres con los que otros runtimes llaman a la misma tarea. Están acá y no en cada
# implementación para que el chequeo entienda lo que le informe el framework.
_TASK_ALIASES = {
    "classify": TASK_CLASSIFICATION, "cls": TASK_CLASSIFICATION,
    "detect": TASK_DETECTION, "det": TASK_DETECTION, "obb": TASK_DETECTION,
    "segment": TASK_SEGMENTATION, "seg": TASK_SEGMENTATION,
    "pose": TASK_KEYPOINTS, "keypoint": TASK_KEYPOINTS,
}

_DEFAULT_MIN_CONFIDENCE_PCT = 50.0


def normalize_task(name: str) -> str:
    """
    Traduce el nombre de tarea de un framework al vocabulario TASK_* de este módulo.

    Devuelve '' cuando no lo reconoce, que es distinto de un desacuerdo: un `.pt` de
    torch pelado no informa ninguna tarea y eso no es un error.
    """
    candidate = str(name or "").strip().lower()
    if candidate in TASKS:
        return candidate
    return _TASK_ALIASES.get(candidate, "")


class AbstractModel(ABC):
    """
    Contrato de todo modelo de inferencia: ciclo de vida, detecciones y estado legible
    desde afuera.

    Una instancia por slot de `inference.models`. La subclase pone el framework; de acá
    salen la lectura de su sección del config, el vocabulario de status y los nombres
    de clase.
    """

    # Marcadores de la implementación concreta: el consumidor los lee sin saber qué
    # modelo le tocó. Cada implementación a la que apliquen los pisa en True.
    is_synthetic = False
    is_config_error = False

    # Qué produce la implementación: uno de los TASK_* de este módulo. Vacío sólo en las
    # que no decodifican nada, como NullModel.
    task = ""

    def __init__(self, config_manager: ConfigManager, model_slot: str):
        self.model_slot = model_slot
        self._config = config_manager
        self._status = STATUS_UNLOADED
        self._error: str | None = None

    # ── Estado ───────────────────────────────────────────────────────────────

    @property
    def status(self) -> str:
        """Estado del modelo: uno de los `STATUS_*` de este módulo."""
        return self._status

    @property
    def is_loaded(self) -> bool:
        return self._status == STATUS_LOADED

    @property
    def error(self) -> str | None:
        """Motivo por el que el modelo no puede inferir, o None si está sano."""
        return self._error

    def get_status(self) -> dict:
        """
        Estado del modelo con claves estables:

            model_slot : str  — su sección de `inference.models`
            task       : str  — vocabulario TASK_* de este módulo
            status     : str  — vocabulario STATUS_* de este módulo
            loaded     : bool
            synthetic  : bool — las detecciones son sintéticas, no una medición
            error      : str | None
        """
        return {
            "model_slot": self.model_slot,
            "task": self.task,
            "status": self._status,
            "loaded": self.is_loaded,
            "synthetic": self.is_synthetic,
            "error": self._error,
        }

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    @abstractmethod
    def load(self):
        """
        Carga los pesos y deja el resultado en `status`.

        No propaga errores y no bloquea a nadie más que a quien la llama. Llamarla dos
        veces no recarga nada.
        """

    @abstractmethod
    def predict(self, frame_bgr: np.ndarray,
                previous: list[Detection] | None = None) -> list[Detection]:
        """
        Devuelve las detecciones del frame, en coordenadas del frame que recibió.

        Sin modelo cargado devuelve una lista vacía en vez de levantar excepción.
        `previous` son las detecciones de la etapa anterior del pipeline, o None si es
        la primera: la implementación decide si recorta por ahí o las ignora.
        """

    @abstractmethod
    def unload(self):
        """Libera memoria y VRAM, y deja `status` en 'unloaded'. Idempotente."""

    # ── Configuración del modelo ─────────────────────────────────────────────

    @property
    def min_confidence_pct(self) -> float:
        """Confianza mínima de una detección para salir de `predict()`, 0–100."""
        return float(self._get_option("min_confidence_pct", _DEFAULT_MIN_CONFIDENCE_PCT))

    def _get_option(self, key: str, default: object = None) -> object:
        """Valor de `inference.models.<slot>.<key>`, con su default."""
        return self._config.get(f"inference.models.{self.model_slot}.{key}", default)

    def _get_model_path(self) -> str:
        """
        Ruta absoluta de los pesos, o '' si el config no declara ninguna.

        Las relativas se anclan a la raíz del repo. La cadena vacía se devuelve tal cual
        —y no resuelta— porque significa 'sin pesos propios': una implementación que baja
        un preentrenado la distingue así de una ruta que apunta a un archivo.
        """
        raw_path = str(self._get_option("path", "") or "").strip()
        return resolve(raw_path, "") if raw_path else ""

    def _get_max_side_px(self) -> int:
        """Lado mayor al que reducir antes de inferir; 0 = resolución nativa."""
        return max(0, int(self._get_option("max_side_px", 0) or 0))

    def _get_params(self) -> dict:
        """Extras propios del framework, tal como estén en el config."""
        return self._get_option("params", {}) or {}

    def _get_class_name(self, class_index: int) -> str:
        """
        Nombre de una clase según `class_names`, o 'class_<i>' si no está declarada.

        Los nombres son datos de configuración: no aparecen como identificadores en el
        código, ni acá ni en el fork.
        """
        class_names = self._get_option("class_names", []) or []
        if 0 <= class_index < len(class_names):
            return str(class_names[class_index])
        return f"class_{class_index}"

    # ── Guardas de load() ────────────────────────────────────────────────────

    def _verify_task(self, reported_task: str) -> bool:
        """
        Confronta la tarea que declara la implementación con la que informa el archivo.

        Se llama desde `load()`, con lo que haya dicho el runtime al abrir los pesos. Un
        desacuerdo deja el modelo en STATUS_ERROR y devuelve False: es un `.engine` de
        detección cargado como segmentador, y el postproceso lo haría fallar recién con
        el primer frame —o peor, devolvería números creíbles— en un equipo ya instalado.

        Devuelve True cuando no hay nada que confrontar: un archivo sin metadata, o una
        implementación sin `task` declarada. No poder verificar no es un desacuerdo.
        """
        reported = normalize_task(reported_task)
        if not self.task or not reported:
            logger.debug(
                f"[Inference] Modelo '{self.model_slot}': sin metadata de tarea que "
                f"verificar (declarada '{self.task or 'ninguna'}')."
            )
            return True
        if reported == self.task:
            return True
        self._set_error(
            f"Los pesos son de '{reported}' y el modelo los carga como '{self.task}'. "
            f"Revisar 'type' y 'path' en 'inference.models.{self.model_slot}'."
        )
        logger.error(f"[Inference] Modelo '{self.model_slot}': {self._error}")
        return False

    def _set_loaded(self):
        self._status = STATUS_LOADED
        self._error = None

    def _set_error(self, reason: str):
        self._status = STATUS_ERROR
        self._error = reason
