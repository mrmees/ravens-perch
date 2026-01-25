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

    pip install --upgrade pip wheel setuptools -q

    # Install core requirements
    pip install flask requests psutil ruamel.yaml -q

    # Install optional packages (may fail on some platforms)
    pip install pyudev -q 2>/dev/null || log_warn "pyudev not installed (using polling fallback)"
    pip install av -q 2>/dev/null || log_warn "PyAV not installed (using ffmpeg fallback for snapshots)"
    pip install pyturbojpeg -q 2>/dev/null || log_warn "pyturbojpeg not installed (using PIL fallback)"

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
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python -m daemon.main
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    log_success "Ravens Perch service created"
}

# Configure nginx reverse proxy
configure_nginx() {
    log_info "Configuring nginx..."

    # Create the location block content
    local location_block='
    # Ravens Perch Camera UI
    location /cameras/ {
        proxy_pass http://127.0.0.1:8585/cameras/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }'

    # Try to find the active nginx config
    local configs=(
        "/etc/nginx/sites-enabled/fluidd"
        "/etc/nginx/sites-enabled/mainsail"
        "/etc/nginx/sites-available/fluidd"
        "/etc/nginx/sites-available/mainsail"
        "/etc/nginx/sites-enabled/default"
    )

    local target_conf=""
    for conf in "${configs[@]}"; do
        if [ -f "$conf" ]; then
            target_conf="$conf"
            break
        fi
    done

    if [ -z "$target_conf" ]; then
        log_warn "Could not find nginx site configuration."
        log_info "Please manually add the following to your nginx server block:"
        echo ""
        echo "    location /cameras/ {"
        echo "        proxy_pass http://127.0.0.1:8585/cameras/;"
        echo "        proxy_http_version 1.1;"
        echo "        proxy_set_header Host \$host;"
        echo "        proxy_set_header X-Real-IP \$remote_addr;"
        echo "        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;"
        echo "        proxy_set_header X-Forwarded-Proto \$scheme;"
        echo "    }"
        echo ""
        return
    fi

    # Check if already configured
    if grep -q "location /cameras/" "$target_conf" 2>/dev/null; then
        log_info "/cameras/ location already configured in ${target_conf}"
    else
        log_info "Adding /cameras/ location to ${target_conf}..."

        # Create a backup
        sudo cp "$target_conf" "${target_conf}.ravens-perch.bak"

        # Use Python for reliable config modification (more reliable than sed)
        sudo python3 << PYTHON_SCRIPT
import re

with open('${target_conf}', 'r') as f:
    content = f.read()

# Check if already has the location
if 'location /cameras/' in content:
    print("Already configured")
else:
    # Find the last location block or the server block closing brace
    # Insert before the final closing brace of the server block

    location_block = '''
    # Ravens Perch Camera UI
    location /cameras/ {
        proxy_pass http://127.0.0.1:8585/cameras/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
'''

    # Find the position to insert (before the last closing brace)
    # Look for pattern: whitespace + } at end (server block close)
    lines = content.split('\n')
    insert_idx = -1
    brace_count = 0
    in_server = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('server') and '{' in stripped:
            in_server = True
            brace_count = 1
        elif in_server:
            brace_count += line.count('{') - line.count('}')
            if brace_count == 0:
                insert_idx = i
                break

    if insert_idx > 0:
        lines.insert(insert_idx, location_block)
        content = '\n'.join(lines)

        with open('${target_conf}', 'w') as f:
            f.write(content)
        print("Configuration added successfully")
    else:
        print("Could not find insertion point")
        exit(1)
PYTHON_SCRIPT

        if [ $? -eq 0 ]; then
            log_success "Added /cameras/ location to nginx config"
        else
            log_warn "Failed to auto-configure nginx"
            log_info "Please manually add the location block to ${target_conf}"
        fi
    fi

    # Test and reload nginx
    if sudo nginx -t 2>/dev/null; then
        sudo systemctl reload nginx || true
        log_success "Nginx configured and reloaded"
    else
        log_warn "Nginx config test failed"
        log_info "Restoring backup..."
        if [ -f "${target_conf}.ravens-perch.bak" ]; then
            sudo cp "${target_conf}.ravens-perch.bak" "$target_conf"
            sudo nginx -t && sudo systemctl reload nginx
        fi
        log_info "Please manually configure nginx for /cameras/ location"
    fi
}

# Add to Moonraker update_manager
configure_moonraker() {
    log_info "Configuring Moonraker update manager..."

    local moonraker_conf="${KLIPPER_CONFIG_DIR}/moonraker.conf"
    local moonraker_asvc="${HOME}/printer_data/moonraker.asvc"

    if [ -f "${moonraker_conf}" ]; then
        if ! grep -q "\[update_manager ravens-perch\]" "${moonraker_conf}"; then
            log_info "Adding ravens-perch to moonraker.conf..."
            cat >> "${moonraker_conf}" << EOF

[update_manager ravens-perch]
type: git_repo
path: ${INSTALL_DIR}
origin: https://github.com/USER/ravens-perch.git
primary_branch: main
managed_services: ravens-perch mediamtx
EOF
            log_success "Added to Moonraker update manager"
        else
            log_info "Already configured in Moonraker"
        fi

        # Add services to moonraker.asvc for service management permissions
        if [ -f "${moonraker_asvc}" ]; then
            log_info "Adding services to moonraker.asvc..."
            for service in ravens-perch mediamtx; do
                if ! grep -q "^${service}$" "${moonraker_asvc}"; then
                    echo "${service}" >> "${moonraker_asvc}"
                    log_info "Added ${service} to moonraker.asvc"
                fi
            done
            log_success "Service permissions configured"
        else
            log_warn "moonraker.asvc not found at ${moonraker_asvc}"
            log_info "To allow Moonraker to manage services, create the file and add:"
            echo ""
            echo "ravens-perch"
            echo "mediamtx"
            echo ""
        fi
    else
        log_warn "moonraker.conf not found at ${moonraker_conf}"
        log_info "To enable automatic updates, add to your moonraker.conf:"
        echo ""
        echo "[update_manager ravens-perch]"
        echo "type: git_repo"
        echo "path: ${INSTALL_DIR}"
        echo "origin: https://github.com/USER/ravens-perch.git"
        echo "primary_branch: main"
        echo "managed_services: ravens-perch mediamtx"
        echo ""
        log_info "And add to ${moonraker_asvc}:"
        echo ""
        echo "ravens-perch"
        echo "mediamtx"
        echo ""
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
    echo "Cameras will be automatically detected and configured."
    echo "Check the web UI to customize settings."
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
    copy_source_files
    install_mediamtx
    create_venv
    init_database
    create_mediamtx_service
    create_ravens_service
    configure_nginx
    configure_moonraker
    start_services

    print_success
}

# Run main
main "$@"
