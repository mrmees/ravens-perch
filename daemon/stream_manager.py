"""
Ravens Perch - MediaMTX Stream Manager
"""
import time
import logging
from typing import Optional, Dict, List, Tuple, Any

import requests

from .config import (
    MEDIAMTX_API_BASE, MEDIAMTX_RTSP_PORT, MEDIAMTX_WEBRTC_PORT,
    ENCODER_DEFAULTS, FFMPEG_INPUT_FORMATS, WEB_UI_PORT
)
from .camera_manager import apply_v4l2_controls

logger = logging.getLogger(__name__)


class MediaMTXClient:
    """Client for MediaMTX API."""

    def __init__(self, api_base: str = MEDIAMTX_API_BASE):
        self.api_base = api_base.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({'Content-Type': 'application/json'})

    def api_request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict] = None,
        timeout: int = 5
    ) -> Tuple[bool, Optional[Dict], Optional[str]]:
        """
        Make an API request to MediaMTX.

        Returns: (success, data, error_message)
        """
        url = f"{self.api_base}{endpoint}"

        try:
            if method == "GET":
                response = self.session.get(url, timeout=timeout)
            elif method == "POST":
                response = self.session.post(url, json=data, timeout=timeout)
            elif method == "PATCH":
                response = self.session.patch(url, json=data, timeout=timeout)
            elif method == "DELETE":
                response = self.session.delete(url, timeout=timeout)
            else:
                return False, None, f"Unsupported method: {method}"

            if response.status_code in (200, 201, 204):
                try:
                    return True, response.json() if response.text else {}, None
                except ValueError:
                    return True, {}, None
            else:
                return False, None, f"HTTP {response.status_code}: {response.text}"

        except requests.Timeout:
            return False, None, "Request timeout"
        except requests.ConnectionError:
            return False, None, "Connection failed"
        except Exception as e:
            return False, None, str(e)

    def is_available(self) -> bool:
        """Check if MediaMTX API is responding."""
        success, _, _ = self.api_request("/v3/config/global/get", timeout=2)
        return success

    def wait_for_available(self, timeout: int = 30, interval: float = 1.0) -> bool:
        """Wait for MediaMTX to become available."""
        start = time.time()
        while time.time() - start < timeout:
            if self.is_available():
                return True
            time.sleep(interval)
        return False


# Global client instance
_client: Optional[MediaMTXClient] = None


def get_client() -> MediaMTXClient:
    """Get or create the MediaMTX client."""
    global _client
    if _client is None:
        _client = MediaMTXClient()
    return _client


def is_available() -> bool:
    """Check if MediaMTX API is responding."""
    return get_client().is_available()


def wait_for_available(timeout: int = 30) -> bool:
    """Wait for MediaMTX to become available."""
    return get_client().wait_for_available(timeout)


def add_stream(camera_id: str, ffmpeg_command: str) -> Tuple[bool, Optional[str]]:
    """
    Add a new stream to MediaMTX.

    Returns: (success, error_message)
    """
    client = get_client()

    # URL-encode the camera_id for the path
    path_name = camera_id.replace(' ', '_').lower()

    payload = {
        "name": path_name,
        "source": "publisher",
        "runOnInit": ffmpeg_command,
        "runOnInitRestart": True,
    }

    success, _, error = client.api_request(
        f"/v3/config/paths/add/{path_name}",
        method="POST",
        data=payload
    )

    if success:
        logger.info(f"Added stream: {path_name}")
    else:
        logger.error(f"Failed to add stream {path_name}: {error}")

    return success, error


def update_stream(camera_id: str, ffmpeg_command: str) -> Tuple[bool, Optional[str]]:
    """
    Update an existing stream in MediaMTX.

    Returns: (success, error_message)
    """
    client = get_client()
    path_name = camera_id.replace(' ', '_').lower()

    payload = {
        "runOnInit": ffmpeg_command,
        "runOnInitRestart": True,
    }

    success, _, error = client.api_request(
        f"/v3/config/paths/patch/{path_name}",
        method="PATCH",
        data=payload
    )

    if success:
        logger.info(f"Updated stream: {path_name}")
    else:
        logger.error(f"Failed to update stream {path_name}: {error}")

    return success, error


