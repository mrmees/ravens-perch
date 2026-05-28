"""
Ravens Perch - Bandwidth Estimation Utilities
"""
import logging
import requests
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Bytes per pixel for different formats
FORMAT_BPP = {
    'yuyv': 2.0,      # YUY2/YUYV: 16 bits per pixel
    'yuyv 4:2:2': 2.0,
    'yuyv422': 2.0,
    'yuy2': 2.0,
    'uyvy': 2.0,
    'nv12': 1.5,      # NV12: 12 bits per pixel
    'yuv420p': 1.5,
    'rgb24': 3.0,     # RGB24: 24 bits per pixel
    'bgr24': 3.0,
    'rgb565': 2.0,    # RGB565: 16 bits per pixel
    'bayer_grbg': 1.0,
    'bayer_rggb': 1.0,
    'bayer_bggr': 1.0,
    'bayer_gbrg': 1.0,
    'grbg': 1.0,
    'rggb': 1.0,
    'bggr': 1.0,
    'gbrg': 1.0,
}

MJPEG_FORMATS = {'mjpeg', 'mjpg'}
ENCODED_FORMATS = {'h264', 'hevc', 'h265'}

RESOLUTION_BUCKETS = (
    ('480p', 640 * 480),
    ('720p', 1280 * 720),
    ('1080p', 1920 * 1080),
    ('1440p', 2560 * 1440),
    ('2160p', 3840 * 2160),
)
FRAMERATE_BUCKETS = (10, 15, 30, 60)

# Estimated USB input throughput in Mbit/s for common UVC camera formats.
# These are throughput estimates only; they do not judge whether a specific hub
# can handle them.
MJPEG_USB_MBIT_TABLE = {
    '480p': {10: 5.0, 15: 7.0, 30: 14.0, 60: 28.0},
    '720p': {10: 14.0, 15: 21.0, 30: 42.0, 60: 84.0},
    '1080p': {10: 31.0, 15: 47.0, 30: 93.0, 60: 187.0},
    '1440p': {10: 55.0, 15: 83.0, 30: 166.0, 60: 332.0},
    '2160p': {10: 124.0, 15: 187.0, 30: 373.0, 60: 746.0},
}

