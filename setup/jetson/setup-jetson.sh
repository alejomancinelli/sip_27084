#!/usr/bin/env bash
#
# System-level setup of a Jetson on JetPack 6: everything that needs sudo and apt.
#
# Order on a new Jetson:
#     1. bash setup/jetson/setup-jetson.sh --camera-iface=<iface>     this script
#     2. bash setup/cameras/linux/sentech.sh   (or basler.sh)          the camera SDK
#     3. bash setup/venv-setup/venv-setup.sh                           the Python side
#
# Run it as the user that will run the app, not as root: it adds that user to the
# groups that own the GPIO chip and the serial ports. Re-running it is safe: what is
# already in place is only re-checked.
#
#     --camera-iface=IFACE   NIC the GigE camera is on: MTU 9000. Repeatable.
#     --no-hold              do not apt-mark hold the L4T boot and kernel packages
#
# Not here, on purpose: Docker. The app ships as a Nuitka build with its own launchers
# (build/), not as a container.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_FILE="$PROJECT_ROOT/config.yaml"

CAMERA_IFACES=()
HOLD_L4T=1
for _arg in "$@"; do
    case "$_arg" in
        --camera-iface=*) CAMERA_IFACES+=("${_arg#--camera-iface=}") ;;
        --no-hold)        HOLD_L4T=0 ;;
        *) echo "unknown option: $_arg" >&2; exit 2 ;;
    esac
done