def remove_stream(camera_id: str) -> Tuple[bool, Optional[str]]:
    """
    Remove a stream from MediaMTX.

    Returns: (success, error_message)
    """
    client = get_client()
    path_name = camera_id.replace(' ', '_').lower()

    success, _, error = client.api_request(
        f"/v3/config/paths/delete/{path_name}",
        method="DELETE"
    )

    if success:
        logger.info(f"Removed stream: {path_name}")
    else:
        # Not an error if path doesn't exist
        if "not found" in str(error).lower():
            return True, None
        logger.error(f"Failed to remove stream {path_name}: {error}")

    return success, error


def list_streams() -> Dict[str, Dict]:
    """
    List all active streams.

    Returns: {path_name: stream_info}
    """
    client = get_client()

    success, data, error = client.api_request("/v3/paths/list")

    if not success:
        logger.error(f"Failed to list streams: {error}")
        return {}

    streams = {}
    items = data.get('items', []) if data else []
    for item in items:
        name = item.get('name', '')
        streams[name] = item

    return streams


def remove_all_streams() -> int:
    """
    Remove all streams from MediaMTX.

    Returns: count of streams removed
    """
    streams = list_streams()
    count = 0

    for path_name in streams.keys():
        success, _ = remove_stream(path_name)
        if success:
            count += 1

    return count


def get_stream_status(camera_id: str) -> Optional[Dict]:
    """
    Get status for a specific stream.

    Returns stream info dict or None if not found.
    """
    client = get_client()
    path_name = camera_id.replace(' ', '_').lower()

    success, data, _ = client.api_request(f"/v3/paths/get/{path_name}")

    if success and data:
        return data
    return None


def is_stream_active(camera_id: str) -> bool:
    """Check if a stream is active and has readers."""
    status = get_stream_status(camera_id)
    if not status:
        return False

    # Check if stream has source ready
    return status.get('ready', False)


def scale_bitrate(resolution: str, base_bitrate: str) -> str:
    """
    Scale bitrate based on actual resolution to avoid wasting bandwidth.

    Base bitrates are calibrated for 1080p. Scale down proportionally.
    """
    try:
        width, height = map(int, resolution.split('x'))
        pixels = width * height

        # Reference: 1080p = 1920x1080 = 2,073,600 pixels
        reference_pixels = 1920 * 1080

        # Parse base bitrate (e.g., "6M" -> 6.0, "500K" -> 0.5)
        base = base_bitrate.upper().strip()
        if base.endswith('M'):
            base_value = float(base[:-1])
        elif base.endswith('K'):
            base_value = float(base[:-1]) / 1000
        else:
            base_value = float(base) / 1000000

        # Scale proportionally with a minimum floor
        scale = pixels / reference_pixels
        scaled_value = base_value * scale

        # Minimum 500K, maximum is the base bitrate
        scaled_value = max(0.5, min(scaled_value, base_value))

        # Format output
        if scaled_value >= 1.0:
            return f"{scaled_value:.1f}M".replace('.0M', 'M')
        else:
            return f"{int(scaled_value * 1000)}K"

    except (ValueError, ZeroDivisionError):
        return base_bitrate


