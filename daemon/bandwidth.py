"""
Ravens Perch - Bandwidth Estimation Utilities
"""
import logging
import requests
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# MJPEG compression ratios (approximate, varies by scene complexity)
# These are typical compression ratios compared to raw YUV
MJPEG_COMPRESSION_RATIO = 10  # MJPEG is typically 1/10th to 1/20th of raw

# Bytes per pixel for different formats
FORMAT_BPP = {
    'yuyv': 2.0,      # YUY2/YUYV: 16 bits per pixel
    'yuyv 4:2:2': 2.0,
    'yuy2': 2.0,
    'uyvy': 2.0,
    'nv12': 1.5,      # NV12: 12 bits per pixel
    'yuv420p': 1.5,
    'rgb24': 3.0,     # RGB24: 24 bits per pixel
    'bgr24': 3.0,
    'rgb565': 2.0,    # RGB565: 16 bits per pixel
    'mjpeg': None,    # Compressed, calculated differently
    'mjpg': None,
    'h264': None,     # Compressed
    'hevc': None,     # Compressed
}


def parse_resolution(resolution: str) -> Tuple[int, int]:
    """Parse resolution string like '1920x1080' into (width, height)."""
    try:
        parts = resolution.lower().split('x')
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 1280, 720  # Default fallback


def parse_bitrate(bitrate: str) -> int:
    """Parse bitrate string like '4M' into bits per second."""
    if not bitrate:
        return 4_000_000  # Default 4 Mbps

    bitrate = bitrate.upper().strip()
    multipliers = {
        'K': 1_000,
        'M': 1_000_000,
        'G': 1_000_000_000,
    }

    for suffix, mult in multipliers.items():
        if bitrate.endswith(suffix):
            try:
                return int(float(bitrate[:-1]) * mult)
            except ValueError:
                pass

    try:
        return int(bitrate)
    except ValueError:
        return 4_000_000


def estimate_usb_bandwidth(format_name: str, resolution: str, framerate: int) -> Dict:
    """
    Estimate USB bandwidth usage for a camera stream.

    Returns dict with:
        - bytes_per_second: Estimated bytes/sec
        - mbps: Megabits per second
        - mb_per_second: Megabytes per second
        - description: Human-readable description
        - is_estimate: True if this is an estimate (MJPEG), False if calculated
    """
    width, height = parse_resolution(resolution)
    pixels = width * height

    format_lower = format_name.lower()

    # Check if it's a compressed format
    if format_lower in ('mjpeg', 'mjpg'):
        # MJPEG bandwidth varies greatly by scene complexity
        # Typical range: 1-10 MB/s for 1080p30
        # We'll estimate based on resolution and framerate
        # Average MJPEG frame size is roughly 50-200KB for 1080p
        avg_frame_size = (pixels * 3) / MJPEG_COMPRESSION_RATIO  # Compressed from RGB
        bytes_per_second = avg_frame_size * framerate
        is_estimate = True

    elif format_lower in ('h264', 'hevc', 'h265'):
        # Hardware-compressed formats - very low bandwidth
        # Typically 1-8 Mbps depending on quality
        bytes_per_second = 4_000_000 / 8  # Assume 4 Mbps
        is_estimate = True

    else:
        # Raw/uncompressed formats
        bpp = FORMAT_BPP.get(format_lower, 2.0)  # Default to YUYV
        bytes_per_second = pixels * bpp * framerate
        is_estimate = False

    mbps = (bytes_per_second * 8) / 1_000_000
    mb_per_second = bytes_per_second / 1_000_000

    return {
        'bytes_per_second': int(bytes_per_second),
        'mbps': round(mbps, 1),
        'mb_per_second': round(mb_per_second, 1),
        'is_estimate': is_estimate,
        'format': format_name,
        'resolution': resolution,
        'framerate': framerate,
    }


def get_network_bandwidth(bitrate: str) -> Dict:
    """
    Get network bandwidth based on encoder bitrate setting.

    Returns dict with:
        - bits_per_second: Output bitrate in bps
        - mbps: Megabits per second
        - kb_per_second: Kilobytes per second
    """
    bps = parse_bitrate(bitrate)

    return {
        'bits_per_second': bps,
        'mbps': round(bps / 1_000_000, 1),
        'kb_per_second': round(bps / 8 / 1000, 0),
    }


def get_mediamtx_stream_stats(camera_id: str, mediamtx_api: str = "http://127.0.0.1:9997") -> Optional[Dict]:
    """
    Query MediaMTX API for stream statistics.

    Returns dict with:
        - readers: Number of active readers/viewers
        - ready: Whether the stream is ready
        - source_ready: Whether the source (FFmpeg) is connected
    """
    try:
        # Get path info from MediaMTX API
        response = requests.get(
            f"{mediamtx_api}/v3/paths/get/{camera_id}",
            timeout=2
        )

        if response.status_code == 200:
            data = response.json()
            return {
                'readers': len(data.get('readers', [])),
                'ready': data.get('ready', False),
                'source_ready': data.get('sourceReady', False),
            }
        elif response.status_code == 404:
            return {
                'readers': 0,
                'ready': False,
                'source_ready': False,
            }
    except requests.RequestException as e:
        logger.debug(f"Failed to get MediaMTX stats for {camera_id}: {e}")

    return None


def get_camera_bandwidth_stats(camera: Dict) -> Dict:
    """
    Get complete bandwidth statistics for a camera.

    Args:
        camera: Camera dict with settings

    Returns dict with usb and network bandwidth info.
    """
    settings = camera.get('settings') or {}

    # Get USB bandwidth estimate
    format_name = settings.get('format', 'mjpeg')
    resolution = settings.get('resolution', '1280x720')
    framerate = settings.get('framerate', 30)

    usb = estimate_usb_bandwidth(format_name, resolution, framerate)

    # Get network bandwidth from bitrate setting
    bitrate = settings.get('bitrate', '4M')
    network = get_network_bandwidth(bitrate)

    # Get MediaMTX stats
    mediamtx = get_mediamtx_stream_stats(str(camera.get('id', '')))

    return {
        'usb': usb,
        'network': network,
        'mediamtx': mediamtx,
    }
