"""
Prueba manual: corre los pipelines del config sobre cada imagen de una carpeta y guarda el
resultado de la corrida en una carpeta con la fecha y la hora.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\offline_inference\\run_images.py
    Linux:    .venv/bin/python manual_test/offline_inference/run_images.py

    --config ruta     config con los pipelines, los modelos, las cámaras y `process:`. Sin
                      esto, el `config.yaml` de esta carpeta, que corre el modelo mock. Para
                      probar el modelo del fork: `--config config.yaml`, el de la app.
    --images ruta     carpeta de imágenes. Sin esto, `images/` de esta carpeta.
    --camera slot     cámara de las imágenes sueltas en la carpeta; sin esto, la primera de
                      `cameras:`. Decide el ROI, el preprocessor, `process:` y qué
                      pipelines la reciben.
    --preprocessed    las imágenes ya son frame de referencia —salen del dataset, que las
                      guarda corregidas y ajustadas— y no se les vuelve a pasar el
                      preprocessor.

Las imágenes de una subcarpeta con el nombre de una cámara (`images/camera_2/`) se corren
como de esa cámara; una subcarpeta con otro nombre se saltea y se avisa. Se toman .png
.jpg .jpeg .bmp .tif .tiff.

Cada corrida deja en `output/<fecha>_<hora>/`:

    <pipeline>/<cámara>/<imagen>.png    el anotado, igual al que se vería en vivo
    <pipeline>/<cámara>/<imagen>.json   el resultado (`InferenceResult.to_dict()`)
    summary.csv                         una fila por imagen y pipeline, con las métricas
    run.json                            con qué config, qué imágenes y qué modelos se corrió

Dos imágenes que caen en la misma carpeta de salida con el mismo nombre —`a.png` y `a.jpg`—
salen como `a` y `a_2`; la columna `output` del resumen dice cuál es cuál.

El resultado es el que saldría en vivo si la imagen llegara de esa cámara: el runner arma
el mismo `Pipeline`, el mismo preprocessor y los mismos hooks del proyecto que la app.

Qué mirar:
    - **Carga**: si un modelo no carga, la consola lo dice antes de la primera imagen y
      todos los resultados de ese pipeline salen inválidos con `no_model`.
    - **Brillo**: una imagen oscura sale inválida con `dark_frame` sin pasar por el
      modelo, igual que en la línea: el mínimo es `illumination_min` de la cámara.
    - **Dataset**: correr una imagen del dataset SIN `--preprocessed` con una cámara que
      declara `image_adjust` muestra el brillo multiplicado dos veces.
"""

import argparse
import csv
import json
import sys
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

from system.config_manager import ConfigManager  # noqa: E402
from system.inference.offline import OfflineRunner  # noqa: E402
from system.inference.result import InferenceResult  # noqa: E402

_HERE = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _HERE / "config.yaml"
_DEFAULT_IMAGES = _HERE / "images"
_OUTPUT_DIR = _HERE / "output"

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")
_FALLBACK_CAMERA = "camera_1"
_RUN_DIR_FORMAT = "%Y-%m-%d_%H-%M-%S"

# Lo que se guarda en run.json del estado de cada pipeline. Los contadores del motor no:
# cuentan lo que procesó el hilo, y acá el hilo no corre.
_PIPELINE_STATUS_KEYS = ("loaded", "synthetic", "cameras", "models")

# Columnas fijas del resumen; después van las métricas, una columna por nombre.
_SUMMARY_COLUMNS = ("image", "output", "camera_slot", "pipeline_slot", "is_valid", "invalid_reason",
                    "detection_count", "confidence_pct", "illumination_pct",
                    "inference_time_ms", "labels")


def _parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Corre los pipelines del config sobre una carpeta de imágenes.")
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    parser.add_argument("--images", type=Path, default=_DEFAULT_IMAGES)
    parser.add_argument("--camera", default=None)
    parser.add_argument("--preprocessed", action="store_true")
    return parser.parse_args(argv)


def _is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _IMAGE_EXTENSIONS


def _collect_images(images_dir: Path, default_camera: str,
                    camera_slots: tuple[str, ...]) -> list[tuple[Path, str]]:
    """(imagen, cámara) de la carpeta: las sueltas son de `default_camera`, las de una
    subcarpeta con nombre de cámara son de esa cámara."""
    jobs = [(path, default_camera) for path in sorted(images_dir.iterdir()) if _is_image(path)]
    for subdir in sorted(path for path in images_dir.iterdir() if path.is_dir()):
        if subdir.name not in camera_slots:
            print(f"Se saltea {subdir.name}/: no es una cámara del config "
                  f"({', '.join(camera_slots) or 'ninguna'}).")
            continue
        jobs.extend((path, subdir.name) for path in sorted(subdir.iterdir()) if _is_image(path))
    return jobs


def _read_image(path: Path) -> np.ndarray | None:
    """imdecode sobre los bytes: imread no abre rutas con caracteres no ASCII en Windows."""
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def _write_image(path: Path, frame_bgr: np.ndarray):
    is_encoded, buffer = cv2.imencode(".png", frame_bgr)
    if is_encoded:
        buffer.tofile(str(path))


def _unique_stem(result_dir: Path, stem: str, used_stems: set) -> str:
    """
    Nombre de salida que nadie usó en esta corrida.

    `a.png` y `a.jpg`, o `a.png` suelta y `camera_1/a.png`, van a la misma carpeta de
    salida: sin esto la segunda pisaría a la primera sin avisar.
    """
    candidate = stem
    suffix = 2
    while (result_dir, candidate) in used_stems:
        candidate = f"{stem}_{suffix}"
        suffix += 1
    used_stems.add((result_dir, candidate))
    return candidate