def build_ffmpeg_command(
    device_path: str,
    settings: Dict,
    stream_name: str,
    encoder_type: str = 'libx264',
    v4l2_controls: Optional[Dict[str, int]] = None,
    overlay_path: Optional[str] = None
) -> str:
    """
    Build complete FFmpeg command string for streaming.

    Args:
        device_path: V4L2 device path (e.g., /dev/video0)
        settings: Camera settings dict
        stream_name: Name for the RTSP stream
        encoder_type: Encoder to use (libx264, h264_vaapi, h264_v4l2m2m)
        v4l2_controls: Optional dict of V4L2 controls to apply before streaming
        overlay_path: Optional path to text file for print status overlay

    Returns: Complete FFmpeg command string (may be wrapped in shell for V4L2 controls)
    """
    cmd_parts = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]

    # Input format - use 'or' to handle None values
    input_format = settings.get('format') or 'mjpeg'
    resolution = settings.get('resolution') or '1280x720'
    framerate = settings.get('framerate') or 30

    # Convert internal format name to FFmpeg format name
    ffmpeg_format = FFMPEG_INPUT_FORMATS.get(input_format, input_format)

    # V4L2 input options
    cmd_parts.extend([
        "-f", "v4l2",
        "-input_format", ffmpeg_format,
        "-video_size", resolution,
        "-framerate", str(framerate),
        "-i", device_path
    ])

    # Video filters - order matters!
    # 1. Pixel format conversion first (debayers Bayer input, converts YUV formats)
    # 2. Then rotation (must operate on converted pixel data, not raw Bayer)
    # 3. Then hardware upload (for VAAPI)
    filters = []

    # Pixel format conversion FIRST (critical for Bayer formats)
    if encoder_type == 'h264_vaapi':
        filters.append("format=nv12")
    else:
        # Convert to yuv420p for compatibility - most players can't decode 4:2:2
        # Also debayers raw Bayer sensor data via libswscale
        filters.append("format=yuv420p")

    # Rotation AFTER format conversion
    rotation = settings.get('rotation') or 0
    if rotation == 90:
        filters.append("transpose=1")
    elif rotation == 180:
        filters.append("transpose=1,transpose=1")
    elif rotation == 270:
        filters.append("transpose=2")

    # Print status overlay (after rotation, before hardware upload)
    if overlay_path:
        # Get overlay customization settings
        font_size = settings.get('overlay_font_size') or 24
        position = settings.get('overlay_position') or 'bottom_center'
        color = settings.get('overlay_color') or 'white'

        # Map position to x/y coordinates
        position_map = {
            'top_left': ('20', '20'),
            'top_center': ('(w-text_w)/2', '20'),
            'top_right': ('w-text_w-20', '20'),
            'bottom_left': ('20', 'h-th-20'),
            'bottom_center': ('(w-text_w)/2', 'h-th-20'),
            'bottom_right': ('w-text_w-20', 'h-th-20'),
        }
        x_pos, y_pos = position_map.get(position, ('(w-text_w)/2', 'h-th-20'))

        # Determine border color for contrast
        border_color = 'black' if color in ('white', 'yellow', 'cyan') else 'white'

        # Escape path for FFmpeg filter (colons and backslashes need escaping)
        escaped_path = overlay_path.replace('\\', '/').replace(':', '\\:')

        # drawtext filter with text file that reloads every second
        # expansion=none disables strftime % sequences so we can use literal %
        drawtext = (
            f"drawtext=textfile='{escaped_path}'"
            f":reload=1"
            f":expansion=none"
            f":fontcolor={color}"
            f":fontsize={font_size}"
            f":borderw=2"
            f":bordercolor={border_color}"
            f":x={x_pos}"
            f":y={y_pos}"
        )
        filters.append(drawtext)

    # Hardware upload last (for VAAPI)
    if encoder_type == 'h264_vaapi':
        filters.append("hwupload")

    if filters:
        cmd_parts.extend(["-vf", ",".join(filters)])

    # Hardware acceleration setup
    if encoder_type == 'h264_vaapi':
        # VAAPI initialization (before input)
        vaapi_device = "/dev/dri/renderD128"
        cmd_parts = (
            ["ffmpeg", "-hide_banner", "-loglevel", "warning",
             "-vaapi_device", vaapi_device] +
            cmd_parts[4:]  # Skip the initial ffmpeg command
        )

    # Encoder settings - use 'or' to handle None values
    base_bitrate = settings.get('bitrate') or '4M'
    bitrate = scale_bitrate(resolution, base_bitrate)

    if encoder_type == 'libx264':
        preset = settings.get('preset') or 'ultrafast'
        cmd_parts.extend([
            "-c:v", "libx264",
            "-preset", preset,
            "-tune", "zerolatency",
            "-profile:v", "baseline",  # Maximum browser/mobile compatibility
            "-level", "3.1",           # Safe for most devices
            "-bf", "0",                # No B-frames (explicit for low-latency)
            "-b:v", bitrate,
            "-maxrate", bitrate,
            "-bufsize", bitrate,
        ])
    elif encoder_type == 'h264_vaapi':
        cmd_parts.extend([
            "-c:v", "h264_vaapi",
            "-profile:v", "constrained_baseline",
            "-level", "31",
            "-b:v", bitrate,
        ])
    elif encoder_type == 'h264_rkmpp':
        cmd_parts.extend([
            "-c:v", "h264_rkmpp",
            "-profile:v", "baseline",
            "-level", "31",
            "-b:v", bitrate,
        ])
    elif encoder_type == 'h264_v4l2m2m':
        cmd_parts.extend([
            "-c:v", "h264_v4l2m2m",
            "-profile:v", "baseline",
            "-level", "31",
            "-b:v", bitrate,
        ])

    # Common output settings
    cmd_parts.extend([
        "-g", str(framerate * 2),  # Keyframe interval (2 seconds)
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        f"rtsp://127.0.0.1:{MEDIAMTX_RTSP_PORT}/{stream_name}"
    ])

    ffmpeg_cmd = " ".join(cmd_parts)

    if overlay_path:
        logger.info(f"Built FFmpeg command with overlay: {overlay_path}")
        logger.debug(f"Full FFmpeg command: {ffmpeg_cmd}")

    # If V4L2 controls are provided, wrap command to apply them first
    if v4l2_controls:
        ctrl_parts = []
        for name, value in v4l2_controls.items():
            if value is not None:
                ctrl_parts.append(f"{name}={value}")

        if ctrl_parts:
            ctrl_str = ",".join(ctrl_parts)
            v4l2_cmd = f"v4l2-ctl -d {device_path} --set-ctrl={ctrl_str}"
            # Wrap in shell to run v4l2-ctl before ffmpeg
            ffmpeg_cmd = f"sh -c '{v4l2_cmd}; {ffmpeg_cmd}'"

    return ffmpeg_cmd


