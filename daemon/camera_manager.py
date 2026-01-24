"""
Ravens Perch - Camera Detection and Management
"""
import os
import re
import subprocess
import logging
import threading
import time
from pathlib import Path
from typing import Optional, Dict, List, Callable, Tuple
from dataclasses import dataclass

from .config import (
    FORMAT_PRIORITY, FORMAT_ALIASES, QUALITY_TIERS,
    DEFAULT_CAMERA_SETTINGS, DEBOUNCE_DELAY
)
from .hardware import estimate_cpu_capability, get_best_encoder, detect_encoders

logger = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    """Camera device information."""
    path: str
    hardware_name: str
    serial_number: Optional[str]
    hardware_id: str


def get_device_info(device_path: str) -> Optional[DeviceInfo]:
    """
    Get device information for a V4L2 device.

    Returns DeviceInfo with hardware_name, serial_number, and hardware_id.
    """
    try:
        # Get device name from v4l2-ctl
        result = subprocess.run(
            ["v4l2-ctl", "--device", device_path, "--info"],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            return None

        # Parse card name
        hardware_name = "Unknown Camera"
        for line in result.stdout.split('\n'):
            if 'Card type' in line:
                # Extract name after colon
                parts = line.split(':', 1)
                if len(parts) > 1:
                    hardware_name = parts[1].strip()
                break

        # Try to get serial number from udev
        serial_number = None
        try:
            # Get the device's sysfs path
            device_name = Path(device_path).name  # e.g., "video0"
            sysfs_path = Path(f"/sys/class/video4linux/{device_name}/device")

            if sysfs_path.exists():
                sysfs_path = sysfs_path.resolve()

                # Try to find serial in parent USB device
                usb_path = sysfs_path
                for _ in range(5):  # Walk up the tree
                    serial_file = usb_path / "serial"
                    if serial_file.exists():
                        serial_number = serial_file.read_text().strip()
                        break
                    parent = usb_path.parent
                    if parent == usb_path:
                        break
                    usb_path = parent

        except Exception as e:
            logger.debug(f"Serial number lookup failed for {device_path}: {e}")

        # Generate hardware_id
        if serial_number:
            hardware_id = f"{hardware_name}-{serial_number}"
        else:
            hardware_id = hardware_name

        return DeviceInfo(
            path=device_path,
            hardware_name=hardware_name,
            serial_number=serial_number,
            hardware_id=hardware_id
        )

    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout getting device info for {device_path}")
        return None
    except Exception as e:
        logger.error(f"Error getting device info for {device_path}: {e}")
        return None


def is_capture_device(device_path: str) -> bool:
    """Check if a V4L2 device is a video capture device (not metadata/etc)."""
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device_path, "--all"],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            return False

        # Check for video capture capability
        return "Video Capture" in result.stdout

    except Exception:
        return False


def probe_capabilities(device_path: str) -> Dict:
    """
    Probe camera capabilities using v4l2-ctl.

    Returns: {format: {resolution: [fps_list]}}
    """
    capabilities = {}

    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device_path, "--list-formats-ext"],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode != 0:
            logger.warning(f"Failed to probe capabilities for {device_path}")
            return capabilities

        current_format = None
        current_resolution = None

        for line in result.stdout.split('\n'):
            line = line.strip()

            # Parse format line (e.g., "[0]: 'MJPG' (Motion-JPEG)")
            format_match = re.match(r"\[\d+\]:\s*'(\w+)'\s*\(([^)]+)\)", line)
            if format_match:
                raw_format = format_match.group(1)
                format_desc = format_match.group(2)

                # Normalize format name
                current_format = FORMAT_ALIASES.get(format_desc, raw_format.lower())
                if current_format not in capabilities:
                    capabilities[current_format] = {}
                continue

            # Parse resolution line (e.g., "Size: Discrete 1920x1080")
            size_match = re.search(r"Size:\s*\w+\s+(\d+)x(\d+)", line)
            if size_match and current_format:
                width = size_match.group(1)
                height = size_match.group(2)
                current_resolution = f"{width}x{height}"
                if current_resolution not in capabilities[current_format]:
                    capabilities[current_format][current_resolution] = []
                continue

            # Parse framerate line (e.g., "Interval: Discrete 0.033s (30.000 fps)")
            fps_match = re.search(r"\((\d+(?:\.\d+)?)\s*fps\)", line)
            if fps_match and current_format and current_resolution:
                fps = int(float(fps_match.group(1)))
                if fps not in capabilities[current_format][current_resolution]:
                    capabilities[current_format][current_resolution].append(fps)

        logger.debug(f"Probed capabilities for {device_path}: {capabilities}")
        return capabilities

    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout probing capabilities for {device_path}")
        return capabilities
    except Exception as e:
        logger.error(f"Error probing capabilities for {device_path}: {e}")
        return capabilities


