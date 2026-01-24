#!/usr/bin/env bash

# ==============================================================================
# Full Installer for MediaMTX + SnapFeeder
# ----------------------------------------
# - Installs dependencies via APT and pip if needed
# - Creates Python virtual environment in ./venv/
# - Downloads MediaMTX and places it into ./mediamtx/
# - Enables MediaMTX API for hot-reload configuration
# - Generates mediamtx.yml using scripts/generate_mediamtx_config.py
# - Processes *.service.template files from ./templates/
#   - Injects current user and absolute install path
#   - Saves rendered files into ./services/
#   - Creates symlinks into /etc/systemd/system/
# - Starts and enables systemd services
#
# Last modified: 2026-01-11 12:42 CST
# ==============================================================================

set -e

# Define directories
BASE_DIR="$(dirname $(realpath $0))"
VENV_DIR="$BASE_DIR/venv"
SERVICE_DIR="/etc/systemd/system"
TEMPLATE_DIR="$BASE_DIR/templates"
RENDERED_DIR="$BASE_DIR/services"
SCRIPTS_DIR="$BASE_DIR/scripts"
MEDIAMTX_DIR="$BASE_DIR/mediamtx"
MEDIAMTX_BIN="$MEDIAMTX_DIR/mediamtx"
MEDIAMTX_CONFIG="$MEDIAMTX_DIR/mediamtx.yml"

USERNAME=$(whoami)

# ----------------------------------------------
# 🤖 Detect Rockchip platform (e.g., RK3588, RK3399)
# If detected, offer to install custom FFmpeg build
# with Rockchip hardware acceleration (MPP/RGA)
# ----------------------------------------------

# Read SoC info from device tree
ROCKCHIP_CPU=""
if [ -f /proc/device-tree/compatible ]; then
    ROCKCHIP_CPU=$(tr -d '\0' < /proc/device-tree/compatible | grep -o 'rockchip,[^,]*' || true)
fi

if [[ -n "$ROCKCHIP_CPU" ]]; then
    echo -e "🧠  \e[33mDetected Rockchip platform:\e[0m $ROCKCHIP_CPU"
    
    # Prompt user to optionally install the custom FFmpeg
    echo -e "🚀  \e[36mWould you like to install a custom FFmpeg build with Rockchip hardware acceleration (MPP/RGA)?\e[0m"
    read -p "✅  Type 'yes' to proceed or press Enter to skip: " user_input

    if [[ "$user_input" == "yes" ]]; then
      echo -e "🔧  \e[32mLaunching FFmpeg installer...\e[0m"
      bash "$BASE_DIR"/extras/rockchip_ffmpeg_installer.sh
    else
        echo -e "⏭️  \e[34mSkipping custom FFmpeg installation.\e[0m"
    fi
else
    echo -e "ℹ️  \e[34mNo Rockchip platform detected. Skipping hardware-accelerated FFmpeg prompt.\e[0m"
fi

# Ensure required system packages are installed
REQUIRED_PKGS=(python3 python3-pip python3-venv curl v4l-utils)
MISSING_PKGS=()

for pkg in "${REQUIRED_PKGS[@]}"; do
  if ! dpkg -s "$pkg" >/dev/null 2>&1; then
    MISSING_PKGS+=("$pkg")
  fi
done

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ℹ️  ffmpeg not found, adding to install list"
  MISSING_PKGS+=(ffmpeg)
fi

if apt-cache show libturbojpeg0 >/dev/null 2>&1; then
  if ! dpkg -s libturbojpeg0 >/dev/null 2>&1; then
    echo "ℹ️  libturbojpeg0 is available and not installed — adding to install list"
    MISSING_PKGS+=(libturbojpeg0)
  fi
elif apt-cache show libturbojpeg >/dev/null 2>&1; then
  if ! dpkg -s libturbojpeg >/dev/null 2>&1; then
    echo "ℹ️  libturbojpeg is available and not installed — adding to install list"
    MISSING_PKGS+=(libturbojpeg)
  fi
else
  echo "❌ Neither 'libturbojpeg0' nor 'libturbojpeg' are available in APT repositories."
  echo "   Please check your APT sources."
  exit 1
fi

if [ ${#MISSING_PKGS[@]} -ne 0 ]; then
  echo "🔧 Installing missing system packages: ${MISSING_PKGS[*]}"
  sudo apt update
  sudo apt install -y "${MISSING_PKGS[@]}"
fi


# Create Python virtual environment
echo "Checking for existing venv at: $VENV_DIR"
if [ -d "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/python" ]; then
  echo "⚠️  Virtual environment already exists"
  read -p "Remove and recreate? (y/n): " recreate_venv
  if [[ "$recreate_venv" == "y" ]]; then
    echo "🗑️  Removing existing venv..."
    rm -rf "$VENV_DIR"
    echo "🔧 Creating Python virtual environment"
    python3 -m venv "$VENV_DIR"
  else
    echo "✅ Using existing virtual environment"
  fi
else
  echo "🔧 Creating Python virtual environment"
  python3 -m venv "$VENV_DIR"
fi

# Install packages directly using venv's pip (not activating to avoid shell issues)
echo "📦 Installing Python packages..."
"$VENV_DIR/bin/pip" install --upgrade pip wheel
"$VENV_DIR/bin/pip" install -r "$BASE_DIR/venv-requirements.txt"

# Download latest MediaMTX binary
VERSION=$(curl -s https://api.github.com/repos/bluenviron/mediamtx/releases/latest | grep tag_name | cut -d '"' -f 4)
ARCH=$(uname -m)
case "$ARCH" in
  armv6l)       PLATFORM="linux_armv6" ;;
  armv7l)       PLATFORM="linux_armv7" ;;
  aarch64)      PLATFORM="linux_arm64" ;;
  amd64|x86_64) PLATFORM="linux_amd64" ;;
  *) echo "❌ Unsupported architecture: $ARCH"; exit 1 ;;