def get_stream_urls(camera_id: str, host: str = "127.0.0.1") -> Dict[str, str]:
    """
    Get all stream URLs for a camera.

    Returns: {
        'rtsp': 'rtsp://...',
        'webrtc': 'http://...',
        'hls': 'http://...',
        'snapshot': 'http://...'
    }
    """
    path_name = camera_id.replace(' ', '_').lower()

    return {
        'rtsp': f"rtsp://{host}:{MEDIAMTX_RTSP_PORT}/{path_name}",
        'webrtc': f"http://{host}:{MEDIAMTX_WEBRTC_PORT}/{path_name}/",
        'hls': f"http://{host}:8888/{path_name}/",
        'snapshot': f"http://{host}:{WEB_UI_PORT}/cameras/snapshot/{camera_id}.jpg",
    }


def add_or_update_stream(camera_id: str, ffmpeg_command: str) -> Tuple[bool, Optional[str]]:
    """Add a stream, or update it if it already exists."""
    # Try to add first
    success, error = add_stream(camera_id, ffmpeg_command)

    if not success and error and "already exists" in error.lower():
        # Stream exists - remove and re-add to restart with new settings
        # Just patching the config doesn't restart the running FFmpeg process
        remove_stream(camera_id)
        time.sleep(0.3)  # Brief delay to ensure cleanup
        return add_stream(camera_id, ffmpeg_command)

    return success, error


def restart_stream(camera_id: str) -> Tuple[bool, Optional[str]]:
    """Restart a stream by removing and re-adding it."""
    client = get_client()
    path_name = camera_id.replace(' ', '_').lower()

    # Get current config
    success, data, error = client.api_request(f"/v3/config/paths/get/{path_name}")
    if not success:
        return False, f"Failed to get stream config: {error}"

    # Remove the stream
    remove_stream(camera_id)
    time.sleep(0.5)

    # Re-add with same config
    if data:
        ffmpeg_command = data.get('runOnInit', '')
        if ffmpeg_command:
            return add_stream(camera_id, ffmpeg_command)

    return False, "No FFmpeg command found in stream config"
