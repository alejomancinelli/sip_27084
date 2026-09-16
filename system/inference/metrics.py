"""
Métricas del proceso: qué significan las detecciones en la cinta de pellet.

Módulo puro: sin Qt, sin ConfigManager y sin I/O.

**Específico del proyecto — no cross-portar tal cual.** Acá está la cuenta de esta
instalación: cuánto de lo que pasa por la cinta es pellet entero y cuánto viene
desmenuzado, y cuán cargada está la cinta.

Lo que se mide es **superficie de píxeles segmentados**, en dos ejes que tienen
denominadores distintos a propósito:

    pct_<clase>        superficie de la clase sobre el área del FRAME COMPLETO
    pct_fondo          el complemento: cinta a la vista, que el modelo no detecta
    pct_carga          todo lo detectado sobre el área del ROI DE CINTA; pasa de 100 %
                       cuando hay desborde, y por eso no está acotado
    pct_<clase>_norm   la composición: entre las clases detectadas, suman 100 %

El reparto se cuenta por **unión y no por suma de instancias**: un píxel es pellet, o es
desmenuzado, o es cinta, y no puede ser dos cosas. Sumar el área de cada detección contaría
dos veces lo que dos polígonos se pisan e inflaría la carga sin que pase nada en la cinta.
Por eso las máscaras se rasterizan sobre un lienzo de etiquetas antes de contar, y donde
dos se superponen gana la última —el mismo criterio con el que se dibuja el overlay—.

Sobre los nombres de las claves: son las que ya consumen el mapa de registros y el
dashboard, así que **no se renombran**. `pct_fondo` y `pct_carga` quedaron en castellano de
la primera versión del proyecto; el resto se arma con el nombre de clase del config. Cuál
es cuál está en `docs/influxdb.md` y en `docs/modbus_map.md`.
"""

from collections.abc import Callable

import cv2
import numpy as np

from .result import InferenceResult

# Claves fijas: no salen de ningún nombre de clase, así que se declaran una vez.
FRAME_FRACTION_KEY = "pct_fondo"        # cinta a la vista: 100 % menos lo detectado
LOAD_KEY = "pct_carga"                  # carga respecto del ROI de cinta

_PCT = 100.0


def build_analyzer(*, class_names: list, belt_roi_px: dict,
                   dark_background_threshold: dict) -> Callable:
    """
    Devuelve el analyzer con los parámetros de la planta ya leídos.

    Los tres salen de `main.py`, que es el único que lee el config: así esta cuenta se
    testea con tres números y sin archivo, y el annotator que dibuja el ROI de cinta y el
    analyzer que mide contra él reciben **el mismo valor leído una sola vez**.

    `class_names` es la lista del modelo, en orden de índice de clase. Hace falta para que
    una clase que no aparece en un frame salga igual en cero en vez de desaparecer de las
    claves: un registro que deja de publicarse se queda con su último valor, y el PLC no
    tiene forma de distinguir eso de una medición que no cambió.

    `belt_roi_px` y `dark_background_threshold` van por slot de cámara. Una cámara sin ROI
    de cinta declarado mide la carga contra el frame entero; una sin umbrales no refina.
    """
    ordered_names = [str(name) for name in (class_names or [])]

    def compute_metrics(result: InferenceResult) -> dict:
        return _compute(result, ordered_names, belt_roi_px or {},
                        dark_background_threshold or {})

    return compute_metrics


def compute_metrics(result: InferenceResult) -> dict:
    """
    Analyzer sin parámetros de planta: sólo lo que se puede contar sin calibración.

    Es el que corre si nadie arma el de `build_analyzer()`. Mide contra el frame completo
    —sin ROI de cinta, así que `pct_carga` es la superficie cubierta— y no refina nada.
    """
    return _compute(result, [], {}, {})


# ── La cuenta ────────────────────────────────────────────────────────────────