def auto_configure(capabilities: Dict, camera_count: int = 1) -> Dict:
    """
    Auto-configure camera settings based on capabilities and system resources.

    Returns optimal settings dict.
    """
    # Estimate system capability
    cpu_rating = estimate_cpu_capability()

    # Reduce rating if multiple cameras
    adjusted_rating = max(1, cpu_rating - (camera_count - 1))

    # Find quality tier
    resolution = "1280x720"
    framerate = 30
    bitrate = "4M"

    for (min_r, max_r), (res, fps, br) in QUALITY_TIERS.items():
        if min_r <= adjusted_rating <= max_r:
            resolution = res
            framerate = fps
            bitrate = br
            break

    # Select best format from capabilities
    selected_format = "mjpeg"  # Default
    best_priority = -1

    for fmt in capabilities.keys():
        priority = FORMAT_PRIORITY.get(fmt, 0)
        if priority > best_priority:
            # Check if our desired resolution exists
            if resolution in capabilities[fmt]:
                selected_format = fmt
                best_priority = priority
            elif capabilities[fmt]:
                # Use this format but adjust resolution
                selected_format = fmt
                best_priority = priority

    # Adjust resolution if not available
    if selected_format in capabilities:
        available_resolutions = list(capabilities[selected_format].keys())
        if resolution not in available_resolutions and available_resolutions:
            # Find closest resolution
            resolution = find_closest_resolution(resolution, available_resolutions)

        # Adjust framerate if not available
        if resolution in capabilities[selected_format]:
            available_fps = capabilities[selected_format][resolution]
            if framerate not in available_fps and available_fps:
                # Find closest framerate
                framerate = min(available_fps, key=lambda x: abs(x - framerate))

    # Get best encoder
    encoders = detect_encoders()
    encoder = get_best_encoder(encoders)

    settings = {
        'format': selected_format,
        'resolution': resolution,
        'framerate': framerate,
        'encoder': encoder,
        'bitrate': bitrate,
        'preset': 'ultrafast' if encoder == 'libx264' else None,
        'rotation': 0,
        'audio_enabled': False,
    }

    logger.info(f"Auto-configured settings: {settings} (CPU rating: {cpu_rating})")
    return settings


def find_closest_resolution(target: str, available: List[str]) -> str:
    """Find the closest resolution to the target from available options."""
    try:
        target_w, target_h = map(int, target.split('x'))
        target_pixels = target_w * target_h

        best = available[0]
        best_diff = float('inf')

        for res in available:
            w, h = map(int, res.split('x'))
            pixels = w * h
            diff = abs(pixels - target_pixels)
            if diff < best_diff:
                best = res
                best_diff = diff

        return best
    except Exception:
        return available[0] if available else "1280x720"


def find_video_devices() -> List[str]:
    """Find all V4L2 video capture devices."""
    devices = []

    try:
        video_path = Path("/dev")
        for dev in sorted(video_path.glob("video*")):
            device_path = str(dev)
            if is_capture_device(device_path):
                devices.append(device_path)

    except Exception as e:
        logger.error(f"Error finding video devices: {e}")

    return devices


