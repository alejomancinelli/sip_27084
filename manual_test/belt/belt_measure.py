"""
Mide la cinta con el modelo real y muestra los números, para la puesta en marcha.

Es la prueba que cierra las tres cosas que no se pueden decidir sin el equipo delante:

  1. **Que el `.engine` carga y las clases están en el orden correcto.** Invertirlas
     invierte todos los porcentajes sin que nada falle. El pipeline confronta las clases
     que declaran los pesos con `CLASS_NAMES` y no arranca si no coinciden; el script
     imprime las dos listas, y el anotado pinta pellet en verde y desmenuzado en rojo.
  2. **Que la imagen que se mide es la de la versión anterior.** Como la app, el script
     corrige el lente y aplica `cameras.<slot>.image_adjust` —el ×4.6 a 800 µs— antes de
     inferir, así que `luma` es el nivel del frame ajustado: el mismo contra el que se
     compara `dark_background_threshold`. Al lado sale `luma_cruda`, la del sensor, que es
     la que mueve la exposición. Cambiar la exposición o el factor obliga a recalibrar el
     umbral, y los percentiles que imprime el diagnóstico son para eso.
  3. **Que los porcentajes siguen dando lo mismo que antes de migrar.** Con `--frames` se
     corre sobre imágenes guardadas en vez de la cámara, así que se pueden pasar las
     mismas capturas por el equipo viejo y por éste y comparar número contra número. Es la
     única forma de saber que la migración no movió la medición. Las imágenes se toman
     como crudas, que es como las guarda la tecla `g`; las del dataset de la app ya vienen
     ajustadas y van con `--adjusted`, para no ajustarlas dos veces.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\belt\\belt_measure.py
    Linux:    .venv/bin/python manual_test/belt/belt_measure.py
              .venv/bin/python manual_test/belt/belt_measure.py --frames capturas/
              .venv/bin/python manual_test/belt/belt_measure.py --json medicion.json

Lee el `config.yaml` que tiene al lado —no el de la app— y arma la cámara, el preprocessor,
el modelo, el pipeline y el analyzer con las mismas fábricas que usa el sistema: lo que se
prueba es el camino real y no una instancia a mano.

Teclas, con ventana:
    q / ESC   salir (también sirve cerrar la ventana)
    g         guardar el frame crudo y el anotado en la carpeta del script
"""

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

# La raíz del repo es el primer ancestro que contiene `tools/`, no un número fijo de
# niveles: así el script sobrevive a que lo muevan de carpeta.
_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "tools").is_dir()),
    Path(__file__).resolve().parents[2],
)
sys.path.insert(0, str(_REPO_ROOT))

from system.config_manager import ConfigManager                         # noqa: E402
from system.inference import annotations, metrics, overlay              # noqa: E402
from system.inference.pipeline import CLASS_NAMES, Pipeline             # noqa: E402
from system.inference.preprocessing import build_preprocessor           # noqa: E402
from system.inference.result import InferenceResult                     # noqa: E402
from tools.camera.camera_factory import create_camera                   # noqa: E402

_CONFIG_PATH = Path(__file__).with_name("config.yaml")
_WINDOW_NAME = "Cinta - medicion"
_MAX_WINDOW_WIDTH_PX = 1280
_FRAME_TIMEOUT_MS = 2000
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp")


