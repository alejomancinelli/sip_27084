"""
Verifies that the project virtual environment is complete and usable on this machine.

Checks two different things, because an import that succeeds is not proof that the
code will run: every dependency imports, AND the specific APIs and devices this project
uses are there in the installed versions. A pinned range in requirements.txt says what
we asked for; this says what we got.

**The same check does not weigh the same on every machine.** On the Jetson —the target,
detected by /etc/nv_tegra_release— the inference framework, the GPU, the model files
and what config.yaml turns on (GPIO, the RTSP codec) are requirements: without them the
app still starts, degraded, and catching that before the plant does is the point. On a
development PC they are notes: the mock model covers inference and the Linux-only
modules are expected to be absent.

**Machinery, with one block per fork.** The framework the fork's model runs on, and how
to probe its GPU, are in the block marked below. Everything else is generic and asks the
app instead of repeating it: the paths come from `system.paths`, the RTSP codec is
built by `rtsp_server.probe_codec()`, and what is enabled comes from config.yaml.

Run by venv-setup at the end of the install, and useful on its own as a diagnostic:

    .venv\\Scripts\\python.exe setup\\venv-setup\\verify_env.py     # Windows
    .venv/bin/python setup/venv-setup/verify_env.py                 # Linux

Exits 1 when a requirement is unmet, 0 otherwise.
"""

import ctypes.util
import importlib
import importlib.metadata
import os
import platform
import sys

# A framework that imports matplotlib may make it pick a Qt backend built against PyQt5,
# which aborts the process next to PySide6. Has to be set before anything is imported.
os.environ.setdefault("MPLBACKEND", "Agg")

# The app's own modules answer what they know: paths, the codec pipeline.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

OK, FAIL, WARN = "  OK  ", " FAIL ", " WARN "

_SEPARATOR = "=" * 70
_DIVIDER = "-" * 70

_IS_LINUX = sys.platform.startswith("linux")
_TEGRA_RELEASE = "/etc/nv_tegra_release"
IS_JETSON = os.path.isfile(_TEGRA_RELEASE)

# (module, where it comes from). What the app imports at startup, on every machine.
REQUIRED = [
    ("PySide6", "pip: PySide6"),
    ("numpy", "pip: numpy"),
    ("cv2", "pip: opencv-python-headless" if _IS_LINUX else "pip: opencv-python"),
    ("yaml", "pip: PyYAML"),
    ("psutil", "pip: psutil"),
    ("pymodbus", "pip: pymodbus"),
    ("serial", "pip: pyserial"),
    ("influxdb_client", "pip: influxdb-client"),
    ("paho.mqtt", "pip: paho-mqtt"),
    # Without it the license verifier rejects every license instead of degrading.
    ("cryptography", "pip: cryptography"),
    ("pytest", "pip: pytest"),
]

# At least one is needed to capture from real hardware; which one is per installation.
CAMERA_SDKS = [
    ("pypylon.pylon", "pip: pypylon"),
    ("stapipy", "SentechSDK wheel in packages/ - setup/cameras/"),
]


# ── What changes in each fork ─────────────────────────────────────────────────────
#
# The framework the fork's model runs on: (module, where it comes from). Required on the
# Jetson; on a development PC a missing one is a note, because the mock model runs there.
INFERENCE_MODULES = [
    ("torch", "NVIDIA wheel in packages/ - setup/jetson/requirements.txt"),
    ("torchvision", "wheel in packages/ - setup/jetson/requirements.txt"),
    ("tensorrt", "apt: python3-libnvinfer, linked into the venv by venv-setup.sh"),
    ("ultralytics", "setup/jetson/requirements-nodeps.txt"),
]


