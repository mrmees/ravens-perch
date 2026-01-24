# Ravens Perch

**Zero-touch camera management for Klipper-based 3D printers**

Cameras are automatically detected, optimally configured, and registered with Moonraker without user intervention. A web UI at `/cameras/` provides optional customization.

---

## Key Features

### Zero-Touch Operation
- **Automatic Detection**: Cameras are detected instantly when plugged in via USB
- **Smart Auto-Configuration**: Resolution, framerate, and encoder selected based on camera capabilities and system resources
- **Moonraker Integration**: Cameras automatically appear in Fluidd/Mainsail
- **Persistent Settings**: Camera configurations survive reboots and reconnections

### Web Interface
- **Dashboard**: View all cameras with live status and thumbnails
- **Per-Camera Settings**: Adjust resolution, framerate, rotation, and more
- **Advanced Options**: Encoder selection, bitrate, V4L2 controls
- **Log Viewer**: Monitor system activity
- **Dark Theme**: Matches Fluidd/Mainsail aesthetic

### Streaming
- **MediaMTX Backend**: RTSP, WebRTC, and HLS streaming
- **Hardware Acceleration**: VAAPI (Intel/AMD), V4L2M2M (Raspberry Pi)
- **JPEG Snapshots**: On-demand snapshots with caching
- **Quality Tiers**: Automatic quality adjustment based on CPU capability

---

## Requirements

### System
- Linux (Debian/Ubuntu-based, Raspberry Pi OS)
- Python 3.8+
- FFmpeg
- v4l-utils
- nginx (for reverse proxy)

### Supported Platforms
- Raspberry Pi 3/4/5
- x86_64 (Intel/AMD)
- ARM64 (Orange Pi, etc.)

---

## Installation

### Quick Install

```bash
cd ~
git clone https://github.com/mrmees/ravens-perch-v2.git ravens-perch
cd ravens-perch
bash install.sh
```

The installer will:
1. Install system dependencies
2. Download MediaMTX
3. Create Python virtual environment
4. Initialize the database
5. Configure systemd services
6. Set up nginx reverse proxy
7. Add Moonraker update manager entry

### Access the Web UI

After installation:
```
http://<your-ip>/cameras/
```

Or directly:
```
http://<your-ip>:8585/cameras/
```

---

## How It Works

### Automatic Camera Detection

1. **Plug in a USB camera** - Ravens Perch detects it within seconds
2. **Capabilities probed** - Available formats, resolutions, and framerates discovered
3. **Auto-configured** - Optimal settings selected based on system capability
4. **Stream started** - MediaMTX stream created automatically
5. **Registered with Moonraker** - Camera appears in Fluidd/Mainsail

### Quality Tiers

Ravens Perch automatically selects quality based on your system:

| CPU Rating | Resolution | Framerate | Bitrate |
|------------|------------|-----------|---------|
| Low (1-3)  | 640x480    | 15 fps    | 1 Mbps  |
| Medium (4-5) | 1280x720 | 15 fps    | 2 Mbps  |
| Good (6-7) | 1280x720   | 30 fps    | 4 Mbps  |
| High (8-9) | 1920x1080  | 30 fps    | 6 Mbps  |
| Excellent (10) | 1920x1080 | 60 fps | 8 Mbps  |

Hardware encoders (VAAPI, V4L2M2M) boost your CPU rating.

---

## Web UI Guide

### Dashboard (`/cameras/`)

- Grid view of all cameras
- Live/Offline status indicators
- Thumbnail previews (auto-refresh)
- Quick access to configuration

### Camera Detail (`/cameras/<id>`)

**Basic Settings:**
- Friendly name
- Resolution (populated from camera capabilities)
- Framerate
- Enable/disable toggle

**Advanced Settings:**
- Input format (MJPEG, H.264, YUYV)
- Encoder (libx264, VAAPI, V4L2M2M)
- Bitrate
- Rotation (0°, 90°, 180°, 270°)

