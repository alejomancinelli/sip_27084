"""
Prueba manual: cifra unos pesos, los abre en memoria y muestra qué pasa cuando algo no
cierra.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\model_protection\\check_protected_weights.py
    Linux:    .venv/bin/python manual_test/model_protection/check_protected_weights.py

    --weights ruta      pesos de verdad para proteger (.pt, .onnx, .engine). Sin esto se
                        usa un buffer sintético, que sirve para todo menos para la
                        pregunta del framework.
    --size-mb N         tamaño del buffer sintético. Poner acá el tamaño real del modelo
                        del proyecto es la forma de medir el pico de memoria antes de
                        tenerlo.
    --plain             deja los pesos SIN cifrar: es el camino de desarrollo, y tiene
                        que andar igual sin clave y sin configuración.
    --task nombre       tarea que se declara adentro de los pesos. `segmentation` con el
                        mock —que es de detección— es el escenario del desacuerdo.
    --no-metadata       cifra sin metadata: los nombres de clase salen del config.
    --keep              no borra el `.enc` al terminar, para poder mirarlo con un editor.

    --enc archivo       NO cifra nada: verifica un `.enc` que ya existe. Es el modo con el
                        que se comprueba lo que produce el repositorio de firma, que es lo
                        único que este script no puede probar cifrando él mismo.
    --key-hex hex       clave con la que abrir ese `.enc`. Sin esto se usa la que traiga el
                        build en `_model_key.py`, que es la prueba de verdad: si abre así,
                        abre en el equipo entregado.

**Cifrando acá, el script simula el build**: genera una clave al azar para la corrida y
se la pone al programa como si el módulo generado por el build estuviera. Es legítimo por
lo mismo que `manual_test/license/check_license.py` simula el binario: `manual_test/` no
entra en ningún build, y simular la clave **no saltea ninguna verificación** — el archivo
se descifra de verdad y el tag se verifica de verdad. Lo único que evita es tener que
generar un módulo de clave para cada prueba. Esa clave se imprime en hexadecimal y no es
el secreto de nadie: se generó hace un segundo y muere con el proceso.

**Con `--enc` no se simula nada**: la clave sale del `_model_key.py` que tenga el build,
igual que en el equipo entregado.

Qué mirar:
    - **Contenedor**: los pesos no aparecen en claro adentro del `.enc`, y dos cifrados
      del mismo archivo dan nonce distinto. Es lo que hace que un pendrive con el `.enc`
      no valga nada.
    - **Costo**: cuánto tarda en abrir y cuánta memoria pide de pico. Es la medición que
      decide si un `.engine` grande se puede descifrar de una sola vez en una Jetson.
    - **Fallas**: clave equivocada, un bit cambiado, archivo cortado y build sin clave
      tienen que fallar los cuatro, cada uno con su propio motivo. El del bit cambiado
      es el que muestra que la integridad sale gratis: no hay ningún hash guardado.
    - **Camino de la app**: el modelo carga desde el `.enc` y sus detecciones salen con
      los nombres de clase que viajan ADENTRO del archivo, no con los del `config.yaml`
      de al lado. Correr con `--plain` y comparar: ahí sí manda el config.
    - **Framework**: con `--weights` apuntando a un modelo de verdad, el reporte dice si
      ese framework carga desde memoria en ESTA máquina. Es la pregunta que decide si
      todo esto sirve, y no se contesta leyendo documentación.

Verificar lo que produjo el repositorio de firma, que es la otra prueba y la que importa
cuando el fork se entrega:

    1. Allá se genera la clave del fork y se protege el modelo:
           python -m licensing model-keygen --client ACME --project-id LINEA_3
           python -m licensing model-keymodule --key-label acme-linea3-1 --out <ruta>
           python -m licensing protect-model --key-label acme-linea3-1 --weights best.engine
    2. El `--out` del segundo comando es `system/inference/models/_model_key.py` de este
       repo, que es el mismo módulo que compila el build: ponerlo ahí es lo que hace que
       la prueba corra con la clave de verdad y no con una simulada.
    3. Acá, sin cifrar nada:
           check_protected_weights.py --enc <ruta del best.engine.enc>
    4. **El módulo de clave se borra después del build.** No se commitea nunca.

En ese modo el script no cifra ni escribe nada: abre el archivo que le dan, con la clave
que trae el build, y muestra la metadata que el firmante puso adentro. Que abra es la
prueba de que las dos puntas producen y esperan los mismos bytes; que la metadata sea la
que se pidió es la prueba de que no se cifró el modelo equivocado.
"""