def probe_inference_device() -> str:
    """
    What the model needs from the GPU beyond importing: '' if it is there, why if not.

    torch importing is not torch seeing CUDA: a PyPI build imports fine and runs on CPU
    twenty times slower. And a torchvision built against another torch imports fine and
    fails on the first frame, inside the NMS ultralytics calls; running that op here is
    what catches it.
    """
    import torch
    import torchvision

    if not torch.cuda.is_available():
        return "torch does not see CUDA - a PyPI build instead of NVIDIA's wheel?"
    print(f"[{OK}] {'cuda device':<18} {torch.cuda.get_device_name(0)}")
    boxes = torch.tensor([[0.0, 0.0, 1.0, 1.0]], device="cuda")
    torchvision.ops.nms(boxes, torch.tensor([1.0], device="cuda"), 0.5)
    print(f"[{OK}] {'torchvision nms':<18} runs on CUDA")
    return ""

# ── End of the fork block ─────────────────────────────────────────────────────────


def _verdict(label: str, problem: str, required: bool, detail: str = "") -> list[str]:
    """Prints one line; returns the problem as an unmet requirement when it is one."""
    if not problem:
        print(f"[{OK}] {label:<18} {detail}")
        return []
    print(f"[{FAIL if required else WARN}] {label:<18} {problem}")
    return [f"{label}: {problem}"] if required else []


def _import(module_name: str):
    """The module and '', or None and why it did not import."""
    try:
        return importlib.import_module(module_name), ""
    except BaseException as exc:
        # BaseException on purpose: a compiled extension that cannot find its native
        # libraries may raise SystemExit, which does not derive from Exception.
        return None, f"{type(exc).__name__}: {exc}"


def _version(module) -> str:
    """
    Version reported by a module, or by the package that installed it; '?' if neither.

    Some vendor bindings (stapipy) publish no `__version__`, and theirs is the version
    that matters when the native libraries they were built for do not match.
    """
    for attribute in ("__version__", "VERSION", "version"):
        value = getattr(module, attribute, None)
        if isinstance(value, str):
            return value
    top_level = module.__name__.split(".")[0]
    for distribution in importlib.metadata.packages_distributions().get(top_level, []):
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            continue
    return "?"


def _outside_venv(module) -> str:
    """
    Where the module was loaded from, if that is outside this venv; '' if it is inside.

    The release is built from the venv —`make_release.py` copies the camera packages
    from its site-packages— so a module that imports from ~/.local or the system
    passes here and is missing on the plant's machine. The apt modules venv-setup.sh
    links count as inside: the link lives in the venv. Compared without resolving
    links on purpose.
    """
    location = getattr(module, "__file__", None)
    if not location or sys.prefix == sys.base_prefix:
        return ""
    prefix = os.path.normcase(os.path.abspath(sys.prefix)) + os.sep
    return "" if os.path.normcase(os.path.abspath(location)).startswith(prefix) else location


def _check_modules(entries: list, required: bool) -> tuple[list[str], list[str]]:
    """Imports each entry. Returns the unmet requirements and the modules that imported."""
    errors: list[str] = []
    present: list[str] = []
    for module_name, source in entries:
        module, problem = _import(module_name)
        if module is not None:
            outside = _outside_venv(module)
            if outside:
                problem = f"loaded from outside the venv: {outside} - install it into the venv"
            else:
                present.append(module_name)
        errors += _verdict(module_name, f"{problem} ({source})" if problem else "",
                           required, _version(module) if module else "")
    return errors, present


