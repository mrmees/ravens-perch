# Ravens Perch 🦅

**Advanced webcam streaming solution for 3D printers and beyond**

A comprehensive, menu-driven camera configuration tool for MediaMTX with advanced features including Moonraker integration, V4L2 controls, audio support, and recording capabilities.

---

## 🌟 Features

### Core Streaming
- **MediaMTX Backend**: RTSP, WebRTC, and HLS streaming
- **Hardware Acceleration**: VAAPI (Intel/AMD), RKMPP (Rockchip), V4L2M2M (Raspberry Pi)
- **Snapshot Server**: On-demand JPEG snapshots via Flask/PyAV
- **Auto-Detection**: Automatically finds and configures USB cameras

### Advanced Configuration
- **Interactive Menu System**: Easy-to-use terminal interface
- **Bitrate Control**: 500 Kbps to 10 Mbps
- **Video Rotation**: 0°, 90°, 180°, 270°
- **V4L2 Camera Controls**: Brightness, contrast, saturation, exposure, white balance
- **Audio Support**: Microphone capture with AAC or Opus codecs
- **Recording**: Continuous recording with configurable segment duration
- **Per-Camera Settings**: Individual configurations saved to JSON

### Moonraker Integration
- **Direct API Integration**: Add cameras to Moonraker/Fluidd/Mainsail
- **Smart Detection**: Identifies existing MediaMTX cameras
- **Bulk Operations**: Add all cameras at once or selectively
- **WebRTC & HLS Support**: Choose streaming method per camera
- **Auto URL Generation**: Automatically uses correct system IP

---

## 📋 Requirements

### System Requirements
- Linux (Ubuntu 20.04+ or Debian-based)
- Python 3.8+
- FFmpeg (or hardware-accelerated build)
- v4l-utils
- USB webcam(s)

### Supported Platforms
- ✅ x86_64 (Intel/AMD with VAAPI)
- ✅ ARM64 (Raspberry Pi, Orange Pi, etc.)
- ✅ Rockchip (RK3588, RK3399 with MPP/RGA)
- ✅ Raspberry Pi (with V4L2M2M)

---

## 🚀 Quick Start

### 1. Clone the Repository

```bash
cd ~
git clone https://github.com/mrmees/ravens-perch.git
cd ravens-perch
```

### 2. Run the Installer

```bash
bash install.sh
```

The installer will:
- Install system dependencies (python3, ffmpeg, v4l-utils, etc.)
- Create a Python virtual environment
- Download the latest MediaMTX release
- Auto-detect and configure connected cameras
- Create systemd services
- Start streaming

**For Rockchip Platforms:**
The installer will detect Rockchip SoCs and offer to install an optimized FFmpeg build with MPP/RGA hardware acceleration.

### 3. Configure Your Cameras (Interactive)

After installation, run the configuration tool:

```bash
cd ~/ravens-perch
./venv/bin/python scripts/generate_mediamtx_config.py
```

You'll see the main menu:

```
======================================================================
🎥 MediaMTX Camera Configuration Tool
======================================================================

  [1] Configure Connected Devices
      - Select which cameras to use
      - Choose resolution and format
      - Basic setup

  [2] Advanced Video/Audio Settings
      - Adjust bitrate, encoder, rotation
      - Configure V4L2 controls
      - Setup recording and audio

  [3] Moonraker Integration
      - Add cameras to Moonraker/Fluidd/Mainsail
      - Manage existing Moonraker cameras
      - Bulk operations

  [4] Quick Auto-Configure All
      - Automatically configure all cameras
      - Use best available settings

  [q] Quit
```

---

## 📖 Detailed Usage

### Option 1: Configure Connected Devices

**Interactive Mode:**
- View all available resolutions and formats
- Select specific resolution/FPS for each camera
- See quality indicators (High/Medium/Low)
- Skip cameras you don't want to use

**Auto Mode:**
- Automatically selects best format (prefers MJPEG)
- Chooses 1280x720 if available, otherwise highest resolution
- Uses maximum FPS

### Option 2: Advanced Video/Audio Settings

Configure per-camera settings:

#### Video Quality & Performance
- **Bitrate**: 500K to 10M (default: 4M)
  - 1M = Good for 3D printer monitoring
  - 4M = High quality streaming
  - 8M = Maximum quality
- **Encoder Preset**: ultrafast to slow (software encoding)
- **Buffer Size**: small/default/large (latency vs stability)

#### Video Adjustments
- **Rotation**: Fix sideways/upside-down cameras
  - 0°, 90°, 180°, 270°

#### V4L2 Camera Controls
- **Brightness**: Adjust exposure brightness
- **Contrast**: Increase/decrease contrast
- **Saturation**: Control color intensity
- **Auto Exposure**: Enable/disable automatic exposure
- **Auto White Balance**: Enable/disable AWB