import argparse
import io
import os
import sys
import time
from pathlib import Path

# La raíz del repo es el primer ancestro que contiene `system/`, no un número fijo de
# niveles: así el script sobrevive a que lo muevan de carpeta.
_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "system").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene system/.")
sys.path.insert(0, str(_REPO_ROOT))

import numpy as np                                                      # noqa: E402
import psutil                                                           # noqa: E402

from system.config_manager import ConfigManager                         # noqa: E402
from system.inference.models import encrypted_weights as ew             # noqa: E402
from system.inference.models import model_key                           # noqa: E402
from system.inference.models.abstract_model import normalize_task       # noqa: E402
from system.inference.models.mock_model import MockModel                # noqa: E402

_HERE = Path(__file__).resolve().parent
_CONFIG_PATH = _HERE / "config.yaml"
_MODEL_SLOT = "model_1"

_DEFAULT_SIZE_MB = 8
_PROTECTED_CLASS_NAMES = ["tornillo", "tuerca", "arandela"]
_PROTECTED_MIN_CONFIDENCE_PCT = 40.0
_FRAME_SIZE_PX = (480, 640)

# Claves de metadata que el producto entiende; cualquier otra viaja protegida pero no la
# lee nadie, y eso conviene avisarlo antes de que el archivo viaje a una planta.
_KNOWN_METADATA_KEYS = {"task", "class_names", "min_confidence_pct", "max_side_px", "params"}


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cifra unos pesos y los abre en memoria.")
    parser.add_argument("--weights", default=None, help="Pesos de verdad para proteger.")
    parser.add_argument("--size-mb", type=int, default=_DEFAULT_SIZE_MB,
                        help="Tamaño del buffer sintético cuando no se pasan pesos.")
    parser.add_argument("--plain", action="store_true",
                        help="Deja los pesos sin cifrar: el camino de desarrollo.")
    parser.add_argument("--task", default="detection",
                        help="Tarea declarada adentro de los pesos.")
    parser.add_argument("--no-metadata", action="store_true",
                        help="Cifra sin metadata: los nombres salen del config.")
    parser.add_argument("--keep", action="store_true", help="No borra el archivo generado.")
    parser.add_argument("--enc", default=None,
                        help="Verifica un .enc que ya existe, en vez de cifrar uno acá.")
    parser.add_argument("--key-hex", default=None,
                        help="Clave con la que abrir ese .enc; por default, la del build.")
    args = parser.parse_args(argv)

    if not ew.is_available():
        print("Falta la librería 'cryptography': sin ella no hay nada que probar.")
        return 1

    return _check_existing(args) if args.enc else _check_generated(args)


def _check_generated(args) -> int:
    """Cifra unos pesos acá y verifica el formato de punta a punta."""
    key = os.urandom(ew.KEY_SIZE)
    plain_weights = _read_or_build_weights(args)
    metadata = {} if args.no_metadata else {
        "task": args.task,
        "class_names": _PROTECTED_CLASS_NAMES,
        "min_confidence_pct": _PROTECTED_MIN_CONFIDENCE_PCT,
    }

    weights_path = _write_weights_file(args, plain_weights, key, metadata)
    # El build simulado: sin esto el programa no tiene clave y todo falla por el mismo
    # motivo, que es correcto pero no deja probar nada.
    model_key.get_model_key = lambda: key
    model_key.get_key_label = lambda: "manual-test"

    origin = args.weights if args.weights else f"buffer sintético de {args.size_mb} MB"
    try:
        _print_source(origin, plain_weights, weights_path, args.plain)
        _print_container(weights_path, plain_weights, metadata, key, args.plain)
        _print_cost(weights_path, key, args.plain)
        _print_failures(weights_path, key, args.plain)
        model = _print_app_path(weights_path, metadata if not args.plain else {})
        _print_framework(args.weights, plain_weights)
    finally:
        if not args.keep and weights_path.exists():
            weights_path.unlink()

    print(f"\nClave de esta corrida: {key.hex()}")
    print("Muere con el proceso: el archivo generado ya no se puede volver a abrir.\n")
    return 0 if model.is_loaded or _is_expected_task_mismatch(metadata) else 1


