"""
Ravens Perch - MediaMTX Stream Manager
"""
import os
import shlex
import signal
import subprocess
import threading
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from urllib.parse import urlencode

import requests

from .config import (
    MEDIAMTX_API_BASE, MEDIAMTX_RTSP_PORT, MEDIAMTX_WEBRTC_PORT,
    ENCODER_DEFAULTS, FFMPEG_INPUT_FORMATS
)
from .camera_manager import apply_v4l2_controls, is_capture_device
from .snapshot_access import get_snapshot_token

logger = logging.getLogger(__name__)

FFMPEG_INITIAL_RESTART_DELAY = 2.0
FFMPEG_MAX_RESTART_DELAY = 60.0


@dataclass
class ManagedFFmpegProcess:
    """Daemon-owned FFmpeg process state for one MediaMTX publisher path."""

    camera_id: str
    command: str
    device_path: Optional[str]
    process: Optional[subprocess.Popen] = None
    monitor_thread: Optional[threading.Thread] = None
    stop_requested: bool = False
    restart_attempts: int = 0


_process_lock = threading.RLock()
_managed_processes: Dict[str, ManagedFFmpegProcess] = {}


def _path_name(camera_id: str) -> str:
    """Convert a camera ID to the MediaMTX path name used by this app."""
    return camera_id.replace(' ', '_').lower()


def _is_ravens_path(path_name: str) -> bool:
    """Return whether a MediaMTX path is managed by the daemon database."""
    return str(path_name).isdigit()


def _publisher_path_payload(path_name: str) -> Dict:
    """Build a MediaMTX path config that accepts daemon-owned publishers only."""
    return {
        "name": path_name,
        "source": "publisher",
        # Clear old runOnInit hooks from prior versions so MediaMTX cannot
        # respawn FFmpeg on its own when a camera is missing.
        "runOnInit": "",
        "runOnInitRestart": False,
    }


def _extract_device_path(ffmpeg_command: str) -> Optional[str]:
    """Extract the V4L2 input device from a generated FFmpeg command."""
    try:
        parts = shlex.split(ffmpeg_command)
    except ValueError as e:
        logger.warning(f"Unable to parse FFmpeg command: {e}")
        return None

    for index, part in enumerate(parts[:-1]):
        if part == "-i" and parts[index + 1].startswith("/dev/video"):
            return parts[index + 1]
    return None


