"""
Agregaciones sobre las detecciones de una inferencia.

Módulo puro: sin Qt, sin ConfigManager, sin cv2 y sin I/O. Cada función recibe
detecciones o resultados ya armados y devuelve números; nada de acá conoce el
framework que los produjo ni el proceso que se está midiendo.

Maquinaria genérica: reutilizable entre proyectos. Es lo que casi todos los forks
necesitan y ninguno debería volver a escribir. Las métricas del proceso —qué significan
esas detecciones en esta instalación— van en `metrics.py`, que es el archivo que sí se
reescribe.

Las tres últimas cierran el otro extremo: el motor emite un resultado por frame y nadie
promedia en el camino, así que quien publique hacia afuera junta los resultados que le
interesan y los agrega acá. Promediar en el motor escondería la dispersión, que suele ser
el dato que dice si el proceso está estable.

    average_metrics      la media, cuando alcanza con eso
    summarize_metrics    media, desvío y rango — el ciclo de N frames de un proyecto que
                         responde con varias imágenes por medición
    pick_representative  cuál de los N frames se guarda y se muestra como el del ciclo

Quién decide cuántos frames son un ciclo no está acá: es de quien lo orquesta —hoy
`main.py`, mañana el scheduler de captura—, y lo lee de
`inference.pipelines.<slot>.frames_per_cycle`.
"""

import statistics
from collections.abc import Iterable

from .result import Detection, InferenceResult


def filter_by_confidence(detections: Iterable[Detection],
                         min_confidence_pct: float) -> list[Detection]:
    """Detecciones que llegan al umbral, en escala 0–100."""
    return [d for d in detections if d.confidence_pct >= min_confidence_pct]


def average_confidence_pct(detections: Iterable[Detection]) -> float:
    """
    Confianza media de las detecciones, 0–100. Sin detecciones devuelve 0.

    Es la confianza del resultado completo: en un proyecto que cuenta cientos de
    objetos el umbral por detección va bajo, así que este promedio es lo que dice si el
    conjunto sirve.
    """
    scores = [float(d.confidence_pct) for d in detections]
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 1)


def count_by_class(detections: Iterable[Detection]) -> dict[str, int]:
    """Cuántas detecciones hay de cada clase, por nombre de clase."""
    counts: dict[str, int] = {}
    for detection in detections:
        counts[detection.class_name] = counts.get(detection.class_name, 0) + 1
    return counts


def area_by_class(detections: Iterable[Detection]) -> dict[str, int]:
    """Superficie detectada de cada clase, en píxeles."""
    areas: dict[str, int] = {}
    for detection in detections:
        areas[detection.class_name] = areas.get(detection.class_name, 0) + int(detection.area_px)
    return areas


def class_fractions_pct(detections: Iterable[Detection], *,
                        by_area: bool = False) -> dict[str, float]:
    """
    Reparto porcentual entre clases, por conteo o por superficie.

    Sin detecciones devuelve un dict vacío, no ceros: no hay clases de las que hablar.
    """
    totals = area_by_class(detections) if by_area else count_by_class(detections)
    total = sum(totals.values())
    if not total:
        return {}
    return {name: round(value / total * 100, 1) for name, value in totals.items()}


def coverage_pct(detections: Iterable[Detection], frame_area_px: int) -> float:
    """Porcentaje del área analizada que ocupan las detecciones. 0 si el área no sirve."""
    if frame_area_px <= 0:
        return 0.0
    covered_px = sum(int(d.area_px) for d in detections)
    return round(min(100.0, covered_px / frame_area_px * 100), 1)


# Claves que `summarize_metrics` agrega por su cuenta: el conteo de muestras y, por cada
# métrica, sus cuatro estadísticos. Están acá y no en el consumidor porque las inventa este
# módulo: quien las recibe no tiene por qué saber deducirlas de un sufijo.
SAMPLE_COUNT_KEY = "sample_count"
SUMMARY_SUFFIXES = ("_mean", "_std", "_min", "_max")


def is_summary_key(name: str) -> bool:
    """
    Si la clave la agregó `summarize_metrics` en vez de venir del analyzer.

    Sirve para que un consumidor que sólo quiere las métricas del proceso —los registros
    del PLC, por ejemplo— separe lo derivado sin repetir la lista de sufijos. Una métrica
    del proyecto que de verdad se llame `algo_max` da un falso positivo; es el precio de no
    marcar cada clave, y se evita no llamando así a una métrica.
    """
    return name == SAMPLE_COUNT_KEY or name.endswith(SUMMARY_SUFFIXES)