def _check_existing(args) -> int:
    """
    Verifica un contenedor que produjo otro: el del repositorio de firma.

    Acá no se cifra ni se escribe nada. El archivo que llega es el que va a viajar al
    equipo del cliente, así que se abre y se mira; no se lo toca ni se lo borra.
    """
    weights_path = Path(args.enc)
    if not weights_path.is_file():
        raise SystemExit(f"No existe el archivo: {weights_path}")

    raw = weights_path.read_bytes()
    if not ew.is_encrypted(raw):
        print(f"\n{weights_path.name} NO es un contenedor de este formato: no empieza con "
              f"el magic {ew.MAGIC!r}.")
        print("O son unos pesos en claro, o los produjo otra herramienta.")
        return 1

    key = _resolve_key(args)
    if key is None:
        return 1

    try:
        bundle = ew.unpack(raw, key)
    except ew.WeightsError as e:
        print(f"\nNO ABRE: {type(e).__name__} — {e}")
        print("Si la clave es la que corresponde, las dos puntas no están produciendo los")
        print("mismos bytes: comparar contra el docstring de encrypted_weights.py, que es")
        print("la especificación normativa del formato.")
        return 1

    _print_source(str(weights_path), bundle.weights, weights_path, False)
    _print_container(weights_path, bundle.weights, bundle.metadata, key, False)
    _print_metadata(bundle.metadata)
    _print_cost(weights_path, key, False)
    _print_failures(weights_path, key, False)
    model = _print_app_path(weights_path, bundle.metadata)
    _print_framework(weights_path.name[:-len(ew.ENCRYPTED_SUFFIX)]
                     if weights_path.name.endswith(ew.ENCRYPTED_SUFFIX) else weights_path.name,
                     bundle.weights)

    print("\nEl archivo abrió con la clave de este build: las dos puntas producen y")
    print("esperan los mismos bytes.\n")
    return 0 if model.is_loaded or _is_expected_task_mismatch(bundle.metadata) else 1


def _resolve_key(args) -> bytes | None:
    """Clave con la que abrir el contenedor: la de `--key-hex`, o la que traiga el build."""
    if args.key_hex:
        try:
            key = bytes.fromhex(args.key_hex.strip())
        except ValueError:
            raise SystemExit("--key-hex no es hexadecimal.")
        if len(key) != ew.KEY_SIZE:
            raise SystemExit(f"--key-hex tiene que traer {ew.KEY_SIZE} bytes.")
        # Con la clave puesta a mano el build queda simulado, igual que al cifrar acá.
        model_key.get_model_key = lambda: key
        model_key.get_key_label = lambda: "manual-test"
        return key

    key = model_key.get_model_key()
    if key is None:
        print("\nEste build no trae clave: falta system/inference/models/_model_key.py.")
        print("Se genera en el repositorio de firma y se deja en su lugar:")
        print("    python -m licensing model-keymodule --key-label <etiqueta> --out <ruta>")
        print("O se pasa la clave a mano con --key-hex, que simula el build.")
    return key


# ── Bloques del reporte ──────────────────────────────────────────────────────

