"""
Ravens Perch v3 - Configuration Constants and Defaults
"""
import os
from pathlib import Path

# Base paths
BASE_DIR = Path(os.environ.get("RAVENS_PERCH_DIR", Path.home() / "ravens-perch"))
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"

# Database
DATABASE_PATH = DATA_DIR / "ravens-perch.db"

# MediaMTX ports
MEDIAMTX_RTSP_PORT = 8554
MEDIAMTX_WEBRTC_PORT = 8889
MEDIAMTX_HLS_PORT = 8888
MEDIAMTX_API_PORT = 9997
MEDIAMTX_API_BASE = f"http://127.0.0.1:{MEDIAMTX_API_PORT}"

# Web UI
WEB_UI_PORT = 8585
WEB_UI_HOST = "0.0.0.0"

# Moonraker
MOONRAKER_DEFAULT_URL = "http://127.0.0.1:7125"
MOONRAKER_FALLBACK_URLS = [
    "http://localhost:7125",
    "http://127.0.0.1:7125",
]

# CPU and performance
DEFAULT_CPU_THRESHOLD = 30  # Percent
DEBOUNCE_DELAY = 2.0  # Seconds after device events

# Quality tiers (based on CPU capability rating 1-10)
QUALITY_TIERS = {
    # (min_rating, max_rating): (resolution, framerate, bitrate)
    (1, 3): ("640x480", 15, "1M"),
    (4, 5): ("1280x720", 15, "2M"),
    (6, 7): ("1280x720", 30, "4M"),
    (8, 9): ("1920x1080", 30, "6M"),
    (10, 10): ("1920x1080", 60, "8M"),
}

# Format priorities (higher = preferred)
FORMAT_PRIORITY = {
    "mjpeg": 100,  # Best for USB cameras - low CPU decode
    "h264": 90,    # Good if camera supports native H.264
    "yuyv": 50,    # Raw - high bandwidth, requires encoding
    "yuyv422": 50,
    "nv12": 40,
    "rgb24": 30,
}

# Format aliases (v4l2-ctl output -> normalized name)
FORMAT_ALIASES = {
    "Motion-JPEG": "mjpeg",
    "MJPG": "mjpeg",
    "H.264": "h264",
    "H264": "h264",
    "YUYV 4:2:2": "yuyv",
    "YUYV": "yuyv",
    "NV12": "nv12",
    "RGB3": "rgb24",
}

# Encoder settings
ENCODER_DEFAULTS = {
    "libx264": {
        "preset": "ultrafast",
        "tune": "zerolatency",
    },
    "h264_vaapi": {
        "quality": 25,
    },
    "h264_v4l2m2m": {
        # V4L2M2M typically doesn't need extra options
    },
}

# Default camera settings
DEFAULT_CAMERA_SETTINGS = {
    "format": "mjpeg",
    "resolution": "1280x720",
    "framerate": 30,
    "encoder": "libx264",
    "bitrate": "4M",
    "preset": "ultrafast",
    "rotation": 0,
    "audio_enabled": False,
}

# Snapshot settings
SNAPSHOT_CACHE_TTL_MS = 100  # Cache duration in milliseconds
SNAPSHOT_TIMEOUT = 5.0  # Seconds to wait for frame

# Logging
LOG_ROTATION_SIZE = 10 * 1024 * 1024  # 10 MB
LOG_ROTATION_COUNT = 5
LOG_LEVEL = os.environ.get("RAVENS_PERCH_LOG_LEVEL", "INFO")

# Supported resolutions (for UI dropdowns)
COMMON_RESOLUTIONS = [
    "640x480",
    "800x600",
    "1024x768",
    "1280x720",
    "1280x960",
    "1920x1080",
    "2560x1440",
    "3840x2160",
]

# Supported framerates
COMMON_FRAMERATES = [5, 10, 15, 20, 25, 30, 60]

# V4L2 control names for UI
V4L2_CONTROLS = [
    "brightness",
    "contrast",
    "saturation",
    "hue",
    "gamma",
    "sharpness",
    "backlight_compensation",
    "exposure_auto",
    "exposure_absolute",
    "focus_auto",
    "focus_absolute",
    "white_balance_temperature_auto",
    "white_balance_temperature",
]