step() { echo ""; echo "[STEP] $*"; }
ok()   { echo "  OK   $*"; }
note() { echo " NOTE  $*"; }
hint() { echo "        $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

# No `grep -q` at the end of a pipe in this script: -q exits on the first match, the
# command feeding it can die of SIGPIPE, and under pipefail a match reads as a miss.

# Installed according to dpkg; `dpkg -s` alone also matches removed-but-configured ones.
is_installed() {
    dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep "install ok installed" > /dev/null
}

echo "======================================================================"
echo " Jetson system setup"
echo "======================================================================"

# --- 1/7  Preconditions --------------------------------------------------------
step "1/7  Checking this is a Jetson on JetPack 6"

[ "$(id -u)" -ne 0 ] || fail "Run as the app user, not as root: sudo is called where needed."
[ -f /etc/nv_tegra_release ] || fail "/etc/nv_tegra_release not found: this is not a Jetson."

# "# R36 (release), REVISION: 4.3, ..." -> R36. JetPack 6 is L4T R36.
L4T_RELEASE="$(head -n 1 /etc/nv_tegra_release)"
L4T_MAJOR="$(echo "$L4T_RELEASE" | sed -n 's/^# R\([0-9]*\).*/\1/p')"
ok "$L4T_RELEASE"
MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
ok "Module: $MODEL"

# R35 (JetPack 5) ships Python 3.8 and its TensorRT bindings are for 3.8; the project is
# pinned to 3.10 by the stapipy wheel. R38+ moves to another Python. Neither can work.
[ "$L4T_MAJOR" = "36" ] || fail "L4T R$L4T_MAJOR found; this project needs R36 (JetPack 6.x, Python 3.10)."

# --- 2/7  L4T package hold ---------------------------------------------------------
# On a third-party carrier board an apt upgrade of these can flash a bootloader or a
# device tree made for NVIDIA's devkit, and the board stops booting.
step "2/7  Holding the L4T boot and kernel packages"

L4T_HOLD=(nvidia-l4t-bootloader nvidia-l4t-kernel nvidia-l4t-kernel-dtbs
          nvidia-l4t-kernel-headers nvidia-l4t-core)
if [ "$HOLD_L4T" -eq 1 ]; then
    _to_hold=()
    for _pkg in "${L4T_HOLD[@]}"; do
        if is_installed "$_pkg"; then _to_hold+=("$_pkg"); fi
    done
    if [ ${#_to_hold[@]} -gt 0 ]; then
        sudo apt-mark hold "${_to_hold[@]}" > /dev/null
        ok "held: ${_to_hold[*]}"
    else
        note "none of the L4T packages is installed through apt; nothing to hold"
    fi
else
    note "--no-hold given: L4T packages left as they are"
fi

# --- 3/7  apt packages -----------------------------------------------------------
step "3/7  Installing system packages"

APT_PACKAGES=(
    # Python: the venv and the headers Nuitka compiles against.
    python3.10 python3.10-venv python3.10-dev python3-pip
    # Nuitka on Linux needs patchelf for a standalone build; ccache cuts rebuilds.
    build-essential patchelf ccache
    # NVIDIA's torch for Jetson links against OpenBLAS. Harmless for another framework.
    libopenblas-dev
    # GStreamer, the RTSP server and their Python bindings. The bindings come from apt;
    # venv-setup.sh links `gi` into the venv.
    python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0
    gir1.2-gst-rtsp-server-1.0 libgstrtspserver-1.0-0
    gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good
    gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav
    # Qt 6 xcb platform plugin. Qt 6.5+ refuses to open a window without xcb-cursor.
    libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1
    libxcb-render-util0 libxcb-shape0 libxcb-xfixes0 libxcb-xinerama0 libxcb-randr0
    libegl1 libgl1 libfontconfig1 libdbus-1-3
    # GPIO command-line tools (gpioinfo). The Python binding is gpiod from PyPI, in the
    # venv: apt's python3-libgpiod is the v1 API and the app uses v2.
    gpiod
)

# --no-upgrade: installs what is missing and leaves what is there at its version. Without
# it, listing python3.10 or the GStreamer plugins upgrades them as a side effect, and the
# build Jetson drifts away from the plant's, which has to match it.
sudo apt-get update -qq
sudo apt-get install -y --no-upgrade "${APT_PACKAGES[@]}"
ok "${#APT_PACKAGES[@]} packages present"

# --- 4/7  NVIDIA runtime pieces ------------------------------------------------------
step "4/7  Checking TensorRT, the Jetson GStreamer plugins and what NVIDIA's torch loads"

# TensorRT's Python binding is a JetPack apt package for the system Python 3.10;
# venv-setup.sh links it into the venv.
if python3.10 -c "import tensorrt" > /dev/null 2>&1; then
    ok "tensorrt $(python3.10 -c 'import tensorrt; print(tensorrt.__version__)')"
else
    note "tensorrt not importable from python3.10 - installing python3-libnvinfer"
    sudo apt-get install -y --no-upgrade python3-libnvinfer
    python3.10 -c "import tensorrt" > /dev/null 2>&1 \
        || fail "tensorrt still not importable. Is the JetPack runtime installed? (sudo apt install nvidia-jetpack)"
    ok "tensorrt $(python3.10 -c 'import tensorrt; print(tensorrt.__version__)')"
fi

if ! is_installed nvidia-l4t-gstreamer; then
    sudo apt-get install -y --no-upgrade nvidia-l4t-gstreamer || note "nvidia-l4t-gstreamer not available through apt"
fi

# Which video.rtsp.codec values this module can use. The Orin Nano has no NVENC: there
# nvv4l2h264enc does not exist and only the _sw codecs work.
for _element in nvvidconv nvv4l2h264enc nvv4l2h265enc x264enc x265enc; do
    if gst-inspect-1.0 "$_element" > /dev/null 2>&1; then
        ok "GStreamer element $_element"
    else
        note "GStreamer element $_element absent"
    fi
done

# NVIDIA's torch (2.5 and later) loads libcusparseLt.so.0 at import. It is not part of
# JetPack's default install; verify_env.py shows the import error if it stays missing.
if ldconfig -p | grep "libcusparseLt.so.0" > /dev/null; then
    ok "libcusparseLt.so.0"
else
    note "libcusparseLt.so.0 not found - NVIDIA's torch 2.5+ fails to import without it."
    hint "It comes from NVIDIA's CUDA apt repository:"
    hint "  wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/arm64/cuda-keyring_1.1-1_all.deb"
    hint "  sudo dpkg -i cuda-keyring_1.1-1_all.deb && sudo apt-get update"
    hint "  sudo apt-get install -y libcusparselt0 libcusparselt-dev"
fi

# --- 5/7  Device permissions -------------------------------------------------------
# The app opens /dev/gpiochipN (GPIO) and /dev/ttyTHSn or a USB adapter (Modbus RTU)
# as a normal user. Without these groups it degrades to simulated GPIO, or the RTU
# server reports the port, and nothing says "permission".
step "5/7  GPIO chip and serial port permissions"

GPIO_RULE=/etc/udev/rules.d/99-gpiochip.rules
sudo groupadd -f gpio
if [ ! -f "$GPIO_RULE" ]; then
    echo 'SUBSYSTEM=="gpio", KERNEL=="gpiochip*", GROUP="gpio", MODE="0660"' \
        | sudo tee "$GPIO_RULE" > /dev/null
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=gpio
fi
ok "udev rule $GPIO_RULE"

_relogin=0
for _group in gpio dialout; do
    if id -nG "$USER" | tr ' ' '\n' | grep -x "$_group" > /dev/null; then
        ok "$USER is in $_group"
    else
        sudo usermod -aG "$_group" "$USER"
        ok "$USER added to $_group"
        _relogin=1
    fi
done

# The lines config.yaml declares, as the kernel sees them. Offsets are lines of the
# chip, not header pins, and they depend on the carrier board.
GPIO_SPEC="$(python3.10 - "$CONFIG_FILE" <<'EOF' 2>/dev/null || true
import sys, yaml
gpio = (yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}).get("gpio") or {}
if gpio.get("enabled"):
    lines = {**(gpio.get("outputs") or {}), **(gpio.get("inputs") or {})}
    print(gpio.get("chip", "gpiochip0"), *(f"{name}={offset}" for name, offset in lines.items()))
EOF
)"
if [ -z "$GPIO_SPEC" ]; then
    note "gpio.enabled is false in config.yaml (or the file could not be read): no lines to check"
else
    read -r _chip _lines <<< "$GPIO_SPEC"
    _chip="${_chip#/dev/}"
    if ! _info="$(gpioinfo "$_chip" 2>&1)"; then
        note "gpioinfo $_chip failed: $_info"
    else
        for _entry in $_lines; do
            _name="${_entry%%=*}"; _offset="${_entry#*=}"
            _line="$(echo "$_info" | grep -E "^[[:space:]]*line[[:space:]]+${_offset}:" || true)"
            if [ -n "$_line" ]; then
                ok "$_name -> $_chip line $_offset: $(echo "$_line" | sed 's/^[[:space:]]*line[[:space:]]*[0-9]*:[[:space:]]*//')"
            else
                note "$_name -> line $_offset does not exist on $_chip"
            fi
        done
    fi
fi

# --- 6/7  Network ------------------------------------------------------------------
# Each sysctl file is loaded on its own (-p), not with --system: that reloads every file
# in /etc/sysctl.d and prints the errors of other packages' settings as if they were ours.
step "6/7  Network: listening ports and GigE Vision cameras"

# Modbus TCP listens on 502, and Linux reserves ports below 1024 for root. The app runs
# as this user, so the threshold drops to the lowest port config.yaml enables. That
# opens the ports from there to 1023 to any user, which is fine on a dedicated machine.
# Not setcap: the venv's python is a symlink to /usr/bin/python3.10, so every Python
# script on the machine would get it, and on the compiled main.bin each update drops it.
LOW_PORT="$(python3.10 - "$CONFIG_FILE" <<'EOF' 2>/dev/null || true
import sys, yaml
config = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
def section(*keys):
    node = config
    for key in keys:
        node = (node or {}).get(key) or {}
    return node
servers = (section("modbus", "tcp"), section("video", "http"), section("video", "rtsp"))
low = [int(s.get("port") or 0) for s in servers if s.get("enabled")]
low = [port for port in low if 0 < port < 1024]
if low:
    print(min(low))
EOF
)"
PORTS_FILE=/etc/sysctl.d/61-unprivileged-ports.conf
if [ -z "$LOW_PORT" ]; then
    ok "no enabled server in config.yaml listens below port 1024"
