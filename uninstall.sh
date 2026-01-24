#!/bin/bash
#
# Ravens Perch v3 Uninstall Script
# Removes all components installed by install.sh
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

INSTALL_DIR="${HOME}/ravens-perch"
KLIPPER_CONFIG_DIR="${HOME}/printer_data/config"

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

echo ""
echo -e "${YELLOW}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${YELLOW}║            Ravens Perch v3.0 Uninstaller                   ║${NC}"
echo -e "${YELLOW}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Confirm uninstall
read -p "This will remove Ravens Perch. Continue? (y/N): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Cancelled."
    exit 0
fi

echo ""

# Stop services
log_info "Stopping services..."
for service in ravens-perch mediamtx; do
    if systemctl is-active --quiet ${service}.service 2>/dev/null; then
        log_info "Stopping ${service}..."
        sudo systemctl stop ${service}.service || true
    fi
done
log_success "Services stopped"

# Disable services
log_info "Disabling services..."
for service in ravens-perch mediamtx; do
    if systemctl is-enabled --quiet ${service}.service 2>/dev/null; then
        sudo systemctl disable ${service}.service || true
    fi
done
log_success "Services disabled"

# Remove service files
log_info "Removing service files..."
for service in ravens-perch mediamtx; do
    if [ -f "/etc/systemd/system/${service}.service" ]; then
        sudo rm -f "/etc/systemd/system/${service}.service"
    fi
done
sudo systemctl daemon-reload
log_success "Service files removed"

# Remove nginx configuration
log_info "Removing nginx configuration..."

# Try to remove from common nginx configs
configs=(
    "/etc/nginx/sites-available/fluidd"
    "/etc/nginx/sites-available/mainsail"
    "/etc/nginx/sites-enabled/default"
)

for conf in "${configs[@]}"; do
    if [ -f "$conf" ]; then
        if grep -q "location /cameras/" "$conf" 2>/dev/null; then
            log_info "Removing /cameras/ location from ${conf}..."
            # Remove the location block (this is a best-effort removal)
            sudo sed -i '/# Ravens Perch Camera UI/,/^[[:space:]]*}/d' "$conf" 2>/dev/null || true
        fi
    fi
done

# Remove standalone config
if [ -f "/etc/nginx/conf.d/ravens-perch.conf" ]; then
    sudo rm -f "/etc/nginx/conf.d/ravens-perch.conf"
fi

# Reload nginx
if sudo nginx -t 2>/dev/null; then
    sudo systemctl reload nginx || true
fi
log_success "Nginx configuration removed"

# Remove Moonraker update_manager entry
log_info "Removing Moonraker configuration..."
moonraker_conf="${KLIPPER_CONFIG_DIR}/moonraker.conf"
if [ -f "${moonraker_conf}" ]; then
    if grep -q "\[update_manager ravens-perch\]" "${moonraker_conf}"; then
        # Remove the ravens-perch section
        sudo sed -i '/\[update_manager ravens-perch\]/,/^$/d' "${moonraker_conf}" 2>/dev/null || true
        log_success "Removed from Moonraker configuration"
    fi
fi

# Ask about keeping data
echo ""
read -p "Keep database and logs (for reinstall)? (Y/n): " keep_data
if [[ "$keep_data" == "n" || "$keep_data" == "N" ]]; then
    log_info "Removing data directory..."
    rm -rf "${INSTALL_DIR}/data"
    rm -rf "${INSTALL_DIR}/logs"
    log_success "Data removed"
else
    log_info "Keeping data directory"
fi

# Ask about removing install directory
echo ""
read -p "Remove entire install directory (${INSTALL_DIR})? (y/N): " remove_all
if [[ "$remove_all" == "y" || "$remove_all" == "Y" ]]; then
    log_info "Removing install directory..."
    rm -rf "${INSTALL_DIR}"
    log_success "Install directory removed"
else
    # Only remove specific components
    log_info "Removing components but keeping directory..."
    rm -rf "${INSTALL_DIR}/venv" 2>/dev/null || true
    rm -rf "${INSTALL_DIR}/mediamtx" 2>/dev/null || true
    rm -rf "${INSTALL_DIR}/daemon" 2>/dev/null || true
    log_success "Components removed"
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║            Ravens Perch Uninstalled Successfully           ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "The following have been removed:"
echo "  - Systemd services (ravens-perch, mediamtx)"
echo "  - Nginx configuration (/cameras/ location)"
echo "  - Moonraker update_manager entry"
if [[ "$remove_all" == "y" || "$remove_all" == "Y" ]]; then
    echo "  - Install directory (${INSTALL_DIR})"
else
    echo "  - Python virtual environment"
    echo "  - MediaMTX binary"
    echo "  - Daemon module"
fi
if [[ "$keep_data" != "n" && "$keep_data" != "N" ]]; then
    echo ""
    echo "Data preserved at: ${INSTALL_DIR}/data"
    echo "Logs preserved at: ${INSTALL_DIR}/logs"
fi
echo ""
