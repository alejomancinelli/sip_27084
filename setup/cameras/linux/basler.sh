#!/bin/bash
# Basler camera pylon SDK setup.
# Run from anywhere: bash setup/cameras/linux/basler.sh
#
# The Windows counterpart is setup/cameras/windows/basler.ps1, which only
# diagnoses: there the pypylon wheel bundles the runtime and no SDK is needed.
set -e

SCRIPT_TAG="Basler"
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

log "Looking for the pylon setup archive in $PACKAGES_DIR ..."
PYLON_TGZ="$(find_first_match 'pylon*.tar.gz')"

if [ -z "$PYLON_TGZ" ]; then
    fail "No pylon archive found in $PACKAGES_DIR" \
         "Download pylon-*.tar.gz from https://www.baslerweb.com/downloads/software/" \
         "and place it in packages/ before re-running."
fi

log "Found: $PYLON_TGZ"

# The archive name carries the architecture: catch an x86 tarball on the Jetson
# before tar unpacks binaries that will never load.
case "$(basename "$PYLON_TGZ")" in
    *"$HOST_ARCH"*) ;;
    *) log "WARNING: $(basename "$PYLON_TGZ") does not mention $HOST_ARCH. Wrong build?" ;;
esac

if [ -d /opt/pylon ]; then
    log "/opt/pylon already exists. Remove it first if you want to reinstall:"
    hint "sudo rm -rf /opt/pylon"
    show_detected_cameras
    exit 0
fi

# Extract the outer setup archive into a temp directory.
SETUP_DIR="$(mktemp -d)"
trap 'rm -rf "$SETUP_DIR"' EXIT

log "Extracting setup archive..."
tar -C "$SETUP_DIR" -xzf "$PYLON_TGZ"

# Locate the inner pylon SDK archive produced by the extraction.
PYLON_SDK="$(find_first_match 'pylon*.tar.gz' "$SETUP_DIR")"
if [ -z "$PYLON_SDK" ]; then
    echo "    Contents of the extracted archive:" >&2
    ls "$SETUP_DIR" >&2
    fail "Inner pylon-*.tar.gz not found inside the setup archive."
fi

log "Installing the pylon SDK to /opt/pylon..."
sudo mkdir -p /opt/pylon
sudo tar -C /opt/pylon -xzf "$PYLON_SDK"
sudo chmod 755 /opt/pylon

# Symlink so 'pylonviewer' works system-wide.
if [ -f /opt/pylon/bin/pylonviewer ]; then
    sudo ln -sf /opt/pylon/bin/pylonviewer /usr/local/bin/pylonviewer
    log "pylonviewer symlink created."
else
    log "WARNING: pylonviewer not found at /opt/pylon/bin/pylonviewer - skipping symlink."
fi

show_detected_cameras

echo ""
log "pylon SDK installed to /opt/pylon/"
hint "Viewer:      pylonviewer"
hint "Python test: python3 -c 'import pypylon.pylon; print(pypylon.pylon.TlFactory.GetInstance())'"
echo ""
hint "NOTE: the pypylon Python binding is installed via requirements.txt (or by pip"
hint "      inside Docker). No additional step is needed for camera capture."
hint "Set cameras.camera_1.driver: basler_gige in config.yaml to use it."
