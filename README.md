# Ravens Perch

**Zero-touch camera management for Klipper-based 3D printers**

Cameras are automatically detected, optimally configured, and registered with Moonraker without user intervention. Features print status overlays, dynamic framerate switching, and a web UI for customization.

---

## Key Features

### Zero-Touch Operation
- **Automatic Detection**: Cameras are detected instantly when plugged in via USB
- **Smart Auto-Configuration**: Resolution, framerate, and encoder selected based on camera capabilities and system resources
- **Moonraker Integration**: Cameras automatically appear in Fluidd/Mainsail
- **Persistent Settings**: Camera configurations survive reboots and reconnections

### Print Integration
- **Live Print Status Overlay**: Display print progress, temperatures, and more directly on the video feed
- **16+ Overlay Options**: Progress %, layer, ETA, elapsed time, filename, hotend/bed temps, fan speed, print state, filament used, current time, print speed, Z height, head speed, flow rate, filament type
- **Customizable Appearance**: Font selection, size, color, position, and layout options
- **Dynamic Framerate**: Automatically switch between high framerate during prints and low framerate on standby to save resources

### Web Interface
- **Dashboard**: View all cameras with live status and thumbnails
- **Per-Camera Settings**: Adjust resolution, framerate, rotation, and more
- **Camera Controls**: Real-time adjustment of brightness, contrast, exposure, and other V4L2 controls
- **Advanced Options**: Encoder selection, bitrate, input format
- **Log Viewer**: Monitor system activity
- **Dark Theme**: Matches Fluidd/Mainsail aesthetic

### Streaming
- **MediaMTX Backend**: RTSP, WebRTC, and HLS streaming
- **Hardware Acceleration**: VAAPI (Intel/AMD), V4L2M2M (Raspberry Pi), RKMPP (Rockchip)
- **JPEG Snapshots**: On-demand snapshots with caching
- **Quality Tiers**: Automatic quality adjustment based on CPU capability

---

## Requirements

### System
- Linux (Debian/Ubuntu-based, Raspberry Pi OS, Armbian)
- Python 3.8+
- FFmpeg
- v4l-utils
- nginx (for reverse proxy)

### Supported Platforms
- Raspberry Pi 3/4/5
- x86_64 (Intel/AMD)
- ARM64 (Orange Pi, Radxa, BTT CB1/CB2, etc.)
- Rockchip RK3566/RK3568/RK3588 (with RKMPP hardware encoding)

---

## Before You Begin

### Disabling Crowsnest

If you're currently using Crowsnest for camera streaming, you'll need to disable it before running Ravens Perch. Both applications try to access the same camera devices, which will cause conflicts.

**Step 1: Stop the Crowsnest service**
```bash
sudo systemctl stop crowsnest
sudo systemctl disable crowsnest
```

**Step 2: Configuration files (optional)**

No changes to `printer.cfg` or `moonraker.conf` are strictly required. However, you may want to:

**Comment out existing webcam entries** in `moonraker.conf` or `webcams.conf` to avoid seeing duplicate/offline cameras in Fluidd/Mainsail:
```ini
# [webcam my_camera]
# location: printer
# stream_url: /webcam/?action=stream
# snapshot_url: /webcam/?action=snapshot
```

**The Crowsnest update_manager entry can remain** - it won't cause any issues:
```ini
[update_manager crowsnest]
type: git_repo
path: ~/crowsnest
...
```

Ravens Perch will register its own webcams with Moonraker automatically.

**To switch back to Crowsnest later:**
```bash
# Stop Ravens Perch
sudo systemctl stop ravens-perch
sudo systemctl disable ravens-perch

# Re-enable Crowsnest
sudo systemctl enable crowsnest
sudo systemctl start crowsnest

# Uncomment your webcam entries in moonraker.conf if you commented them out
```

---

## Installation

### Quick Install

**Single command:**
```bash
git clone https://github.com/mrmees/ravens-perch.git ~/ravens-perch && bash ~/ravens-perch/install.sh
```

