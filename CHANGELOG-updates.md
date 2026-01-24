# Ravens Perch Updates - Summary

## New Features Implemented

### 1. Web Configuration Interface (`http://localhost/cameras`)

A complete web-based camera configuration UI that allows:

- **Live WebRTC Preview** - See real-time video from each camera directly in the browser
- **Dynamic Settings** - Change format, resolution, frame rate, and bitrate on the fly
- **Cascading Dropdowns** - Resolution and FPS options automatically update based on selected format
- **Moonraker Toggle** - Enable/disable Moonraker integration per camera
- **Add New Cameras** - Easily add unconfigured devices with auto-detected capabilities
- **Delete Cameras** - Remove cameras from MediaMTX and Moonraker

**Files Added:**
- `scripts/web_ui.py` - Flask application (port 80)
- `scripts/web_ui/templates/cameras.html` - Main UI template
- `scripts/web_ui/static/css/style.css` - Styling (dark theme)
- `scripts/web_ui/static/js/cameras.js` - Client-side logic
- `templates/web-ui.service.template` - systemd service

### 2. Plug-and-Play Camera Detection

Automatic camera detection and configuration when cameras are plugged in:

- **Auto-Detection** - Uses pyudev (preferred) or polling to detect new cameras
- **Smart Quality Selection** - Uses `estimate_cpu_capability()` to choose optimal settings
- **Auto MediaMTX** - Immediately adds camera to MediaMTX streaming
- **Auto Moonraker** - Automatically registers camera with Moonraker/Fluidd/Mainsail
- **Graceful Disconnect** - Handles camera removal without crashing

**Files Added:**
- `scripts/camera_hotplug.py` - Hotplug daemon
- `templates/camera-hotplug.service.template` - systemd service

## Files Modified

- `install.sh` - Added web-ui and camera-hotplug services
- `uninstall.sh` - Added cleanup for new services
- `venv-requirements.txt` - Added pyudev dependency

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    User Browser                                  │
│                  http://localhost/cameras                        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    web-ui.py (port 80)                          │
│              Flask app serving web interface                     │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
┌────────────────┐ ┌──────────────┐ ┌─────────────────┐
│ raven_settings │ │  MediaMTX    │ │   Moonraker     │
│     .yml       │ │  API :9997   │ │   API :7125     │
└────────────────┘ └──────────────┘ └─────────────────┘
              ▲
              │
┌─────────────────────────────────────────────────────────────────┐
│               camera_hotplug.py (daemon)                         │
│         Monitors USB events, auto-configures new cameras         │
└─────────────────────────────────────────────────────────────────┘
```

## Services (after install)

| Service | Port | Purpose |
|---------|------|---------|
| mediamtx | 8554, 8889, 8888 | RTSP/WebRTC/HLS streaming |
| snapfeeder | 5050 | JPEG snapshots |
| raven-watchdog | 5051 | Config sync API |
| **web-ui** | **80** | **Web configuration interface** |
| **camera-hotplug** | - | **Plug-and-play daemon** |

## Usage

### After Installation

1. **Web Interface**: Open `http://<your-ip>/cameras` in a browser
2. **Plug and Play**: Simply plug in a USB camera - it will be auto-configured

### API Endpoints (web-ui.py)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/cameras` | List all configured cameras |
| GET | `/api/cameras/<uid>` | Get camera details |
| PUT | `/api/cameras/<uid>` | Update camera settings |
| DELETE | `/api/cameras/<uid>` | Delete camera |
| GET | `/api/devices` | List all connected devices |
| POST | `/api/devices/add` | Add new camera |
| GET | `/api/status` | System status |
| POST | `/api/sync` | Force sync all cameras |

## Notes

- **Port 80**: The web UI runs on port 80, which requires root privileges. The service runs as root.
- **pyudev**: For best hotplug performance, pyudev is used. Falls back to polling if unavailable.
- **Quality Selection**: Uses the existing `estimate_cpu_capability()` logic to auto-select appropriate resolution/fps based on system power and number of cameras.