#### Recording
- **Enable/Disable**: Continuous recording to disk
- **Recording Path**: Where to save files
- **Segment Duration**: File length (60s, 5m, 1h, etc.)

#### Audio
- **Enable/Disable**: Capture microphone audio
- **Audio Device**: Auto-detected ALSA devices
- **Codec**: AAC (best compatibility) or Opus (better quality)

### Option 3: Moonraker Integration

Integrate with Klipper/Moonraker for 3D printer webcams:

**Features:**
- Auto-detects localhost Moonraker instance
- Lists existing webcams with MediaMTX status
- Shows which cameras are already assigned
- Prevents duplicate assignments
- Supports WebRTC and HLS streaming

**Operations:**
1. Add new camera to Moonraker
2. Update existing camera
3. Add ALL cameras (bulk)
4. Add only unassigned cameras
5. Delete cameras
6. Change Moonraker URL

### Option 4: Quick Auto-Configure

Fastest way to get started:
- Detects all cameras
- Auto-selects best settings
- Uses defaults (4M bitrate, no rotation, etc.)
- No prompts

---

## 🎯 Camera Access URLs

After configuration, cameras are available at:

```
🎥 cam0:
   📡 RTSP:     rtsp://192.168.1.100:8554/cam0
   🌐 WebRTC:   http://192.168.1.100:8889/cam0/
   📺 HLS:      http://192.168.1.100:8888/cam0/index.m3u8
   🖼️ Snapshot: http://192.168.1.100:5050/cam0.jpg
```

Replace `192.168.1.100` with your system's IP address.

---

## 🔧 Configuration Files

### Main Configuration
- **mediamtx.yml**: `~/ravens-perch/mediamtx/mediamtx.yml`
  - MediaMTX configuration
  - FFmpeg commands
  - Stream settings

- **camera_settings.json**: `~/ravens-perch/mediamtx/camera_settings.json`
  - Advanced settings per camera
  - Persists between reconfigurations

### Service Files
- **mediamtx.service**: Streaming server
- **snapfeeder.service**: Snapshot server

---

## 🔄 Managing Services

### View Status
```bash
sudo systemctl status mediamtx.service
sudo systemctl status snapfeeder.service
```

### Restart After Changes
```bash
sudo systemctl restart mediamtx.service snapfeeder.service
```

### View Logs
```bash
# MediaMTX logs
sudo journalctl -u mediamtx.service -f

# SnapFeeder logs
sudo journalctl -u snapfeeder.service -f
```

### Stop Services
```bash
sudo systemctl stop mediamtx.service snapfeeder.service
```

### Disable Auto-Start
```bash
sudo systemctl disable mediamtx.service snapfeeder.service
```

---

## 🗑️ Uninstallation

```bash
cd ~/ravens-perch
bash uninstall.sh
```

This will:
- Stop and disable services
- Remove systemd service files
- Delete MediaMTX installation
- Remove Python virtual environment
- Delete generated service files

**Note:** This preserves your configuration files in case you want to reinstall.

---

## 🌐 Moonraker Integration Guide

### For Fluidd/Mainsail Users

1. Run the configuration tool:
   ```bash
   ./venv/bin/python scripts/generate_mediamtx_config.py
   ```

2. Select **[3] Moonraker Integration**

3. Choose **[1] Add MediaMTX camera to Moonraker (new)**

4. Select your camera and streaming service:
   - **WebRTC** (recommended): Lower latency, better for live viewing
   - **HLS**: Better compatibility, works on more devices

5. Enter a name (or accept default)

6. Camera will appear in Fluidd/Mainsail webcam settings!

### Manual Moonraker Configuration

Alternatively, add to your `moonraker.conf`:

**WebRTC:**
```ini
[webcam my_camera]
service: webrtc-mediamtx
stream_url: http://192.168.1.100:8889/cam0/
snapshot_url: http://192.168.1.100:5050/cam0.jpg
```

**HLS:**
```ini
[webcam my_camera]
service: hlsstream
stream_url: http://192.168.1.100:8888/cam0/index.m3u8
snapshot_url: http://192.168.1.100:5050/cam0.jpg
```

---

## 🛠️ Troubleshooting

### Camera Not Detected

**Check if camera is recognized:**
```bash
ls -la /dev/video*
v4l2-ctl --list-devices
```

**Check supported formats:**
```bash
v4l2-ctl --list-formats-ext -d /dev/video0
```

### Services Not Starting

**Check MediaMTX logs:**
```bash
sudo journalctl -u mediamtx.service -n 50
```

**Check SnapFeeder logs:**
```bash
sudo journalctl -u snapfeeder.service -n 50
```

