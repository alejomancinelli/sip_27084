#!/usr/bin/env bash
#
# Creates and provisions the project virtual environment on Linux.
#
# Linux counterpart of venv-setup.ps1. Two substantive differences:
#
#   - --system-site-packages: GStreamer's Python bindings (python3-gi) and TensorRT's
#     (python3-libnvinfer) are installed by apt as native modules for the system
#     Python and cannot be pip installed, so the venv has to be able to see them.
#     Everything the app needs from PyPI still gets installed into the venv, and
#     shadows any older apt copy of the same module.
#   - On a Jetson it also installs the inference framework from setup/jetson/: the
#     vendor wheels in packages/ first, then the rest pinned by its constraints.txt.
#     On any other machine the framework is not installed: the mock model covers
#     development. Run setup/jetson/setup-jetson.sh before this on a Jetson.
#
#     ./setup/venv-setup/venv-setup.sh
#     ./setup/venv-setup/venv-setup.sh --force
#     ./setup/venv-setup/venv-setup.sh --skip-install
#
# Exits with the code of verify_env.py, so it can gate an install script.

set -euo pipefail

FORCE=0
SKIP_INSTALL=0
for _arg in "$@"; do
    case "$_arg" in
        --force)        FORCE=1 ;;
        --skip-install) SKIP_INSTALL=1 ;;
        *) echo "unknown option: $_arg" >&2; exit 2 ;;
    esac
done

# Paths resolve from the script location, not the CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_PATH="$PROJECT_ROOT/.venv"
VENV_PYTHON="$VENV_PATH/bin/python"
REQUIREMENTS="$PROJECT_ROOT/requirements.txt"
PACKAGES_DIR="$PROJECT_ROOT/packages"
VERIFY_SCRIPT="$SCRIPT_DIR/verify_env.py"
JETSON_DIR="$PROJECT_ROOT/setup/jetson"

IS_JETSON=0
[ -f /etc/nv_tegra_release ] && IS_JETSON=1

step() { echo ""; echo "[STEP] $*"; }
ok()   { echo "  OK   $*"; }
note() { echo " NOTE  $*"; }
hint() { echo "        $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

echo "======================================================================"
echo " Project environment provisioning (Linux)"
echo "======================================================================"
echo " Project: $PROJECT_ROOT"

# --- 1/5  Locate CPython 3.10 ------------------------------------------------
# Pinned to 3.10: that is the interpreter the vendor camera wheels are built for,
# and on the Jetson it is what JetPack ships.
step "1/5  Locating CPython 3.10"

PY_EXE=""
for _candidate in python3.10 python3; do
    if command -v "$_candidate" >/dev/null 2>&1; then
        if "$_candidate" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 10) else 1)'; then
            PY_EXE="$(command -v "$_candidate")"
            break
        fi
    fi
done

[ -n "$PY_EXE" ] || fail "CPython 3.10 not found. Install it with 'sudo apt install python3.10 python3.10-venv', then retry."
ok "Python $("$PY_EXE" -c 'import platform; print(platform.python_version())') - $PY_EXE"

# --- 2/5  Create the venv ----------------------------------------------------
step "2/5  Creating the virtual environment"

if [ -d "$VENV_PATH" ]; then
    if [ "$FORCE" -eq 1 ]; then
        note "Removing the existing .venv (--force)"
        rm -rf "$VENV_PATH"
    else
        note ".venv already exists - reusing it. Use --force to recreate."
    fi
fi

if [ ! -d "$VENV_PATH" ]; then
    # --system-site-packages: python3-gi and python3-libgpiod come from apt.
    "$PY_EXE" -m venv --system-site-packages "$VENV_PATH" \
        || fail "venv creation failed. Is python3.10-venv installed?"
    ok "venv created at $VENV_PATH"
fi

[ -x "$VENV_PYTHON" ] || fail "$VENV_PYTHON is missing - the venv is corrupt. Retry with --force."

if [ "$SKIP_INSTALL" -eq 1 ]; then
    note "--skip-install given: stopping after venv creation."
    exit 0
fi

# --- 3/5  PyPI dependencies --------------------------------------------------
step "3/5  Installing dependencies"

"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel || fail "Failed to upgrade pip."

if [ "$IS_JETSON" -eq 0 ]; then
    "$VENV_PYTHON" -m pip install -r "$REQUIREMENTS" || fail "'pip install -r requirements.txt' failed."
    ok "requirements.txt installed"
    note "Not a Jetson: the inference framework (setup/jetson/) is not installed."
