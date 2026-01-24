"""
Ravens Perch v3 - Snapshot Server

Provides JPEG snapshot endpoints by grabbing frames from RTSP streams.
"""
import io
import time
import logging
import threading
from typing import Optional, Dict, Tuple
from dataclasses import dataclass

from .config import (
    SNAPSHOT_CACHE_TTL_MS, SNAPSHOT_TIMEOUT,
    MEDIAMTX_RTSP_PORT
)

logger = logging.getLogger(__name__)

# Try to import optional dependencies
try:
    import av
    AV_AVAILABLE = True
except ImportError:
    AV_AVAILABLE = False
    logger.warning("PyAV not available - snapshots will use fallback method")

try:
    from turbojpeg import TurboJPEG
    jpeg = TurboJPEG()
    TURBOJPEG_AVAILABLE = True
except (ImportError, OSError):
    TURBOJPEG_AVAILABLE = False
    logger.warning("TurboJPEG not available - using PIL for JPEG encoding")

if not TURBOJPEG_AVAILABLE:
    try:
        from PIL import Image
        PIL_AVAILABLE = True
    except ImportError:
        PIL_AVAILABLE = False


@dataclass
class CachedFrame:
    """Cached snapshot frame."""
    data: bytes
    timestamp: float
    width: int
    height: int


class SnapshotCache:
    """Thread-safe cache for snapshot frames."""

    def __init__(self, ttl_ms: int = SNAPSHOT_CACHE_TTL_MS):
        self.ttl_ms = ttl_ms
        self._cache: Dict[str, CachedFrame] = {}
        self._lock = threading.Lock()

    def get(self, camera_id: str) -> Optional[bytes]:
        """Get cached frame if still valid."""
        with self._lock:
            if camera_id not in self._cache:
                return None

            frame = self._cache[camera_id]
            age_ms = (time.time() - frame.timestamp) * 1000

            if age_ms > self.ttl_ms:
                del self._cache[camera_id]
                return None

            return frame.data

    def put(self, camera_id: str, data: bytes, width: int, height: int):
        """Store frame in cache."""
        with self._lock:
            self._cache[camera_id] = CachedFrame(
                data=data,
                timestamp=time.time(),
                width=width,
                height=height
            )

    def invalidate(self, camera_id: str):
        """Remove frame from cache."""
        with self._lock:
            if camera_id in self._cache:
                del self._cache[camera_id]

    def clear(self):
        """Clear all cached frames."""
        with self._lock:
            self._cache.clear()


# Global cache instance
_cache = SnapshotCache()


def get_rtsp_url(camera_id: str) -> str:
    """Get RTSP URL for a camera."""
    path_name = camera_id.replace(' ', '_').lower()
    return f"rtsp://127.0.0.1:{MEDIAMTX_RTSP_PORT}/{path_name}"


def grab_frame_av(rtsp_url: str, timeout: float = SNAPSHOT_TIMEOUT) -> Optional[Tuple[bytes, int, int]]:
    """
    Grab a single frame from RTSP stream using PyAV.

    Returns: (jpeg_bytes, width, height) or None
    """
    if not AV_AVAILABLE:
        return None

    container = None
    try:
        # Open RTSP stream with timeout
        options = {
            'rtsp_transport': 'tcp',
            'stimeout': str(int(timeout * 1000000)),  # microseconds
        }

        container = av.open(rtsp_url, options=options, timeout=timeout)
        container.streams.video[0].thread_type = 'AUTO'

        # Get single frame
        for frame in container.decode(video=0):
            # Convert to RGB
            rgb_frame = frame.to_ndarray(format='rgb24')
            width = frame.width
            height = frame.height

            # Encode to JPEG
            jpeg_data = encode_jpeg(rgb_frame, width, height)
            if jpeg_data:
                return jpeg_data, width, height

            break  # Only need one frame

    except av.AVError as e:
        logger.debug(f"AV error grabbing frame: {e}")
    except Exception as e:
        logger.debug(f"Error grabbing frame: {e}")
    finally:
        if container:
            try:
                container.close()
            except Exception:
                pass

    return None


def encode_jpeg(rgb_array, width: int, height: int, quality: int = 85) -> Optional[bytes]:
    """Encode RGB array to JPEG bytes."""
    try:
        if TURBOJPEG_AVAILABLE:
            # TurboJPEG expects BGR, but we have RGB - it handles this
            return jpeg.encode(rgb_array, quality=quality)

        elif PIL_AVAILABLE:
            from PIL import Image
            img = Image.fromarray(rgb_array, 'RGB')
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=quality)
            return buffer.getvalue()

        else:
            logger.error("No JPEG encoder available")
            return None

    except Exception as e:
        logger.error(f"JPEG encoding error: {e}")
        return None