else
    _wanted="net.ipv4.ip_unprivileged_port_start=$LOW_PORT"
    if ! grep -sx "$_wanted" "$PORTS_FILE" > /dev/null; then
        printf '%s\n' "# The app listens on port $LOW_PORT (config.yaml) as a normal user." "$_wanted" \
            | sudo tee "$PORTS_FILE" > /dev/null
        sudo sysctl --quiet -p "$PORTS_FILE"
    fi
    ok "ports from $(sysctl -n net.ipv4.ip_unprivileged_port_start) up need no root ($PORTS_FILE)"
fi

# Socket buffers: a full frame arrives in a burst, and with the default 208 KB the
# driver drops packets and the camera reports incomplete frames.
SYSCTL_FILE=/etc/sysctl.d/60-gige-vision.conf
if [ ! -f "$SYSCTL_FILE" ]; then
    printf '%s\n' "# GigE Vision: room for a full frame burst" \
        "net.core.rmem_max=16777216" "net.core.wmem_max=16777216" \
        | sudo tee "$SYSCTL_FILE" > /dev/null
    sudo sysctl --quiet -p "$SYSCTL_FILE"
fi
ok "socket buffers: $(sysctl -n net.core.rmem_max) bytes ($SYSCTL_FILE)"

if [ ${#CAMERA_IFACES[@]} -eq 0 ]; then
    note "no --camera-iface given: MTU left as it is. Ethernet interfaces here:"
    nmcli -t -f DEVICE,TYPE,STATE device 2>/dev/null | grep ":ethernet:" | sed 's/^/        /' || true
fi
for _iface in "${CAMERA_IFACES[@]}"; do
    # By interface, not by connection name: NetworkManager names connections after a MAC
    # address or "Wired connection 1", and that changes between boards.
    _uuid="$(nmcli -t -f UUID,DEVICE connection show 2>/dev/null | grep ":${_iface}$" | cut -d: -f1 | head -n 1 || true)"
    if [ -z "$_uuid" ]; then
        note "$_iface: no NetworkManager connection; set MTU 9000 on it by hand"
        continue
    fi
    sudo nmcli connection modify "$_uuid" 802-3-ethernet.mtu 9000
    sudo nmcli connection up "$_uuid" > /dev/null
    ok "$_iface: MTU $(cat "/sys/class/net/$_iface/mtu")"
done

# --- 7/7  Summary ------------------------------------------------------------------
step "7/7  Done"
echo ""
[ "$_relogin" -eq 0 ] || echo " Log out and back in (or reboot): the new groups apply to new sessions."
echo " Next:"
echo "   1. Camera SDK:   bash setup/cameras/linux/sentech.sh"
echo "   2. The vendor wheels the fork's framework needs (NVIDIA torch...) into packages/ - see setup/jetson/requirements.txt"
echo "   3. Python side:  bash setup/venv-setup/venv-setup.sh"
echo "   4. The .engine into models/, matching inference.models.*.path in config.yaml"
