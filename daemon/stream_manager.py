"""
Ravens Perch v3 - MediaMTX Stream Manager
"""
import time
import logging
from typing import Optional, Dict, List, Tuple, Any

import requests

from .config import (
    MEDIAMTX_API_BASE, MEDIAMTX_RTSP_PORT, MEDIAMTX_WEBRTC_PORT,
    ENCODER_DEFAULTS
)

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


def build_ffmpeg_command(
    device_path: str,
    settings: Dict,
    stream_name: str,
    encoder_type: str = 'libx264'
) -> str:
    """
    Build complete FFmpeg command string for streaming.

    Args:
        device_path: V4L2 device path (e.g., /dev/video0)
        settings: Camera settings dict
        stream_name: Name for the RTSP stream
        encoder_type: Encoder to use (libx264, h264_vaapi, h264_v4l2m2m)

    Returns: Complete FFmpeg command string
    """
    cmd_parts = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]

    # Input format
    input_format = settings.get('format', 'mjpeg')
    resolution = settings.get('resolution', '1280x720')
    framerate = settings.get('framerate', 30)

    # V4L2 input options
    cmd_parts.extend([
        "-f", "v4l2",
        "-input_format", input_format,
        "-video_size", resolution,
        "-framerate", str(framerate),
        "-i", device_path
    ])

    # Video filters
    filters = []

    # Rotation
    rotation = settings.get('rotation', 0)
    if rotation == 90:
        filters.append("transpose=1")
    elif rotation == 180:
        filters.append("transpose=1,transpose=1")
    elif rotation == 270:
        filters.append("transpose=2")

    # Pixel format conversion for hardware encoders
    if encoder_type == 'h264_vaapi':
        filters.append("format=nv12")
        filters.append("hwupload")
    elif input_format == 'mjpeg':
        # MJPEG needs format conversion
        filters.append("format=yuv420p")

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

    # Encoder settings
    bitrate = settings.get('bitrate', '4M')

    if encoder_type == 'libx264':
        preset = settings.get('preset', 'ultrafast')
        cmd_parts.extend([
            "-c:v", "libx264",
            "-preset", preset,
            "-tune", "zerolatency",
            "-b:v", bitrate,
            "-maxrate", bitrate,
            "-bufsize", bitrate,
        ])
    elif encoder_type == 'h264_vaapi':
        cmd_parts.extend([
            "-c:v", "h264_vaapi",
            "-b:v", bitrate,
        ])
    elif encoder_type == 'h264_v4l2m2m':
        cmd_parts.extend([
            "-c:v", "h264_v4l2m2m",
            "-b:v", bitrate,
        ])

    # Common output settings
    cmd_parts.extend([
        "-g", str(framerate * 2),  # Keyframe interval (2 seconds)
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        f"rtsp://127.0.0.1:{MEDIAMTX_RTSP_PORT}/{stream_name}"
    ])

    return " ".join(cmd_parts)


def get_stream_urls(camera_id: str, host: str = "127.0.0.1") -> Dict[str, str]:
    """
    Get all stream URLs for a camera.

    Returns: {
        'rtsp': 'rtsp://...',
        'webrtc': 'http://...',
        'hls': 'http://...'
    }
    """
    path_name = camera_id.replace(' ', '_').lower()

    return {
        'rtsp': f"rtsp://{host}:{MEDIAMTX_RTSP_PORT}/{path_name}",
        'webrtc': f"http://{host}:{MEDIAMTX_WEBRTC_PORT}/{path_name}/",
        'hls': f"http://{host}:8888/{path_name}/",
    }


def add_or_update_stream(camera_id: str, ffmpeg_command: str) -> Tuple[bool, Optional[str]]:
    """Add a stream, or update it if it already exists."""
    # Try to add first
    success, error = add_stream(camera_id, ffmpeg_command)

    if not success and error and "already exists" in error.lower():
        # Stream exists, update it
        return update_stream(camera_id, ffmpeg_command)

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
