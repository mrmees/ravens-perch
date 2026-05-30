"""
Ravens Perch - Bandwidth Estimation Utilities
"""
import logging
import requests
from typing import Dict, Optional

logger = logging.getLogger(__name__)


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

    Returns dict with network output and MediaMTX source info.
    """
    camera_id = str(camera.get('id', ''))
    settings = camera.get('settings') or {}

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
        'network': network,
        'source': source,
        'output': output,
        'mediamtx': mediamtx,
    }
