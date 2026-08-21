#!/bin/bash
# Sentech / Omron GigE camera SDK setup.
# Run from anywhere: bash setup/cameras/linux/sentech.sh
#
# The Windows counterpart is setup/cameras/windows/sentech.ps1.
set -e

SCRIPT_TAG="Sentech"
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

# The vendor ships one installer and one wheel per architecture, and their names
# are the only way to tell them apart.
case "$HOST_ARCH" in
    aarch64)
        SDK_PATTERN='SentechSDK*Linux64*ARM*.run'
        WHEEL_PATTERN='stapipy*linux_aarch64*.whl'
        ;;
    *)
        fail "Only aarch64 (Jetson) is wired up. Detected: $HOST_ARCH." \
             "Add this architecture's installer and wheel filename patterns above."
        ;;
esac

log "Looking for the installer in $PACKAGES_DIR ..."
SENTECH_RUN="$(find_first_match "$SDK_PATTERN")"
SENTECH_WHL="$(find_first_match "$WHEEL_PATTERN")"

if [ -z "$SENTECH_RUN" ] && [ -z "$SENTECH_WHL" ]; then
    fail "Neither installer nor wheel found in $PACKAGES_DIR" \
         "Required files:" \
         "  packages/SentechSDK-X.Y.Z-Linux64-ARM-install.run" \
         "  packages/stapipy-X.Y.Z-cpXXX-cpXXX-linux_aarch64.whl"
elif [ -z "$SENTECH_WHL" ]; then
    fail "Sentech SDK installer found but the stapipy wheel is missing." \
         "Place the Linux $HOST_ARCH wheel in packages/ and re-run."
elif [ -z "$SENTECH_RUN" ]; then
    fail "stapipy wheel found but the Sentech SDK installer is missing." \
         "Place SentechSDK-*-Linux64-ARM-install.run in packages/ and re-run."
fi

log "Installing the system SDK: $SENTECH_RUN"
chmod +x "$SENTECH_RUN"
sudo "$SENTECH_RUN"
sudo ldconfig

log "Installing the stapipy Python binding: $SENTECH_WHL"
install_wheel "$SENTECH_WHL"

# Register the Sentech libraries with the dynamic linker - no LD_LIBRARY_PATH needed.
sudo tee /etc/ld.so.conf.d/sentech.conf > /dev/null <<'EOF'
/opt/sentech/lib
/opt/sentech/lib/GenICam
EOF
sudo ldconfig

# GENICAM_GENTL64_PATH must be an env var (the GenTL layer uses it to find .cti files).
# /etc/environment is sourced by PAM for every session - login, non-login, SSH, GUI.
if ! grep -q "GENICAM_GENTL64_PATH" /etc/environment; then
    echo "GENICAM_GENTL64_PATH=/opt/sentech/lib" | sudo tee -a /etc/environment > /dev/null
fi

# Keep /etc/profile.d/ as a fallback for login shells that bypass PAM.
sudo tee /etc/profile.d/sentech.sh > /dev/null <<'EOF'
export LD_LIBRARY_PATH=/opt/sentech/lib:/opt/sentech/lib/GenICam:${LD_LIBRARY_PATH:-}
export GENICAM_GENTL64_PATH=/opt/sentech/lib
EOF

# Apply inside this script process for the commands that follow.
export LD_LIBRARY_PATH=/opt/sentech/lib:/opt/sentech/lib/GenICam:${LD_LIBRARY_PATH:-}
export GENICAM_GENTL64_PATH=/opt/sentech/lib

# Symlink so 'stviewer' works system-wide.
if [ -f /opt/sentech/bin/StViewer ]; then
    sudo ln -sf /opt/sentech/bin/StViewer /usr/local/bin/stviewer
    log "stviewer symlink created."
else
    log "WARNING: StViewer not found at /opt/sentech/bin/StViewer - skipping symlink."
fi

show_detected_cameras

echo ""
log "SDK installed to /opt/sentech/"
hint "StViewer:    stviewer"
hint "Network cfg: sudo /opt/sentech/bin/setnetwork.sh"
hint "Python test: python3 -c 'import stapipy; print(stapipy.__version__)'"
echo ""
hint "Libraries registered via /etc/ld.so.conf.d/sentech.conf (ldconfig)."
hint "GENICAM_GENTL64_PATH added to /etc/environment - available in all new sessions."
hint "To apply in this terminal without re-login:"
hint "  source /etc/profile.d/sentech.sh"
hint "Set cameras.camera_1.driver: st_gige in config.yaml to use it."