def _print_source(origin: str, plain_weights: bytes, weights_path: Path, is_plain: bool):
    print("\n── Pesos ─────────────────────────────────────────────────────────────")
    print(f"  Origen              : {origin}")
    print(f"  Tamaño en claro     : {_megabytes(len(plain_weights))}")
    print(f"  Archivo             : {weights_path.name} "
          f"({'EN CLARO' if is_plain else 'cifrado'})")


def _print_container(weights_path: Path, plain_weights: bytes, metadata: dict, key: bytes,
                     is_plain: bool):
    raw = weights_path.read_bytes()

    print("\n── Contenedor ────────────────────────────────────────────────────────")
    if is_plain:
        print("  Sin contenedor: los pesos van tal cual, como en cualquier desarrollo.")
        print(f"  ¿Los reconoce como cifrados? {'sí' if ew.is_encrypted(raw) else 'no'}")
        return

    sample = plain_weights[:64]
    print(f"  Magic               : {raw[:len(ew.MAGIC)]!r}")
    print(f"  Versión de formato  : {raw[len(ew.MAGIC)]}")
    print(f"  Overhead            : {len(raw) - len(plain_weights)} bytes "
          f"(encabezado {ew.HEADER_SIZE} + tag {ew.TAG_SIZE})")
    print(f"  ¿Están los pesos en claro adentro? "
          f"{'SÍ — ALGO ESTÁ MAL' if sample in raw else 'no'}")

    class_names = metadata.get("class_names") or []
    if class_names:
        leaked = str(class_names[0]).encode("utf-8") in raw
        print(f"  ¿Están los nombres de clase en claro? "
              f"{'SÍ — ALGO ESTÁ MAL' if leaked else 'no'}")

    second = ew.pack(plain_weights, key)
    nonce = slice(len(ew.MAGIC) + 1, ew.HEADER_SIZE)
    print(f"  Nonce de dos cifrados del mismo archivo: "
          f"{'distinto' if raw[nonce] != second[nonce] else 'IGUAL — ALGO ESTÁ MAL'}")


def _print_metadata(metadata: dict):
    """
    Qué puso el firmante adentro del archivo.

    Es la mitad de la verificación que no mira bytes: que abra prueba que el formato
    coincide; que la metadata sea la que se pidió prueba que se cifró el modelo que
    correspondía y con los nombres que espera el proyecto.
    """
    print("\n── Metadata protegida ────────────────────────────────────────────────")
    if not metadata:
        print("  Vacía: los nombres de clase y el umbral van a salir del config.yaml.")
        return
    for key_name in sorted(metadata):
        print(f"  {key_name:<20}: {metadata[key_name]}")
    unknown = sorted(set(metadata) - _KNOWN_METADATA_KEYS)
    if unknown:
        print(f"  AVISO: el producto ignora estas claves: {', '.join(unknown)}")


def _print_cost(weights_path: Path, key: bytes, is_plain: bool):
    raw = weights_path.read_bytes()
    process = psutil.Process()

    rss_before_bytes = process.memory_info().rss
    started_s = time.perf_counter()
    bundle = ew.unpack(raw, key)
    elapsed_ms = (time.perf_counter() - started_s) * 1000
    rss_peak_bytes = process.memory_info().rss

    print("\n── Costo de abrirlos ─────────────────────────────────────────────────")
    print(f"  Tiempo              : {elapsed_ms:.1f} ms")
    if elapsed_ms > 0 and not is_plain:
        throughput_mb_s = (len(raw) / (1024 * 1024)) / (elapsed_ms / 1000)
        print(f"  Velocidad           : {throughput_mb_s:.0f} MB/s")
    print(f"  RSS antes / después : {_megabytes(rss_before_bytes)} / "
          f"{_megabytes(rss_peak_bytes)}")
    print(f"  Pico atribuible     : {_megabytes(max(0, rss_peak_bytes - rss_before_bytes))}")
    print("  El pico es lo que decide si un modelo grande se descifra de una sola vez:")
    print("  el tiempo nunca es el problema, la memoria de una Jetson chica puede serlo.")
    del bundle