def main(argv: list | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    config = ConfigManager(str(_CONFIG_PATH))
    camera_slot = args.camera or next(iter(config.get("cameras", {}) or {}), "")
    if not camera_slot:
        print("El config.yaml no declara ninguna cámara.")
        return 1

    dark_background_threshold = config.get("process.dark_background_threshold", {}) or {}
    if args.no_refine:
        dark_background_threshold = {}
        print()
        print("Sin refinamiento: las máscaras salen como las da el modelo.")
    pipeline = Pipeline(config, args.pipeline,
                        dark_background_threshold=dark_background_threshold)
    pipeline.load()
    _report_pipeline(pipeline)
    preprocess = build_preprocessor(config)
    print(f"Preprocessor: {preprocess.__name__ if preprocess else 'ninguno, se mide el crudo'}"
          f"{' (no se aplica a --frames: vienen ajustadas)' if args.adjusted else ''}")
    if not pipeline.is_loaded:
        print("\nNingún modelo cargó: lo que salga no es una medición. Se sigue igual para "
              "poder ver la imagen.")

    belt_roi_px = config.get("process.belt_roi_px", {}) or {}
    thresholds = dark_background_threshold.get(camera_slot) or {}
    analyze = metrics.build_analyzer(class_names=list(CLASS_NAMES), belt_roi_px=belt_roi_px)
    # El refinamiento lo aplica el pipeline sobre las máscaras, así que acá no se puede
    # deshacer: para comparar hay que correr el script otra vez con `--no-refine`, que
    # arma el pipeline sin umbrales. Es lo que separa «el umbral se está comiendo la clase»
    # de «el modelo no la detecta», que desde el resultado final se ven igual.
    diagnose = _Diagnosis(thresholds) if thresholds else None
    overlay_options = overlay.OverlayOptions(
        class_colors_bgr=tuple(tuple(color) for color in
                               (config.get("inference.overlay.class_colors_bgr", []) or [])),
        draw_boxes=False, draw_labels=False,
    )
    annotate = annotations.chain(
        annotations.belt_roi_annotator(config.get("process.belt_roi_px", {}) or {}),
        annotations.composition_panel_annotator(
            list(CLASS_NAMES),
            class_colors_bgr=overlay_options.class_colors_bgr),
    )

    measurements = []
    try:
        if args.frames:
            _run_over_files(args, camera_slot, pipeline, analyze, annotate,
                            overlay_options, measurements, diagnose,
                            preprocess=None if args.adjusted else preprocess)
        else:
            _run_over_camera(args, config, camera_slot, pipeline, analyze, annotate,
                             overlay_options, measurements, diagnose, preprocess=preprocess)
    except KeyboardInterrupt:
        print("\nInterrumpido.")
    finally:
        pipeline.unload()
        cv2.destroyAllWindows()

    _report_summary(measurements)
    if args.json and measurements:
        Path(args.json).write_text(json.dumps(measurements, indent=2), encoding="utf-8")
        print(f"\nMediciones guardadas en {args.json}")
    return 0


# ── Los dos modos ────────────────────────────────────────────────────────────

def _run_over_files(args, camera_slot, pipeline, analyze, annotate, overlay_options,
                    measurements: list, diagnose=None, preprocess=None):
    """Corre sobre imágenes guardadas: es el modo que permite comparar contra el equipo viejo."""
    paths = sorted(p for p in Path(args.frames).iterdir()
                   if p.suffix.lower() in _IMAGE_SUFFIXES)
    if not paths:
        print(f"No hay imágenes en {args.frames}.")
        return
    print(f"\n{len(paths)} imagen(es) en {args.frames}.\n")
    for path in paths:
        frame_bgr = cv2.imread(str(path))
        if frame_bgr is None:
            print(f"  {path.name}: no se pudo leer.")
            continue
        result = _measure(frame_bgr, camera_slot, pipeline, analyze, preprocess)
        measurements.append({"source": path.name, **result.metrics})
        _print_measurement(path.name, result, diagnose, raw_bgr=frame_bgr)
        if not args.no_window:
            _show(result, annotate, overlay_options)
            if _wait_key() == "quit":
                return


def _run_over_camera(args, config, camera_slot, pipeline, analyze, annotate,
                     overlay_options, measurements: list, diagnose=None, preprocess=None):
    """Corre en vivo contra la cámara, que es el modo de calibrar la exposición."""
    driver = create_camera(config.get(f"cameras.{camera_slot}", {}) or {})
    if not driver.connect():
        print(f"No se pudo conectar con {camera_slot}: "
              f"{driver.get_status().get('error', 'sin motivo')}")
        return
    print(f"\nCámara {camera_slot} conectada. 'q' para salir, 'g' para guardar el frame.\n")
    try:
        while True:
            frame_bgr = driver.get_frame(timeout_ms=_FRAME_TIMEOUT_MS)
            if frame_bgr is None:
                print("  sin frame...")
                continue
            result = _measure(frame_bgr, camera_slot, pipeline, analyze, preprocess)
            measurements.append(dict(result.metrics))
            _print_measurement(time.strftime("%H:%M:%S"), result, diagnose, raw_bgr=frame_bgr)
            if args.no_window:
                continue
            _show(result, annotate, overlay_options)
            action = _wait_key()
            if action == "quit":
                return
            if action == "save":
                _save(result, frame_bgr)
    finally:
        driver.disconnect()


# ── Medición y presentación ──────────────────────────────────────────────────

def _measure(frame_bgr: np.ndarray, camera_slot: str, pipeline: Pipeline,
             analyze, preprocess=None) -> InferenceResult:
    """
    Un resultado completo, por el mismo camino que el motor: preprocessor, pipeline y
    analyzer. Lo que devuelve el preprocessor es el frame de referencia, y es el que queda
    en `source_bgr`.
    """
    reference_bgr = preprocess(frame_bgr, camera_slot) if preprocess else frame_bgr
    started_s = time.perf_counter()
    detections = pipeline.run(reference_bgr, camera_slot)
    elapsed_ms = (time.perf_counter() - started_s) * 1000
    result = InferenceResult(
        camera_slot=camera_slot,
        pipeline_slot=pipeline.pipeline_slot,
        detections=detections,
        source_bgr=reference_bgr,
        inference_time_ms=elapsed_ms,
        stage_times_ms=dict(pipeline.stage_times_ms),
        labels=dict(pipeline.labels),
        is_synthetic=pipeline.is_synthetic,
    )
    result.confidence_pct = (
        round(sum(d.confidence_pct for d in detections) / len(detections), 1)
        if detections else 0.0)
    result.metrics = analyze(result)
    return result


def _print_measurement(label: str, result: InferenceResult, diagnose=None,
                       raw_bgr: np.ndarray | None = None):
    """
    Una línea por medición. `luma` es la del frame que se midió —la que ve el umbral— y
    `luma_cruda` la del sensor, que sale sólo si el preprocessor cambió la imagen.
    """
    gray = cv2.cvtColor(result.source_bgr, cv2.COLOR_BGR2GRAY)
    percentages = "  ".join(
        f"{name}={value}" for name, value in sorted(result.metrics.items())
        if name.startswith("pct_"))
    raw_luma = ""
    if raw_bgr is not None and raw_bgr is not result.source_bgr:
        raw_luma = f"  luma_cruda={float(cv2.cvtColor(raw_bgr, cv2.COLOR_BGR2GRAY).mean()):5.1f}"
    print(f"  {label}  luma={float(gray.mean()):5.1f}{raw_luma}  det={len(result.detections):4d}  "
          f"conf={result.confidence_pct:5.1f}  {result.inference_time_ms:6.1f} ms  "
          f"{percentages}")
    if diagnose is not None:
        diagnose(result, gray)


class _Diagnosis:
    """
    Dice con qué umbral se midió cada clase y contra qué luma se lo puede elegir.

    Una clase en cero se ve igual venga de donde venga: el modelo no la detectó, o la
    detectó y el umbral se la comió entera. Para separarlos se corre el script dos veces,
    la segunda con `--no-refine`, y se comparan los dos resúmenes.
    """

    def __init__(self, threshold_by_class: dict):
        self._threshold_by_class = threshold_by_class

    def __call__(self, result: InferenceResult, gray):
        for class_name, threshold in sorted(self._threshold_by_class.items()):
            measured = result.metrics.get(f"pct_{class_name}")
            eaten = " <-- ¿se la come el umbral? probar con --no-refine" if (
                measured == 0 and int(threshold) > 0) else ""
            print(f"      {class_name:14s} umbral {int(threshold):3d} -> "
                  f"{measured} %{eaten}")
        # Los percentiles dicen dónde poner el umbral: por debajo del que deja pasar la
        # fracción de imagen que de verdad es material.
        percentiles = [int(value) for value in np.percentile(gray, [5, 25, 50, 75, 95])]
        print(f"      luma p5/p25/p50/p75/p95 = {percentiles}")


def _report_pipeline(pipeline: Pipeline):
    """Qué cargó y con qué clases: es lo primero que hay que confirmar contra el `.engine`."""
    status = pipeline.get_status()
    print(f"\nPipeline '{status['pipeline_slot']}' - cargado: {status['loaded']}")
    for model_slot, model_status in status["models"].items():
        print(f"  {model_slot}: {model_status['status']} tarea={model_status['task'] or '-'}"
              f"{'  SINTETICO' if model_status['synthetic'] else ''}")
        if model_status["error"]:
            print(f"    motivo: {model_status['error']}")
    print(f"  clases esperadas: "
          f"{', '.join(f'{i}={name}' for i, name in enumerate(CLASS_NAMES))}")
    if status["class_error"]:
        print(f"  NO ARRANCA: {status['class_error']}.")
    print("\n  Las clases de los pesos salen en el log, al cargar el modelo. Si el modelo no "
          "las declara\n  y el anotado pinta el pellet de rojo, tiene las clases en otro "
          "orden.\n")


def _report_summary(measurements: list):
    """Media de cada métrica sobre todo lo medido, que es lo que se compara con el equipo viejo."""
    if not measurements:
        return
    numeric_names = sorted({
        name for measurement in measurements
        for name, value in measurement.items() if isinstance(value, (int, float))
    })
    print()
    print(f"--- Media de {len(measurements)} medicion(es) ---")
    for name in numeric_names:
        values = [m[name] for m in measurements if isinstance(m.get(name), (int, float))]
        print(f"  {name:28s} {sum(values) / len(values):8.2f}")


def _show(result: InferenceResult, annotate, overlay_options: overlay.OverlayOptions):
    # El mismo orden que el motor: el timestamp va último para que nada lo tape.
    annotated = overlay.annotate(result, replace(overlay_options, draw_timestamp=False))
    if annotated is None:
        annotated = result.source_bgr.copy()
    annotate(annotated, result)
    overlay.draw_timestamp(annotated, result.timestamp_s, options=overlay_options)
    height_px, width_px = annotated.shape[:2]
    if width_px > _MAX_WINDOW_WIDTH_PX:
        ratio = _MAX_WINDOW_WIDTH_PX / width_px
        annotated = cv2.resize(annotated, (_MAX_WINDOW_WIDTH_PX,
                                           int(round(height_px * ratio))))
    result.annotated_bgr = annotated
    cv2.imshow(_WINDOW_NAME, annotated)


def _wait_key() -> str:
    key = cv2.waitKey(1) & 0xFF
    if key in (ord("q"), 27) or cv2.getWindowProperty(_WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
        return "quit"
    return "save" if key == ord("g") else ""


def _save(result: InferenceResult, raw_bgr: np.ndarray):
    """Guarda el frame del sensor, no el ajustado: así vuelve a entrar por `--frames`."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    folder = Path(__file__).parent
    cv2.imwrite(str(folder / f"{stamp}_raw.png"), raw_bgr)
    if result.annotated_bgr is not None:
        cv2.imwrite(str(folder / f"{stamp}_annotated.png"), result.annotated_bgr)
    print(f"    guardado {stamp}_raw.png / _annotated.png")


def _parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--frames", help="carpeta con imágenes, en vez de la cámara")
    parser.add_argument("--camera", default="", help="slot de cámara; por defecto, la primera")
    parser.add_argument("--pipeline", default="pipeline_1", help="slot del pipeline")
    parser.add_argument("--model", default="segmenter", help="slot del modelo")
    parser.add_argument("--json", default="", help="archivo donde guardar las mediciones")
    parser.add_argument("--no-window", action="store_true", help="sin ventana, sólo consola")
    parser.add_argument("--no-refine", action="store_true",
                        help="arma el pipeline sin umbrales de fondo oscuro, para comparar")
    parser.add_argument("--adjusted", action="store_true",
                        help="las imágenes de --frames ya traen image_adjust (las del dataset)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
