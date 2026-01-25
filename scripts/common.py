#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
common.py
---------
Shared utilities, constants, and helper functions for Ravens Perch camera configuration.

Philosophy:
- raven_settings.yml is the source of truth for user preferences
- MediaMTX and Moonraker are configured via API only (ephemeral)
- Settings are synced from raven_settings.yml to services on demand

Last modified: 2026-01-12
"""

import os
import re
import sys
import json
import time
import subprocess
import socket
import urllib.request
import urllib.error
import string
import random
import copy
from pathlib import Path
from collections import defaultdict
from ruamel.yaml import YAML

# ===== PATHS =====
SCRIPT_DIR = Path(__file__).resolve().parent
RAVEN_SETTINGS_PATH = SCRIPT_DIR.parent / "raven_settings.yml"

# Legacy paths for migration
LEGACY_JSON_SETTINGS_PATH = SCRIPT_DIR.parent / "raven_settings.json"

# ===== FORMAT CONSTANTS =====
FORMAT_PRIORITY = ["mjpeg", "h264", "nv12", "yuv420", "yuyv422", "rawvideo"]
FORMAT_ALIASES = {
    "mjpg": "mjpeg",
    "yuyv": "yuyv422",
    "yu12": "yuv420",
    "rgb3": "rawvideo",
    "bgr3": "rawvideo",
    "grbg": "rawvideo",
    "rggb": "rawvideo",
    "gbrg": "rawvideo",
    "bggr": "rawvideo",
}

# ===== API CONSTANTS =====
MEDIAMTX_API_HOST = "localhost"
MEDIAMTX_API_PORT = 9997
MEDIAMTX_API_BASE = f"http://{MEDIAMTX_API_HOST}:{MEDIAMTX_API_PORT}"

# ===== COLOR CONSTANTS =====
COLOR_HIGH = "\033[92m"     # Bright green
COLOR_MED = "\033[93m"      # Bright yellow
COLOR_LOW = "\033[91m"      # Bright red
COLOR_CYAN = "\033[96m"     # Cyan for headers
COLOR_YELLOW = "\033[93m"   # Yellow for warnings
COLOR_RESET = "\033[0m"     # Reset

# ===== UID PATTERN =====
# Our camera UIDs are 4 lowercase alphanumeric characters
UID_PATTERN = re.compile(r'^[a-z0-9]{4}$')

# ===== DEFAULT RAVEN SETTINGS STRUCTURE =====
DEFAULT_RAVEN_SETTINGS = {
    "version": 2,
    
    "mediamtx": {
        "api_port": 9997,
        "rtsp_port": 8554,
        "webrtc_port": 8889,
        "hls_port": 8888,
        "snapshot_port": 5050
    },
    
    "moonraker": {
        "url": "http://localhost:7125",
        "api_key": None
    },
    
    "cameras": []
}

DEFAULT_CAMERA_CONFIG = {
    "uid": None,  # 4-char alphanumeric, generated
    "hardware_name": None,  # V4L2 device name for matching
    "serial_number": None,  # Serial if available, for disambiguation
    "friendly_name": None,  # User-assigned display name
    
    "mediamtx": {
        "enabled": True,
        
        "ffmpeg": {
            "capture": {
                "format": "mjpeg",
                "resolution": "1280x720",
                "framerate": 30
            },
            "encoding": {
                "encoder": "libx264",  # libx264, vaapi, v4l2m2m
                "bitrate": "4M",
                "preset": "ultrafast",
                "gop": 15,
                "output_fps": 30,
                "rotation": 0
            },
            "audio": {
                "enabled": False,
                "device": None,
                "codec": "aac"
            }
        }
    },
    
    # Moonraker webcam settings - synced from Moonraker API
    "moonraker": {
        "enabled": False,
        "moonraker_uid": None,  # Moonraker's UUID for this webcam
        "flip_horizontal": False,
        "flip_vertical": False,
        "rotation": 0
    },
    
    "v4l2-ctl": {},
    
    # Device capabilities - format → resolution → [framerates]
    "capabilities": {},
    "capabilities_updated": None  # ISO date string
}

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def clear_screen():
    """Clear the terminal screen"""
    os.system('cls' if os.name == 'nt' else 'clear')

def deep_copy(obj):
    """Create a deep copy of a dict/list structure"""
    return copy.deepcopy(obj)

def sanitize_camera_name(name):
    """Convert camera name to a safe identifier"""
    if not name:
        return "camera"
    # Remove special characters, replace spaces with underscores
    sanitized = re.sub(r'[^\w\s-]', '', name)
    sanitized = re.sub(r'[-\s]+', '_', sanitized)
    return sanitized.strip('_')[:32]  # Limit length

def get_system_ip():
    """Get the system's primary IP address"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def generate_camera_uid():
    """Generate a unique 4-character alphanumeric UID for a camera"""
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(4))

def is_valid_uid(uid):
    """Check if a string matches our UID pattern (4 lowercase alphanumeric)"""
    return bool(UID_PATTERN.match(str(uid))) if uid else False

def truncate_friendly_name(name, max_length=20):
    """
    Truncate a friendly name to max_length characters.
    Appends '...' if truncated.
    
    Args:
        name: The friendly name to truncate
        max_length: Maximum length (default 20)
        
    Returns:
        Truncated name with ellipsis if needed
    """
    if not name:
        return "Camera"
    
    if len(name) <= max_length:
        return name
    
    return name[:max_length] + "..."

def get_moonraker_webcam_by_uid(moonraker_uid, url=None):
    """
    Get a specific webcam from Moonraker by its UID.
    
    Args:
        moonraker_uid: Moonraker's UUID for the webcam
        url: Moonraker URL
        
    Returns:
        Webcam dict or None if not found
    """
    webcams = get_moonraker_webcams(url)
    
    for cam in webcams:
        if cam.get('uid') == moonraker_uid:
            return cam
    
    return None

def sync_moonraker_settings_to_config(camera_config, url=None):
    """
    Sync user-adjustable settings from Moonraker back to our camera config.
    This preserves settings the user may have changed in Mainsail/Fluidd.
    
    Synced settings:
        - enabled
        - flip_horizontal
        - flip_vertical
        - rotation
    
    Args:
        camera_config: Our camera configuration dict (modified in place)
        url: Moonraker URL
        
    Returns:
        True if settings were synced, False if webcam not found
    """
    moonraker_uid = camera_config.get("moonraker", {}).get("moonraker_uid")
    
    if not moonraker_uid:
        return False
    
    webcam = get_moonraker_webcam_by_uid(moonraker_uid, url)
    
    if not webcam:
        return False
    
    # Ensure moonraker section exists
    if "moonraker" not in camera_config:
        camera_config["moonraker"] = {}
    
    # Sync the user-adjustable settings
    camera_config["moonraker"]["enabled"] = webcam.get("enabled", True)
    camera_config["moonraker"]["flip_horizontal"] = webcam.get("flip_horizontal", False)
    camera_config["moonraker"]["flip_vertical"] = webcam.get("flip_vertical", False)
    camera_config["moonraker"]["rotation"] = webcam.get("rotation", 0)
    
    return True

# ============================================================================
# SERVICE MANAGEMENT
# ============================================================================

def check_mediamtx_service_running():
    """Check if MediaMTX service is running"""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "mediamtx.service"],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False

def start_mediamtx_service():
    """Start MediaMTX service"""
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "start", "mediamtx.service"],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0, result.stderr
    except Exception as e:
        return False, str(e)

def stop_mediamtx_service():
    """Stop MediaMTX service"""
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "stop", "mediamtx.service"],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0, result.stderr
    except Exception as e:
        return False, str(e)