def _compute(result: InferenceResult, ordered_names: list, belt_roi_px: dict,
             dark_background_threshold: dict) -> dict:
    frame_bgr = result.source_bgr
    if frame_bgr is None or frame_bgr.size == 0:
        return {}

    height_px, width_px = frame_bgr.shape[:2]
    frame_area_px = height_px * width_px
    belt_area_px = _belt_area_px(belt_roi_px.get(result.camera_slot), frame_area_px)

    px_by_class = _count_union_px_by_class(
        result.detections, frame_bgr,
        dark_background_threshold.get(result.camera_slot) or {})
    # Las clases declaradas salen siempre, aunque este frame no haya visto ninguna.
    for class_name in ordered_names:
        px_by_class.setdefault(class_name, 0)

    detected_px = sum(px_by_class.values())
    frame_pct_by_class = {name: px / frame_area_px * _PCT
                          for name, px in px_by_class.items()}

    metrics: dict = {}
    for class_name, fraction_pct in frame_pct_by_class.items():
        metrics[f"pct_{class_name}"] = int(round(fraction_pct))
    metrics[FRAME_FRACTION_KEY] = max(0, int(round(_PCT - sum(frame_pct_by_class.values()))))
    metrics[LOAD_KEY] = round(detected_px / belt_area_px * _PCT, 2)

    # Composición: qué proporción de lo detectado es cada clase. Sin nada detectado son
    # todas cero, que es distinto de un reparto en partes iguales.
    detected_pct = sum(frame_pct_by_class.values())
    for class_name, fraction_pct in frame_pct_by_class.items():
        normalized_pct = fraction_pct / detected_pct * _PCT if detected_pct > 0 else 0.0
        metrics[f"pct_{class_name}_norm"] = round(normalized_pct, 2)

    # Dos escalares que el motor ya calculó y que el PLC lee por registro. Viajan como
    # métricas porque el mapa de registros se indexa por nombre de métrica: es el contrato
    # con el integrador y no hay una tabla de traducción aparte.
    metrics["confidence_pct"] = round(float(result.confidence_pct), 2)
    metrics["inference_time_ms"] = round(float(result.inference_time_ms), 2)
    return metrics


def _belt_area_px(roi_px: dict | None, frame_area_px: int) -> int:
    """
    Área contra la que se mide la carga: el ROI de cinta, o el frame si no está declarado.

    Nunca devuelve cero: sin un área válida la división no tendría sentido y el frame
    entero es el denominador honesto —da una carga más baja, no un número inventado—.
    """
    if not roi_px:
        return max(1, frame_area_px)
    area_px = int(roi_px.get("width_px", 0)) * int(roi_px.get("height_px", 0))
    return area_px if area_px > 0 else max(1, frame_area_px)


def _count_union_px_by_class(detections: list, frame_bgr: np.ndarray,
                             threshold_by_class: dict) -> dict[str, int]:
    """
    Píxeles de cada clase, contados por unión sobre un lienzo de etiquetas.

    Cada máscara se pega en el lienzo en el orden en que llegaron las detecciones, así que
    donde dos se superponen queda la última: el mismo criterio con el que se pintan las
    máscaras del overlay, para que lo que se ve y lo que se mide no discrepen.

    El refinamiento de fondo oscuro se aplica **después** de armar el lienzo y no por
    detección: un píxel que una clase le ganó a otra y que después se descarta por oscuro
    queda como cinta, no vuelve a la clase que lo había perdido.
    """
    if not detections:
        return {}

    height_px, width_px = frame_bgr.shape[:2]
    # 0 = sin clase; una clase es su posición en `names` + 1. uint8 alcanza y de sobra:
    # 255 clases distintas en un frame no es un caso de este dominio.
    labels = np.zeros((height_px, width_px), dtype=np.uint8)
    names: list[str] = []
    for detection in detections:
        if detection.mask is None:
            continue
        if detection.class_name not in names:
            names.append(detection.class_name)
        x1, y1, x2, y2 = detection.bbox_px
        window = labels[y1:y2, x1:x2]
        mask = detection.mask[:window.shape[0], :window.shape[1]]
        window[mask > 0] = names.index(detection.class_name) + 1

    if threshold_by_class:
        luma = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
        for class_name, threshold in threshold_by_class.items():
            if class_name not in names or int(threshold) <= 0:
                continue
            label = names.index(class_name) + 1
            labels[(labels == label) & (luma < int(threshold))] = 0

    return {name: int(np.count_nonzero(labels == index + 1))
            for index, name in enumerate(names)}