def _save_result(run_dir: Path, image_path: Path, image_label: str, result: InferenceResult,
                 used_stems: set) -> dict:
    """
    Escribe el anotado y el JSON del resultado; devuelve la fila del resumen.

    `image_label` es la ruta relativa a la carpeta de imágenes, que es lo que distingue en
    el resumen una imagen suelta de la de una subcarpeta con el mismo nombre.
    """
    result_dir = run_dir / result.pipeline_slot / result.camera_slot
    result_dir.mkdir(parents=True, exist_ok=True)
    stem = _unique_stem(result_dir, image_path.stem, used_stems)
    payload = result.to_dict()
    payload["image"] = str(image_path)
    if result.annotated_bgr is not None:
        _write_image(result_dir / f"{stem}.png", result.annotated_bgr)
    (result_dir / f"{stem}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    row = {column: payload.get(column, "") for column in _SUMMARY_COLUMNS}
    row["image"] = image_label
    row["output"] = f"{result.pipeline_slot}/{result.camera_slot}/{stem}"
    row["labels"] = json.dumps(payload["labels"], ensure_ascii=False) if payload["labels"] else ""
    row.update(payload["metrics"])
    return row


def _write_summary(run_dir: Path, rows: list[dict]):
    metric_columns: list[str] = []
    for row in rows:
        metric_columns.extend(key for key in row
                              if key not in _SUMMARY_COLUMNS and key not in metric_columns)
    with open(run_dir / "summary.csv", "w", newline="", encoding="utf-8") as summary_file:
        writer = csv.DictWriter(summary_file, fieldnames=[*_SUMMARY_COLUMNS, *metric_columns])
        writer.writeheader()
        writer.writerows(rows)


def _print_load_status(status_by_pipeline: dict) -> bool:
    """Muestra qué cargó cada pipeline; False si alguna etapa quedó sin modelo."""
    is_all_loaded = True
    for pipeline_slot, status in status_by_pipeline.items():
        cameras = ", ".join(status["cameras"]) or "todas"
        print(f"{pipeline_slot} (cámaras: {cameras})")
        for model_slot, model_status in status["models"].items():
            error = model_status.get("error")
            is_all_loaded &= not error
            state = f"ERROR — {error}" if error else "cargado"
            print(f"  {model_slot}: {state}")
    return is_all_loaded


def _describe(result: InferenceResult) -> str:
    verdict = "válido" if result.is_valid else f"inválido ({result.invalid_reason})"
    return (f"{result.pipeline_slot}: {verdict}, {result.detection_count} detecciones, "
            f"{result.inference_time_ms:.0f} ms")


def main(argv: list | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    config_path = args.config.resolve()
    images_dir = args.images.resolve()
    if not config_path.is_file():
        print(f"No existe el config {config_path}.")
        return 2
    if not images_dir.is_dir():
        print(f"No existe la carpeta de imágenes {images_dir}.")
        return 2

    config = ConfigManager(str(config_path))
    camera_slots = tuple(config.get("cameras", {}) or {})
    default_camera = args.camera or (camera_slots[0] if camera_slots else _FALLBACK_CAMERA)
    jobs = _collect_images(images_dir, default_camera, camera_slots)
    if not jobs:
        print(f"{images_dir} no tiene imágenes ({' '.join(_IMAGE_EXTENSIONS)}).")
        return 2

    runner = OfflineRunner(config, apply_preprocessor=not args.preprocessed)
    if not runner.pipeline_slots:
        print(f"{config_path} no tiene ningún pipeline habilitado.")
        return 2

    started_at = datetime.now()
    run_dir = _OUTPUT_DIR / started_at.strftime(_RUN_DIR_FORMAT)
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Config: {config_path}")
    print(f"Imágenes: {len(jobs)} de {images_dir}"
          f"{' — ya preprocesadas' if args.preprocessed else ''}")
    runner.load()
    status_by_pipeline = runner.get_status()
    if not _print_load_status(status_by_pipeline):
        print("Hay etapas sin modelo: sus resultados van a salir inválidos con `no_model`.")
    print()

    rows: list[dict] = []
    unreadable: list[str] = []
    used_stems: set = set()
    try:
        for image_path, camera_slot in jobs:
            frame_bgr = _read_image(image_path)
            if frame_bgr is None:
                print(f"{image_path.name}: no se pudo leer, se saltea.")
                unreadable.append(str(image_path))
                continue
            results = runner.run_image(frame_bgr, camera_slot)
            if not results:
                print(f"{image_path.name} [{camera_slot}]: ningún pipeline recibe esa cámara.")
                continue
            for result in results:
                print(f"{image_path.name} [{camera_slot}] {_describe(result)}")
                image_label = image_path.relative_to(images_dir).as_posix()
                rows.append(_save_result(run_dir, image_path, image_label, result, used_stems))
    finally:
        runner.unload()

    _write_summary(run_dir, rows)
    (run_dir / "run.json").write_text(json.dumps({
        "started_at": started_at.isoformat(timespec="seconds"),
        "config": str(config_path),
        "images": str(images_dir),
        "default_camera": default_camera,
        "preprocessed": args.preprocessed,
        "image_count": len(jobs),
        "unreadable": unreadable,
        "pipelines": {
            pipeline_slot: {key: status[key] for key in _PIPELINE_STATUS_KEYS}
            for pipeline_slot, status in status_by_pipeline.items()
        },
    }, ensure_ascii=False, indent=2, default=list), encoding="utf-8")

    valid_count = sum(1 for row in rows if row["is_valid"])
    print(f"\n{len(rows)} resultados, {valid_count} válidos. Guardado en {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