ENCODED_USB_MBIT_TABLE = {
    '480p': {10: 2.0, 15: 3.0, 30: 5.0, 60: 10.0},
    '720p': {10: 4.0, 15: 6.0, 30: 10.0, 60: 20.0},
    '1080p': {10: 8.0, 15: 12.0, 30: 20.0, 60: 40.0},
    '1440p': {10: 14.0, 15: 21.0, 30: 35.0, 60: 70.0},
    '2160p': {10: 32.0, 15: 48.0, 30: 80.0, 60: 160.0},
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


def _format_usb_bandwidth(
    bits_per_second: float,
    format_name: str,
    resolution: str,
    framerate: int,
    is_estimate: bool,
    method: str,
    format_family: str,
    resolution_bucket: Optional[str] = None,
    framerate_bucket: Optional[int] = None,
) -> Dict:
    mbit_per_second = bits_per_second / 1_000_000
    bytes_per_second = bits_per_second / 8
    mb_per_second = bytes_per_second / 1_000_000

    return {
        'bits_per_second': int(bits_per_second),
        'bytes_per_second': int(bytes_per_second),
        'mbit_per_second': round(mbit_per_second, 1),
        'mbps': round(mbit_per_second, 1),
        'mb_per_second': round(mb_per_second, 1),
        'is_estimate': is_estimate,
        'available': True,
        'method': method,
        'format_family': format_family,
        'unit': 'Mbit/s',
        'resolution_bucket': resolution_bucket,
        'framerate_bucket': framerate_bucket,
        'format': format_name,
        'resolution': resolution,
        'framerate': framerate,
    }


def _resolution_bucket(width: int, height: int) -> str:
    pixels = width * height
    for label, max_pixels in RESOLUTION_BUCKETS:
        if pixels <= max_pixels:
            return label
    return RESOLUTION_BUCKETS[-1][0]


def _framerate_bucket(framerate: int) -> int:
    for bucket in FRAMERATE_BUCKETS:
        if framerate <= bucket:
            return bucket
    return FRAMERATE_BUCKETS[-1]


def _lookup_usb_mbit(
    table: Dict[str, Dict[int, float]],
    width: int,
    height: int,
    framerate: int,
) -> Tuple[float, str, int]:
    resolution_bucket = _resolution_bucket(width, height)
    framerate_bucket = _framerate_bucket(framerate)
    estimate = table[resolution_bucket][framerate_bucket]

    if framerate > FRAMERATE_BUCKETS[-1]:
        estimate *= framerate / FRAMERATE_BUCKETS[-1]

    pixels = width * height
    max_bucket_pixels = RESOLUTION_BUCKETS[-1][1]
    if pixels > max_bucket_pixels:
        estimate *= pixels / max_bucket_pixels

    return round(estimate, 1), resolution_bucket, framerate_bucket


def estimate_usb_bandwidth(
    format_name: str,
    resolution: str,
    framerate: int,
    camera_id: Optional[str] = None
) -> Dict:
    """
    Estimate USB bandwidth usage for a camera stream.

    Raw formats are calculated from pixel size. Compressed camera formats use
    deterministic resolution/fps lookup tables and are reported in Mbit/s so
    they can be compared with advertised USB bus speeds.
    """
    width, height = parse_resolution(resolution)
    pixels = width * height

    format_lower = format_name.lower()

    if format_lower in MJPEG_FORMATS:
        mbit_per_second, resolution_bucket, framerate_bucket = _lookup_usb_mbit(
            MJPEG_USB_MBIT_TABLE,
            width,
            height,
            framerate,
        )
        return _format_usb_bandwidth(
            mbit_per_second * 1_000_000,
            format_name,
            resolution,
            framerate,
            is_estimate=True,
            method='lookup',
            format_family='mjpeg',
            resolution_bucket=resolution_bucket,
            framerate_bucket=framerate_bucket,
        )

    if format_lower in ENCODED_FORMATS:
        mbit_per_second, resolution_bucket, framerate_bucket = _lookup_usb_mbit(
            ENCODED_USB_MBIT_TABLE,
            width,
            height,
            framerate,
        )
        return _format_usb_bandwidth(
            mbit_per_second * 1_000_000,
            format_name,
            resolution,
            framerate,
            is_estimate=True,
            method='lookup',
            format_family='encoded',
            resolution_bucket=resolution_bucket,
            framerate_bucket=framerate_bucket,
        )

    # Raw/uncompressed formats can be calculated from dimensions and pixel format.
    bpp = FORMAT_BPP.get(format_lower, 2.0)  # Default to YUYV
    bits_per_second = pixels * bpp * framerate * 8
    return _format_usb_bandwidth(
        bits_per_second,
        format_name,
        resolution,
        framerate,
        is_estimate=False,
        method='calculated',
        format_family='raw',
    )


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

    camera_id = str(camera.get('id', ''))
    usb = estimate_usb_bandwidth(format_name, resolution, framerate, camera_id=camera_id)

    # Get network bandwidth from bitrate setting
    bitrate = settings.get('bitrate', '4M')
    network = get_network_bandwidth(bitrate)

    # Get MediaMTX stats
    mediamtx = get_mediamtx_stream_stats(camera_id)
    readers = mediamtx.get('readers', 0) if mediamtx else 0
    source_ready = bool(mediamtx and (mediamtx.get('ready') or mediamtx.get('source_ready')))
    source = {
        'state': 'ok' if source_ready else 'waiting',
        'label': 'OK' if source_ready else 'Waiting',
    }
    output = {
        'bits_per_second': network['bits_per_second'] * readers,
        'mbps': round(network['mbps'] * readers, 1),
        'readers': readers,
    }

    return {
        'usb': usb,
        'network': network,
        'source': source,
        'output': output,
        'mediamtx': mediamtx,
    }
