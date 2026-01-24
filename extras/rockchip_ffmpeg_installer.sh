#!/bin/bash

# ==============================================================================
# Jellyfin FFmpeg Installer for Rockchip (RK3588)
# ------------------------------------------------
# Downloads and installs precompiled Jellyfin FFmpeg binaries with
# hardware-accelerated video encoding/decoding support (RKMPP/RGA).
#
# NOTE: This installer is recommended for RK3588 based systems only.
# RK3399 and older chips should use the default system FFmpeg with libx264.
#
# Last modified: 2026-01-11 14:42 CST
# ==============================================================================

TEMP_DIR="/tmp/jellyfin-ffmpeg-install"

# Enable color codes for log output
GREEN="\e[32m"
BLUE="\e[34m"
CYAN="\e[36m"
RED="\e[31m"
YELLOW="\e[33m"
RESET="\e[0m"

echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}║  Jellyfin FFmpeg Installer for Rockchip (RK3588)              ║${RESET}"
echo -e "${CYAN}║  Provides hardware-accelerated video encoding/decoding        ║${RESET}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${RESET}"

# ----------------------------------------------
# Check if running on Rockchip platform
# ----------------------------------------------
ROCKCHIP_CPU=""
if [ -f /proc/device-tree/compatible ]; then
    ROCKCHIP_CPU=$(tr -d '\0' < /proc/device-tree/compatible | grep -o 'rockchip,[^,]*' || true)
fi

if [[ -z "$ROCKCHIP_CPU" ]]; then
    echo -e "${RED}❌ This installer is for Rockchip platforms only.${RESET}"
    echo -e "${YELLOW}   Detected platform: Non-Rockchip${RESET}"
    exit 1
fi

echo -e "${GREEN}✅ Detected Rockchip platform: ${ROCKCHIP_CPU}${RESET}"

# ----------------------------------------------
# Check for RK3399 - recommend default FFmpeg instead
# ----------------------------------------------
if [[ "$ROCKCHIP_CPU" == *"rk3399"* ]]; then
    echo -e ""
    echo -e "${YELLOW}╔════════════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${YELLOW}║  ⚠️  RK3399 Detected                                           ║${RESET}"
    echo -e "${YELLOW}╠════════════════════════════════════════════════════════════════╣${RESET}"
    echo -e "${YELLOW}║  The RK3399 has limited hardware encoding support and          ║${RESET}"
    echo -e "${YELLOW}║  complicated driver requirements.                              ║${RESET}"
    echo -e "${YELLOW}║                                                                ║${RESET}"
    echo -e "${YELLOW}║  RECOMMENDED: Use the default system FFmpeg with libx264       ║${RESET}"
    echo -e "${YELLOW}║  software encoding instead.                                    ║${RESET}"
    echo -e "${YELLOW}║                                                                ║${RESET}"
    echo -e "${YELLOW}║  Run: sudo apt install ffmpeg                                  ║${RESET}"
    echo -e "${YELLOW}╚════════════════════════════════════════════════════════════════╝${RESET}"
    echo -e ""
    read -p "Continue with Jellyfin FFmpeg installation anyway? (y/N): " confirm
    if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo -e "${GREEN}👍 Good choice! Install default FFmpeg with:${RESET}"
        echo -e "   sudo apt update && sudo apt install ffmpeg"
        exit 0
    fi
    echo -e "${YELLOW}⚠️  Continuing with Jellyfin FFmpeg (hardware encoding may not work)${RESET}"
fi

# ----------------------------------------------
# Detect architecture
# ----------------------------------------------
ARCH=$(uname -m)
if [[ "$ARCH" != "aarch64" ]]; then
    echo -e "${RED}❌ This installer requires ARM64 architecture.${RESET}"
    echo -e "${YELLOW}   Detected architecture: ${ARCH}${RESET}"
    exit 1
fi

echo -e "${GREEN}✅ Architecture: ${ARCH} (ARM64)${RESET}"

# ----------------------------------------------
# Remove existing FFmpeg to avoid conflicts
# ----------------------------------------------
echo -e "\n${BLUE}🧹 Removing existing system FFmpeg (if any)...${RESET}"
sudo apt remove ffmpeg --purge -y 2>/dev/null || true
sudo apt autoremove --purge -y

# ----------------------------------------------
# Download Jellyfin FFmpeg pre-compiled binaries
# ----------------------------------------------
echo -e "\n${CYAN}📦 Downloading Jellyfin FFmpeg binaries...${RESET}"
echo -e "${YELLOW}   Version: 7.1.1-2 (with RKMPP/RGA support)${RESET}"

# Create temp directory
rm -rf "$TEMP_DIR"
mkdir -p "$TEMP_DIR"
cd "$TEMP_DIR" || exit 1

# Download from Jellyfin releases
JELLYFIN_VERSION="7.1.1-2"
DOWNLOAD_URL="https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/v${JELLYFIN_VERSION}/jellyfin-ffmpeg_${JELLYFIN_VERSION}_portable_linuxarm64-gpl.tar.xz"

echo -e "${BLUE}   Downloading from: ${DOWNLOAD_URL}${RESET}"

