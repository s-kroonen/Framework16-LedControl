#!/usr/bin/env bash
# Build the Linux ELF binary.
# Run from the project root with the venv activated:
#   source .venv/bin/activate
#   ./build_linux.sh

set -e

echo "Installing/updating dependencies..."
pip install -r requirements.txt

echo "Building with PyInstaller..."
pyinstaller led_control.spec

echo ""
echo "Done. Binary: dist/LedMatrixControl"
echo ""
echo "Linux runtime notes:"
echo "  - pystray requires a system tray: GNOME needs 'gnome-shell-extension-appindicator'"
echo "  - Temperature sensors: install lm-sensors and run 'sudo sensors-detect'"
echo "  - iGPU busy %: works on AMD (DRM sysfs); Intel/NVIDIA require extra tooling"
echo "  - Battery Saver auto-off: requires power-profiles-daemon or UPower"
echo "  - Serial port access: add your user to the 'dialout' group:"
echo "      sudo usermod -aG dialout \$USER  (then log out and back in)"