esac

TMP_DIR=$(mktemp -d)
cd "$TMP_DIR"
echo "⬇️  Downloading MediaMTX $VERSION for $PLATFORM..."
curl -L -o mediamtx.tar.gz "https://github.com/bluenviron/mediamtx/releases/download/${VERSION}/mediamtx_${VERSION}_${PLATFORM}.tar.gz"
tar -xzf mediamtx.tar.gz

mkdir -p "$MEDIAMTX_DIR"
mv mediamtx "$MEDIAMTX_BIN"
chmod +x "$MEDIAMTX_BIN"
mv mediamtx.yml "$MEDIAMTX_CONFIG"
chmod 644 "$MEDIAMTX_CONFIG"

# Enable API in MediaMTX config (required for hot-reload configuration)
echo "🔧 Enabling MediaMTX API..."
if grep -q "^api:" "$MEDIAMTX_CONFIG"; then
  # Replace existing api setting
  sed -i 's/^api:.*/api: yes/' "$MEDIAMTX_CONFIG"
else
  # Add api setting near the top of the file (after first non-comment line)
  sed -i '0,/^[^#]/s//api: yes\n&/' "$MEDIAMTX_CONFIG"
fi

# Verify API is enabled
if grep -q "^api: yes" "$MEDIAMTX_CONFIG"; then
  echo "✅ MediaMTX API enabled"
else
  echo "⚠️  Could not verify API setting - manual check recommended"
fi

# Render systemd service templates BEFORE running configuration
mkdir -p "$RENDERED_DIR"

# Render .service files from templates and symlink to systemd
for template in "$TEMPLATE_DIR"/*.service.template; do
  base=$(basename "$template" .template)
  output="$RENDERED_DIR/$base"
  systemd_target="$SERVICE_DIR/$base"

  echo "🛠️  Rendering $base..."

  # Render template with variable substitution
  sed \
    -e "s|__BASE_DIR__|$BASE_DIR|g" \
    -e "s|__VENV_DIR__|$VENV_DIR|g" \
    -e "s|__USERNAME__|$USERNAME|g" \
    "$template" > "$output"

  # Create symlink to systemd
  echo "🔗 Linking $base → $systemd_target"
  sudo ln -sf "$output" "$systemd_target"
done

# Reload systemd so services are available
echo "🔄 Reloading systemd..."
sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable mediamtx.service snapfeeder.service raven-watchdog.service web-ui.service camera-hotplug.service

# Ensure all scripts have executable permissions
echo "🔧 Setting executable permissions..."
chmod +x "$BASE_DIR/ravens-perch"
chmod +x "$BASE_DIR/install.sh"
chmod +x "$BASE_DIR/uninstall.sh"
chmod +x "$SCRIPTS_DIR/generate_mediamtx_config.py"
# Make any extras scripts executable
if [ -d "$BASE_DIR/extras" ]; then
    find "$BASE_DIR/extras" -name "*.sh" -exec chmod +x {} \;
fi

# Show installation complete message
echo ""
echo "✅ Installation complete!"
echo ""
echo "🚀 Launching camera configuration..."
echo ""
sleep 2

# Launch the wrapper to configure cameras
"$BASE_DIR/ravens-perch"

# Ensure all services are running
echo ""
echo "🚀 Ensuring services are running..."
for service in mediamtx snapfeeder raven-watchdog web-ui camera-hotplug; do
  if ! systemctl is-active --quiet ${service}.service; then
    echo "   Starting ${service}..."
    sudo systemctl start ${service}.service
  else
    echo "   ✓ ${service} already running"
  fi
done

echo ""
echo "✅ Setup complete!"
echo ""
echo "📹 To reconfigure cameras anytime, run:"
echo "   ./ravens-perch"
echo ""
echo "🌐 Web Interface:"
echo "   http://$(hostname -I | awk '{print $1}')/cameras"
echo ""
echo "🔧 Services running:"
echo "   - mediamtx (RTSP/WebRTC/HLS streaming)"
echo "   - snapfeeder (JPEG snapshots on port 5050)"
echo "   - raven-watchdog (config sync & override API on port 5051)"
echo "   - web-ui (camera configuration on port 80)"
echo "   - camera-hotplug (plug-and-play camera detection)"
echo ""
echo "🔌 Plug-and-Play:"
echo "   New cameras will be auto-configured when plugged in!"
echo ""