def restart_services():
    """Restart MediaMTX and Snapfeeder services"""
    results = []
    
    # Restart MediaMTX
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", "mediamtx.service"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            results.append(("mediamtx", True, None))
        else:
            results.append(("mediamtx", False, result.stderr))
    except Exception as e:
        results.append(("mediamtx", False, str(e)))
    
    # Restart Snapfeeder
    try:
        result = subprocess.run(
            ["sudo", "systemctl", "restart", "snapfeeder.service"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            results.append(("snapfeeder", True, None))
        else:
            results.append(("snapfeeder", False, result.stderr))
    except Exception as e:
        results.append(("snapfeeder", False, str(e)))
    
    return results

# ============================================================================
# RAVEN SETTINGS - LOAD/SAVE
# ============================================================================

def create_default_raven_settings():
    """Create a default raven_settings.yml file"""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    
    settings = deep_copy(DEFAULT_RAVEN_SETTINGS)
    
    try:
        with open(RAVEN_SETTINGS_PATH, 'w') as f:
            # Add header comment
            f.write("# Ravens Perch Camera Configuration\n")
            f.write("# This file stores user preferences for camera setup\n")
            f.write("# MediaMTX and Moonraker are configured via API from these settings\n\n")
        
        # Append the YAML content
        with open(RAVEN_SETTINGS_PATH, 'a') as f:
            yaml.dump(settings, f)
        
        return True, None
    except Exception as e:
        return False, str(e)

def load_raven_settings():
    """
    Load settings from raven_settings.yml.
    Returns settings dict or None if file doesn't exist.
    
    Note: If file doesn't exist, caller should prompt user to create it.
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    
    try:
        if not RAVEN_SETTINGS_PATH.exists():
            return None
        
        with open(RAVEN_SETTINGS_PATH, 'r') as f:
            settings = yaml.load(f)
        
        if settings is None:
            settings = {}
        
        # Ensure all required top-level keys exist
        for key in DEFAULT_RAVEN_SETTINGS:
            if key not in settings:
                settings[key] = deep_copy(DEFAULT_RAVEN_SETTINGS[key])
        
        return settings
        
    except Exception as e:
        print(f"Error loading raven settings: {e}")
        return None

def save_raven_settings(settings):
    """
    Save settings to raven_settings.yml.
    
    Args:
        settings: Complete settings dict
        
    Returns:
        bool: True on success
    """
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    
    try:
        with open(RAVEN_SETTINGS_PATH, 'w') as f:
            yaml.dump(settings, f)
        return True
    except Exception as e:
        print(f"Error saving raven settings: {e}")
        return False

def ensure_raven_settings_exist():
    """
    Ensure raven_settings.yml exists. If not, offer to create it.
    
    Returns:
        bool: True if settings file exists or was created
    """
    if RAVEN_SETTINGS_PATH.exists():
        return True
    
    print(f"\n{COLOR_YELLOW}⚠️  No raven_settings.yml found!{COLOR_RESET}")
    print(f"   Expected location: {RAVEN_SETTINGS_PATH}")
    print(f"\nThis file is required to store camera configurations.")
    
    choice = input(f"\n{COLOR_CYAN}Create default settings file? (Y/n):{COLOR_RESET} ").strip().lower()
    
    if choice in ('', 'y', 'yes'):
        success, error = create_default_raven_settings()
        if success:
            print(f"✅ Created {RAVEN_SETTINGS_PATH}")
            return True
        else:
            print(f"❌ Failed to create settings file: {error}")
            return False
    
    return False

# ============================================================================
# CAMERA CONFIG MANAGEMENT
# ============================================================================

def find_camera_by_uid(settings, uid):
    """
    Find a camera configuration by its UID.
    
    Returns:
        Tuple of (camera_config, index) or (None, -1) if not found
    """
    cameras = settings.get("cameras", [])
    
    for i, cam in enumerate(cameras):
        if cam.get("uid") == uid:
            return cam, i
    
    return None, -1

def find_camera_by_hardware(settings, hardware_name, serial_number=None):
    """
    Find a camera configuration by hardware name (and optionally serial).
    
    Returns:
        Tuple of (camera_config, index) or (None, -1) if not found
    """
    cameras = settings.get("cameras", [])
    
    for i, cam in enumerate(cameras):
        if cam.get("hardware_name") == hardware_name:
            if serial_number and cam.get("serial_number"):
                if cam["serial_number"] == serial_number:
                    return cam, i
            else:
                return cam, i
    
    return None, -1

def find_cameras_by_hardware(settings, hardware_name, serial_number=None):
    """
    Find ALL camera configurations matching hardware name (and optionally serial).
    Used when multiple cameras might match.
    
    Returns:
        List of (camera_config, index) tuples
    """
    cameras = settings.get("cameras", [])
    matches = []
    
    for i, cam in enumerate(cameras):
        if cam.get("hardware_name") == hardware_name:
            if serial_number:
                if cam.get("serial_number") == serial_number:
                    matches.append((cam, i))
            else:
                # No serial filter, match all with this hardware name
                matches.append((cam, i))
    
    return matches

def detect_duplicate_cameras(devices):
    """
    Detect cameras that share the same hardware_name AND serial_number.
    These are non-compliant with USB standards and cannot be reliably distinguished.
    
    Args:
        devices: List of device dicts from get_all_video_devices()
        
    Returns:
        Dict mapping (hardware_name, serial_number) to list of device paths
        Only includes entries with more than one device (actual duplicates)
    """
    # Group devices by (hardware_name, serial_number)
    groups = {}
    for dev in devices:
        key = (dev['hardware_name'], dev['serial_number'])
        if key not in groups:
            groups[key] = []
        groups[key].append(dev['path'])
    
    # Return only groups with duplicates
    return {k: v for k, v in groups.items() if len(v) > 1}

def check_for_duplicate_cameras(devices):
    """
    Check for non-compliant duplicate cameras and return warning message if found.
    
    Args:
        devices: List of device dicts from get_all_video_devices()
        
    Returns:
        Tuple of (has_duplicates, warning_message, duplicate_keys)
        duplicate_keys is a set of (hardware_name, serial_number) tuples to skip
    """
    duplicates = detect_duplicate_cameras(devices)
    
    if not duplicates:
        return False, None, set()
    
    # Build warning message
    lines = [
        f"{COLOR_YELLOW}{'='*70}",
        "⚠️  NON-COMPLIANT USB CAMERAS DETECTED",
        f"{'='*70}{COLOR_RESET}",
        "",
        "The following cameras share identical hardware names AND serial numbers.",
        "This violates USB standards and makes it impossible to reliably",
        "distinguish between them. These cameras will be SKIPPED.",
        ""
    ]
    
    for (hw_name, serial), paths in duplicates.items():
        serial_str = serial if serial else "(no serial)"
        lines.append(f"   {COLOR_LOW}{hw_name}{COLOR_RESET} [{serial_str}]")
        for path in paths:
            lines.append(f"      - {path}")
        lines.append("")
    
    lines.append("To use these cameras, please:")
    lines.append("   1. Use cameras from different manufacturers, or")
    lines.append("   2. Use cameras with unique serial numbers, or")
    lines.append("   3. Connect only ONE of each duplicate camera type")
    
    return True, "\n".join(lines), set(duplicates.keys())

def create_camera_config(hardware_name, friendly_name=None, serial_number=None):
    """
    Create a new camera configuration with defaults.
    
    Args:
        hardware_name: V4L2 device name
        friendly_name: Optional friendly name (defaults to sanitized device name)
        serial_number: Optional serial number
        
    Returns:
        New camera config dict
    """
    config = deep_copy(DEFAULT_CAMERA_CONFIG)
    config["uid"] = generate_camera_uid()
    config["hardware_name"] = hardware_name
    config["serial_number"] = serial_number
    config["friendly_name"] = friendly_name or sanitize_camera_name(hardware_name)
    
    return config

def save_camera_config(settings, camera_config):
    """
    Save or update a camera configuration in settings.
    Matches by UID.
    
    Args:
        settings: Raven settings dict (will be modified)
        camera_config: Camera config dict to save
        
    Returns:
        Updated settings dict
    """
    if "cameras" not in settings:
        settings["cameras"] = []
    
    uid = camera_config.get("uid")
    
    # Try to find existing by UID
    for i, cam in enumerate(settings["cameras"]):
        if uid and cam.get("uid") == uid:
            settings["cameras"][i] = camera_config
            return settings
    
    # Not found, append new
    if not uid:
        camera_config["uid"] = generate_camera_uid()
    settings["cameras"].append(camera_config)
    
    return settings

def delete_camera_config(settings, uid):
    """
    Delete a camera configuration by UID.
    
    Returns:
        Updated settings dict
    """
    if "cameras" not in settings:
        return settings
    
    settings["cameras"] = [c for c in settings["cameras"] if c.get("uid") != uid]
    
    return settings

def get_all_cameras(settings):
    """Get list of all camera configs"""
    return settings.get("cameras", [])

# ============================================================================
# VIDEO DEVICE DETECTION
# ============================================================================

def get_device_serial(device_path):
    """
    Get serial number for a video device using udevadm.
    
    Only returns actual hardware serial numbers (ID_SERIAL_SHORT).
    Returns None if no real serial is available.
    """
    try:
        result = subprocess.run(
            ["udevadm", "info", "--query=property", "--name=" + device_path],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        for line in result.stdout.splitlines():
            # Only use ID_SERIAL_SHORT - this is the actual hardware serial
            if line.startswith("ID_SERIAL_SHORT="):
                serial = line.split("=", 1)[1].strip()
                # Validate it looks like a real serial (not just model info)
                # Real serials are typically alphanumeric, 6+ chars
                if serial and len(serial) >= 6 and not serial.startswith("HD-"):
                    return serial
        
        return None
    except Exception:
        return None

def get_device_capabilities(device_path):
    """
    Get V4L2 capabilities for a device using v4l2-ctl -D.
    
    Returns:
        Dict with capabilities or None
    """
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device=" + device_path, "-D"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        caps = {
            "video_capture": False,
            "memory_to_memory": False,
            "driver": None,
            "card": None
        }
        
        in_device_caps = False
        
        for line in result.stdout.splitlines():
            line_stripped = line.strip()
            
            # Get driver and card info
            if line_stripped.startswith("Driver name"):
                caps["driver"] = line_stripped.split(":", 1)[1].strip()
            elif line_stripped.startswith("Card type"):
                caps["card"] = line_stripped.split(":", 1)[1].strip()
            
            # Look for Device Caps section (more specific than general Capabilities)
            elif "Device Caps" in line:
                in_device_caps = True
            elif in_device_caps:
                # Check for Video Capture (real cameras)
                if "Video Capture" in line_stripped and "Multiplanar" not in line_stripped:
                    caps["video_capture"] = True
                # Check for Memory-to-Memory (hardware codecs, not cameras)
                elif "Memory-to-Memory" in line_stripped:
                    caps["memory_to_memory"] = True
                # End of caps section
                elif line_stripped and not line_stripped.startswith("Video") and not line_stripped.startswith("Streaming") and not line_stripped.startswith("Read") and not line_stripped.startswith("Extended") and not line_stripped.startswith("Device"):
                    in_device_caps = False
        
        return caps
    except Exception:
        return None

def is_capture_device(device_path):
    """
    Check if a video device is a real capture device (camera).
    Excludes hardware codecs, ISPs, and other internal video processing devices.
    """
    caps = get_device_capabilities(device_path)
    if caps is None:
        return False

    # Must have Video Capture capability and NOT be a Memory-to-Memory device
    if not caps.get("video_capture", False) or caps.get("memory_to_memory", False):
        return False

    # Filter out hardware codec/ISP devices by driver or card name
    # These are internal video processing devices, not cameras
    codec_patterns = [
        'bcm2835-codec',   # Raspberry Pi codec
        'bcm2835-isp',     # Raspberry Pi ISP
        'rpi-hevc',        # Raspberry Pi HEVC decoder
        'rkvdec', 'rkvenc', 'rkisp',  # Rockchip codecs
        'rga',             # Rockchip RGA
        'hantro',          # Hantro codec
        'cedrus',          # Allwinner Cedrus
        'vchiq',           # Raspberry Pi VCHIQ
        'm2m', 'mem2mem',  # Memory-to-memory devices
        'decoder', 'encoder',
        '-dec', '-enc',    # Common codec suffixes
    ]

    driver = (caps.get("driver") or "").lower()
    card = (caps.get("card") or "").lower()

    for pattern in codec_patterns:
        if pattern in driver or pattern in card:
            return False

    return True

def get_primary_video_devices():
    """
    Get list of primary video devices using v4l2-ctl --list-devices.
    This uses the device groupings to only return the FIRST device
    for each physical camera, avoiding secondary nodes.
    
    Returns:
        Dict: {'/dev/video0': 'HD Pro Webcam C920', ...}
              Only includes first device per camera group
    """
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--list-devices"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        primary_devices = {}
        current_name = None
        found_first = False
        
        for line in result.stdout.splitlines():
            line_stripped = line.strip()
            
            # Camera name line (not indented, has colon)
            if line and not line.startswith('\t') and not line.startswith(' ') and ':' in line:
                current_name = line.split(':')[0].strip()
                found_first = False  # Reset for new camera group
            # Device path line (indented)
            elif line_stripped.startswith('/dev/video') and current_name:
                # Only take the FIRST /dev/video* for each camera
                if not found_first:
                    primary_devices[line_stripped] = current_name
                    found_first = True
        
        return primary_devices
    except Exception:
        return {}

def get_primary_capture_devices():
    """
    Get list of primary capture devices (real cameras, not hardware codecs).
    
    Filters out:
    - Hardware codecs (rkvdec, hantro-vpu, rockchip-rga) which are Memory-to-Memory devices
    - Secondary device nodes (metadata, etc.)
    
    Returns:
        List of device paths like ['/dev/video0', '/dev/video2']
    """
    devices = []
    video_dir = Path("/dev")
    
    # Get all video devices sorted numerically
    video_devices = sorted(
        video_dir.glob("video*"), 
        key=lambda x: int(x.name[5:]) if x.name[5:].isdigit() else 999
    )
    
    # Track camera names we've seen to avoid secondary nodes
    seen_cards = set()
    
    for dev in video_devices:
        dev_path = str(dev)
        
        # Get capabilities
        caps = get_device_capabilities(dev_path)
        if caps is None:
            continue
        
        # Skip if not a real capture device
        if not caps.get("video_capture", False):
            continue
        
        # Skip Memory-to-Memory devices (hardware codecs)
        if caps.get("memory_to_memory", False):
            continue
        
        # Skip if we've already seen this card (secondary node)
        card = caps.get("card")
        if card and card in seen_cards:
            continue
        
        if card:
            seen_cards.add(card)
        
        devices.append(dev_path)
    
    return devices

def get_device_names():
    """
    Get friendly names for video devices using v4l2-ctl --list-devices
    
    Returns:
        Dict: {'/dev/video0': 'HD Pro Webcam C920', ...}
    """
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--list-devices"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        device_names = {}
        current_name = None
        
        for line in result.stdout.splitlines():
            line_stripped = line.strip()
            
            if line and not line.startswith('\t') and not line.startswith(' ') and ':' in line:
                current_name = line.split(':')[0].strip()
            elif line_stripped.startswith('/dev/video'):
                if current_name:
                    device_names[line_stripped] = current_name
        
        return device_names
    except Exception:
        return {}

def get_all_video_devices():
    """
    Get comprehensive info about all video capture devices.
    
    Returns:
        List of dicts with: {
            'path': '/dev/video0',
            'hardware_name': 'HD Pro Webcam C920',
            'serial_number': 'ABC123' or None
        }
    """
    devices = []
    device_names = get_device_names()
    
    for dev_path in get_primary_capture_devices():
        name = device_names.get(dev_path)
        if name:
            devices.append({
                'path': dev_path,
                'hardware_name': name,
                'serial_number': get_device_serial(dev_path)
            })
    
    return devices

def list_video_devices():
    """List all /dev/video* capture devices (filtered)"""
    return get_primary_capture_devices()

def resolve_device_path(settings, camera_config):
    """
    Find the current device path for a camera config.
    Matches by hardware_name and serial_number.
    
    Args:
        settings: Raven settings (unused, for future)
        camera_config: Camera configuration from settings
        
    Returns:
        Tuple of (device_path, warning_message) or (None, error_message)
    """
    hardware_name = camera_config.get("hardware_name")
    serial_number = camera_config.get("serial_number")
    
    if not hardware_name:
        return None, "Camera has no hardware_name"
    
    # Get all current devices
    devices = get_all_video_devices()
    
    # Find matches
    matches = []
    for dev in devices:
        if dev['hardware_name'] == hardware_name:
            if serial_number:
                if dev['serial_number'] == serial_number:
                    matches.append(dev)
            else:
                matches.append(dev)
    
    if len(matches) == 0:
        return None, f"Device not found: {hardware_name}"
    
    if len(matches) == 1:
        return matches[0]['path'], None
    
    # Multiple matches - check if serial could disambiguate
    if not serial_number:
        # See if all matches have different serials
        serials = [m['serial_number'] for m in matches if m['serial_number']]
        if len(serials) == len(matches) and len(set(serials)) == len(serials):
            # All have unique serials, but we don't have one stored
            warning = f"Multiple '{hardware_name}' devices found. Consider re-configuring to capture serial numbers."
            return matches[0]['path'], warning
    
    # Multiple matches with same or no serial - warn about potential switching
    warning = (f"⚠️  Multiple identical '{hardware_name}' cameras detected. "
               f"Feeds may switch if cameras are reconnected in different order.")
    return matches[0]['path'], warning

def prompt_select_device_for_camera(camera_config, matches):
    """
    Prompt user to select which physical device matches a camera config.
    
    Args:
        camera_config: Camera config from settings
        matches: List of device dicts that match
        
    Returns:
        Selected device dict or None if cancelled
    """
    friendly_name = camera_config.get("friendly_name", camera_config.get("hardware_name"))
    
    print(f"\n{COLOR_YELLOW}Multiple devices match '{friendly_name}':{COLOR_RESET}")
    
    for i, dev in enumerate(matches, 1):
        serial_str = f" (Serial: {dev['serial_number']})" if dev['serial_number'] else " (No serial)"
        print(f"  [{i}] {dev['path']} - {dev['hardware_name']}{serial_str}")
    
    print(f"  [c] Cancel")
    
    while True:
        choice = input(f"\n{COLOR_CYAN}Select device:{COLOR_RESET} ").strip().lower()
        
        if choice == 'c':
            return None
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(matches):
                return matches[idx]
        except ValueError:
            pass
        
        print("Invalid selection")

# ============================================================================
# FORMAT AND CAPABILITY DETECTION
# ============================================================================

def run_v4l2ctl(device, args):
    """Run v4l2-ctl with given arguments and return output"""
    try:
        cmd = ["v4l2-ctl", f"--device={device}"] + args
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.stdout
    except Exception as e:
        return ""

def parse_formats(output):
    """
    Parse v4l2-ctl --list-formats-ext output.
    
    Returns:
        Dict: {format: {resolution: [fps_list]}}
    """
    formats = defaultdict(lambda: defaultdict(list))
    current_format = None
    current_res = None
    
    for line in output.splitlines():
        line = line.strip()
        
        # Match format line like "[0]: 'MJPG' (Motion-JPEG, compressed)"
        fmt_match = re.match(r"\[\d+\]:\s*'(\w+)'", line)
        if fmt_match:
            raw_fmt = fmt_match.group(1).lower()
            current_format = FORMAT_ALIASES.get(raw_fmt, raw_fmt)
            continue
        
        # Match resolution like "Size: Discrete 1920x1080"
        res_match = re.match(r"Size:\s*Discrete\s*(\d+x\d+)", line)
        if res_match and current_format:
            current_res = res_match.group(1)
            continue
        
        # Match FPS like "Interval: Discrete 0.033s (30.000 fps)"
        fps_match = re.search(r"\((\d+(?:\.\d+)?)\s*fps\)", line)
        if fps_match and current_format and current_res:
            fps = int(float(fps_match.group(1)))
            if fps not in formats[current_format][current_res]:
                formats[current_format][current_res].append(fps)
    
    # Convert to regular dicts and sort FPS lists
    result = {}
    for fmt, resolutions in formats.items():
        result[fmt] = {}
        for res, fps_list in resolutions.items():
            result[fmt][res] = sorted(fps_list, reverse=True)
    
    return result

def get_device_formats(device_path):
    """Get available formats for a device"""
    output = run_v4l2ctl(device_path, ["--list-formats-ext"])
    return parse_formats(output)

def update_camera_capabilities(camera_config, device_path=None):
    """
    Update capabilities for a camera by querying the device.
    
    Args:
        camera_config: Camera config dict (modified in place)
        device_path: Optional device path. If None, will resolve from config.
        
    Returns:
        Tuple of (success, error_message)
    """
    from datetime import datetime
    
    # Resolve device path if not provided
    if device_path is None:
        device_path, warning = resolve_device_path(None, camera_config)
        if not device_path:
            return False, warning or "Device not found"
    
    # Get formats from device
    try:
        capabilities = get_device_formats(device_path)
        if not capabilities:
            return False, "No formats reported by device"
        
        camera_config['capabilities'] = capabilities
        camera_config['capabilities_updated'] = datetime.now().strftime("%Y-%m-%d")
        return True, None
        
    except Exception as e:
        return False, f"Error querying device: {e}"

def update_all_camera_capabilities(settings):
    """
    Update capabilities for all cameras in settings.
    
    Args:
        settings: Full settings dict
        
    Returns:
        Tuple of (updated_count, errors_list)
    """
    updated = 0
    errors = []
    
    for cam in settings.get('cameras', []):
        uid = cam.get('uid', '?')
        friendly = cam.get('friendly_name', cam.get('hardware_name', uid))
        
        success, error = update_camera_capabilities(cam)
        if success:
            updated += 1
        else:
            errors.append(f"{friendly}: {error}")
    
    return updated, errors

def validate_camera_settings(camera_config, format=None, resolution=None, fps=None):
    """
    Validate capture settings against camera capabilities.
    
    Args:
        camera_config: Camera config with capabilities
        format: Format to validate (or None to use current)
        resolution: Resolution to validate (or None to use current)
        fps: FPS to validate (or None to use current)
        
    Returns:
        Tuple of (valid, error_message)
    """
    caps = camera_config.get('capabilities', {})
    if not caps:
        return True, None  # Can't validate without capabilities
    
    # Get current settings as defaults
    capture = camera_config.get('mediamtx', {}).get('ffmpeg', {}).get('capture', {})
    fmt = format or capture.get('format', 'mjpeg')
    res = resolution or capture.get('resolution', '1280x720')
    framerate = fps or capture.get('framerate', 30)
    
    # Validate format
    if fmt not in caps:
        available = list(caps.keys())
        return False, f"Format '{fmt}' not supported. Available: {available}"
    
    # Validate resolution
    if res not in caps[fmt]:
        available = list(caps[fmt].keys())
        return False, f"Resolution '{res}' not supported for {fmt}. Available: {available}"
    
    # Validate FPS
    available_fps = caps[fmt][res]
    if framerate not in available_fps:
        return False, f"FPS {framerate} not supported for {fmt}@{res}. Available: {available_fps}"
    
    return True, None

def get_best_matching_fps(camera_config, format, resolution, target_fps):
    """
    Find the best available FPS that doesn't exceed target.
    
    Args:
        camera_config: Camera config with capabilities
        format: Video format
        resolution: Resolution string
        target_fps: Desired FPS
        
    Returns:
        Best matching FPS or None if no valid options
    """
    caps = camera_config.get('capabilities', {})
    if not caps or format not in caps or resolution not in caps[format]:
        return None
    
    available = caps[format][resolution]
    # Find highest FPS that doesn't exceed target
    valid = [f for f in available if f <= target_fps]
    return max(valid) if valid else min(available)  # Fallback to lowest if all exceed

# ============================================================================
# HARDWARE ACCELERATION DETECTION
# ============================================================================

def has_vaapi_encoder():
    """Check if VAAPI H.264 encoding is available"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return "h264_vaapi" in result.stdout
    except Exception:
        return False

def is_raspberry_pi():
    """Check if running on Raspberry Pi hardware"""
    try:
        with open("/proc/cpuinfo", "r") as f:
            cpuinfo = f.read().lower()
            if "raspberry pi" in cpuinfo or "bcm2" in cpuinfo:
                return True
        
        # Also check device tree
        try:
            with open("/proc/device-tree/model", "r") as f:
                model = f.read().lower()
                if "raspberry pi" in model:
                    return True
        except:
            pass
        
        return False
    except Exception:
        return False

def has_v4l2m2m_encoder():
    """
    Check if V4L2 M2M H.264 encoding is available.
    Only returns True on actual Raspberry Pi hardware, since v4l2m2m
    doesn't work reliably on other platforms even if FFmpeg has it.
    """
    # Only use v4l2m2m on Raspberry Pi
    if not is_raspberry_pi():
        return False
    
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return "h264_v4l2m2m" in result.stdout
    except Exception:
        return False

def detect_hardware_acceleration():
    """
    Detect available hardware acceleration.
    
    Note: Rockchip MPP (rkmpp) is not used due to compatibility issues.
    V4L2 M2M is only enabled on Raspberry Pi hardware.
    
    Returns:
        Tuple of (use_vaapi, use_v4l2m2m)
    """
    use_vaapi = has_vaapi_encoder()
    use_v4l2m2m = has_v4l2m2m_encoder()
    
    return use_vaapi, use_v4l2m2m

# ============================================================================
# FFMPEG COMMAND BUILDING
# ============================================================================

def build_ffmpeg_cmd(device, fmt, res, fps, cam_id, use_vaapi, use_v4l2m2m, settings=None):
    """
    Build FFmpeg command with hardware acceleration.
    
    Args:
        device: Device path like /dev/video0
        fmt: Format like mjpeg
        res: Resolution like 1280x720
        fps: Frame rate
        cam_id: Camera ID (UID) for RTSP path
        use_vaapi, use_v4l2m2m: Hardware acceleration flags
        settings: Optional dict with encoding settings
        
    Returns:
        FFmpeg command string
    """
    if settings is None:
        settings = {}
    
    rtsp_url = f"rtsp://localhost:8554/{cam_id}"
    
    # Get settings with defaults
    bitrate = settings.get('bitrate', '4M')
    preset = settings.get('encoder_preset', 'ultrafast')
    rotation = settings.get('rotation', 0)
    output_fps = settings.get('output_fps')
    
    # Hardware acceleration setup
    hwaccel_args = []
    if use_vaapi:
        hwaccel_args = [
            "-hwaccel", "vaapi",
            "-hwaccel_device", "/dev/dri/renderD128"
        ]
    
    # Input arguments
    input_args = [
        "-f", "v4l2",
        "-input_format", fmt,
        "-video_size", res,
        "-framerate", str(fps),
        "-i", device
    ]
    
    # Audio arguments
    audio_args = []
    if settings.get('enable_audio') and settings.get('audio_device'):
        audio_args = [
            "-f", "alsa",
            "-i", settings['audio_device']
        ]
    
    # Calculate GOP
    effective_fps = output_fps if output_fps and output_fps < fps else fps
    gop = max(1, effective_fps // 2)
    
    # Video filtering
    vf_filters = []
    
    if rotation == 90:
        vf_filters.append("transpose=1")
    elif rotation == 180:
        vf_filters.append("transpose=1,transpose=1")
    elif rotation == 270:
        vf_filters.append("transpose=2")
    
    # Encoder selection
    encoder_args = []
    
    if use_vaapi:
        vf_filters.append("format=nv12")
        vf_filters.append("hwupload")
        encoder_args.extend([
            "-vf", ",".join(vf_filters),
            "-c:v", "h264_vaapi",
            "-b:v", bitrate
        ])
    elif use_v4l2m2m:
        if vf_filters:
            encoder_args.extend(["-vf", ",".join(vf_filters)])
        encoder_args.extend([
            "-pix_fmt", "yuv420p",
            "-c:v", "h264_v4l2m2m",
            "-b:v", bitrate
        ])
    else:
        # Software encoding
        if vf_filters:
            encoder_args.extend(["-vf", ",".join(vf_filters)])
        encoder_args.extend([
            "-pix_fmt", "yuv420p",
            "-c:v", "libx264",
            "-preset", preset,
            "-profile:v", "baseline",
            "-b:v", bitrate,
            "-tune", "zerolatency"
        ])
    
    # Audio encoder
    if settings.get('enable_audio'):
        codec = settings.get('audio_codec', 'aac')
        if codec == 'opus':
            encoder_args += ["-c:a", "libopus", "-b:a", "128k"]
        else:
            encoder_args += ["-c:a", "aac", "-b:a", "128k"]
    
    # Output frame rate
    output_rate_args = []
    if output_fps and output_fps < fps:
        output_rate_args = ["-r", str(output_fps)]
    
    # Output settings
    output_args = ["-g", str(gop), "-bf", "0"] + output_rate_args + ["-f", "rtsp", rtsp_url]
    
    cmd = ["ffmpeg", "-y"] + hwaccel_args + input_args + audio_args + encoder_args + output_args
    return " ".join(cmd)

def build_ffmpeg_cmd_from_config(camera_config, device_path, use_vaapi, use_v4l2m2m):
    """
    Build FFmpeg command from a camera config and resolved device path.
    
    Args:
        camera_config: Camera config from raven_settings
        device_path: Resolved device path like /dev/video0
        use_vaapi, use_v4l2m2m: Hardware acceleration flags
        
    Returns:
        FFmpeg command string
    """
    uid = camera_config.get("uid")
    
    # Extract capture settings
    capture = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("capture", {})
    fmt = capture.get("format", "mjpeg")
    res = capture.get("resolution", "1280x720")
    fps = capture.get("framerate", 30)
    
    # Extract encoding settings
    encoding = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("encoding", {})
    audio = camera_config.get("mediamtx", {}).get("ffmpeg", {}).get("audio", {})
    
    settings = {
        'bitrate': encoding.get("bitrate", "4M"),
        'encoder_preset': encoding.get("preset", "ultrafast"),
        'rotation': encoding.get("rotation", 0),
        'output_fps': encoding.get("output_fps", fps),
        'enable_audio': audio.get("enabled", False),
        'audio_device': audio.get("device"),
        'audio_codec': audio.get("codec", "aac"),
    }
    
    return build_ffmpeg_cmd(device_path, fmt, res, fps, uid, use_vaapi, use_v4l2m2m, settings)

# ============================================================================
# V4L2 CONTROLS
# ============================================================================

def get_v4l2_controls(device_path):
    """
    Get available V4L2 controls for a device.
    
    Parses both User Controls and Camera Controls sections.
    For menu-type controls, also captures the available options.
    
    Returns:
        Dict of {control_name: control_info}
        control_info contains: type, min, max, default, value, step, flags
        For menu types, also contains 'menu_options': {value: label}
    """
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device=" + device_path, "-L"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        controls = {}
        current_control = None
        current_section = None
        
        for line in result.stdout.splitlines():
            # Check for section headers
            stripped = line.strip()
            if stripped in ('User Controls', 'Camera Controls'):
                current_section = stripped
                continue
            
            # Parse control lines like:
            # "brightness 0x00980900 (int)    : min=-64 max=64 step=1 default=0 value=0"
            # "power_line_frequency 0x00980918 (menu)   : min=0 max=2 default=2 value=2 (60 Hz)"
            match = re.match(r'\s*(\w+)\s+0x[0-9a-fA-F]+\s+\((\w+)\)\s*:\s*(.+)', line)
            if match:
                name = match.group(1)
                ctrl_type = match.group(2)
                params_str = match.group(3)
                
                ctrl = {
                    'type': ctrl_type,
                    'section': current_section or 'Unknown'
                }
                
                # Parse parameters (min=X max=Y etc)
                # Handle the case where value might have a label like "value=2 (60 Hz)"
                # First extract flags if present
                if 'flags=' in params_str:
                    flags_match = re.search(r'flags=(\w+)', params_str)
                    if flags_match:
                        ctrl['flags'] = flags_match.group(1)
                
                # Parse key=value pairs
                for param_match in re.finditer(r'(\w+)=(-?\d+)', params_str):
                    key = param_match.group(1)
                    val = param_match.group(2)
                    ctrl[key] = val
                
                # Initialize menu_options for menu type
                if ctrl_type == 'menu':
                    ctrl['menu_options'] = {}
                
                controls[name] = ctrl
                current_control = name
                continue
            
            # Parse menu option lines like "0: Disabled" or "1: 50 Hz"
            menu_match = re.match(r'\s+(\d+):\s*(.+)', line)
            if menu_match and current_control:
                ctrl = controls.get(current_control)
                if ctrl and ctrl.get('type') == 'menu':
                    option_val = menu_match.group(1)
                    option_label = menu_match.group(2).strip()
                    ctrl['menu_options'][option_val] = option_label
        
        return controls
    except Exception as e:
        return {}

def apply_v4l2_controls(device_path, controls_dict):
    """
    Apply V4L2 controls to a device.
    
    Args:
        device_path: Device path
        controls_dict: Dict of {control_name: value}
        
    Returns:
        Command string that was executed (for inclusion in runOnInit)
    """
    if not controls_dict:
        return None
    
    # Build v4l2-ctl command
    ctrl_args = []
    for name, value in controls_dict.items():
        ctrl_args.append(f"{name}={value}")
    
    if not ctrl_args:
        return None
    
    cmd = f"v4l2-ctl --device={device_path} --set-ctrl=" + ",".join(ctrl_args)
    
    try:
        subprocess.run(cmd.split(), capture_output=True, timeout=5)
    except Exception:
        pass
    
    return cmd

def apply_all_v4l2_controls(settings, verbose=True):
    """
    Apply saved V4L2 controls for all cameras.
    
    This should be called at startup to restore image adjustments.
    V4L2 controls are applied directly to the camera hardware and are
    independent from the streaming/encoding settings.
    
    Args:
        settings: Raven settings dict
        verbose: Print status messages
        
    Returns:
        Tuple of (success_count, error_count)
    """
    cameras = get_all_cameras(settings)
    success_count = 0
    error_count = 0
    
    for cam in cameras:
        v4l2_controls = cam.get("v4l2-ctl", {})
        if not v4l2_controls:
            continue
        
        friendly = cam.get("friendly_name", "Unknown")
        
        # Resolve device path
        device_path, warning = resolve_device_path(settings, cam)
        
        if not device_path:
            if verbose:
                print(f"   ⚠️  {friendly}: Camera not found, skipping V4L2 controls")
            error_count += 1
            continue
        
        # Apply controls
        cmd = apply_v4l2_controls(device_path, v4l2_controls)
        
        if cmd:
            if verbose:
                ctrl_count = len(v4l2_controls)
                print(f"   ✅ {friendly}: Applied {ctrl_count} V4L2 control(s)")
            success_count += 1
        else:
            error_count += 1
    
    return success_count, error_count

# ============================================================================
# MEDIAMTX API
# ============================================================================

def mediamtx_api_request(endpoint, method="GET", data=None, timeout=5):
    """
    Make a request to the MediaMTX API.
    
    Args:
        endpoint: API endpoint (e.g., "/v3/paths/list")
        method: HTTP method
        data: Optional data to send (will be JSON encoded)
        timeout: Request timeout in seconds
        
    Returns:
        Tuple of (success, response_data, error_message)
    """
    url = f"{MEDIAMTX_API_BASE}{endpoint}"
    
    try:
        if data is not None:
            json_data = json.dumps(data).encode('utf-8')
            req = urllib.request.Request(url, data=json_data, method=method)
            req.add_header('Content-Type', 'application/json')
        else:
            req = urllib.request.Request(url, method=method)
        
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status in (200, 201):
                try:
                    response_data = json.loads(response.read().decode('utf-8'))
                    return True, response_data, None
                except json.JSONDecodeError:
                    return True, None, None
            else:
                return False, None, f"HTTP {response.status}"
    
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode('utf-8')
        except:
            pass
        return False, None, f"HTTP {e.code}: {error_body}"
    
    except urllib.error.URLError as e:
        return False, None, f"Connection error: {e.reason}"
    
    except Exception as e:
        return False, None, str(e)

def mediamtx_api_available():
    """Check if MediaMTX API is available"""
    success, _, _ = mediamtx_api_request("/v3/paths/list", timeout=2)
    return success

def list_mediamtx_paths():
    """
    Get all CONFIGURED paths from MediaMTX via API.
    These are the paths that have been configured (either statically or dynamically).
    
    Returns:
        Dict of {path_name: path_config} or empty dict on error
    """
    success, data, error = mediamtx_api_request("/v3/config/paths/list")
    
    if not success:
        return {}
    
    items = data.get('items') if data else None
    
    if items is None:
        return {}
    
    paths = {}
    for item in items:
        name = item.get('name')
        if name:
            paths[name] = item
    
    return paths

def list_active_streams():
    """
    Get all ACTIVE streams from MediaMTX via API.
    These are streams that are currently running/connected.
    
    Returns:
        Dict of {path_name: stream_info} or empty dict on error
    """
    success, data, error = mediamtx_api_request("/v3/paths/list")
    
    if not success:
        return {}
    
    items = data.get('items') if data else None
    
    if items is None:
        return {}
    
    paths = {}
    for item in items:
        name = item.get('name')
        if name:
            paths[name] = item
    
    return paths

def add_mediamtx_path(path_name, config):
    """
    Add a new path to MediaMTX via API.
    
    Args:
        path_name: Path name (should be camera UID)
        config: Path configuration dict
        
    Returns:
        Tuple of (success, error_message)
    """
    success, _, error = mediamtx_api_request(
        f"/v3/config/paths/add/{path_name}",
        method="POST",
        data=config
    )
    return success, error

def update_mediamtx_path(path_name, config):
    """
    Update an existing path in MediaMTX via API.
    
    Args:
        path_name: Path name (should be camera UID)
        config: Path configuration dict
        
    Returns:
        Tuple of (success, error_message)
    """
    success, _, error = mediamtx_api_request(
        f"/v3/config/paths/patch/{path_name}",
        method="PATCH",
        data=config
    )
    return success, error

def delete_mediamtx_path(path_name):
    """
    Delete a path from MediaMTX via API.
    
    Args:
        path_name: Path name to delete
        
    Returns:
        Tuple of (success, error_message)
    """
    success, _, error = mediamtx_api_request(
        f"/v3/config/paths/delete/{path_name}",
        method="DELETE"
    )
    return success, error

def add_or_update_mediamtx_path(path_name, config):
    """
    Add or update a path in MediaMTX.
    
    Returns:
        Tuple of (success, action, error_message)
        action is 'added' or 'updated'
    """
    # Try to add first
    success, error = add_mediamtx_path(path_name, config)
    if success:
        return True, 'added', None
    
    # If add failed, try update
    if "already exists" in str(error).lower():
        success, error = update_mediamtx_path(path_name, config)
        if success:
            return True, 'updated', None
    
    return False, None, error

def cleanup_our_mediamtx_paths():
    """
    Remove all MediaMTX paths that match our UID pattern.
    
    Returns:
        Tuple of (removed_count, errors)
    """
    paths = list_mediamtx_paths()
    removed = 0
    errors = []
    
    for path_name in paths.keys():
        if is_valid_uid(path_name):
            success, error = delete_mediamtx_path(path_name)
            if success:
                removed += 1
            else:
                errors.append(f"{path_name}: {error}")
    
    return removed, errors

# ============================================================================
# MOONRAKER API
# ============================================================================

def detect_moonraker_url():
    """
    Auto-detect Moonraker URL.
    
    Returns:
        URL string or None if not found
    """
    common_urls = [
        "http://localhost:7125",
        "http://127.0.0.1:7125",
    ]
    
    for url in common_urls:
        try:
            req = urllib.request.Request(f"{url}/server/info")
            with urllib.request.urlopen(req, timeout=2) as response:
                data = json.loads(response.read().decode())
                if 'result' in data:
                    return url
        except:
            pass
    
    return None

def moonraker_api_available(url=None):
    """Check if Moonraker API is available"""
    if url is None:
        url = detect_moonraker_url()
    
    if not url:
        return False
    
    try:
        req = urllib.request.Request(f"{url}/server/info")
        with urllib.request.urlopen(req, timeout=2) as response:
            return response.status == 200
    except:
        return False

def get_moonraker_webcams(url=None):
    """
    Get list of webcams from Moonraker.
    
    Returns:
        List of webcam dicts or empty list on error
    """
    if url is None:
        url = detect_moonraker_url()
    
    if not url:
        return []
    
    try:
        req = urllib.request.Request(f"{url}/server/webcams/list")
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            return data.get('result', {}).get('webcams', [])
    except:
        return []

def add_moonraker_webcam(name, stream_url, snapshot_url, target_fps=15, url=None,
                         flip_horizontal=False, flip_vertical=False, rotation=0):
    """
    Add a webcam to Moonraker.
    
    Args:
        name: Webcam display name (truncated friendly_name)
        stream_url: WebRTC stream URL
        snapshot_url: Snapshot URL
        target_fps: Target frame rate
        url: Moonraker URL
        flip_horizontal: Horizontal flip setting
        flip_vertical: Vertical flip setting
        rotation: Rotation angle (0, 90, 180, 270)
        
    Returns:
        Tuple of (success, moonraker_uid or error_message)
        On success, moonraker_uid is the UUID assigned by Moonraker
        On failure, returns the error message
    """
    if url is None:
        url = detect_moonraker_url()
    
    if not url:
        return False, "Moonraker not available"
    
    webcam_data = {
        "name": name,
        "location": "printer",
        "service": "webrtc-mediamtx",
        "enabled": True,
        "icon": "mdiWebcam",
        "target_fps": target_fps,
        "target_fps_idle": 5,
        "stream_url": stream_url,
        "snapshot_url": snapshot_url,
        "flip_horizontal": flip_horizontal,
        "flip_vertical": flip_vertical,
        "rotation": rotation,
        "aspect_ratio": "16:9"
    }
    
    try:
        json_data = json.dumps(webcam_data).encode('utf-8')
        req = urllib.request.Request(
            f"{url}/server/webcams/item",
            data=json_data,
            method="POST"
        )
        req.add_header('Content-Type', 'application/json')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status in (200, 201):
                # Parse response to get the moonraker_uid
                response_data = json.loads(response.read().decode('utf-8'))
                webcam_result = response_data.get('result', {}).get('webcam', {})
                moonraker_uid = webcam_result.get('uid')
                
                if moonraker_uid:
                    return True, moonraker_uid
                else:
                    return True, None  # Success but no UID returned (shouldn't happen)
            return False, f"HTTP {response.status}"
    
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)

def update_moonraker_webcam(uid, webcam_data, url=None):
    """
    Update a webcam in Moonraker.
    
    Args:
        uid: Webcam UID (from Moonraker, not our camera UID)
        webcam_data: Updated webcam data
        url: Moonraker URL
        
    Returns:
        Tuple of (success, error_message)
    """
    if url is None:
        url = detect_moonraker_url()
    
    if not url:
        return False, "Moonraker not available"
    
    try:
        json_data = json.dumps(webcam_data).encode('utf-8')
        req = urllib.request.Request(
            f"{url}/server/webcams/item?uid={uid}",
            data=json_data,
            method="POST"
        )
        req.add_header('Content-Type', 'application/json')
        
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status in (200, 201), None
    
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)

def delete_moonraker_webcam(uid, url=None):
    """
    Delete a webcam from Moonraker by its UID.
    
    Args:
        uid: Webcam UID (from Moonraker)
        url: Moonraker URL
        
    Returns:
        Tuple of (success, error_message)
    """
    if url is None:
        url = detect_moonraker_url()
    
    if not url:
        return False, "Moonraker not available"
    
    try:
        req = urllib.request.Request(
            f"{url}/server/webcams/item?uid={uid}",
            method="DELETE"
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 200, None
    
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)

def get_our_moonraker_cameras(settings=None, url=None):
    """
    Get Moonraker webcams that belong to our cameras (via moonraker_uid).
    
    Args:
        settings: Our raven_settings dict. If provided, finds webcams 
                  matching moonraker_uids in our camera configs.
        url: Moonraker URL
        
    Returns:
        List of (webcam_dict, camera_config) tuples for cameras we manage
    """
    webcams = get_moonraker_webcams(url)
    our_cams = []
    
    if not settings:
        return our_cams
    
    cameras = settings.get("cameras", [])
    
    # Build a map of moonraker_uid -> camera_config
    uid_to_camera = {}
    for cam in cameras:
        moonraker_uid = cam.get("moonraker", {}).get("moonraker_uid")
        if moonraker_uid:
            uid_to_camera[moonraker_uid] = cam
    
    # Find webcams that match our moonraker_uids
    for webcam in webcams:
        webcam_uid = webcam.get('uid')
        if webcam_uid and webcam_uid in uid_to_camera:
            our_cams.append((webcam, uid_to_camera[webcam_uid]))
    
    return our_cams

# ============================================================================
# SYNC FUNCTIONS
# ============================================================================

def sync_camera_to_mediamtx(camera_config, use_vaapi, use_v4l2m2m):
    """
    Sync a single camera to MediaMTX.
    
    Returns:
        Tuple of (success, error_message)
    """
    if not camera_config.get("mediamtx", {}).get("enabled", True):
        return True, "Disabled in config"
    
    uid = camera_config.get("uid")
    if not uid:
        return False, "Camera has no UID"
    
    # Resolve device path
    device_path, warning = resolve_device_path(None, camera_config)
    if not device_path:
        return False, warning
    
    if warning:
        print(f"   {COLOR_YELLOW}{warning}{COLOR_RESET}")
    
    # Build FFmpeg command
    ffmpeg_cmd = build_ffmpeg_cmd_from_config(camera_config, device_path, use_vaapi, use_v4l2m2m)
    
    # Add to MediaMTX
    mtx_config = {
        "source": "publisher",
        "runOnInit": ffmpeg_cmd,
        "runOnInitRestart": True
    }
    
    success, action, error = add_or_update_mediamtx_path(uid, mtx_config)
    return success, error

def sync_camera_to_moonraker(camera_config, system_ip, moonraker_url=None):
    """
    Sync a single camera to Moonraker.
    
    If the camera has a moonraker_uid, we update the existing webcam and sync
    back user settings (flip, rotation). If not, we create a new webcam and
    store the moonraker_uid.
    
    Returns:
        Tuple of (success, error_message, moonraker_uid)
    """
    moonraker = camera_config.get("moonraker", {})
    
    if not moonraker.get("enabled", False):
        return True, "Not enabled for Moonraker", None
    
    uid = camera_config.get("uid")
    friendly_name = camera_config.get("friendly_name", "Camera")
    
    # Use truncated friendly name (no uid prefix)
    name = truncate_friendly_name(friendly_name, 20)
    
    # Get ports from settings or defaults
    webrtc_port = 8889
    snapshot_port = 5050
    
    stream_url = f"http://{system_ip}:{webrtc_port}/{uid}/"
    snapshot_url = f"http://{system_ip}:{snapshot_port}/{uid}.jpg"
    
    target_fps = moonraker.get("target_fps", 15)
    
    # Check if we already have a moonraker_uid
    existing_moonraker_uid = moonraker.get("moonraker_uid")
    
    if existing_moonraker_uid:
        # Check if it still exists in Moonraker and sync settings back
        existing_webcam = get_moonraker_webcam_by_uid(existing_moonraker_uid, moonraker_url)
        
        if existing_webcam:
            # Sync user settings from Moonraker back to our config
            sync_moonraker_settings_to_config(camera_config, moonraker_url)
            
            # Update the webcam with our stream URLs (in case they changed)
            webcam_data = {
                "name": name,
                "stream_url": stream_url,
                "snapshot_url": snapshot_url,
                "target_fps": target_fps
            }
            
            success, error = update_moonraker_webcam(existing_moonraker_uid, webcam_data, moonraker_url)
            return success, error, existing_moonraker_uid
        else:
            # Webcam was deleted from Moonraker, clear our stored uid
            camera_config["moonraker"]["moonraker_uid"] = None
    
    # Create new webcam
    # Use stored settings if available (preserved from previous Moonraker config)
    flip_h = moonraker.get("flip_horizontal", False)
    flip_v = moonraker.get("flip_vertical", False)
    rotation = moonraker.get("rotation", 0)
    
    success, result = add_moonraker_webcam(
        name, stream_url, snapshot_url, target_fps, moonraker_url,
        flip_horizontal=flip_h, flip_vertical=flip_v, rotation=rotation
    )
    
    if success and result:
        # Store the moonraker_uid in our config
        camera_config["moonraker"]["moonraker_uid"] = result
        return True, None, result
    
    return success, result, None

def sync_all_cameras(settings):
    """
    Sync all enabled cameras to MediaMTX and Moonraker.
    
    Note: This function may modify settings (adding moonraker_uids, syncing
    flip/rotation from Moonraker). Caller should save settings after calling.
    
    Returns:
        Dict with sync results
    """
    results = {
        'mediamtx_success': [],
        'mediamtx_failed': [],
        'moonraker_success': [],
        'moonraker_failed': [],
        'moonraker_skipped': [],
        'settings_modified': False
    }
    
    cameras = get_all_cameras(settings)
    
    if not cameras:
        return results
    
    # Detect hardware acceleration
    use_vaapi, use_v4l2m2m = detect_hardware_acceleration()
    
    # Get system IP and moonraker URL
    system_ip = get_system_ip()
    moonraker_url = settings.get("moonraker", {}).get("url") or detect_moonraker_url()
    
    # Apply V4L2 image controls first (independent from streaming)
    cameras_with_v4l2 = [c for c in cameras if c.get("v4l2-ctl")]
    if cameras_with_v4l2:
        print(f"\n🎛️  Applying V4L2 image controls...")
        apply_all_v4l2_controls(settings, verbose=True)
    
    print(f"\n📡 Syncing {len(cameras)} camera(s) to MediaMTX...")
    
    for cam in cameras:
        uid = cam.get("uid", "unknown")
        friendly = cam.get("friendly_name", uid)
        
        # Sync to MediaMTX
        success, error = sync_camera_to_mediamtx(cam, use_vaapi, use_v4l2m2m)
        if success:
            print(f"   ✅ {uid} ({friendly})")
            results['mediamtx_success'].append(uid)
        else:
            print(f"   ❌ {uid} ({friendly}): {error}")
            results['mediamtx_failed'].append((uid, error))
    
    # Sync to Moonraker if available
    if moonraker_url and moonraker_api_available(moonraker_url):
        # Check if any cameras need Moonraker sync
        cameras_for_moonraker = [c for c in cameras if c.get("moonraker", {}).get("enabled", False)]
        
        if cameras_for_moonraker and results['mediamtx_success']:
            # Wait for FFmpeg streams to initialize before adding to Moonraker
            print(f"\n⏳ Waiting for streams to initialize...")
            stream_wait_time = 5  # seconds
            for i in range(stream_wait_time, 0, -1):
                print(f"   Syncing to Moonraker in {i}s...", end='\r')
                time.sleep(1)
            print(f"   Streams should be ready.         ")
        
        print(f"\n🌙 Syncing cameras to Moonraker...")
        
        for cam in cameras:
            uid = cam.get("uid", "unknown")
            friendly = cam.get("friendly_name", uid)
            moonraker = cam.get("moonraker", {})
            
            if not moonraker.get("enabled", False):
                results['moonraker_skipped'].append(uid)
                continue
            
            success, error, moonraker_uid = sync_camera_to_moonraker(cam, system_ip, moonraker_url)
            if success:
                print(f"   ✅ {uid} ({friendly})")
                results['moonraker_success'].append(uid)
                if moonraker_uid:
                    results['settings_modified'] = True
            else:
                print(f"   ❌ {uid} ({friendly}): {error}")
                results['moonraker_failed'].append((uid, error))
    
    return results

# ============================================================================
# ORPHAN DETECTION AND CLEANUP
# ============================================================================

def find_orphaned_cameras(settings):
    """
    Find cameras in settings that don't exist on the system.
    
    Returns:
        List of camera configs that have no matching device
    """
    orphans = []
    devices = get_all_video_devices()
    device_names = {d['hardware_name'] for d in devices}
    
    for cam in get_all_cameras(settings):
        hw_name = cam.get("hardware_name")
        if hw_name and hw_name not in device_names:
            orphans.append(cam)
    
    return orphans

def find_orphaned_moonraker_cameras(settings, moonraker_url=None):
    """
    Find cameras in our settings that have a moonraker_uid that no longer exists.
    (User may have deleted them from Mainsail/Fluidd)
    
    Returns:
        List of camera_config dicts that have stale moonraker_uids
    """
    stale_cameras = []
    
    webcams = get_moonraker_webcams(moonraker_url)
    moonraker_uids_in_moonraker = {w.get('uid') for w in webcams if w.get('uid')}
    
    for cam in get_all_cameras(settings):
        moonraker_uid = cam.get("moonraker", {}).get("moonraker_uid")
        if moonraker_uid and moonraker_uid not in moonraker_uids_in_moonraker:
            stale_cameras.append(cam)
    
    return stale_cameras

def cleanup_orphaned_cameras(settings, orphans):
    """
    Remove orphaned cameras from settings.
    
    Args:
        settings: Raven settings dict
        orphans: List of camera configs to remove
        
    Returns:
        Updated settings dict
    """
    for cam in orphans:
        uid = cam.get("uid")
        if uid:
            settings = delete_camera_config(settings, uid)
    
    return settings

def cleanup_orphaned_moonraker_cameras(stale_cameras, moonraker_url=None):
    """
    Clear stale moonraker_uids from our camera configs.
    (These webcams no longer exist in Moonraker)
    
    Args:
        stale_cameras: List of camera_config dicts with stale moonraker_uids
        moonraker_url: Moonraker URL (unused, kept for API compatibility)
        
    Returns:
        Tuple of (cleared_count, errors)
    """
    cleared_count = 0
    errors = []
    
    for cam in stale_cameras:
        if "moonraker" in cam:
            friendly = cam.get("friendly_name", "Unknown")
            cam["moonraker"]["moonraker_uid"] = None
            # Keep enabled=True so it gets re-added on next sync
            cleared_count += 1
    
    return cleared_count, errors

# ============================================================================
# AUDIO DEVICES
# ============================================================================

def get_audio_devices():
    """Get list of ALSA audio input devices"""
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        devices = []
        for line in result.stdout.splitlines():
            if line.startswith('card'):
                match = re.match(r'card (\d+):.*device (\d+):', line)
                if match:
                    card = match.group(1)
                    device = match.group(2)
                    devices.append({
                        'id': f"hw:{card},{device}",
                        'name': line.split(':')[1].strip() if ':' in line else f"Card {card}"
                    })
        
        return devices
    except Exception:
        return []

# ============================================================================
# CPU MEASUREMENT
# ============================================================================

def measure_cpu_usage(duration=3.0):
    """
    Measure current CPU usage over a duration.
    
    Returns:
        CPU usage percentage (0-100)
    """
    try:
        # Read initial CPU stats
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        
        parts = line.split()
        idle1 = int(parts[4])
        total1 = sum(int(x) for x in parts[1:])
        
        import time
        time.sleep(duration)
        
        # Read final CPU stats
        with open('/proc/stat', 'r') as f:
            line = f.readline()
        
        parts = line.split()
        idle2 = int(parts[4])
        total2 = sum(int(x) for x in parts[1:])
        
        idle_delta = idle2 - idle1
        total_delta = total2 - total1
        
        if total_delta == 0:
            return 0.0
        
        usage = 100.0 * (1.0 - idle_delta / total_delta)
        return round(usage, 1)
    
    except Exception:
        return 0.0

def get_cpu_core_count():
    """Get number of CPU cores"""
    try:
        return os.cpu_count() or 1
    except:
        return 1