def _print_failures(weights_path: Path, key: bytes, is_plain: bool):
    print("\n── Fallas ────────────────────────────────────────────────────────────")
    if is_plain:
        print("  Con los pesos en claro no hay nada que falle: se devuelven tal cual.")
        return

    raw = weights_path.read_bytes()
    flipped = bytearray(raw)
    flipped[-1] ^= 0x01

    scenarios = [
        ("Clave equivocada   ", raw, os.urandom(ew.KEY_SIZE)),
        ("Build sin clave    ", raw, None),
        ("Un bit cambiado    ", bytes(flipped), key),
        ("Archivo cortado    ", raw[:ew.HEADER_SIZE + 4], key),
        ("Encabezado ajeno   ", b"IEAWGTS\x00\x09" + raw[10:], key),
    ]
    for name, payload, candidate_key in scenarios:
        try:
            ew.unpack(payload, candidate_key)
            print(f"  {name}: ABRIÓ — ALGO ESTÁ MAL")
        except ew.WeightsError as e:
            print(f"  {name}: {type(e).__name__} — {e}")


def _print_app_path(weights_path: Path, metadata: dict) -> MockModel:
    config = ConfigManager(str(_CONFIG_PATH))
    # En memoria y sin `save()`: la prueba no reescribe su propio config.
    config.set(f"inference.models.{_MODEL_SLOT}.path", str(weights_path))

    model = MockModel(config, _MODEL_SLOT)
    model.load()

    print("\n── Camino de la app ──────────────────────────────────────────────────")
    print(f"  Estado del modelo   : {model.status}")
    if model.error:
        print(f"  Motivo              : {model.error}")
    print(f"  Umbral aplicado     : {model.min_confidence_pct} % "
          f"({'de los pesos' if 'min_confidence_pct' in metadata else 'del config'})")

    if not model.is_loaded:
        if _is_expected_task_mismatch(metadata):
            print(f"  ESPERADO: el mock es de '{MockModel.task}' y estos pesos son de "
                  f"'{normalize_task(metadata.get('task', ''))}'.")
            print("  La guarda de tarea los frenó antes del primer frame, que es lo que")
            print("  tiene que pasar. Del cifrado no dice nada: el archivo ya abrió y su")
            print(f"  metadata se leyó. El camino completo lo muestran unos pesos de "
                  f"'{MockModel.task}'.")
        return model

    frame = np.full((*_FRAME_SIZE_PX, 3), 128, np.uint8)
    names = [detection.class_name for detection in model.predict(frame)]
    print(f"  Detecciones         : {len(names)}")
    print(f"  Nombres de clase    : {', '.join(names) or 'ninguno'}")
    if metadata.get("class_names"):
        expected = metadata["class_names"][:len(names)]
        verdict = "los de adentro de los pesos" if names == expected else "LOS DEL CONFIG — REVISAR"
        print(f"  Vienen de           : {verdict}")
    else:
        print("  Vienen de           : el config, que es lo que corresponde sin metadata")
    return model


def _is_expected_task_mismatch(metadata: dict) -> bool:
    """
    True si el modelo no cargó sólo porque los pesos son de otra tarea que el mock.

    El mock es un detector, así que unos pesos de clasificación lo dejan en error por
    diseño. Eso prueba la guarda de tarea y no dice nada del cifrado, así que no cuenta
    como una corrida fallida: el contenedor ya se abrió antes de llegar ahí.
    """
    declared = normalize_task(str(metadata.get("task", "")))
    return bool(declared) and declared != MockModel.task