else
    CONSTRAINTS="$JETSON_DIR/constraints.txt"
    [ -f "$CONSTRAINTS" ] || fail "$CONSTRAINTS is missing."

    # Vendor wheels first, without dependencies: the framework builds that see the GPU
    # (NVIDIA's torch) are not on PyPI, and once they are installed and satisfy
    # constraints.txt nothing below replaces them. No -c here: pip refuses to constrain
    # a wheel given by path, so the pins are checked by the next install instead, and a
    # wheel of the wrong version fails there. stapipy is step 4.
    shopt -s nullglob
    _vendor=()
    for _wheel in "$PACKAGES_DIR"/*aarch64.whl; do
        case "$(basename "$_wheel")" in stapipy*) ;; *) _vendor+=("$_wheel") ;; esac
    done
    shopt -u nullglob
    if [ ${#_vendor[@]} -gt 0 ]; then
        "$VENV_PYTHON" -m pip install --no-deps "${_vendor[@]}" \
            || fail "Installing the vendor wheels in packages/ failed."
        for _wheel in "${_vendor[@]}"; do ok "vendor wheel $(basename "$_wheel")"; done
    else
        note "No vendor aarch64 wheels in packages/ - the next step will fail on any"
        hint "framework pinned in setup/jetson/constraints.txt that PyPI does not have."
    fi

    "$VENV_PYTHON" -m pip install -c "$CONSTRAINTS" -r "$REQUIREMENTS" \
        || fail "'pip install -r requirements.txt' failed."
    ok "requirements.txt installed"

    if [ -f "$JETSON_DIR/requirements.txt" ]; then
        "$VENV_PYTHON" -m pip install -c "$CONSTRAINTS" -r "$JETSON_DIR/requirements.txt" \
            || fail "'pip install -r setup/jetson/requirements.txt' failed."
        ok "setup/jetson/requirements.txt installed"
    fi

    # Packages whose declared dependencies would replace something installed above.
    if [ -f "$JETSON_DIR/requirements-nodeps.txt" ]; then
        "$VENV_PYTHON" -m pip install --no-deps -c "$CONSTRAINTS" \
            -r "$JETSON_DIR/requirements-nodeps.txt" \
            || fail "'pip install --no-deps -r setup/jetson/requirements-nodeps.txt' failed."
        ok "setup/jetson/requirements-nodeps.txt installed (--no-deps)"
    fi
fi

# --- 4/5  stapipy (Sentech) from the local wheel -----------------------------
# Not published on PyPI: it ships inside the SentechSDK. Watch the architecture -
# the Jetson needs the aarch64 wheel, a desktop the x86_64 one.
step "4/5  Installing stapipy (Sentech) if a wheel exists in packages/"

STAPI_WHEEL=""
if [ -d "$PACKAGES_DIR" ]; then
    shopt -s nullglob
    _matches=("$PACKAGES_DIR"/stapipy*"$(uname -m)".whl)
    shopt -u nullglob
    [ ${#_matches[@]} -gt 0 ] && STAPI_WHEEL="${_matches[0]}"
fi

if [ -n "$STAPI_WHEEL" ]; then
    if "$VENV_PYTHON" -m pip install "$STAPI_WHEEL"; then
        ok "stapipy installed from $(basename "$STAPI_WHEEL")"
    else
        note "Install of $(basename "$STAPI_WHEEL") failed - st_gige unavailable."
    fi
else
    note "No stapipy wheel for $(uname -m) in packages/ - st_gige driver unavailable."
    hint "To enable Sentech cameras: install the SentechSDK with Python support,"
    hint "copy the matching wheel into packages/, and re-run this script (or"
    hint "setup/cameras/linux/sentech.sh)."
fi

# --- 5/5  Verification -------------------------------------------------------
step "5/5  Verifying the environment"

set +e
"$VENV_PYTHON" "$VERIFY_SCRIPT"
VERIFY_EXIT=$?
set -e

echo ""
echo "======================================================================"
if [ "$VERIFY_EXIT" -eq 0 ]; then
    echo " Environment ready."
    echo "======================================================================"
    echo ""
    echo " Run:    .venv/bin/python main.py             (with window)"
    echo "         .venv/bin/python main.py --headless  (no window)"
    echo " Tests:  .venv/bin/python -m pytest -q"
else
    echo " Environment INCOMPLETE - review the failures above."
    echo "======================================================================"
fi

exit "$VERIFY_EXIT"