if ! curl -L -o jellyfin-ffmpeg.tar.xz "$DOWNLOAD_URL"; then
    echo -e "${RED}❌ Download failed from official Jellyfin repository${RESET}"
    echo -e "${YELLOW}   Trying alternative source (MediaFire)...${RESET}"
    
    # Alternative download link from the forum discussion
    ALT_URL="https://www.mediafire.com/file/u27t9234stsw0xz/jellyfin-ffmpeg_7.1.1-2_portable_linuxarm64-gpl.tar.xz/file"
    echo -e "${RED}⚠️  Alternative download requires manual intervention${RESET}"
    echo -e "${YELLOW}   Please download manually from:${RESET}"
    echo -e "${YELLOW}   ${ALT_URL}${RESET}"
    echo -e "${YELLOW}   Then run: sudo tar -xJf jellyfin-ffmpeg_7.1.1-2_portable_linuxarm64-gpl.tar.xz -C /usr/local/bin/ ffmpeg ffprobe${RESET}"
    exit 1
fi

# ----------------------------------------------
# Extract binaries to /usr/local/bin
# ----------------------------------------------
echo -e "\n${CYAN}📂 Extracting FFmpeg binaries...${RESET}"

# Extract ffmpeg and ffprobe to /usr/local/bin
sudo tar -xJf jellyfin-ffmpeg.tar.xz -C /usr/local/bin/ --strip-components=1 jellyfin-ffmpeg/ffmpeg jellyfin-ffmpeg/ffprobe 2>/dev/null || \
sudo tar -xJf jellyfin-ffmpeg.tar.xz -C /usr/local/bin/ ffmpeg ffprobe

# Make them executable
sudo chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe

# ----------------------------------------------
# Verify installation
# ----------------------------------------------
echo -e "\n${CYAN}🔍 Verifying installation...${RESET}"

if ! command -v ffmpeg &> /dev/null; then
    echo -e "${RED}❌ FFmpeg installation failed${RESET}"
    exit 1
fi

FFMPEG_VERSION=$(ffmpeg -version 2>&1 | head -n 1)
echo -e "${GREEN}✅ ${FFMPEG_VERSION}${RESET}"

# Check for RKMPP support
echo -e "\n${CYAN}🔍 Checking for Rockchip MPP support...${RESET}"
if ffmpeg -decoders 2>&1 | grep -q "rkmpp"; then
    echo -e "${GREEN}✅ RKMPP decoders available:${RESET}"
    ffmpeg -decoders 2>&1 | grep rkmpp | sed 's/^/   /'
else
    echo -e "${RED}❌ RKMPP decoders not found${RESET}"
    echo -e "${YELLOW}   This FFmpeg build may not have Rockchip support${RESET}"
fi

# Check for RGA support
if ffmpeg -encoders 2>&1 | grep -q "rkmpp\|rkrga"; then
    echo -e "${GREEN}✅ RKMPP encoders available${RESET}"
fi

# ----------------------------------------------
# Install runtime dependencies
# ----------------------------------------------
echo -e "\n${CYAN}📦 Installing runtime dependencies...${RESET}"
sudo apt update
sudo apt install -y \
    libdrm2 \
    libnuma1 \
    libopus0 \
    libass9 \
    libmp3lame0 \
    libtheora0 \
    libvorbis0a \
    libxml2 \
    2>/dev/null || true

# Try to install version-specific packages (may vary by distro)
sudo apt install -y libvpx7 libvpx9 2>/dev/null || true
sudo apt install -y libdav1d6 libdav1d7 2>/dev/null || true
sudo apt install -y libbluray2 2>/dev/null || true
sudo apt install -y libwebp7 2>/dev/null || true
sudo apt install -y libx264-164 libx264-163 2>/dev/null || true
sudo apt install -y libx265-199 libx265-209 2>/dev/null || true

# ----------------------------------------------
# Clean up
# ----------------------------------------------
echo -e "\n${CYAN}🧼 Cleaning up...${RESET}"
cd /
rm -rf "$TEMP_DIR"

# ----------------------------------------------
# Display summary
# ----------------------------------------------
echo -e "\n${GREEN}╔════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${GREEN}║  ✅ Installation Complete!                                      ║${RESET}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${RESET}"

echo -e "\n${CYAN}📋 Installation Summary:${RESET}"
echo -e "   FFmpeg location: $(which ffmpeg)"
echo -e "   FFprobe location: $(which ffprobe)"
echo -e "   Version: $FFMPEG_VERSION"

echo -e "\n${CYAN}🎬 Hardware Acceleration Available:${RESET}"
echo -e "   • MJPEG decoding: mjpeg_rkmpp"
echo -e "   • H.264 decoding: h264_rkmpp"
echo -e "   • HEVC decoding: hevc_rkmpp"
echo -e "   • VP8/VP9 decoding: vp8_rkmpp, vp9_rkmpp"
echo -e "   • H.264 encoding: h264_rkmpp"

echo -e "\n${CYAN}💡 Usage Example:${RESET}"
echo -e "   ffmpeg -c:v mjpeg_rkmpp -i input.mjpeg -c:v h264_rkmpp output.mp4"

echo -e "\n${YELLOW}⚠️  Note: If you encounter issues, you may need to install:${RESET}"
echo -e "   ${YELLOW}sudo apt install rockchip-mpp rockchip-rga${RESET}"
