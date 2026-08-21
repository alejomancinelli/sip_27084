# Shared paths and helpers for the Linux camera setup scripts.
#
# Source it from a sibling script:
#
#     source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
#
# Every path lives here, so moving this folder is a one-line fix instead of one
# per script. Not executable on its own.

# This file lives in setup/cameras/linux/, so the repo root is three levels up.
_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CAMERAS_DIR="$(cd "$_COMMON_DIR/.." && pwd)"
PROJECT_ROOT="$(cd "$_COMMON_DIR/../../.." && pwd)"
PACKAGES_DIR="$PROJECT_ROOT/packages"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
LIST_CAMERAS="$CAMERAS_DIR/list_cameras.py"

# aarch64 on the Jetson, x86_64 on a desktop.
HOST_ARCH="$(uname -m)"

# Each script sets SCRIPT_TAG before sourcing this file; the default keeps the
# prefix readable if one forgets.
SCRIPT_TAG="${SCRIPT_TAG:-setup}"

log()  { echo "[$SCRIPT_TAG] $*"; }
hint() { echo "    $*"; }

fail() {
    echo "ERROR: $1" >&2
    shift
    for _line in "$@"; do
        echo "    $_line" >&2
    done
    exit 1
}

# Prints the first file matching the glob, or nothing. Searches packages/ unless a
# directory is given. Uses an array instead of `ls` so filenames with spaces survive.
find_first_match() {
    local _pattern="$1"
    local _dir="${2:-$PACKAGES_DIR}"
    local _matches=()
    # nullglob: a pattern with no match expands to nothing instead of to itself.
    shopt -s nullglob
    _matches=("$_dir"/$_pattern)
    shopt -u nullglob
    if [ ${#_matches[@]} -gt 0 ]; then
        printf '%s\n' "${_matches[0]}"
    fi
    return 0
}

# Installs a wheel into the project venv, falling back to the system Python.
# Without this the binding lands outside the venv the app actually runs on.
install_wheel() {
    local _wheel="$1"
    if [ -x "$VENV_PYTHON" ]; then
        "$VENV_PYTHON" -m pip install "$_wheel"
    else
        log "WARNING: no venv at $PROJECT_ROOT/.venv - installing into the system Python."
        python3 -m pip install "$_wheel"
    fi
}

# Enumerates cameras to prove the SDK works. No camera attached is not a failure.
show_detected_cameras() {
    if [ ! -f "$LIST_CAMERAS" ]; then
        log "list_cameras.py not found at $LIST_CAMERAS; skipping enumeration."
        return 0
    fi

    local _python="python3"
    [ -x "$VENV_PYTHON" ] && _python="$VENV_PYTHON"

    echo ""
    echo "--- Cameras visible to the installed SDKs ---"
    "$_python" "$LIST_CAMERAS" || log "No camera attached right now. Support is installed correctly."
    return 0
}
