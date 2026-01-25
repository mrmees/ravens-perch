#!/bin/bash
#
# Ravens Perch Uninstall Script
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
echo -e "${YELLOW}║              Ravens Perch Uninstaller                      ║${NC}"
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

# Remove include directive from nginx configs
configs=(
    "/etc/nginx/sites-available/fluidd"
    "/etc/nginx/sites-available/mainsail"
    "/etc/nginx/sites-enabled/default"
)

for conf in "${configs[@]}"; do
    if [ -f "$conf" ]; then
        # Remove include line (new snippet-based approach)
        if grep -q "ravens-perch.conf" "$conf" 2>/dev/null; then
            log_info "Removing include directive from ${conf}..."
            sudo sed -i '/ravens-perch\.conf/d' "$conf" 2>/dev/null || true
        fi
        # Also handle legacy inline location block (old installations)
        if grep -q "# Ravens Perch Camera UI" "$conf" 2>/dev/null; then
            log_info "Removing legacy location block from ${conf}..."
            sudo sed -i '/# Ravens Perch Camera UI/,/^[[:space:]]*}/d' "$conf" 2>/dev/null || true
        fi
    fi
done

# Remove snippet file
if [ -f "/etc/nginx/snippets/ravens-perch.conf" ]; then
    log_info "Removing nginx snippet..."
    sudo rm -f "/etc/nginx/snippets/ravens-perch.conf"
fi

# Remove legacy standalone config (old installations)
if [ -f "/etc/nginx/conf.d/ravens-perch.conf" ]; then
    sudo rm -f "/etc/nginx/conf.d/ravens-perch.conf"
fi

# Reload nginx
if sudo nginx -t 2>/dev/null; then
    sudo systemctl reload nginx || true
fi
log_success "Nginx configuration removed"

# Remove printer UI integrations
log_info "Removing printer UI integrations..."
config_dir="${HOME}/printer_data/config"

# Remove Mainsail sidebar entry
mainsail_sidebar="${config_dir}/.theme/sidebar.json"
if [ -f "$mainsail_sidebar" ]; then
    if grep -q "Ravens Perch" "$mainsail_sidebar" 2>/dev/null; then
        log_info "Removing Ravens Perch from Mainsail sidebar..."
        python3 - "$mainsail_sidebar" << 'PYTHON_SCRIPT' 2>/dev/null || true
import sys
import json

sidebar_file = sys.argv[1]

with open(sidebar_file, 'r') as f:
    sidebar = json.load(f)

# Remove Ravens Perch entry
sidebar = [item for item in sidebar if item.get('title') != 'Ravens Perch']

if sidebar:
    with open(sidebar_file, 'w') as f:
        json.dump(sidebar, f, indent=2)
else:
    # Empty array - delete the file
    import os
    os.remove(sidebar_file)
PYTHON_SCRIPT
        log_success "Removed Ravens Perch from Mainsail sidebar"
    fi
fi

# Remove Fluidd CSS
fluidd_css="${config_dir}/.fluidd-theme/custom.css"
if [ -f "$fluidd_css" ]; then
    if grep -q "Ravens Perch Integration" "$fluidd_css" 2>/dev/null; then
        log_info "Removing Ravens Perch CSS from Fluidd theme..."
        # Remove the Ravens Perch section using sed
        sed -i '/\/\* Ravens Perch Integration \*\//,/^$/d' "$fluidd_css" 2>/dev/null || true
        # If file is now empty (just whitespace), remove it
        if [ ! -s "$fluidd_css" ] || ! grep -q '[^[:space:]]' "$fluidd_css" 2>/dev/null; then
            rm -f "$fluidd_css"
        fi
        log_success "Removed Ravens Perch CSS from Fluidd theme"
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
echo "  - Printer UI integrations (Mainsail sidebar, Fluidd CSS)"
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