def _load_config() -> dict:
    """config.yaml from the data directory the app will use, or {} if it cannot be read."""
    try:
        import yaml

        from system.paths import DATA_DIR

        with open(os.path.join(DATA_DIR, "config.yaml"), encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except BaseException as exc:
        print(f"[{WARN}] {'config.yaml':<18} {type(exc).__name__}: {exc} - "
              f"the checks that depend on it are skipped")
        return {}


def _get(config: dict, dotted_key: str, default=None):
    """`config['a']['b']` for 'a.b', or `default` if any level is missing."""
    node = config
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _check_numpy_abi() -> list[str]:
    """
    numpy 1.x, as requirements.txt pins it.

    The compiled wheels next to it —NVIDIA's torch, OpenCV 4.8— are built against the
    numpy 1 ABI, and with numpy 2 they fail at import with a message about a module
    compiled for another version, which does not name numpy as the cause.
    """
    module, problem = _import("numpy")
    if module is None:
        return []
    major = int(module.__version__.split(".")[0])
    return _verdict("numpy ABI", f"numpy {module.__version__}: the pin is <2.0" if major >= 2
                    else "", True, "1.x")


def _check_opencv_variant() -> list[str]:
    """
    On Linux, only the headless OpenCV.

    The GUI wheel ships its own Qt 5 and points Qt's plugin path at it, which beats
    PySide6 when cv2 is imported before the window. Installing both is worse: they
    write the same `cv2/` and the last one wins.
    """
    if not _IS_LINUX:
        return []
    try:
        version = importlib.metadata.version("opencv-python")
    except importlib.metadata.PackageNotFoundError:
        return _verdict("opencv variant", "", IS_JETSON, "headless only")
    return _verdict("opencv variant",
                    f"opencv-python {version} is installed - uninstall it and reinstall "
                    f"opencv-python-headless", IS_JETSON)


def _check_qt_platform() -> list[str]:
    """
    On Linux, the system library Qt 6.5+ needs to open a window.

    Missing it, the window does not open ("could not load the Qt platform plugin xcb")
    and the headless mode still works, so it is never a failure here.
    """
    if not _IS_LINUX:
        return []
    found = ctypes.util.find_library("xcb-cursor")
    return _verdict("qt xcb-cursor", "" if found else "libxcb-cursor0 missing - the window "
                    "will not open; --headless works", False, found or "")


def _check_pymodbus_api() -> list[str]:
    """
    The server context takes `devices=`.

    The keyword was `slaves=` up to pymodbus 3.9 and became `devices=` in 3.10, so the
    range in requirements.txt is not decorative: with an older build the Modbus server
    fails at construction, long after the install looked successful.
    """
    try:
        import inspect

        from pymodbus.datastore import ModbusServerContext

        parameters = inspect.signature(ModbusServerContext.__init__).parameters
        problem = ("" if "devices" in parameters else
                   "ModbusServerContext does not accept `devices=` (pymodbus < 3.10)")
    except BaseException as exc:
        problem = f"{type(exc).__name__}: {exc}"
    return _verdict("pymodbus API", problem, True, "ModbusServerContext(devices=...)")


def _check_gpio(config: dict) -> list[str]:
    """
    The libgpiod v2 binding, and the chip config.yaml names, readable by this user.

    apt's python3-libgpiod is the v1 API: it imports, has no `request_lines`, and the
    app drops to simulated GPIO with the outputs never driven. A chip without
    permission does the same. Required on the Jetson when `gpio.enabled` is true.
    """
    if not _IS_LINUX:
        return _verdict("gpiod", "absent - GPIO simulated (expected on Windows)", False)
    required = IS_JETSON and bool(_get(config, "gpio.enabled", False))
    module, problem = _import("gpiod")
    if module is None:
        return _verdict("gpiod", f"{problem} (pip: gpiod>=2.1)", required)
    if not hasattr(module, "request_lines"):
        return _verdict("gpiod", f"{_version(module)} is the v1 API (apt python3-libgpiod?) "
                        f"- the app needs v2: pip install 'gpiod>=2.1'", required)
    errors = _verdict("gpiod", "", required, f"{_version(module)} (v2 API)")

    chip = str(_get(config, "gpio.chip", "") or "").strip()
    if not chip:
        return errors
    chip_path = chip if chip.startswith("/") else f"/dev/{chip}"
    if not os.path.exists(chip_path):
        problem = f"{chip_path} does not exist - see `gpiodetect`"
    elif not os.access(chip_path, os.R_OK | os.W_OK):
        problem = (f"{chip_path} not readable and writable by this user - "
                   f"setup/jetson/setup-jetson.sh, then log in again")
    else:
        problem = ""
    return errors + _verdict("gpio chip", problem, required, chip_path)


def _check_rtsp(config: dict) -> list[str]:
    """
    The configured RTSP codec, built the way the server builds it.

    `probe_codec()` assembles the same pipeline the server assembles for its first
    client, so a missing plugin or a hardware encoder this module does not have —the
    Orin Nano has none— shows up here and not with the first player. Required on the
    Jetson when `video.rtsp.enabled` is true.
    """
    codec = str(_get(config, "video.rtsp.codec", "") or "h264_sw")
    required = IS_JETSON and bool(_get(config, "video.rtsp.enabled", False))
    try:
        from system.video.rtsp_server import probe_codec

        problem = probe_codec(codec)
    except BaseException as exc:
        problem = f"{type(exc).__name__}: {exc}"
    if problem and not _IS_LINUX:
        problem += " (expected on Windows)"
    return _verdict("rtsp codec", problem, required, f"{codec} pipeline builds")


def _check_models(config: dict) -> list[str]:
    """
    Every model file config.yaml points at exists, where the app will look for it.

    The weights are not in the repository (`models/` is gitignored) and a TensorRT
    engine is built on the Jetson, so a fresh checkout has none. Required on the Jetson.
    """
    models = _get(config, "inference.models", {}) or {}
    if not isinstance(models, dict):
        return []
    from system.paths import resolve

    errors: list[str] = []
    for slot, options in models.items():
        raw_path = str((options or {}).get("path", "") or "").strip()
        if not raw_path:
            continue
        path = resolve(raw_path, "")
        errors += _verdict(f"model {slot}", "" if os.path.isfile(path) else
                           f"{path} not found", IS_JETSON,
                           f"{os.path.getsize(path) / 1e6:.0f} MB" if os.path.isfile(path)
                           else "")
    return errors


def _check_inference(config: dict) -> list[str]:
    """The fork's framework: imports, then its GPU probe once everything imported."""
    if not INFERENCE_MODULES:
        print(f"[{OK}] {'inference':<18} no framework declared - the mock model needs none")
        return []
    errors, present = _check_modules(INFERENCE_MODULES, IS_JETSON)
    if len(present) < len(INFERENCE_MODULES):
        if not IS_JETSON:
            print(f"        not a Jetson: the mock model covers inference here")
        return errors
    try:
        problem = probe_inference_device()
    except BaseException as exc:
        problem = f"{type(exc).__name__}: {exc}"
    if problem:
        errors += _verdict("inference device", problem, IS_JETSON)
    return errors


def main() -> int:
    print(_SEPARATOR)
    print(" Project environment verification")
    print(_SEPARATOR)
    print(f" Python     : {sys.version.split()[0]} ({platform.architecture()[0]})")
    print(f" Executable : {sys.executable}")
    print(f" Platform   : {platform.system()} {platform.release()} {platform.machine()}")
    if IS_JETSON:
        with open(_TEGRA_RELEASE, encoding="utf-8", errors="replace") as handle:
            print(f" Jetson     : {handle.readline().strip().lstrip('# ')}")
    else:
        print(" Jetson     : no - inference, GPIO and RTSP are reported, not required")
    print(_DIVIDER)

    errors, _ = _check_modules(REQUIRED, True)
    errors += _check_numpy_abi()
    errors += _check_opencv_variant()
    errors += _check_pymodbus_api()
    errors += _check_qt_platform()

    print(_DIVIDER)
    _, cameras = _check_modules(CAMERA_SDKS, False)
    if not cameras:
        print(f"[{WARN}] {'camera SDKs':<18} none - only the 'mock' driver can capture. "
              f"See setup/cameras/.")

    print(_DIVIDER)
    config = _load_config()
    errors += _check_gpio(config)
    errors += _check_rtsp(config)

    print(_DIVIDER)
    errors += _check_inference(config)
    errors += _check_models(config)

    print(_SEPARATOR)
    if errors:
        print(f" RESULT: {len(errors)} requirement(s) unmet:")
        for reason in errors:
            print(f"   - {reason}")
        return 1
    print(" RESULT: environment OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