**Common issues:**
- Camera permissions (add user to `video` group)
- Port conflicts (8554, 8889, 8888, 5050)
- FFmpeg not found

### Stream Not Working

**Test RTSP directly:**
```bash
ffplay rtsp://localhost:8554/cam0
```

**Check if MediaMTX is running:**
```bash
sudo systemctl status mediamtx.service
```

**Verify camera is streaming:**
```bash
curl http://localhost:5050/cam0.jpg --output test.jpg
```

### High CPU Usage

Try these optimizations:
1. **Lower resolution** (720p → 480p)
2. **Lower bitrate** (4M → 2M)
3. **Enable hardware acceleration** (check if VAAPI/RKMPP/V4L2M2M is available)
4. **Use MJPEG format** (most efficient for USB cameras)

### Camera Appears Rotated

Use **Option 2 → Advanced Settings** and set rotation to:
- 90° for clockwise
- 180° for upside down
- 270° for counter-clockwise

### Poor Image Quality

Adjust in **Option 2 → Advanced Settings**:
- Increase bitrate (4M → 6M or 8M)
- Adjust brightness/contrast
- Enable auto exposure/white balance

### Moonraker Connection Failed

**Verify Moonraker is running:**
```bash
curl http://localhost:7125/server/info
```

**Check Moonraker port:**
Default is 7125, but could be different. Check your `moonraker.conf`.

---

## 💡 Tips & Best Practices

### Camera Placement
- Mount cameras securely to avoid vibration
- Consider lighting (good lighting = better image quality than any software adjustment)
- USB cable quality matters (use short, high-quality cables)

### Network Bandwidth
- **WiFi 2.4GHz**: Max ~2-3 cameras at 2M bitrate
- **WiFi 5GHz**: Max ~10+ cameras at 4M bitrate
- **Wired Ethernet**: No practical limit

### Storage for Recording
Recording uses significant disk space:
- **1M bitrate**: ~450 MB/hour
- **4M bitrate**: ~1.8 GB/hour
- **8M bitrate**: ~3.6 GB/hour

Set up automatic cleanup or use large storage.

### Multiple Cameras
- Name cameras logically (front, side, tool_head, etc.)
- Use different resolutions based on importance
- Lower bitrate on less critical cameras

### Hardware Acceleration
Always use hardware encoding when available:
- **Intel/AMD**: VAAPI (automatic on most modern CPUs)
- **Rockchip**: Install custom FFmpeg during setup
- **Raspberry Pi**: V4L2M2M (built-in on Pi 3/4/5)

---

## 🔐 Security Considerations

### Default Configuration
- Services bind to `0.0.0.0` (all interfaces)
- No authentication by default
- Suitable for trusted local networks

### For Public/Untrusted Networks
1. **Use a reverse proxy** (NGINX, Caddy) with authentication
2. **Add firewall rules** to restrict access
3. **Use VPN** for remote access
4. **Enable HTTPS** via reverse proxy

Example NGINX with basic auth:
```nginx
location /cam0/ {
    auth_basic "Camera Access";
    auth_basic_user_file /etc/nginx/.htpasswd;
    proxy_pass http://localhost:8889/cam0/;
}
```

---

## 🤝 Contributing

This project is a fork of [mtx-stream-snap](https://github.com/thesydoruk/mtx-stream-snap) by thesydoruk, with significant enhancements including:
- Advanced configuration menu system
- Moonraker integration
- V4L2 camera controls
- Audio support
- Recording capabilities
- Per-camera advanced settings

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

---

## 📜 License

MIT License

Original work: Copyright (c) 2025 Valerii Sydoruk (thesydoruk)  
Modified work: Copyright (c) 2025 mrmees

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## 🙏 Acknowledgments

- **thesydoruk**: Original [mtx-stream-snap](https://github.com/thesydoruk/mtx-stream-snap) project
- **bluenviron**: [MediaMTX](https://github.com/bluenviron/mediamtx) - Excellent RTSP server
- **nyanmisaka**: Rockchip-optimized [FFmpeg fork](https://github.com/nyanmisaka/ffmpeg-rockchip)
- **Moonraker/Klipper**: 3D printer interface integration

---

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/mrmees/ravens-perch/issues)
- **Discussions**: [GitHub Discussions](https://github.com/mrmees/ravens-perch/discussions)
- **Original Project**: [mtx-stream-snap](https://github.com/thesydoruk/mtx-stream-snap)

---

## 🗺️ Roadmap

Future enhancements being considered:
- [ ] Web UI for configuration
- [ ] Motion detection
- [ ] MQTT support for Home Assistant
- [ ] Network camera support (not just USB)
- [ ] Multi-camera view layouts
- [ ] Timelapse generation
- [ ] Cloud upload integration
- [ ] Mobile app

---

**Made with ❤️ for the 3D printing and maker community**
