#!/bin/bash
#
# Ravens Perch Uninstall Script
# Removes all components installed by install.sh
#
# Usage:
#   ./uninstall.sh        # Full uninstall with prompts
#   ./uninstall.sh --dev  # Dev mode: keep venv/mediamtx, clear database
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

# Parse arguments
DEV_MODE=false
if [[ "$1" == "--dev" ]]; then
    DEV_MODE=true
fi

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
if [ "$DEV_MODE" = true ]; then
    echo -e "${YELLOW}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║         Ravens Perch Uninstaller (Dev Mode)                ║${NC}"
    echo -e "${YELLOW}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "Dev mode: Keeps venv and mediamtx, clears database"
    echo ""
else
    echo -e "${YELLOW}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║              Ravens Perch Uninstaller                      ║${NC}"
    echo -e "${YELLOW}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
fi

# Confirm uninstall
read -p "This will remove Ravens Perch. Continue? (y/N): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Cancelled."
    exit 0
fi

echo ""

# Remove Ravens Perch cameras from Moonraker (before stopping services)
log_info "Removing Ravens Perch cameras from Moonraker..."
MOONRAKER_URL="http://127.0.0.1:7125"

# Get list of webcams and remove any registered by Ravens Perch
webcams=$(curl -s "${MOONRAKER_URL}/server/webcams/list" 2>/dev/null)
if [ -n "$webcams" ]; then
    # Extract UIDs of Ravens Perch cameras (those with /cameras/ in stream URL or ravens in name)
    echo "$webcams" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    webcams = data.get('result', {}).get('webcams', [])
    for cam in webcams:
        stream_url = cam.get('stream_url', '')
        name = cam.get('name', '').lower()
        uid = cam.get('uid', '')
        # Identify Ravens Perch cameras by stream URL pattern or name
        if '/cameras/' in stream_url or 'ravens' in name or uid.startswith('ravens-'):
            print(uid)
except:
    pass
" 2>/dev/null | while read uid; do
        if [ -n "$uid" ]; then
            log_info "Removing camera: $uid"
            curl -s -X DELETE "${MOONRAKER_URL}/server/webcams/item?uid=${uid}" >/dev/null 2>&1 || true
        fi
    done
    log_success "Ravens Perch cameras removed from Moonraker"
else
    log_info "Moonraker not accessible or no webcams found"
fi

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
mainsail_navi="${config_dir}/.theme/navi.json"
if [ -f "$mainsail_navi" ]; then
    if grep -q "Ravens Perch" "$mainsail_navi" 2>/dev/null; then
        log_info "Removing Ravens Perch from Mainsail sidebar..."
        python3 - "$mainsail_navi" << 'PYTHON_SCRIPT' 2>/dev/null || true
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

# Check if crowsnest was disabled during install
backup_dir="${INSTALL_DIR}/data/crowsnest_backup"
if [ -f "${backup_dir}/migration_date" ]; then
    echo ""
    log_info "Crowsnest was disabled during Ravens Perch installation"
    read -p "Restore crowsnest? (Y/n): " restore_crowsnest

    if [[ "$restore_crowsnest" != "n" && "$restore_crowsnest" != "N" ]]; then
        # Uncomment crowsnest section in moonraker.conf
        moonraker_conf="${KLIPPER_CONFIG_DIR}/moonraker.conf"
        if [ -f "$moonraker_conf" ]; then
            if grep -q "Ravens Perch: crowsnest disabled" "$moonraker_conf" 2>/dev/null; then
                log_info "Restoring crowsnest section in moonraker.conf..."
                python3 - "$moonraker_conf" << 'PYTHON_SCRIPT' 2>/dev/null || true
import sys
import re

conf_file = sys.argv[1]

with open(conf_file, 'r') as f:
    content = f.read()

# Find the commented crowsnest section and uncomment it
pattern = r'# --- Ravens Perch: crowsnest disabled ---\n(.*?)# --- End crowsnest section ---'
match = re.search(pattern, content, re.DOTALL)

if match:
    section = match.group(1)
    # Uncomment each line (remove leading "# ")
    uncommented = '\n'.join(
        line[2:] if line.startswith('# ') else line
        for line in section.split('\n')
    )
    content = content[:match.start()] + uncommented.strip() + '\n' + content[match.end():]

    with open(conf_file, 'w') as f:
        f.write(content)
    print("Crowsnest section restored")
PYTHON_SCRIPT
                log_success "Crowsnest section restored in moonraker.conf"
            fi
        fi

        # Restore crowsnest.conf from backup
        crowsnest_backup="${KLIPPER_CONFIG_DIR}/crowsnest.backup"
        crowsnest_conf="${KLIPPER_CONFIG_DIR}/crowsnest.conf"
        if [ -f "$crowsnest_backup" ] && [ ! -f "$crowsnest_conf" ]; then
            log_info "Restoring crowsnest.conf from backup..."
            mv "$crowsnest_backup" "$crowsnest_conf"
            log_success "Restored crowsnest.conf"
        fi

        # Re-enable and start crowsnest service
        log_info "Re-enabling crowsnest service..."
        if systemctl list-unit-files crowsnest.service >/dev/null 2>&1; then
            sudo systemctl enable crowsnest.service 2>/dev/null || true
            sudo systemctl start crowsnest.service 2>/dev/null || true
            log_success "Crowsnest service re-enabled and started"
        else
            log_warn "Crowsnest service not found - may need manual reinstall"
        fi

        # Restart Moonraker
        if systemctl is-active --quiet moonraker.service 2>/dev/null; then
            log_info "Restarting Moonraker..."
            sudo systemctl restart moonraker.service || true
            log_success "Moonraker restarted"
        fi

        log_success "Crowsnest restored"
    else
        log_info "Keeping crowsnest disabled"
    fi
fi

# Handle data and install directory based on mode
if [ "$DEV_MODE" = true ]; then
    # Dev mode: clear database but keep venv and mediamtx
    log_info "Clearing database (dev mode)..."
    rm -f "${INSTALL_DIR}/data/ravens_perch.db" 2>/dev/null || true
    rm -rf "${INSTALL_DIR}/logs"/* 2>/dev/null || true
    log_success "Database and logs cleared"
    log_info "Keeping venv and mediamtx for faster reinstall"
else
    # Full mode: prompt user
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
fi

echo ""
if [ "$DEV_MODE" = true ]; then
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║       Ravens Perch Dev Uninstall Complete                  ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "Removed:"
    echo "  - Systemd services (ravens-perch, mediamtx)"
    echo "  - Nginx configuration (/cameras/ location)"
    echo "  - Printer UI integrations (Mainsail sidebar, Fluidd CSS)"
    echo "  - Moonraker camera registrations"
    echo "  - Database contents"
    echo ""
    echo "Kept for faster reinstall:"
    echo "  - Python virtual environment (${INSTALL_DIR}/venv)"
    echo "  - MediaMTX binary (${INSTALL_DIR}/mediamtx)"
    echo ""
    echo "Run 'bash install.sh' to reinstall quickly"
else
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
fi
echo ""
