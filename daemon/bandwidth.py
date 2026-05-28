"""
Ravens Perch - Bandwidth Estimation Utilities
"""
import logging
import requests
import threading
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

INPUT_SAMPLE_WARMUP_SECONDS = 5.0

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

COMPRESSED_FORMATS = {'mjpeg', 'mjpg', 'h264', 'hevc', 'h265'}
SNAPSHOT_SAMPLE_FORMATS = {'mjpeg', 'mjpg'}

_input_bandwidth_samples: Dict[str, Dict] = {}
_pending_input_bandwidth_samples = set()
_input_sample_tokens: Dict[str, int] = {}
_input_sample_lock = threading.Lock()


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


def _empty_usb_bandwidth(format_name: str, resolution: str, framerate: int, reason: str) -> Dict:
    """Return an unavailable USB bandwidth value without inventing a compressed bitrate."""
    return {
        'bytes_per_second': None,
        'mbps': None,
        'mb_per_second': None,
        'is_estimate': True,
        'available': False,
        'sampled': False,
        'sample_source': None,
        'reason': reason,
        'format': format_name,
        'resolution': resolution,
        'framerate': framerate,
    }


def _format_usb_bandwidth(
    bytes_per_second: float,
    format_name: str,
    resolution: str,
    framerate: int,
    is_estimate: bool,
    sampled: bool = False,
    sample_source: Optional[str] = None,
) -> Dict:
    mbps = (bytes_per_second * 8) / 1_000_000
    mb_per_second = bytes_per_second / 1_000_000

    return {
        'bytes_per_second': int(bytes_per_second),
        'mbps': round(mbps, 1),
        'mb_per_second': round(mb_per_second, 1),
        'is_estimate': is_estimate,
        'available': True,
        'sampled': sampled,
        'sample_source': sample_source,
        'format': format_name,
        'resolution': resolution,
        'framerate': framerate,
    }


def estimate_usb_bandwidth(
    format_name: str,
    resolution: str,
    framerate: int,
    camera_id: Optional[str] = None
) -> Dict:
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

    if format_lower in COMPRESSED_FORMATS:
        with _input_sample_lock:
            sample = dict(_input_bandwidth_samples.get(str(camera_id)) or {}) if camera_id is not None else None
        if sample:
            return _format_usb_bandwidth(
                sample['bytes_per_second'],
                format_name,
                resolution,
                framerate,
                is_estimate=True,
                sampled=True,
                sample_source=sample.get('source'),
            )

        reason = 'sample_pending' if camera_id is not None else 'sample_unavailable'
        return _empty_usb_bandwidth(format_name, resolution, framerate, reason)

    # Raw/uncompressed formats can be calculated from dimensions and pixel format.
    bpp = FORMAT_BPP.get(format_lower, 2.0)  # Default to YUYV
    bytes_per_second = pixels * bpp * framerate
    return _format_usb_bandwidth(
        bytes_per_second,
        format_name,
        resolution,
        framerate,
        is_estimate=False,
    )


def reset_input_bandwidth_sample(camera_id: str):
    """Clear any one-shot compressed input bandwidth sample for a camera."""
    camera_id = str(camera_id)
    with _input_sample_lock:
        _input_bandwidth_samples.pop(camera_id, None)
        _pending_input_bandwidth_samples.discard(camera_id)
        _input_sample_tokens[camera_id] = _input_sample_tokens.get(camera_id, 0) + 1


def sample_input_bandwidth_once(camera_id: str, settings: Dict) -> bool:
    """Capture one compressed input bandwidth estimate from the warmed snapshot cache."""
    camera_id = str(camera_id)
    format_name = (settings.get('format') or 'mjpeg').lower()

    if format_name not in SNAPSHOT_SAMPLE_FORMATS:
        return False

    framerate = int(settings.get('framerate') or 30)

    try:
        from .snapshot_server import get_cached_snapshot_info
        snapshot = get_cached_snapshot_info(camera_id)
    except Exception as e:
        logger.debug(f"Unable to sample snapshot size for camera {camera_id}: {e}")
        return False

    if not snapshot or not snapshot.get('bytes'):
        return False

    bytes_per_second = snapshot['bytes'] * framerate
    with _input_sample_lock:
        _input_bandwidth_samples[camera_id] = {
            'bytes_per_second': bytes_per_second,
            'source': 'snapshot_sample',
            'sampled_at': time.time(),
        }
        _pending_input_bandwidth_samples.discard(camera_id)

    return True


def schedule_input_bandwidth_sample(
    camera_id: str,
    settings: Dict,
    warmup_seconds: float = INPUT_SAMPLE_WARMUP_SECONDS
):
    """Schedule one compressed input bandwidth sample after the stream warms up."""
    camera_id = str(camera_id)
    format_name = (settings.get('format') or 'mjpeg').lower()

    if format_name not in SNAPSHOT_SAMPLE_FORMATS:
        return

    with _input_sample_lock:
        if camera_id in _input_bandwidth_samples or camera_id in _pending_input_bandwidth_samples:
            return
        token = _input_sample_tokens.get(camera_id, 0) + 1
        _input_sample_tokens[camera_id] = token
        _pending_input_bandwidth_samples.add(camera_id)

    settings_snapshot = dict(settings)

    def worker():
        try:
            time.sleep(max(0.0, warmup_seconds))
            with _input_sample_lock:
                if _input_sample_tokens.get(camera_id) != token:
                    return
            sample_input_bandwidth_once(camera_id, settings_snapshot)
        finally:
            with _input_sample_lock:
                _pending_input_bandwidth_samples.discard(camera_id)

    threading.Thread(
        target=worker,
        daemon=True,
        name=f"input-bandwidth-sample-{camera_id}",
    ).start()


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
