#!/bin/bash
# Base Jetson setup: Docker, NVIDIA toolkit, GStreamer, GPIO, system deps.
# Camera SDK installation is handled separately — see setup/cameras/.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CAMERA_ARG=""
PIP_CMD=""

for arg in "$@"; do
    case "$arg" in
        --camera=*) CAMERA_ARG="${arg#--camera=}" ;;
        --camera)   CAMERA_ARG="__next__" ;;
        *)          [ "$CAMERA_ARG" = "__next__" ] && CAMERA_ARG="$arg" ;;
    esac
done

echo "=== Jetson Setup ==="
echo "Target: Seeed reComputer J4011 / JetPack 6.1"
echo ""

# ── Hold NVIDIA L4T packages ──────────────────────────────────────────────────
# Holding these packages prevents apt from ever upgrading them and breaking the system.
echo "Holding NVIDIA L4T packages to prevent bootloader flash on third-party board..."
sudo apt-mark hold \
    nvidia-l4t-bootloader \
    nvidia-l4t-kernel \
    nvidia-l4t-kernel-dtbs \
    nvidia-l4t-kernel-headers \
    nvidia-l4t-core
echo "L4T packages held."
echo ""

# ── Python pip ───────────────────────────────────────────────────────────────
echo "Installing python3-pip..."
sudo apt-get update -qq
sudo apt-get install -y python3-pip
# Resolve pip command for the rest of this script (and used by setup/cameras/sentech.sh)
if command -v pip3 &>/dev/null; then
    PIP_CMD="pip3"
else
    PIP_CMD="python3 -m pip"
fi
echo "pip ready: $PIP_CMD"
echo ""

# ── Docker Engine ─────────────────────────────────────────────────────────────
echo "[Docker] Checking Docker installation..."
if command -v docker &> /dev/null; then
    echo "[Docker] Docker already installed: $(docker --version)"
else
    echo "[Docker] Installing Docker Engine..."
    sudo apt-get update
    sudo apt-get install -y ca-certificates curl gnupg lsb-release

    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
        sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
        "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu \
        $(lsb_release -cs) stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

    sudo usermod -aG docker "$USER"
    echo "[Docker] Docker installed. NOTE: reboot for group changes to take effect."
fi

# ── NVIDIA Container Toolkit ──────────────────────────────────────────────────
if dpkg -s nvidia-container-toolkit &>/dev/null; then
    echo "[Docker] NVIDIA Container Toolkit already installed: $(dpkg -s nvidia-container-toolkit | grep Version | awk '{print $2}')"
else
    echo "[Docker] Installing NVIDIA Container Toolkit..."
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
        sudo gpg --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

    curl -sL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
        sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
        sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

    sudo apt-get update
    sudo apt-get install -y nvidia-container-toolkit
fi

sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

echo "[Docker] NVIDIA Container Toolkit ready."
echo "[Docker] Test with: docker run --rm --runtime nvidia ubuntu:22.04 nvidia-smi"
echo ""

# ── GStreamer RTSP ────────────────────────────────────────────────────────────
echo "Installing GStreamer + RTSP server..."
sudo apt-get install -y \
    python3-gi \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    libgstrtspserver-1.0-0 \
    libgstrtspserver-1.0-dev \
    gir1.2-gst-rtsp-server-1.0

# On JetPack 6.x the GPU GStreamer plugins ship as nvidia-l4t-gstreamer.
echo "Installing Jetson GPU GStreamer plugins..."
if sudo apt-get install -y nvidia-l4t-gstreamer 2>/dev/null; then
    echo "nvidia-l4t-gstreamer installed."
else
    echo "Note: nvidia-l4t-gstreamer not found via apt (likely pre-installed with JetPack)."
fi

# Verify GPU H.264 encoding element is actually available
if gst-inspect-1.0 nvv4l2h264enc &>/dev/null; then
    echo "GPU H.264 encoding (nvv4l2h264enc): available."
else
    echo "Warning: nvv4l2h264enc not found — RTSP pipeline will use software x264enc."
fi

echo "GStreamer ready."
echo ""

# ── GPIO ──────────────────────────────────────────────────────────────────────
echo "Installing gpiod..."
sudo apt-get install -y libgpiod2

echo "Checking GPIO availability..."
gpioinfo gpiochip0 2>/dev/null | grep -E "pin (51|52|53|50|105|144|106|43)" || \
  echo "Warning: Expected GPIO pins not found. Check Seeed J4011 pinout."

echo "GPIO ready. Test with: python3 tools/gpio_control.py"
echo ""

# ── Network: MTU + socket buffers (GigE cameras) ─────────────────────────────
echo "Configuring network for GigE cameras..."

# Camera interface name comes from config.yaml → red.eth0.interfaz_os
CAM_IFACE="enP1p1s0"

# Resolve the connection UUID by interface name — works regardless of what
# NetworkManager named the connection (MAC address, "Wired connection 1", etc.)
CAM_UUID=$(nmcli -t -f UUID,DEVICE connection show 2>/dev/null | \
           grep ":${CAM_IFACE}$" | cut -d: -f1 | head -1)

if [ -n "$CAM_UUID" ]; then
    sudo nmcli connection modify "$CAM_UUID" 802-3-ethernet.mtu 9000
    sudo nmcli connection up "$CAM_UUID"
    echo "MTU 9000 set on $CAM_IFACE (UUID: $CAM_UUID)."
else
    echo "Warning: No NetworkManager connection found for interface $CAM_IFACE."
    echo "  Configure MTU manually — see README.md → 'Configurar MTU para cámaras GigE'."
fi

# Socket receive/send buffers for GigE Vision (persistent across reboots)
SYSCTL_CONF="/etc/sysctl.conf"
if grep -q "net.core.rmem_max" "$SYSCTL_CONF"; then
    echo "Socket buffers already configured in $SYSCTL_CONF, skipping."
else
    printf "\n# GigE Vision socket buffers\nnet.core.rmem_max=16777216\nnet.core.wmem_max=16777216\n" | \
        sudo tee -a "$SYSCTL_CONF" > /dev/null
    sudo sysctl -p
    echo "Socket buffers configured (rmem/wmem = 16 MB)."
fi

echo "Network ready."
echo ""

# ── Additional system packages ────────────────────────────────────────────────
echo "Installing additional dependencies..."
sudo apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libxcb-shape0 \
    libxcb-xfixes0

echo ""

echo "=== Setup complete ==="
echo "Next steps:"
echo "  1. Install the corresponding camera drivers"
echo "  2. Update config.yaml with your camera IPs and RS485 port (/dev/ttyTHS1)"
echo "  3. Place TensorRT .engine model in ./models/"
echo "  4. Run: docker compose up --build -d"
echo "  5. Check: docker compose logs -f vision-app"