**Or step by step:**
```bash
cd ~
git clone https://github.com/mrmees/ravens-perch.git ravens-perch
cd ravens-perch
bash install.sh
```

The installer will:
1. Install system dependencies (FFmpeg, v4l-utils, etc.)
2. Download and configure MediaMTX
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

Or directly (bypassing nginx):
```
http://<your-ip>:8585/
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
| Low (1-3)  | 640x480    | 10 fps    | 500 Kbps |
| Medium (4-5) | 640x480  | 15 fps    | 1 Mbps  |
| Good (6-7) | 1280x720   | 15 fps    | 2 Mbps  |
| High (8-9) | 1280x720   | 15 fps    | 2 Mbps  |
| Excellent (10) | 1280x720 | 30 fps  | 4 Mbps  |

These are conservative defaults for reliable initial setup. You can increase resolution, framerate, and bitrate via the web UI once your camera is running.

Hardware encoders (VAAPI, V4L2M2M, RKMPP) boost your effective CPU rating.

---

## Web UI Guide

### Dashboard (`/cameras/`)

- Grid view of all cameras
- Live/Offline status indicators
- Thumbnail previews (auto-refresh)
- Quick access to configuration
- Scan button to detect new cameras

### Camera Detail (`/cameras/<id>`)

**Basic Settings:**
- Friendly name (auto-syncs with Moonraker)
- Resolution (populated from camera capabilities)
- Framerate
- Enable/disable toggle

**Advanced Settings:**
- Input format (MJPEG, H.264, YUYV)
- Encoder (Software, VAAPI, V4L2M2M, RKMPP)
- Bitrate (1M - 10M)
- Rotation (0°, 90°, 180°, 270°)

**Print Integration:**
- Enable/disable print status overlay
- Overlay appearance (font, size, color, position)
- Stats to display (16+ options):
  - Progress %, Layer, ETA, Elapsed Time
  - Filename, Print State
  - Hotend Temp, Bed Temp, Fan Speed
  - Print Speed, Z Height
  - Head Speed (live velocity), Flow Rate
  - Filament Used, Filament Type, Current Time
- Multi-line or single-line layout
- Show/hide labels
- Custom standby text
- Overlay update interval (1-10 seconds)
- Dynamic framerate (printing vs standby)

**Camera Controls:**
- Real-time V4L2 control adjustment
- Brightness, contrast, saturation
- Exposure, gain, white balance
- Focus (for supported cameras)
- Changes apply immediately and persist

**Stream URLs:**
- WebRTC, RTSP, and snapshot URLs displayed
- Current FFmpeg command shown

### Settings (`/cameras/settings`)

- CPU threshold for quality reduction
- Moonraker URL configuration
- Log level (Debug, Info, Warning, Error)
- System information and encoder status

### Logs (`/cameras/logs`)

- Filterable by level (Info, Warning, Error)
- Camera-specific log entries
- Timestamps and context

---

## Print Status Overlay

The print status overlay displays real-time information from Moonraker directly on your camera feed.

### Available Stats

| Stat | Description |
|------|-------------|
| Progress % | Current print completion percentage |
| Layer | Current layer / total layers |
| ETA | Estimated time remaining |
| Elapsed Time | Time since print started |
| Filename | Current print file name |
| Print State | Printing, Paused, etc. |
| Hotend Temp | Current / target hotend temperature |
| Bed Temp | Current / target bed temperature |
| Fan Speed | Part cooling fan percentage |
| Print Speed | Current speed factor percentage |
| Z Height | Current Z position |
| Head Speed | Live toolhead velocity (mm/s) |
| Flow Rate | Live extruder velocity (mm/s) |
| Filament Used | Total filament extruded |
| Filament Type | From gcode metadata (PLA, PETG, etc.) |
| Current Time | System clock |

### Appearance Options

- **Font**: Select from any installed system font
- **Size**: 16px to 64px
- **Color**: White, Yellow, Cyan, Green, Red, Orange
- **Position**: Top/Bottom + Left/Center/Right
- **Layout**: Single line or multi-line
- **Labels**: Show or hide stat labels
- **Standby Text**: Custom text displayed when printer is idle (default: "On Standby")

---

## Dynamic Framerate

Save CPU and bandwidth by automatically reducing framerate when not printing:

- **Printing Framerate**: Higher FPS during active prints (e.g., 30 fps)
- **Standby Framerate**: Lower FPS when idle (e.g., 5 fps)

The switch happens automatically with a configurable delay after print completion.

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

### Overlay Files
```
~/ravens-perch/data/overlays/camera_<id>.txt
```

Text files updated by the print status monitor, read by FFmpeg's drawtext filter.

---

## Uninstallation

```bash
cd ~/ravens-perch
bash uninstall.sh
```

Options:
- Keep database and logs for reinstall
- Remove everything completely

**Developer mode** — for faster reinstallation during development, use:
```bash
bash uninstall.sh --dev
```
This preserves the Python virtual environment and MediaMTX installation while clearing the database, saving time on reinstall.

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

**Check if another application is using the camera:**
```bash
sudo fuser /dev/video0
```

### Stream Not Starting

**Check Ravens Perch logs:**
```bash
sudo journalctl -u ravens-perch -n 50
```

**Common issues:**
- Camera in use by another application (Crowsnest, etc.)
- Unsupported format/resolution combination
- FFmpeg errors (check logs for details)

### Overlay Not Updating

**Check Moonraker connection:**
```bash
curl http://localhost:7125/printer/objects/query?print_stats
```

**Check overlay files are being written:**
```bash
cat ~/ravens-perch/data/overlays/camera_*.txt
```

### Web UI Not Accessible

**Check nginx configuration:**
```bash
sudo nginx -t
sudo systemctl status nginx
```

**Direct access (bypass nginx):**
```
http://<ip>:8585/
```

### High CPU Usage

1. Check if hardware encoder is detected (Settings page)
2. Enable hardware encoder in camera settings if available
3. Lower resolution
4. Reduce framerate
5. Lower bitrate
6. Use standby framerate feature when not printing

---

## Hardware Encoders

Ravens Perch automatically detects and uses hardware encoders when available:

| Platform | Encoder | Notes |
|----------|---------|-------|
| Intel (6th gen+) | VAAPI | Excellent performance |
| AMD (APU/GPU) | VAAPI | Excellent performance |
| Raspberry Pi 4/5 | V4L2M2M | Good performance |
| Rockchip RK35xx | RKMPP | Experimental support |

Hardware encoding dramatically reduces CPU usage and allows higher quality streams.

---

## Architecture

```
ravens-perch/
├── daemon/
│   ├── main.py              # Entry point, orchestration
│   ├── config.py            # Configuration constants
│   ├── db.py                # SQLite database layer
│   ├── camera_manager.py    # Detection, probing, auto-config
│   ├── stream_manager.py    # FFmpeg commands, MediaMTX API
│   ├── moonraker_client.py  # Moonraker webcam API
│   ├── print_status.py      # Overlay text generation
│   ├── snapshot_server.py   # JPEG snapshot capture
│   ├── hardware.py          # Encoder detection
│   ├── bandwidth.py         # Bandwidth estimation
│   └── web_ui/
│       ├── app.py           # Flask application
│       ├── routes.py        # Route handlers
│       ├── templates/       # Jinja2 templates
│       └── static/          # CSS, JS
├── mediamtx/                # MediaMTX binary and config
├── data/                    # SQLite database, overlay files
├── logs/                    # Log files
├── install.sh               # Installation script
└── uninstall.sh             # Uninstallation script
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

MIT License - See LICENSE file for details.

---

## Acknowledgments

- [MediaMTX](https://github.com/bluenviron/mediamtx) by bluenviron
- [Klipper](https://github.com/Klipper3d/klipper) and [Moonraker](https://github.com/Arksine/moonraker)
- [Fluidd](https://github.com/fluidd-core/fluidd) and [Mainsail](https://github.com/mainsail-crew/mainsail)

---

## Support

- **Issues**: [GitHub Issues](https://github.com/mrmees/ravens-perch/issues)