class CameraMonitor:
    """
    Monitor for camera hotplug events using pyudev.
    Falls back to polling if pyudev is unavailable.
    """

    def __init__(
        self,
        on_connect: Callable[[DeviceInfo], None],
        on_disconnect: Callable[[str], None]
    ):
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._known_devices: Dict[str, str] = {}  # path -> hardware_id
        self._debounce_lock = threading.Lock()
        self._pending_events: Dict[str, float] = {}

    def start(self):
        """Start monitoring for camera events."""
        if self._running:
            return

        self._running = True

        # Try pyudev first
        try:
            import pyudev
            self._thread = threading.Thread(target=self._udev_monitor, daemon=True)
            logger.info("Using pyudev for camera monitoring")
        except ImportError:
            self._thread = threading.Thread(target=self._polling_monitor, daemon=True)
            logger.info("Using polling for camera monitoring (pyudev not available)")

        self._thread.start()

    def stop(self):
        """Stop monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _udev_monitor(self):
        """Monitor using pyudev."""
        import pyudev

        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem='video4linux')
        monitor.start()

        while self._running:
            device = monitor.poll(timeout=1)
            if device is None:
                continue

            device_path = device.device_node
            if not device_path:
                continue

            if device.action == 'add':
                self._schedule_connect(device_path)
            elif device.action == 'remove':
                self._schedule_disconnect(device_path)

    def _polling_monitor(self):
        """Monitor using polling fallback."""
        while self._running:
            current_devices = set(find_video_devices())
            known_paths = set(self._known_devices.keys())

            # New devices
            for device_path in current_devices - known_paths:
                self._schedule_connect(device_path)

            # Removed devices
            for device_path in known_paths - current_devices:
                self._schedule_disconnect(device_path)

            time.sleep(2)  # Poll interval

    def _schedule_connect(self, device_path: str):
        """Schedule a connection event with debouncing."""
        def delayed_connect():
            time.sleep(DEBOUNCE_DELAY)
            with self._debounce_lock:
                if device_path not in self._pending_events:
                    return
                del self._pending_events[device_path]

            # Check if device still exists
            if not Path(device_path).exists():
                return

            if not is_capture_device(device_path):
                return

            device_info = get_device_info(device_path)
            if device_info:
                self._known_devices[device_path] = device_info.hardware_id
                try:
                    self.on_connect(device_info)
                except Exception as e:
                    logger.error(f"Error in connect callback: {e}")

        with self._debounce_lock:
            self._pending_events[device_path] = time.time()

        thread = threading.Thread(target=delayed_connect, daemon=True)
        thread.start()

    def _schedule_disconnect(self, device_path: str):
        """Handle device disconnection."""
        with self._debounce_lock:
            # Cancel any pending connect
            if device_path in self._pending_events:
                del self._pending_events[device_path]

        if device_path in self._known_devices:
            del self._known_devices[device_path]
            try:
                self.on_disconnect(device_path)
            except Exception as e:
                logger.error(f"Error in disconnect callback: {e}")

    def scan_existing(self):
        """Scan for existing cameras (call on startup)."""
        for device_path in find_video_devices():
            device_info = get_device_info(device_path)
            if device_info:
                self._known_devices[device_path] = device_info.hardware_id
                try:
                    self.on_connect(device_info)
                except Exception as e:
                    logger.error(f"Error processing existing camera: {e}")


def get_v4l2_controls(device_path: str) -> Dict[str, Dict]:
    """
    Get available V4L2 controls for a device.

    Returns: {control_name: {'min': x, 'max': y, 'default': z, 'value': v}}
    """
    controls = {}

    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device_path, "--list-ctrls-menus"],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            return controls

        for line in result.stdout.split('\n'):
            # Parse control lines like:
            # brightness 0x00980900 (int)    : min=0 max=255 step=1 default=128 value=128
            match = re.match(
                r'\s*(\w+)\s+0x[0-9a-f]+\s+\((\w+)\)\s*:\s*(.+)',
                line
            )
            if match:
                name = match.group(1)
                ctrl_type = match.group(2)
                attrs_str = match.group(3)

                attrs = {}
                for attr_match in re.finditer(r'(\w+)=(-?\d+)', attrs_str):
                    attrs[attr_match.group(1)] = int(attr_match.group(2))

                controls[name] = {
                    'type': ctrl_type,
                    **attrs
                }

    except Exception as e:
        logger.debug(f"Error getting V4L2 controls: {e}")

    return controls


def set_v4l2_control(device_path: str, control: str, value: int) -> bool:
    """Set a V4L2 control value."""
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--device", device_path, "--set-ctrl", f"{control}={value}"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Error setting V4L2 control {control}: {e}")
        return False