def summarize_metrics(results: Iterable[InferenceResult], *,
                      valid_only: bool = True) -> dict:
    """
    Media, desvío y rango de las `metrics` de un ciclo, clave por clave.

    Devuelve un dict PLANO, con sufijos —`load_pct_mean`, `load_pct_std`, `load_pct_min`,
    `load_pct_max`— más `sample_count`: es lo que va a los fields de la telemetría, al
    JSON del dataset y a los registros del PLC, y ahí un dict anidado no entra.

    El desvío es poblacional: describe la dispersión de las N mediciones que se hicieron y
    no estima la de una población más grande. Con una sola muestra da 0.0, que es la
    respuesta correcta —no hay dispersión que reportar—, y `sample_count` deja ver que fue
    una sola.

    Mismo criterio que `average_metrics` para qué entra: sólo valores numéricos, y sólo
    resultados confiables salvo que se pida lo contrario.
    """
    selected = [r for r in results if not valid_only or r.is_valid]
    samples = _collect_numeric(selected, valid_only=False)
    summary: dict = {"sample_count": len(selected)}
    for key in sorted(samples):
        values = samples[key]
        summary[f"{key}_mean"] = round(statistics.fmean(values), 2)
        summary[f"{key}_std"] = round(statistics.pstdev(values), 2)
        summary[f"{key}_min"] = round(min(values), 2)
        summary[f"{key}_max"] = round(max(values), 2)
    return summary


def pick_representative(results: Iterable[InferenceResult],
                        keys: Iterable[str] | None = None, *,
                        valid_only: bool = True) -> InferenceResult | None:
    """
    El resultado más cercano al promedio del ciclo, o None si no hay ninguno que sirva.

    El promedio no tiene imagen ni detecciones, así que cuando un ciclo de N frames
    responde con un solo número hay que elegir qué frame lo acompaña: éste. Lo que se
    guarda en el dataset y se muestra en la UI es su `annotated_bgr` y sus detecciones,
    aunque el número publicado sea el promedio de los N.

    La distancia se mide en desvíos y no en unidades: así una clave con valores grandes
    —un conteo— no tapa a una en porcentaje. Una clave con desvío 0 no distingue nada y se
    saltea. Sin `keys` usa todas las numéricas que estén en todos los resultados.
    """
    selected = [r for r in results if not valid_only or r.is_valid]
    if not selected:
        return None

    samples = _collect_numeric(selected, valid_only=False)
    if keys is None:
        candidates = [key for key, values in samples.items() if len(values) == len(selected)]
    else:
        candidates = [key for key in keys if len(samples.get(key, ())) == len(selected)]

    weighted = []
    for key in candidates:
        deviation = statistics.pstdev(samples[key])
        if deviation > 0:
            weighted.append((key, statistics.fmean(samples[key]), deviation))
    if not weighted:
        return selected[0]

    def distance(result: InferenceResult) -> float:
        return sum(((float(result.metrics[key]) - mean) / deviation) ** 2
                   for key, mean, deviation in weighted)

    return min(selected, key=distance)


def average_metrics(results: Iterable[InferenceResult], *,
                    valid_only: bool = True) -> dict:
    """
    Promedia las `metrics` de varios resultados, clave por clave.

    Sólo promedia valores numéricos —los bool y los textos se descartan, no se
    convierten— y una clave que falta en algún resultado se promedia sobre los que la
    traen. Con `valid_only` en True descarta los resultados no confiables: promediar un
    frame oscuro con uno bueno da un número que no midió nada.
    """
    samples = _collect_numeric(results, valid_only)
    return {key: round(statistics.fmean(values), 2) for key, values in samples.items()}


def _collect_numeric(results: Iterable[InferenceResult], valid_only: bool) -> dict[str, list]:
    """
    Junta los valores numéricos de `metrics` por clave, sobre los resultados que entran.

    Los bool quedan afuera aunque en Python sean enteros: promediar un flag da un número
    que no significa nada. Una clave ausente en un resultado no aporta muestra, así que
    cada clave se agrega sobre los resultados que la traen.
    """
    samples: dict[str, list] = {}
    for result in results:
        if valid_only and not result.is_valid:
            continue
        for key, value in result.metrics.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            samples.setdefault(key, []).append(float(value))
    return samples