def _device_ready(device_path: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Return whether a V4L2 device exists and is still a capture device."""
    if not device_path:
        return True, None

    if not Path(device_path).exists():
        return False, f"Device not present: {device_path}"

    if not is_capture_device(device_path):
        return False, f"Device is not a capture device: {device_path}"

    return True, None


def _start_ffmpeg_process(managed: ManagedFFmpegProcess) -> Tuple[bool, Optional[str]]:
    """Start FFmpeg for a managed stream without creating a monitor thread."""
    ready, error = _device_ready(managed.device_path)
    if not ready:
        return False, error

    success, error = ensure_stream_path(managed.camera_id)
    if not success:
        return False, f"MediaMTX path unavailable: {error}"

    try:
        args = shlex.split(managed.command)
    except ValueError as e:
        return False, f"Invalid FFmpeg command: {e}"

    if not args or Path(args[0]).name != "ffmpeg":
        return False, "Invalid FFmpeg command: expected ffmpeg executable"

    try:
        process = subprocess.Popen(args, start_new_session=True)
    except FileNotFoundError:
        return False, "FFmpeg executable not found"
    except Exception as e:
        return False, f"Failed to start FFmpeg: {e}"

    with _process_lock:
        managed.process = process

    logger.info(f"Started FFmpeg for camera {managed.camera_id} (pid {process.pid})")
    return True, None


def _restart_delay(attempts: int) -> float:
    """Calculate capped exponential backoff for FFmpeg restarts."""
    return min(FFMPEG_INITIAL_RESTART_DELAY * (2 ** max(0, attempts - 1)), FFMPEG_MAX_RESTART_DELAY)


def _start_snapshot_cache(camera_id: str) -> None:
    """Start the RAM-only latest JPEG cache without coupling stream startup to it."""
    try:
        from .snapshot_server import start_snapshot_cache
        start_snapshot_cache(str(camera_id))
    except Exception as e:
        logger.warning(f"Unable to start snapshot cache for camera {camera_id}: {e}")


def _stop_snapshot_cache(camera_id: str) -> None:
    """Stop the RAM-only latest JPEG cache without coupling stream shutdown to it."""
    try:
        from .snapshot_server import stop_snapshot_cache
        stop_snapshot_cache(str(camera_id))
    except Exception as e:
        logger.warning(f"Unable to stop snapshot cache for camera {camera_id}: {e}")


def _sleep_or_stopped(managed: ManagedFFmpegProcess, seconds: float) -> bool:
    """Sleep in small increments. Returns True if the stream was stopped."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        with _process_lock:
            if managed.stop_requested:
                return True
        time.sleep(min(1.0, max(0.0, deadline - time.time())))
    return False


def _monitor_ffmpeg_process(camera_id: str) -> None:
    """Monitor one FFmpeg process and restart it with backoff when appropriate."""
    while True:
        with _process_lock:
            managed = _managed_processes.get(camera_id)
            if not managed or managed.stop_requested:
                return
            process = managed.process

        if process is None:
            managed.restart_attempts += 1
            delay = _restart_delay(managed.restart_attempts)
            logger.warning(f"FFmpeg for camera {camera_id} is not running; retrying in {delay:.0f}s")
            if _sleep_or_stopped(managed, delay):
                return
            success, error = _start_ffmpeg_process(managed)
            if not success:
                logger.warning(f"FFmpeg restart for camera {camera_id} skipped: {error}")
            continue

        return_code = process.wait()

        with _process_lock:
            managed = _managed_processes.get(camera_id)
            if not managed or managed.stop_requested:
                return
            managed.process = None
            managed.restart_attempts += 1
            delay = _restart_delay(managed.restart_attempts)

        logger.warning(
            f"FFmpeg for camera {camera_id} exited with code {return_code}; "
            f"retrying in {delay:.0f}s"
        )

        if _sleep_or_stopped(managed, delay):
            return

        success, error = _start_ffmpeg_process(managed)
        if not success:
            logger.warning(f"FFmpeg restart for camera {camera_id} skipped: {error}")


def _stop_managed_process(camera_id: str) -> None:
    """Stop and forget the daemon-owned FFmpeg process for a camera."""
    with _process_lock:
        managed = _managed_processes.pop(str(camera_id), None)
        if not managed:
            return
        managed.stop_requested = True
        process = managed.process
        monitor_thread = managed.monitor_thread

    if process and process.poll() is None:
        logger.info(f"Stopping FFmpeg for camera {camera_id} (pid {process.pid})")
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:
            process.terminate()

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning(f"FFmpeg for camera {camera_id} did not stop gracefully; killing")
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception:
                process.kill()
            process.wait(timeout=5)

    if monitor_thread and monitor_thread.is_alive() and monitor_thread is not threading.current_thread():
        monitor_thread.join(timeout=2)


def _replace_ffmpeg_process(camera_id: str, ffmpeg_command: str) -> Tuple[bool, Optional[str]]:
    """Replace any existing FFmpeg process for a camera with a new command."""
    camera_id = str(camera_id)
    _stop_managed_process(camera_id)

    managed = ManagedFFmpegProcess(
        camera_id=camera_id,
        command=ffmpeg_command,
        device_path=_extract_device_path(ffmpeg_command),
    )

    success, error = _start_ffmpeg_process(managed)
    if not success:
        return False, error

    monitor_thread = threading.Thread(
        target=_monitor_ffmpeg_process,
        args=(camera_id,),
        daemon=True,
        name=f"ffmpeg-monitor-{camera_id}",
    )
    managed.monitor_thread = monitor_thread

    with _process_lock:
        _managed_processes[camera_id] = managed

    monitor_thread.start()
    return True, None


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
    Configure a MediaMTX publisher path and start FFmpeg for it.

    Returns: (success, error_message)
    """
    return add_or_update_stream(camera_id, ffmpeg_command, force=True)


def ensure_stream_path(camera_id: str) -> Tuple[bool, Optional[str]]:
    """
    Ensure MediaMTX has a publisher path for a camera.

    The path intentionally does not contain runOnInit. Ravens Perch owns the
    FFmpeg process lifecycle so retries can be bounded and device-aware.
    """
    client = get_client()
    path_name = _path_name(camera_id)
    payload = _publisher_path_payload(path_name)

    success, _, error = client.api_request(
        f"/v3/config/paths/add/{path_name}",
        method="POST",
        data=payload
    )

    if success:
        logger.info(f"Configured stream path: {path_name}")
        return True, None

    if error and "already exists" in error.lower():
        success, _, error = client.api_request(
            f"/v3/config/paths/patch/{path_name}",
            method="PATCH",
            data=payload
        )
        if success:
            logger.debug(f"Updated stream path: {path_name}")
            return True, None

    logger.error(f"Failed to configure stream path {path_name}: {error}")
    return False, error


def update_stream(camera_id: str, ffmpeg_command: str) -> Tuple[bool, Optional[str]]:
    """
    Update an existing stream and restart daemon-owned FFmpeg if needed.

    Returns: (success, error_message)
    """
    return add_or_update_stream(camera_id, ffmpeg_command, force=True)


def remove_stream(camera_id: str) -> Tuple[bool, Optional[str]]:
    """
    Remove a stream from MediaMTX.

    Returns: (success, error_message)
    """
    client = get_client()
    path_name = _path_name(camera_id)

    _stop_managed_process(str(camera_id))
    _stop_snapshot_cache(str(camera_id))

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
    List all configured MediaMTX paths.

    Returns: {path_name: stream_info}
    """
    client = get_client()

    success, data, error = client.api_request("/v3/config/paths/list")

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
    with _process_lock:
        managed_camera_ids = list(_managed_processes.keys())

    for camera_id in managed_camera_ids:
        _stop_managed_process(camera_id)
        _stop_snapshot_cache(camera_id)

    streams = list_streams()
    count = 0

    for path_name in streams.keys():
        if not _is_ravens_path(path_name):
            logger.debug(f"Skipping non-Ravens MediaMTX path during cleanup: {path_name}")
            continue
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
    path_name = _path_name(camera_id)

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
    overlay_path: Optional[str] = None
) -> str:
    """
    Build complete FFmpeg command string for streaming.

    Args:
        device_path: V4L2 device path (e.g., /dev/video0)
        settings: Camera settings dict
        stream_name: Name for the RTSP stream
        encoder_type: Encoder to use (libx264, h264_vaapi, h264_v4l2m2m)
        overlay_path: Optional path to text file for print status overlay
            (only pass if overlay is enabled in settings)

    Returns: Complete FFmpeg command string

    Note: V4L2 controls should be applied separately using apply_v4l2_controls()
          before starting the stream.
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
    # 1. Pixel format conversion first (converts YUV/Bayer formats to yuv420p)
    #    FFmpeg's libswscale handles Bayer demosaicing automatically via format=
    # 2. Then rotation (must operate on converted pixel data, not raw Bayer)
    # 3. Then hardware upload (for VAAPI)
    filters = []

    # Pixel format conversion FIRST (critical for Bayer and YUV formats)
    if encoder_type == 'h264_vaapi':
        filters.append("format=nv12")
    else:
        # Convert to yuv420p for compatibility - most players can't decode 4:2:2
        # libswscale also handles Bayer demosaicing when converting from bayer_*
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
        font = settings.get('overlay_font')  # None means system default

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

        # Build drawtext filter with text file that reloads every second
        # expansion=none disables strftime % sequences so we can use literal %
        drawtext_parts = [
            f"drawtext=textfile='{escaped_path}'",
            "reload=1",
            "expansion=none",
            f"fontcolor={color}",
            f"fontsize={font_size}",
            "borderw=2",
            f"bordercolor={border_color}",
            f"x={x_pos}",
            f"y={y_pos}",
        ]

        # Add font if specified
        if font:
            # Escape font name for FFmpeg (colons need escaping)
            escaped_font = font.replace(':', '\\:')
            drawtext_parts.insert(2, f"font='{escaped_font}'")

        drawtext = ":".join(drawtext_parts)
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
        "-g", str(framerate),  # Keyframe interval (1 second)
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        f"rtsp://127.0.0.1:{MEDIAMTX_RTSP_PORT}/{stream_name}"
    ])

    ffmpeg_cmd = " ".join(cmd_parts)

    if overlay_path:
        logger.info(f"Built FFmpeg command with overlay: {overlay_path}")
        logger.debug(f"Full FFmpeg command: {ffmpeg_cmd}")

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
    path_name = _path_name(camera_id)
    snapshot_query = urlencode({"token": get_snapshot_token()})

    return {
        'rtsp': f"rtsp://{host}:{MEDIAMTX_RTSP_PORT}/{path_name}",
        'webrtc': f"http://{host}:{MEDIAMTX_WEBRTC_PORT}/{path_name}/",
        'hls': f"http://{host}:8888/{path_name}/",
        'snapshot': f"http://{host}/cameras/snapshot/{camera_id}.jpg?{snapshot_query}",
    }


def add_or_update_stream(camera_id: str, ffmpeg_command: str, force: bool = False) -> Tuple[bool, Optional[str]]:
    """Add a stream, or update it if it already exists.

    Args:
        camera_id: The camera ID
        ffmpeg_command: The FFmpeg command to run
        force: If True, restart even if the command hasn't changed

    Returns:
        Tuple of (success, error_message)
    """
    camera_id = str(camera_id)

    success, error = ensure_stream_path(camera_id)
    if not success:
        return False, error

    with _process_lock:
        managed = _managed_processes.get(camera_id)
        process = managed.process if managed else None
        process_running = process is not None and process.poll() is None
        command_unchanged = managed is not None and managed.command == ffmpeg_command

    if command_unchanged and process_running and not force:
        logger.debug(f"Stream {camera_id} command unchanged, FFmpeg already running")
        _start_snapshot_cache(camera_id)
        return True, None

    if command_unchanged and not force:
        logger.info(f"FFmpeg for stream {camera_id} is not running; starting it")
    else:
        logger.info(f"Starting stream {camera_id} with updated FFmpeg command")

    success, error = _replace_ffmpeg_process(camera_id, ffmpeg_command)
    if success:
        _start_snapshot_cache(camera_id)
    else:
        _stop_snapshot_cache(camera_id)
    return success, error


def restart_stream(camera_id: str) -> Tuple[bool, Optional[str]]:
    """Restart a daemon-owned FFmpeg stream using the last known command."""
    camera_id = str(camera_id)
    with _process_lock:
        managed = _managed_processes.get(camera_id)
        ffmpeg_command = managed.command if managed else None

    if not ffmpeg_command:
        return False, "No daemon-managed FFmpeg command found for stream"

    return add_or_update_stream(camera_id, ffmpeg_command, force=True)


def start_camera_stream(
    device_path: str,
    camera_id: str,
    settings: Dict,
    print_monitor=None
) -> Tuple[bool, Optional[str]]:
    """
    Start a camera stream with proper configuration.

    This is the recommended way to start a stream as it:
    1. Applies V4L2 controls (only non-default values from settings)
    2. Determines overlay path (only if overlay is enabled)
    3. Builds the FFmpeg command
    4. Starts the stream

    Args:
        device_path: V4L2 device path (e.g., /dev/video0)
        camera_id: Camera ID for the stream name
        settings: Camera settings dict from database
        print_monitor: Optional print monitor for overlay support

    Returns:
        Tuple of (success: bool, error_message: Optional[str])
    """
    # Apply V4L2 controls if any are saved (these are already filtered to non-defaults)
    v4l2_controls = settings.get('v4l2_controls') or {}
    if v4l2_controls:
        logger.debug(f"Applying V4L2 controls for camera {camera_id}: {v4l2_controls}")
        apply_v4l2_controls(device_path, v4l2_controls)

    # Only include overlay path if overlay is enabled
    overlay_path = None
    if settings.get('overlay_enabled') and print_monitor:
        overlay_path = str(print_monitor.get_overlay_path(str(camera_id)))
        logger.debug(f"Overlay enabled for camera {camera_id}: {overlay_path}")

    # Build the FFmpeg command
    encoder = settings.get('encoder', 'libx264')
    ffmpeg_cmd = build_ffmpeg_command(
        device_path,
        settings,
        str(camera_id),
        encoder,
        overlay_path=overlay_path
    )

    # Start the stream
    success, error = add_or_update_stream(str(camera_id), ffmpeg_cmd)
    return success, error
