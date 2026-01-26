#!/bin/bash
#
# Ravens Perch Installation Script
# Zero-touch camera management for Klipper-based 3D printers
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
RAVENS_USER="${USER}"
INSTALL_DIR="${HOME}/ravens-perch"
MEDIAMTX_VERSION="v1.5.1"
KLIPPER_CONFIG_DIR="${HOME}/printer_data/config"
MOONRAKER_URL="http://127.0.0.1:7125"

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Detect architecture
detect_arch() {
    local arch=$(uname -m)
    case $arch in
        x86_64|amd64)
            echo "amd64"
            ;;
        aarch64|arm64)
            echo "arm64v8"
            ;;
        armv7l|armhf)
            echo "armv7"
            ;;
        armv6l)
            echo "armv6"
            ;;
        *)
            log_error "Unsupported architecture: $arch"
            exit 1
            ;;
    esac
}

# Detect if running on Raspberry Pi
is_raspberry_pi() {
    if [ -f /proc/device-tree/model ]; then
        if grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
            return 0
        fi
    fi
    if grep -qi "raspberry pi\|bcm" /proc/cpuinfo 2>/dev/null; then
        return 0
    fi
    return 1
}

# Detect Rockchip platform
is_rockchip() {
    if [ -f /proc/device-tree/compatible ]; then
        if grep -q "rockchip" /proc/device-tree/compatible 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

# Check if running as correct user
check_user() {
    if [ "$EUID" -eq 0 ]; then
        log_error "Do not run this script as root. Run as your normal user."
        exit 1
    fi
}

# Migrate from crowsnest
migrate_from_crowsnest() {
    # Check if crowsnest service exists
    if ! systemctl list-unit-files crowsnest.service >/dev/null 2>&1; then
        return
    fi

    echo ""
    log_info "Detected crowsnest installation"
    echo ""
    echo "Ravens Perch can replace crowsnest for camera management."
    echo "This will:"
    echo "  - Stop and disable the crowsnest service"
    echo "  - Rename crowsnest.conf to crowsnest.backup"
    echo "  - Comment out crowsnest in moonraker.conf"
    echo "  - Clear existing camera configurations from Moonraker"
    echo "  - All changes are reversible during uninstall"
    echo ""
    read -p "Migrate from crowsnest to Ravens Perch? (y/N): " migrate_choice

    if [[ "$migrate_choice" != "y" && "$migrate_choice" != "Y" ]]; then
        log_info "Keeping crowsnest active alongside Ravens Perch"
        log_warn "Note: You may need to manually configure cameras to avoid conflicts"
        return
    fi

    local backup_dir="${INSTALL_DIR}/data/crowsnest_backup"
    mkdir -p "$backup_dir"

    # Stop and disable crowsnest
    log_info "Stopping crowsnest service..."
    if systemctl is-active --quiet crowsnest.service 2>/dev/null; then
        sudo systemctl stop crowsnest.service || true
    fi
    if systemctl is-enabled --quiet crowsnest.service 2>/dev/null; then
        sudo systemctl disable crowsnest.service || true
    fi
    log_success "Crowsnest service stopped and disabled"

    # Create migration marker
    echo "$(date -Iseconds)" > "${backup_dir}/migration_date"

    # Rename crowsnest.conf to disable its camera definitions
    local crowsnest_conf="${KLIPPER_CONFIG_DIR}/crowsnest.conf"
    if [ -f "$crowsnest_conf" ]; then
        log_info "Renaming crowsnest.conf to crowsnest.backup..."
        mv "$crowsnest_conf" "${crowsnest_conf%.conf}.backup"
        log_success "Renamed crowsnest.conf to crowsnest.backup"
    fi

    # Backup current cameras from Moonraker
    log_info "Backing up camera configuration from Moonraker..."
    if curl -s "${MOONRAKER_URL}/server/webcams/list" > "${backup_dir}/webcams.json" 2>/dev/null; then
        log_success "Camera configuration backed up to ${backup_dir}/webcams.json"
    else
        log_warn "Could not backup cameras (Moonraker may not be running)"
    fi

    # Comment out crowsnest section in moonraker.conf
    local moonraker_conf="${KLIPPER_CONFIG_DIR}/moonraker.conf"
    if [ -f "$moonraker_conf" ]; then
        if grep -q "\[update_manager crowsnest\]" "$moonraker_conf" 2>/dev/null; then
            log_info "Commenting out crowsnest in moonraker.conf..."
            # Backup moonraker.conf
            cp "$moonraker_conf" "${backup_dir}/moonraker.conf.bak"

            # Use Python to safely comment out the section
            python3 - "$moonraker_conf" << 'PYTHON_SCRIPT'
import sys
import re

conf_file = sys.argv[1]

with open(conf_file, 'r') as f:
    content = f.read()

# Find the [update_manager crowsnest] section and comment it out
# Match from [update_manager crowsnest] to the next section or end of file
pattern = r'(\[update_manager crowsnest\].*?)(?=\n\[|\Z)'
match = re.search(pattern, content, re.DOTALL)

if match:
    section = match.group(1)
    # Comment out each line
    commented = '\n'.join('# ' + line if line.strip() and not line.startswith('#') else line
                          for line in section.split('\n'))
    # Add marker comment
    commented = '# --- Ravens Perch: crowsnest disabled ---\n' + commented + '\n# --- End crowsnest section ---'
    content = content[:match.start()] + commented + content[match.end():]

    with open(conf_file, 'w') as f:
        f.write(content)
    print("Crowsnest section commented out")
else:
    print("Crowsnest section not found")
PYTHON_SCRIPT
            log_success "Crowsnest section commented out in moonraker.conf"
        fi
    fi

    # Delete existing cameras from Moonraker
    log_info "Clearing existing cameras from Moonraker..."
    # Get list of cameras and delete each one
    local cameras=$(curl -s "${MOONRAKER_URL}/server/webcams/list" 2>/dev/null | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for cam in data.get('result', {}).get('webcams', []):
        print(cam.get('name', ''))
except:
    pass
" 2>/dev/null)

    if [ -n "$cameras" ]; then
        while IFS= read -r cam_name; do
            if [ -n "$cam_name" ]; then
                curl -s -X POST "${MOONRAKER_URL}/server/webcams/delete" \
                    -H "Content-Type: application/json" \
                    -d "{\"name\": \"$cam_name\"}" >/dev/null 2>&1 || true
                log_info "Removed camera: $cam_name"
            fi
        done <<< "$cameras"
        log_success "Existing cameras cleared"
    else
        log_info "No existing cameras to remove"
    fi

    # Restart Moonraker to apply changes
    log_info "Restarting Moonraker..."
    if systemctl is-active --quiet moonraker.service 2>/dev/null; then
        sudo systemctl restart moonraker.service || true
        sleep 2
        log_success "Moonraker restarted"
    else
        log_warn "Moonraker service not running"
    fi

    log_success "Migration from crowsnest complete"
    echo ""
}

# Install system packages
install_system_packages() {
    log_info "Installing system packages..."

    sudo apt-get update -qq

    local packages=(
        python3
        python3-venv
        python3-pip
        python3-dev
        ffmpeg
        v4l-utils
        curl
        nginx
    )

    # Add libturbojpeg (name varies by distro)
    if apt-cache show libturbojpeg0 >/dev/null 2>&1; then
        packages+=(libturbojpeg0)
    elif apt-cache show libturbojpeg >/dev/null 2>&1; then
        packages+=(libturbojpeg)
    fi

    # Add optional dev packages for PyAV
    local optional_packages=(
        libturbojpeg0-dev
        libavformat-dev
        libavcodec-dev
        libavdevice-dev
        libavutil-dev
        libswscale-dev
        libswresample-dev
        libavfilter-dev
    )

    sudo apt-get install -y "${packages[@]}" || {
        log_warn "Some packages may not be available, continuing..."
    }

    # Try optional packages but don't fail
    sudo apt-get install -y "${optional_packages[@]}" 2>/dev/null || true

    log_success "System packages installed"
}

# Create directory structure
create_directories() {
    log_info "Creating directory structure..."

    mkdir -p "${INSTALL_DIR}/mediamtx"
    mkdir -p "${INSTALL_DIR}/data"
    mkdir -p "${INSTALL_DIR}/logs"

    log_success "Directories created"
}

# Copy source files (only if running from different location)
copy_source_files() {
    local script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    # Skip if already in install directory
    if [ "${script_dir}" = "${INSTALL_DIR}" ]; then
        log_info "Running from install directory, skipping file copy"
        return
    fi

    log_info "Copying source files..."

    # Copy daemon module
    mkdir -p "${INSTALL_DIR}/daemon"
    cp -r "${script_dir}/daemon"/* "${INSTALL_DIR}/daemon/"

    # Copy requirements
    cp "${script_dir}/requirements.txt" "${INSTALL_DIR}/"

    log_success "Source files copied"
}

# Download and install MediaMTX
install_mediamtx() {
    log_info "Installing MediaMTX..."

    local arch=$(detect_arch)
    local mtx_archive="mediamtx_${MEDIAMTX_VERSION}_linux_${arch}.tar.gz"
    local mtx_url="https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/${mtx_archive}"

    cd "${INSTALL_DIR}/mediamtx"

    if [ -f "mediamtx" ]; then
        log_info "MediaMTX already installed, skipping download..."
    else
        log_info "Downloading MediaMTX ${MEDIAMTX_VERSION} for ${arch}..."
        curl -sL "${mtx_url}" -o mediamtx.tar.gz || {
            log_error "Failed to download MediaMTX"
            exit 1
        }

        tar -xzf mediamtx.tar.gz
        rm mediamtx.tar.gz
        chmod +x mediamtx
    fi

    # Enable API in config
    if [ -f "mediamtx.yml" ]; then
        log_info "Configuring MediaMTX..."
        # Enable API
        if grep -q "^api:" mediamtx.yml; then
            sed -i 's/^api:.*/api: yes/' mediamtx.yml
        else
            echo "api: yes" >> mediamtx.yml
        fi
        # Set API address
        if grep -q "^apiAddress:" mediamtx.yml; then
            sed -i 's/^apiAddress:.*/apiAddress: 127.0.0.1:9997/' mediamtx.yml
        fi
    fi

    cd "${INSTALL_DIR}"
    log_success "MediaMTX installed"
}

# Create Python virtual environment
create_venv() {
    log_info "Creating Python virtual environment..."

    cd "${INSTALL_DIR}"

    if [ -d "venv" ]; then
        log_info "Virtual environment exists, updating..."
    else
        python3 -m venv venv
    fi

    # Activate and install packages
    source venv/bin/activate

    log_info "Upgrading pip, wheel, setuptools..."
    pip install --upgrade pip wheel setuptools

    # Install core requirements
    log_info "Installing core packages: flask, requests, psutil, ruamel.yaml..."
    pip install flask requests psutil ruamel.yaml

    # Install optional packages (may fail on some platforms)
    log_info "Installing optional packages..."
    pip install pyudev 2>/dev/null || log_warn "pyudev not installed (using polling fallback)"
    pip install av 2>/dev/null || log_warn "PyAV not installed (using ffmpeg fallback for snapshots)"
    pip install pyturbojpeg 2>/dev/null || log_warn "pyturbojpeg not installed (using PIL fallback)"

    deactivate

    log_success "Python environment created"
}

# Initialize database
init_database() {
    log_info "Initializing database..."

    cd "${INSTALL_DIR}"
    source venv/bin/activate

    # Set the install directory environment variable
    export RAVENS_PERCH_DIR="${INSTALL_DIR}"

    python3 -c "from daemon.db import init_db; init_db()"
    deactivate

    log_success "Database initialized"
}

# Create systemd service for MediaMTX
create_mediamtx_service() {
    log_info "Creating MediaMTX service..."

    sudo tee /etc/systemd/system/mediamtx.service > /dev/null << EOF
[Unit]
Description=MediaMTX Streaming Server
After=network.target

[Service]
Type=simple
User=${RAVENS_USER}
WorkingDirectory=${INSTALL_DIR}/mediamtx
ExecStart=${INSTALL_DIR}/mediamtx/mediamtx
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    log_success "MediaMTX service created"
}

# Create systemd service for Ravens Perch
create_ravens_service() {
    log_info "Creating Ravens Perch service..."

    sudo tee /etc/systemd/system/ravens-perch.service > /dev/null << EOF
[Unit]
Description=Ravens Perch Camera Manager
After=network.target mediamtx.service
Wants=mediamtx.service

[Service]
Type=simple
User=${RAVENS_USER}
Environment="RAVENS_PERCH_DIR=${INSTALL_DIR}"
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python -m daemon.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    log_success "Ravens Perch service created"
}

# Configure nginx reverse proxy using include snippet
configure_nginx() {
    log_info "Configuring nginx..."

    local snippet_dir="/etc/nginx/snippets"
    local snippet_file="${snippet_dir}/ravens-perch.conf"

    # Create snippets directory if it doesn't exist
    if [ ! -d "$snippet_dir" ]; then
        sudo mkdir -p "$snippet_dir"
    fi

    # Create the snippet file with the location block
    log_info "Creating nginx snippet..."
    sudo tee "$snippet_file" > /dev/null << 'EOF'
# Ravens Perch Camera UI
# Include this in your server block: include /etc/nginx/snippets/ravens-perch.conf;
location /cameras/ {
    proxy_pass http://127.0.0.1:8585/cameras/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
EOF
    log_success "Created ${snippet_file}"

    # Find which nginx config is actually serving port 80
    local target_conf=""

    for conf in /etc/nginx/sites-enabled/*; do
        if [ -f "$conf" ] && grep -q "listen 80" "$conf" 2>/dev/null; then
            # Resolve symlink to get the actual file
            target_conf=$(readlink -f "$conf")
            log_info "Found active site on port 80: ${target_conf}"
            break
        fi
    done

    if [ -z "$target_conf" ]; then
        log_warn "Could not find nginx site configuration serving port 80."
        log_info "Please manually add to your nginx server block:"
        echo ""
        echo "    include /etc/nginx/snippets/ravens-perch.conf;"
        echo ""
        return
    fi

    # Check if already configured (either include or direct location)
    if grep -q "ravens-perch.conf" "$target_conf" 2>/dev/null; then
        log_info "Ravens Perch already configured in ${target_conf}"
        return
    fi
    if grep -q "location /cameras/" "$target_conf" 2>/dev/null; then
        log_info "/cameras/ location already exists in ${target_conf}"
        return
    fi

    log_info "Adding include directive to ${target_conf}..."

    # Create a backup
    sudo cp "$target_conf" "${target_conf}.ravens-perch.bak"

    # Insert the include line - much simpler than inserting a full block
    sudo python3 - "${target_conf}" << 'PYTHON_SCRIPT'
import sys

target_conf = sys.argv[1]

with open(target_conf, 'r') as f:
    content = f.read()

include_line = '    include /etc/nginx/snippets/ravens-perch.conf;'

# Try to insert before the first "location" line
lines = content.split('\n')
inserted = False

for i, line in enumerate(lines):
    # Find the first location block and insert before it
    if 'location ' in line and not inserted:
        lines.insert(i, '')
        lines.insert(i + 1, include_line)
        inserted = True
        break

# If no location found, insert before final closing brace
if not inserted:
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == '}':
            lines.insert(i, '')
            lines.insert(i + 1, include_line)
            break

content = '\n'.join(lines)

with open(target_conf, 'w') as f:
    f.write(content)

print("Include directive added successfully")
PYTHON_SCRIPT

    # Verify the include was added
    if grep -q "ravens-perch.conf" "$target_conf"; then
        log_success "Added include directive to nginx config"
    else
        log_warn "Failed to add include directive"
        sudo cp "${target_conf}.ravens-perch.bak" "$target_conf"
        log_info "Please manually add to ${target_conf}:"
        echo "    include /etc/nginx/snippets/ravens-perch.conf;"
        return
    fi

    # Test and reload nginx
    if sudo nginx -t 2>/dev/null; then
        sudo systemctl reload nginx || true
        log_success "Nginx configured and reloaded"
    else
        log_warn "Nginx config test failed - restoring backup..."
        sudo cp "${target_conf}.ravens-perch.bak" "$target_conf"
        sudo nginx -t && sudo systemctl reload nginx
        log_info "Please manually add to your nginx config:"
        echo "    include /etc/nginx/snippets/ravens-perch.conf;"
    fi
}

# Configure printer UI integrations (Mainsail/Fluidd)
configure_printer_ui() {
    local config_dir="${HOME}/printer_data/config"

    # Check if printer_data exists
    if [ ! -d "$config_dir" ]; then
        log_info "No printer_data/config found, skipping UI integration"
        return
    fi

    echo ""
    log_info "Optional: Add Ravens Perch to your printer interface"
    echo ""

    # Mainsail integration
    read -p "Add Ravens Perch to Mainsail sidebar? (y/N): " mainsail_choice
    if [[ "$mainsail_choice" == "y" || "$mainsail_choice" == "Y" ]]; then
        local mainsail_theme_dir="${config_dir}/.theme"
        local mainsail_navi="${mainsail_theme_dir}/navi.json"

        mkdir -p "$mainsail_theme_dir"

        # Crow icon SVG path (24x24 viewBox)
        local raven_icon="m12.455 14.258 0.176 -0.299a24.6 24.6 0 0 1 -5.24 -0.447c-2.385 0.73 -4.492 1.367 -6.654 2.033a0.5 0.5 0 0 1 -0.346 -0.115 0.48 0.48 0 0 1 -0.348 -0.051c-0.135 -0.137 0.07 -0.209 0.178 -0.232a0.8 0.8 0 0 1 0.178 -0.016l1.438 -0.914 -0.781 0.125c-0.373 0.172 -0.604 0.139 -0.756 0 2.551 -1.758 5.137 -3.143 7.635 -4.674 1.492 -0.916 2.697 -2.215 4.078 -3.377 1.291 -1.086 2.734 -1.787 3.943 -2.316 0.487 -2.254 1.474 -3.75 3.31 -3.975a2.13 2.13 0 0 1 1.859 0.822c1.293 0.018 2.49 0.096 2.875 0.701 -0.74 0.496 -1.953 0.643 -2.766 0.977 -1.408 0.576 -0.902 2.199 -0.797 3.473s0.119 2.709 -0.604 4.209c-0.678 1.406 -1.859 2.449 -3.613 3.084l-2.465 1.303c-0.418 0.195 -0.354 0.148 -0.256 0.648 0.164 0.842 0.52 1.789 0.936 2.887l1.457 0.334c0.236 0.059 0.15 0.869 -0.18 0.83l-1.953 -0.264 -1.91 0.363c-0.195 0.063 -0.262 -0.824 -0.096 -0.904l1.68 -0.299a24 24 0 0 1 -1.057 -2.91c-0.178 -0.598 -0.232 -0.482 0.078 -1.006ZM19.81 0.86a0.305 0.305 0 1 1 -0.304 0.312 0.303 0.303 0 0 1 0.305 -0.305Z"

        if [ -f "$mainsail_navi" ]; then
            # File exists - check if we're already in it
            if grep -q "Ravens Perch" "$mainsail_navi" 2>/dev/null; then
                log_info "Ravens Perch already in Mainsail sidebar"
            else
                # Merge with existing file using Python
                python3 - "$mainsail_navi" "$raven_icon" << 'PYTHON_SCRIPT'
import sys
import json

sidebar_file = sys.argv[1]
icon = sys.argv[2]

with open(sidebar_file, 'r') as f:
    sidebar = json.load(f)

# Add Ravens Perch entry
ravens_entry = {
    "title": "Ravens Perch",
    "href": "/cameras/",
    "target": "_self",
    "position": 25,
    "icon": icon
}

# Check if already exists
if not any(item.get('title') == 'Ravens Perch' for item in sidebar):
    sidebar.append(ravens_entry)
    with open(sidebar_file, 'w') as f:
        json.dump(sidebar, f, indent=2)
    print("Added Ravens Perch to existing navi.json")
else:
    print("Ravens Perch already in navi.json")
PYTHON_SCRIPT
                log_success "Added Ravens Perch to Mainsail sidebar"
            fi
        else
            # Create new navi.json
            cat > "$mainsail_navi" << EOF
[
  {
    "title": "Ravens Perch",
    "href": "/cameras/",
    "target": "_self",
    "position": 25,
    "icon": "${raven_icon}"
  }
]
EOF
            log_success "Created Mainsail sidebar with Ravens Perch link"
        fi
    fi

    # Fluidd integration
    read -p "Add Ravens Perch link to Fluidd? (y/N): " fluidd_choice
    if [[ "$fluidd_choice" == "y" || "$fluidd_choice" == "Y" ]]; then
        local fluidd_theme_dir="${config_dir}/.fluidd-theme"
        local fluidd_css="${fluidd_theme_dir}/custom.css"

        mkdir -p "$fluidd_theme_dir"

        # CSS import that pulls dynamic CSS from Ravens Perch
        # This ensures the URL always reflects current hostname/IP
        local ravens_css='/* Ravens Perch Integration */
/* Import dynamic CSS from Ravens Perch (updates with current IP/hostname) */
@import url("/cameras/api/fluidd-theme.css");
'

        if [ -f "$fluidd_css" ]; then
            # Check if already added
            if grep -q "Ravens Perch Integration" "$fluidd_css" 2>/dev/null; then
                log_info "Ravens Perch CSS already in Fluidd theme"
            else
                # Append to existing file
                echo "" >> "$fluidd_css"
                echo "$ravens_css" >> "$fluidd_css"
                log_success "Added Ravens Perch CSS to Fluidd theme"
            fi
        else
            # Create new custom.css
            echo "$ravens_css" > "$fluidd_css"
            log_success "Created Fluidd theme with Ravens Perch link"
        fi

        log_success "Fluidd will display Ravens Perch link with current IP/hostname"
    fi
}

# Enable and start services
start_services() {
    log_info "Starting services..."

    sudo systemctl daemon-reload

    # Enable services
    sudo systemctl enable mediamtx ravens-perch

    # Start MediaMTX first
    sudo systemctl start mediamtx || log_warn "MediaMTX may already be running"
    sleep 2

    # Start Ravens Perch
    sudo systemctl start ravens-perch || log_warn "Ravens Perch may already be running"

    log_success "Services started"
}

# Manage existing Moonraker cameras
manage_existing_cameras() {
    log_info "Checking for existing Moonraker cameras..."

    local MOONRAKER_URL="http://127.0.0.1:7125"

    # Get list of existing cameras
    local cameras_json=$(curl -s "${MOONRAKER_URL}/server/webcams/list" 2>/dev/null)
    if [ -z "$cameras_json" ]; then
        log_info "Could not connect to Moonraker, skipping camera cleanup"
        return
    fi

    # Parse cameras into array
    local cameras=$(echo "$cameras_json" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    webcams = data.get('result', {}).get('webcams', [])
    for cam in webcams:
        name = cam.get('name', 'Unknown')
        uid = cam.get('uid', '')
        stream_url = cam.get('stream_url', '')
        print(f'{uid}|{name}|{stream_url}')
except:
    pass
" 2>/dev/null)

    if [ -z "$cameras" ]; then
        log_info "No existing cameras found in Moonraker"
        return
    fi

    echo ""
    log_warn "Existing cameras found in Moonraker"
    echo ""
    echo "Ravens Perch will manage camera registrations."
    echo "You may delete existing cameras or keep them."
    echo ""

    # Process each camera (use fd 3 for camera list so stdin stays available for read -p)
    while IFS='|' read -r uid name stream_url <&3; do
        if [ -z "$uid" ]; then
            continue
        fi

        echo ""
        echo -e "Camera: ${YELLOW}${name}${NC}"
        echo "  UID: ${uid}"
        echo "  Stream: ${stream_url}"

        read -p "  Delete this camera? (Y/n): " delete_choice </dev/tty
        if [[ "$delete_choice" != "n" && "$delete_choice" != "N" ]]; then
            local delete_result=$(curl -s -X DELETE "${MOONRAKER_URL}/server/webcams/item?uid=${uid}" 2>&1)
            if echo "$delete_result" | grep -q "error"; then
                log_warn "  Failed to delete: ${name}"
                echo "  Response: ${delete_result}"
            else
                log_success "  Deleted: ${name}"
            fi
        else
            log_info "  Keeping: ${name}"
        fi
    done 3<<< "$cameras"

    echo ""
    log_success "Camera cleanup complete"
}

# Verify installation - check service and cameras
verify_installation() {
    echo ""
    log_info "Verifying installation..."

    # Wait for Ravens Perch service to be ready (web UI takes ~12s to start)
    log_info "Waiting for Ravens Perch service..."
    local retries=30
    while [ $retries -gt 0 ]; do
        if curl -s "http://127.0.0.1:8585/cameras/api/health" >/dev/null 2>&1; then
            log_success "Ravens Perch service is running"
            break
        fi
        sleep 1
        ((retries--))
    done

    if [ $retries -eq 0 ]; then
        log_warn "Ravens Perch service not responding - check logs with: sudo journalctl -u ravens-perch -f"
        return
    fi

    # Count expected USB cameras so we know when all are configured
    local expected_cameras=0
    expected_cameras=$(v4l2-ctl --list-devices 2>/dev/null | python3 -c "
import sys
lines = sys.stdin.read().strip().split('\n')
count = 0
is_usb = False
for line in lines:
    if line and not line.startswith('\t'):
        is_usb = '(usb-' in line.lower()
    elif is_usb and '/dev/video' in line:
        # Only count the first /dev/video per USB device
        count += 1
        is_usb = False
print(count)
" 2>/dev/null || echo "0")
    log_info "Detected ${expected_cameras} USB camera(s), waiting for auto-configuration..."

    # Wait for camera auto-configuration
    local camera_retries=45
    local rp_cameras=""
    local rp_count=0
    local last_count=0
    local stable_checks=0

    while [ $camera_retries -gt 0 ]; do
        rp_cameras=$(curl -s "http://127.0.0.1:8585/cameras/api/status" 2>/dev/null)
        rp_count=$(echo "$rp_cameras" | python3 -c "import sys,json; data=json.load(sys.stdin); print(len(data) if isinstance(data, list) else 0)" 2>/dev/null || echo "0")

        if [ "$rp_count" -gt 0 ]; then
            # If we know expected count, wait for it
            if [ "$expected_cameras" -gt 0 ] && [ "$rp_count" -ge "$expected_cameras" ]; then
                break
            fi
            # Otherwise fall back to stability check
            if [ "$rp_count" -eq "$last_count" ]; then
                ((stable_checks++)) || true
                if [ $stable_checks -ge 5 ]; then
                    break
                fi
            else
                stable_checks=0
                echo -n "."
            fi
            last_count=$rp_count
        fi

        sleep 1
        ((camera_retries--)) || true
    done
    echo ""

    if [ "$rp_count" -gt 0 ]; then
        log_success "Ravens Perch detected ${rp_count} camera(s)"

        # List cameras with status
        echo ""
        echo "$rp_cameras" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for cam in data:
    name = cam.get('name', 'Unknown')
    connected = 'connected' if cam.get('connected') else 'disconnected'
    print(f'  - {name}: {connected}')
" 2>/dev/null
        echo ""
    else
        log_warn "No cameras detected by Ravens Perch"
        log_info "Check if cameras are connected: ls /dev/video*"
    fi

    # Check Moonraker registrations
    local mr_cameras=$(curl -s "http://127.0.0.1:7125/server/webcams/list" 2>/dev/null)
    local mr_count=$(echo "$mr_cameras" | python3 -c "import sys,json; data=json.load(sys.stdin); print(len(data.get('result', {}).get('webcams', [])))" 2>/dev/null || echo "0")

    if [ "$mr_count" -gt 0 ]; then
        log_success "Moonraker has ${mr_count} camera(s) registered"
    else
        log_warn "No cameras registered with Moonraker yet"
    fi

    # Check MediaMTX streams
    local mtx_paths=$(curl -s "http://127.0.0.1:8888/v3/paths/list" 2>/dev/null)
    local mtx_count=$(echo "$mtx_paths" | python3 -c "import sys,json; data=json.load(sys.stdin); print(len(data.get('items', [])))" 2>/dev/null || echo "0")

    if [ "$mtx_count" -gt 0 ]; then
        log_success "MediaMTX has ${mtx_count} stream(s) active"
    else
        log_info "No MediaMTX streams active yet (will start when cameras are accessed)"
    fi

    echo ""
    log_success "Verification complete"
}

# Print completion message
print_success() {
    local ip=$(hostname -I | awk '{print $1}')

    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║         Ravens Perch Installed Successfully!               ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "Web UI:        ${BLUE}http://${ip}/cameras/${NC}"
    echo -e "Direct access: ${BLUE}http://${ip}:8585/cameras/${NC}"
    echo ""
    echo "Commands:"
    echo "  sudo systemctl status ravens-perch   - Check status"
    echo "  sudo systemctl restart ravens-perch  - Restart service"
    echo "  sudo journalctl -u ravens-perch -f   - View logs"
    echo ""
    echo "Visit the web UI to view streams and customize settings."
    echo ""
}

# Main installation flow
main() {
    echo ""
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║              Ravens Perch Installation                     ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    check_user

    if is_raspberry_pi; then
        log_info "Detected Raspberry Pi"
    elif is_rockchip; then
        log_info "Detected Rockchip platform"
    fi

    log_info "Architecture: $(detect_arch)"
    log_info "Install directory: ${INSTALL_DIR}"
    echo ""

    install_system_packages
    create_directories
    migrate_from_crowsnest
    copy_source_files
    install_mediamtx
    create_venv
    init_database
    create_mediamtx_service
    create_ravens_service
    configure_nginx
    configure_printer_ui

    # Clean existing Moonraker cameras before starting service
    manage_existing_cameras

    # Start services (ravens-perch will auto-configure cameras)
    start_services

    # Verify everything is working
    verify_installation

    print_success
}

# Run main
main "$@"