**Stream URLs:**
- WebRTC, RTSP, and snapshot URLs displayed
- Click to copy

### Settings (`/cameras/settings`)

- CPU threshold for quality reduction
- Moonraker URL configuration
- Log level
- System information

### Logs (`/cameras/logs`)

- Filterable by level (Info, Warning, Error)
- Camera-specific log entries
- Auto-refresh

---

## Stream URLs

For each camera, streams are available at:

| Protocol | URL |
|----------|-----|
| WebRTC | `http://<ip>:8889/<camera_id>/` |
| RTSP | `rtsp://<ip>:8554/<camera_id>` |
| HLS | `http://<ip>:8888/<camera_id>/` |
| Snapshot | `http://<ip>/cameras/snapshot/<camera_id>.jpg` |

---

## Service Management

### Check Status
```bash
sudo systemctl status ravens-perch
sudo systemctl status mediamtx
```

### Restart Services
```bash
sudo systemctl restart ravens-perch
```

### View Logs
```bash
# Ravens Perch logs
sudo journalctl -u ravens-perch -f

# MediaMTX logs
sudo journalctl -u mediamtx -f

# Or via web UI at /cameras/logs
```

---

## Configuration

### Database Location
```
~/ravens-perch/data/ravens-perch.db
```

Camera settings persist in SQLite. Survives reboots and reinstalls.

### Log Files
```
~/ravens-perch/logs/ravens-perch.log
```

### MediaMTX Configuration
```
~/ravens-perch/mediamtx/mediamtx.yml
```

Streams are managed dynamically via the MediaMTX API - no manual editing needed.

---

## Uninstallation

```bash
cd ~/ravens-perch
bash uninstall.sh
```

Options:
- Keep database and logs for reinstall
- Remove everything completely

---

## Troubleshooting

### Camera Not Detected

**Check if camera is recognized:**
```bash
ls -la /dev/video*
v4l2-ctl --list-devices
```

**Check permissions:**
```bash
# Add user to video group
sudo usermod -aG video $USER
# Log out and back in
```

### Stream Not Starting

**Check Ravens Perch logs:**
```bash
sudo journalctl -u ravens-perch -n 50
```

**Common issues:**
- Camera in use by another application
- Unsupported format/resolution
- FFmpeg errors (check logs)

### Web UI Not Accessible

**Check nginx configuration:**
```bash
sudo nginx -t
sudo systemctl status nginx
```

**Direct access (bypass nginx):**
```
http://<ip>:8585/cameras/
```

### High CPU Usage

1. Check if hardware encoder is detected (Settings page)
2. Lower resolution in camera settings
3. Reduce framerate
4. Lower bitrate

---

## Architecture

```
ravens-perch/
├── daemon/
│   ├── main.py              # Entry point, orchestration
│   ├── config.py            # Configuration constants
│   ├── db.py                # SQLite database layer
│   ├── camera_manager.py    # Detection, auto-config
│   ├── stream_manager.py    # MediaMTX API
│   ├── moonraker_client.py  # Moonraker API
│   ├── snapshot_server.py   # JPEG snapshots
│   ├── hardware.py          # Encoder detection
│   └── web_ui/
│       ├── app.py           # Flask application
│       ├── routes.py        # Route handlers
│       ├── templates/       # Jinja2 templates
│       └── static/          # CSS, JS
├── mediamtx/                # MediaMTX binary
├── data/                    # SQLite database
├── logs/                    # Log files
├── install.sh
└── uninstall.sh
```

---

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

---

## License

MIT License

Copyright (c) 2025 mrmees

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

## Acknowledgments

- **bluenviron**: [MediaMTX](https://github.com/bluenviron/mediamtx)
- **Klipper/Moonraker**: 3D printer firmware and API
- **Fluidd/Mainsail**: Web interfaces for Klipper

---

## Support

- **Issues**: [GitHub Issues](https://github.com/mrmees/ravens-perch-v2/issues)
