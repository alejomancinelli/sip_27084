#!/usr/bin/env bash
#
# Creates and provisions the project virtual environment on Linux.
#
# Linux counterpart of venv-setup.ps1. The one substantive difference is
# --system-site-packages: GStreamer's Python bindings (python3-gi) and libgpiod's
# (python3-libgpiod) are installed by apt as native modules and cannot be pip
# installed, so the venv has to be able to see them. Everything the app needs from
# PyPI still gets installed into the venv.
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
step "3/5  Installing dependencies (large download: TensorFlow is ~250 MB)"

"$VENV_PYTHON" -m pip install --upgrade pip setuptools wheel || fail "Failed to upgrade pip."
"$VENV_PYTHON" -m pip install -r "$REQUIREMENTS" || fail "'pip install -r requirements.txt' failed."
ok "requirements.txt installed"

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
