#!/usr/bin/env bash

# ==============================================================================
# Uninstaller for Ravens Perch
# ----------------------------------------
# - Stops and disables systemd services
# - Removes service files and symlinks
# - Removes MediaMTX binary and config
# - Optionally removes Python virtual environment
# - Preserves raven_settings.yml by default
#
# Last modified: 2026-01-13
# ==============================================================================

set -e

BASE_DIR="$(dirname $(realpath $0))"
VENV_DIR="$BASE_DIR/venv"
SERVICE_DIR="/etc/systemd/system"
RENDERED_DIR="$BASE_DIR/services"
MEDIAMTX_DIR="$BASE_DIR/mediamtx"

echo "=================================================="
echo "  Ravens Perch Uninstaller"
echo "=================================================="
echo ""

# Stop and disable services
echo "🛑 Stopping services..."
for service in mediamtx snapfeeder raven-watchdog web-ui camera-hotplug; do
    if systemctl is-active --quiet ${service}.service 2>/dev/null; then
        echo "   Stopping ${service}..."
        sudo systemctl stop ${service}.service || true
    fi
    if systemctl is-enabled --quiet ${service}.service 2>/dev/null; then
        echo "   Disabling ${service}..."
        sudo systemctl disable ${service}.service || true
    fi
done

# Remove service symlinks from systemd
echo ""
echo "🔗 Removing service symlinks..."
for service in mediamtx snapfeeder raven-watchdog web-ui camera-hotplug; do
    if [ -L "$SERVICE_DIR/${service}.service" ]; then
        echo "   Removing $SERVICE_DIR/${service}.service"
        sudo rm -f "$SERVICE_DIR/${service}.service"
    fi
done

# Reload systemd
echo ""
echo "🔄 Reloading systemd..."
sudo systemctl daemon-reload

# Remove rendered services directory
if [ -d "$RENDERED_DIR" ]; then
    echo ""
    echo "🗑️  Removing rendered services directory..."
    rm -rf "$RENDERED_DIR"
fi

# Remove MediaMTX directory
if [ -d "$MEDIAMTX_DIR" ]; then
    echo ""
    echo "🗑️  Removing MediaMTX directory..."
    rm -rf "$MEDIAMTX_DIR"
fi

# Ask about virtual environment
echo ""
read -p "🐍 Remove Python virtual environment? (y/N): " remove_venv
if [[ "$remove_venv" == "y" || "$remove_venv" == "Y" ]]; then
    if [ -d "$VENV_DIR" ]; then
        echo "   Removing venv..."
        rm -rf "$VENV_DIR"
    fi
else
    echo "   Keeping venv (can be reused on reinstall)"
fi

# Ask about settings file
echo ""
SETTINGS_FILE="$MEDIAMTX_DIR/../mediamtx/raven_settings.yml"
if [ -f "$BASE_DIR/mediamtx/raven_settings.yml" ]; then
    SETTINGS_FILE="$BASE_DIR/mediamtx/raven_settings.yml"
fi

if [ -f "$SETTINGS_FILE" ]; then
    read -p "⚙️  Remove raven_settings.yml? (y/N): " remove_settings
    if [[ "$remove_settings" == "y" || "$remove_settings" == "Y" ]]; then
        echo "   Removing settings file..."
        rm -f "$SETTINGS_FILE"
    else
        echo "   Keeping settings file (preserves your configuration)"
    fi
fi

# Summary
echo ""
echo "=================================================="
echo "  ✅ Uninstallation Complete"
echo "=================================================="
echo ""
echo "The following have been removed:"
echo "  - MediaMTX binary and default config"
echo "  - Systemd service files"
echo "  - Rendered service configurations"
if [[ "$remove_venv" == "y" || "$remove_venv" == "Y" ]]; then
    echo "  - Python virtual environment"
fi
if [[ "$remove_settings" == "y" || "$remove_settings" == "Y" ]]; then
    echo "  - Camera settings file"
fi
echo ""
echo "The ravens-perch source directory remains at:"
echo "  $BASE_DIR"
echo ""
echo "To completely remove, run:"
echo "  rm -rf $BASE_DIR"
echo ""