def _print_framework(weights_name: str | None, plain_weights: bytes):
    print("\n── Framework ─────────────────────────────────────────────────────────")
    if not weights_name:
        print("  Sin --weights no hay nada que cargar: el buffer sintético no es un modelo.")
        print("  Correr de nuevo apuntando al .pt / .onnx / .engine del proyecto es lo que")
        print("  contesta si ESE framework carga desde memoria en ESTA máquina.")
        return

    suffix = Path(weights_name).suffix.lower()
    loaders = {
        ".pt": _load_with_torch,
        ".pth": _load_with_torch,
        ".onnx": _load_with_onnxruntime,
        ".engine": _load_with_tensorrt,
        ".plan": _load_with_tensorrt,
    }
    loader = loaders.get(suffix)
    if loader is None:
        print(f"  Extensión '{suffix}' sin camino de carga conocido en esta prueba.")
        print("  Agregarlo acá es una función de diez líneas; lo que importa es que el")
        print("  framework acepte bytes y no una ruta.")
        return

    started_s = time.perf_counter()
    verdict = loader(plain_weights)
    print(f"  {verdict}")
    print(f"  Tiempo de carga     : {(time.perf_counter() - started_s) * 1000:.0f} ms")


# ── Cargas desde memoria, una por framework ──────────────────────────────────

def _load_with_torch(data: bytes) -> str:
    try:
        import torch
    except ImportError:
        return "PyTorch no está instalado en este equipo: no se pudo probar."
    try:
        checkpoint = torch.load(io.BytesIO(data), map_location="cpu", weights_only=False)
    except Exception as e:                                  # noqa: BLE001 — reporta cualquiera
        return f"PyTorch NO cargó desde memoria: {type(e).__name__} — {e}"

    task = ""
    if isinstance(checkpoint, dict):
        # Un checkpoint de ultralytics trae el modelo adentro y declara su tarea: es la
        # fricción de ese framework, que quiere una ruta en su API de alto nivel.
        task = getattr(checkpoint.get("model", None), "task", "") or ""
    extra = f" El checkpoint declara la tarea '{task}'." if task else ""
    return f"PyTorch cargó desde memoria con torch.load(BytesIO).{extra}"


def _load_with_onnxruntime(data: bytes) -> str:
    try:
        import onnxruntime
    except ImportError:
        return "ONNX Runtime no está instalado en este equipo: no se pudo probar."
    try:
        session = onnxruntime.InferenceSession(data)
    except Exception as e:                                  # noqa: BLE001 — reporta cualquiera
        return f"ONNX Runtime NO cargó desde memoria: {type(e).__name__} — {e}"
    inputs = ", ".join(node.name for node in session.get_inputs())
    return f"ONNX Runtime cargó desde memoria con InferenceSession(bytes). Entradas: {inputs}."


def _load_with_tensorrt(data: bytes) -> str:
    try:
        import tensorrt as trt
    except ImportError:
        return "TensorRT no está instalado en este equipo: no se pudo probar."
    try:
        runtime = trt.Runtime(trt.Logger(trt.Logger.ERROR))
        engine = runtime.deserialize_cuda_engine(data)
    except Exception as e:                                  # noqa: BLE001 — reporta cualquiera
        return f"TensorRT NO cargó desde memoria: {type(e).__name__} — {e}"
    if engine is None:
        return ("TensorRT devolvió None: el .engine no es de esta GPU o de esta versión. "
                "Eso no es un problema del cifrado.")
    return "TensorRT cargó desde memoria con deserialize_cuda_engine(bytes)."


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_or_build_weights(args) -> bytes:
    if args.weights:
        path = Path(args.weights)
        if not path.is_file():
            raise SystemExit(f"No existe el archivo de pesos: {path}")
        return path.read_bytes()
    # Bytes al azar y no ceros: unos ceros se comprimirían y la medición mentiría.
    return os.urandom(max(1, args.size_mb) * 1024 * 1024)


def _write_weights_file(args, plain_weights: bytes, key: bytes, metadata: dict) -> Path:
    name = Path(args.weights).name if args.weights else "synthetic.bin"
    if args.plain:
        path = _HERE / name
        path.write_bytes(plain_weights)
        return path

    path = _HERE / (name + ew.ENCRYPTED_SUFFIX)
    path.write_bytes(ew.pack(plain_weights, key, metadata))
    return path


def _megabytes(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f} MB"


if __name__ == "__main__":
    sys.exit(main())
