"""
Verifies that the project virtual environment is complete and usable.

Checks two different things, because an import that succeeds is not proof that the
code will run: every dependency imports, AND the specific APIs this project calls
still exist in the installed versions. A pinned range in requirements.txt says what
we asked for; this says what we got.

Run by venv-setup at the end of the install, and useful on its own as a diagnostic:

    .venv\\Scripts\\python.exe setup\\venv-setup\\verify_env.py     # Windows
    .venv/bin/python setup/venv-setup/verify_env.py                 # Linux

Exits 1 when a mandatory requirement is unmet, 0 otherwise. An optional dependency
that is missing is reported and does not fail the run: without a camera SDK only the
mock driver works, and the Linux-only modules are expected to be absent on Windows.
"""

import importlib
import os
import platform
import sys

# Keras 3 imports matplotlib, which may pick a Qt backend built against PyQt5 and
# abort the process next to PySide6. Has to be set before anything else is imported.
os.environ.setdefault("MPLBACKEND", "Agg")

OK, FAIL, WARN = "  OK  ", " FAIL ", " WARN "

_SEPARATOR = "=" * 70
_DIVIDER = "-" * 70

# (module, pip name, mandatory). The mandatory ones are what the app imports at
# startup; the optional ones each disable one feature and say so.
REQUIRED = [
    ("PySide6", "PySide6", True),
    ("numpy", "numpy", True),
    ("cv2", "opencv-python-headless", True),
    ("yaml", "PyYAML", True),
    ("psutil", "psutil", True),
    ("pymodbus", "pymodbus", True),
    ("serial", "pyserial", True),
    ("influxdb_client", "influxdb-client", True),
    ("paho.mqtt", "paho-mqtt", True),
    ("tensorflow", "tensorflow", True),
    ("stardist", "stardist", True),
    ("csbdeep", "csbdeep", True),
    ("pytest", "pytest", True),
    # Cameras: at least one SDK is needed to capture from real hardware.
    ("pypylon.pylon", "pypylon", False),
    ("stapipy", "SentechSDK wheel in packages/", False),
]

# Camera SDKs, so the summary can tell "no SDK" from "one failed to import".
CAMERA_MODULES = ("pypylon.pylon", "stapipy")

# Linux-only modules. Each degrades to a no-op inside its own module and the app
# starts either way, so on Windows their absence is the expected outcome.
LINUX_ONLY = [
    ("gpiod", "GPIO"),
    ("gi", "RTSP / GStreamer"),
]


def _version(module) -> str:
    """Version reported by a module, or '?' when it does not publish one."""
    for attribute in ("__version__", "VERSION", "version"):
        value = getattr(module, attribute, None)
        if isinstance(value, str):
            return value
    return "?"


def _check_imports() -> tuple[list[str], list[str]]:
    """Imports every entry of REQUIRED. Returns the unmet mandatory ones and the
    camera SDKs that are available."""
    errors: list[str] = []
    cameras: list[str] = []
    for module_name, pip_name, mandatory in REQUIRED:
        try:
            module = importlib.import_module(module_name)
        except BaseException as exc:
            # BaseException on purpose: a compiled extension that cannot find its
            # DLLs may raise SystemExit, which does not derive from Exception.
            print(f"[{FAIL if mandatory else WARN}] {module_name:<18} "
                  f"{type(exc).__name__}: {exc}")
            if mandatory:
                errors.append(f"{module_name} (pip install {pip_name})")
            continue
        print(f"[{OK}] {module_name:<18} {_version(module)}")
        if module_name in CAMERA_MODULES:
            cameras.append(module_name)
    return errors, cameras


def _check_platform_modules():
    """Reports the Linux-only modules without ever failing the run."""
    for module_name, feature in LINUX_ONLY:
        try:
            importlib.import_module(module_name)
        except BaseException:
            print(f"[{WARN}] {module_name:<18} absent - {feature} disabled "
                  f"(expected on Windows)")
        else:
            print(f"[{OK}] {module_name:<18} present ({feature})")


def _check_pymodbus_api() -> str:
    """
    Confirms the server context takes `devices=`, or returns the reason it cannot.

    The keyword was `slaves=` up to pymodbus 3.9 and became `devices=` in 3.10, so
    the range in requirements.txt is not decorative: with an older build the Modbus
    server fails at construction, long after the install looked successful.
    """
    try:
        import inspect

        from pymodbus.datastore import ModbusServerContext

        parameters = inspect.signature(ModbusServerContext.__init__).parameters
        if "devices" not in parameters:
            return ("ModbusServerContext does not accept `devices=` "
                    "(pymodbus < 3.10). Requires pymodbus>=3.8,<4.")
    except BaseException as exc:
        return f"{type(exc).__name__}: {exc}"
    return ""


def _report_inference_device():
    """Says whether TensorFlow sees a GPU. No GPU is a note, not a failure."""
    try:
        import tensorflow as tf

        gpus = tf.config.list_physical_devices("GPU")
    except BaseException as exc:
        print(f"[{FAIL}] tensorflow device  {type(exc).__name__}: {exc}")
        return f"TensorFlow is installed but not operational: {exc}"
    if gpus:
        print(f"[{OK}] tensorflow device  {len(gpus)} GPU(s) detected")
    else:
        print(f"[{WARN}] tensorflow device  no GPU - inference runs on CPU "
              f"(expected on Windows)")
    return ""


def main() -> int:
    print(_SEPARATOR)
    print(" Project environment verification")
    print(_SEPARATOR)
    print(f" Python     : {sys.version.split()[0]} ({platform.architecture()[0]})")
    print(f" Executable : {sys.executable}")
    print(f" Platform   : {platform.system()} {platform.release()}")
    print(f" MPLBACKEND : {os.environ.get('MPLBACKEND')}")
    print(_DIVIDER)

    errors, cameras = _check_imports()

    print(_DIVIDER)
    _check_platform_modules()

    print(_DIVIDER)
    pymodbus_error = _check_pymodbus_api()
    if pymodbus_error:
        print(f"[{FAIL}] pymodbus API       {pymodbus_error}")
        errors.append(f"pymodbus API: {pymodbus_error}")
    else:
        print(f"[{OK}] pymodbus API       ModbusServerContext(devices=...) available")

    tensorflow_error = _report_inference_device()
    if tensorflow_error:
        errors.append(tensorflow_error)

    print(_DIVIDER)
    if cameras:
        print(f"[{OK}] camera SDKs        available: {', '.join(cameras)}")
    else:
        print(f"[{WARN}] camera SDKs        none installed - only the 'mock' driver "
              f"can capture. See setup/cameras/.")

    print(_SEPARATOR)
    if errors:
        print(f" RESULT: {len(errors)} mandatory requirement(s) unmet:")
        for reason in errors:
            print(f"   - {reason}")
        return 1
    print(" RESULT: environment OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