def grab_snapshot(camera_id: str, use_cache: bool = True) -> Optional[bytes]:
    """
    Grab a JPEG snapshot for a camera.

    Args:
        camera_id: Camera identifier
        use_cache: Whether to use cached frames

    Returns: JPEG bytes or None
    """
    # Check cache first
    if use_cache:
        cached = _cache.get(camera_id)
        if cached:
            return cached

    # Get fresh frame
    rtsp_url = get_rtsp_url(camera_id)

    # Try PyAV first
    result = grab_frame_av(rtsp_url)
    if result:
        jpeg_data, width, height = result
        _cache.put(camera_id, jpeg_data, width, height)
        return jpeg_data

    # Fallback: try FFmpeg subprocess
    result = grab_frame_ffmpeg(rtsp_url)
    if result:
        jpeg_data, width, height = result
        _cache.put(camera_id, jpeg_data, width, height)
        return jpeg_data

    logger.warning(f"Failed to grab snapshot for camera: {camera_id}")
    return None


def grab_frame_ffmpeg(rtsp_url: str, timeout: float = SNAPSHOT_TIMEOUT) -> Optional[Tuple[bytes, int, int]]:
    """
    Grab a single frame using FFmpeg subprocess (fallback).

    Returns: (jpeg_bytes, width, height) or None
    """
    import subprocess

    try:
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-frames:v", "1",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-q:v", "5",
            "pipe:1"
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout
        )

        if result.returncode == 0 and result.stdout:
            # Can't easily get dimensions from pipe, use defaults
            return result.stdout, 0, 0

    except subprocess.TimeoutExpired:
        logger.debug(f"FFmpeg snapshot timeout for {rtsp_url}")
    except Exception as e:
        logger.debug(f"FFmpeg snapshot error: {e}")

    return None


def get_placeholder_image() -> bytes:
    """Generate a placeholder image when no camera is available."""
    if PIL_AVAILABLE:
        from PIL import Image, ImageDraw

        # Create a dark gray placeholder
        img = Image.new('RGB', (640, 480), color=(40, 40, 40))
        draw = ImageDraw.Draw(img)

        # Add "No Signal" text
        text = "No Signal"
        try:
            # Try to center the text (approximate)
            draw.text((250, 230), text, fill=(150, 150, 150))
        except Exception:
            pass

        buffer = io.BytesIO()
        img.save(buffer, format='JPEG', quality=70)
        return buffer.getvalue()

    else:
        # Return a minimal 1x1 gray JPEG
        return bytes([
            0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46,
            0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01,
            0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
            0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08,
            0x07, 0x07, 0x07, 0x09, 0x09, 0x08, 0x0A, 0x0C,
            0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
            0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D,
            0x1A, 0x1C, 0x1C, 0x20, 0x24, 0x2E, 0x27, 0x20,
            0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
            0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27,
            0x39, 0x3D, 0x38, 0x32, 0x3C, 0x2E, 0x33, 0x34,
            0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
            0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4,
            0x00, 0x1F, 0x00, 0x00, 0x01, 0x05, 0x01, 0x01,
            0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04,
            0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0xFF,
            0xC4, 0x00, 0xB5, 0x10, 0x00, 0x02, 0x01, 0x03,
            0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04,
            0x00, 0x00, 0x01, 0x7D, 0x01, 0x02, 0x03, 0x00,
            0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
            0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32,
            0x81, 0x91, 0xA1, 0x08, 0x23, 0x42, 0xB1, 0xC1,
            0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72,
            0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A,
            0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x34, 0x35,
            0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45,
            0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55,
            0x56, 0x57, 0x58, 0x59, 0x5A, 0x63, 0x64, 0x65,
            0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75,
            0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85,
            0x86, 0x87, 0x88, 0x89, 0x8A, 0x92, 0x93, 0x94,
            0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3,
            0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2,
            0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA,
            0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9,
            0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8,
            0xD9, 0xDA, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6,
            0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4,
            0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFF, 0xDA,
            0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3F, 0x00,
            0xFB, 0xD5, 0xDB, 0x20, 0xA8, 0xF1, 0x7F, 0xFF,
            0xD9
        ])


def invalidate_cache(camera_id: str):
    """Invalidate cache for a specific camera."""
    _cache.invalidate(camera_id)


def clear_cache():
    """Clear all snapshot caches."""
    _cache.clear()
