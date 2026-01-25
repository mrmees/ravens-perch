"""
Ravens Perch - Hardware Detection (Encoders, Platform)
"""
import os
import subprocess
import logging
import json
from pathlib import Path
from typing import Dict, Optional

import psutil

logger = logging.getLogger(__name__)

# Cache file location (set by init_encoder_cache)
_encoder_cache_path: Optional[Path] = None
_encoder_cache: Optional[Dict[str, bool]] = None


def init_encoder_cache(data_dir: str):
    """Initialize the encoder cache path."""
    global _encoder_cache_path
    _encoder_cache_path = Path(data_dir) / "encoder_cache.json"


def _load_encoder_cache() -> Optional[Dict[str, bool]]:
    """Load encoder results from cache file."""
    global _encoder_cache
    if _encoder_cache is not None:
        return _encoder_cache

    if _encoder_cache_path and _encoder_cache_path.exists():
        try:
            with open(_encoder_cache_path, 'r') as f:
                _encoder_cache = json.load(f)
                logger.info(f"Loaded encoder cache: {[k for k, v in _encoder_cache.items() if v]}")
                return _encoder_cache
        except Exception as e:
            logger.debug(f"Failed to load encoder cache: {e}")
    return None


def _save_encoder_cache(encoders: Dict[str, bool]):
    """Save encoder results to cache file."""
    global _encoder_cache
    _encoder_cache = encoders

    if _encoder_cache_path:
        try:
            _encoder_cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(_encoder_cache_path, 'w') as f:
                json.dump(encoders, f)
            logger.debug(f"Saved encoder cache to {_encoder_cache_path}")
        except Exception as e:
            logger.debug(f"Failed to save encoder cache: {e}")


def detect_encoders(force: bool = False) -> Dict[str, bool]:
    """
    Detect available hardware encoders.
    Returns dict with encoder availability.

    Args:
        force: If True, bypass cache and re-detect encoders.
    """
    # Check cache first (unless force refresh)
    if not force:
        cached = _load_encoder_cache()
        if cached is not None:
            return cached

    logger.info("Detecting hardware encoders...")
    encoders = {
        'vaapi': False,
        'v4l2m2m': False,
        'rkmpp': False,
        'software': True,  # Always available
    }

    # Get FFmpeg encoder list once
    ffmpeg_encoders = ""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10
        )
        ffmpeg_encoders = result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as e:
        logger.debug(f"FFmpeg encoder list failed: {e}")
        return encoders

    # Check VAAPI (Intel/AMD GPU)
    try:
        if Path("/dev/dri/renderD128").exists():
            if "h264_vaapi" in ffmpeg_encoders:
                encoders['vaapi'] = True
                logger.info("VAAPI hardware encoder detected")
    except Exception as e:
        logger.debug(f"VAAPI detection failed: {e}")

    # Check Rockchip RKMPP (Rockchip SoCs - RK3588, RK3399, etc.)
    try:
        if is_rockchip() or "h264_rkmpp" in ffmpeg_encoders:
            if "h264_rkmpp" in ffmpeg_encoders:
                encoders['rkmpp'] = True
                logger.info("Rockchip RKMPP hardware encoder detected")
    except Exception as e:
        logger.debug(f"RKMPP detection failed: {e}")

    # Check V4L2M2M (Raspberry Pi only - not Rockchip)
    try:
        if is_raspberry_pi() and not is_rockchip():
            if "h264_v4l2m2m" in ffmpeg_encoders:
                encoders['v4l2m2m'] = True
                logger.info("V4L2M2M hardware encoder detected (Raspberry Pi)")
    except Exception as e:
        logger.debug(f"V4L2M2M detection failed: {e}")

    # Save to cache for next startup
    _save_encoder_cache(encoders)

    return encoders


def clear_encoder_cache():
    """Clear the encoder cache to force re-detection on next startup."""
    global _encoder_cache
    _encoder_cache = None
    if _encoder_cache_path and _encoder_cache_path.exists():
        try:
            _encoder_cache_path.unlink()
            logger.info("Encoder cache cleared")
        except Exception as e:
            logger.debug(f"Failed to clear encoder cache: {e}")


def get_best_encoder(encoders: Optional[Dict[str, bool]] = None) -> str:
    """
    Get the best available encoder.
    Priority: rkmpp > vaapi > v4l2m2m > libx264
    """
    if encoders is None:
        encoders = detect_encoders()

    if encoders.get('rkmpp'):
        return 'h264_rkmpp'
    elif encoders.get('vaapi'):
        return 'h264_vaapi'
    elif encoders.get('v4l2m2m'):
        return 'h264_v4l2m2m'
    else:
        return 'libx264'


def is_raspberry_pi() -> bool:
    """Check if running on a Raspberry Pi."""
    try:
        # Check /proc/cpuinfo
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.exists():
            content = cpuinfo.read_text()
            if "Raspberry Pi" in content or "BCM" in content:
                return True

        # Check device tree model
        model_path = Path("/proc/device-tree/model")
        if model_path.exists():
            model = model_path.read_text()
            if "Raspberry Pi" in model:
                return True

    except Exception as e:
        logger.debug(f"Raspberry Pi detection error: {e}")

    return False


def is_rockchip() -> bool:
    """Check if running on a Rockchip SoC (RK3588, RK3399, etc.)."""
    try:
        # Check device tree compatible
        compatible_path = Path("/proc/device-tree/compatible")
        if compatible_path.exists():
            content = compatible_path.read_bytes().decode('utf-8', errors='ignore')
            if "rockchip" in content.lower():
                return True

        # Check device tree model
        model_path = Path("/proc/device-tree/model")
        if model_path.exists():
            model = model_path.read_text()
            rockchip_models = ["RK3588", "RK3399", "RK3328", "RK3568", "RK3566", "Orange Pi", "Radxa"]
            for m in rockchip_models:
                if m in model:
                    return True

        # Check for Rockchip video devices
        if Path("/dev/mpp_service").exists() or Path("/dev/rkvdec").exists():
            return True

    except Exception as e:
        logger.debug(f"Rockchip detection error: {e}")

    return False


def get_platform_info() -> Dict[str, str]:
    """Get platform information."""
    import platform

    info = {
        'system': platform.system(),
        'machine': platform.machine(),
        'processor': platform.processor(),
        'python_version': platform.python_version(),
    }

    if is_raspberry_pi():
        info['platform'] = 'raspberry_pi'
        try:
            model_path = Path("/proc/device-tree/model")
            if model_path.exists():
                info['model'] = model_path.read_text().strip('\x00')
        except Exception:
            pass
    elif is_rockchip():
        info['platform'] = 'rockchip'
        try:
            model_path = Path("/proc/device-tree/model")
            if model_path.exists():
                info['model'] = model_path.read_text().strip('\x00')
        except Exception:
            pass
    else:
        info['platform'] = 'generic_linux'

    return info


def estimate_cpu_capability() -> int:
    """
    Estimate CPU capability on a scale of 1-10.
    Based on core count, current load, and hardware encoders.
    """
    try:
        # Get CPU info
        cpu_count = psutil.cpu_count(logical=True) or 1
        cpu_percent = psutil.cpu_percent(interval=0.5)

        # Base score from core count
        # 1 core = 2, 2 cores = 4, 4 cores = 6, 8+ cores = 8
        if cpu_count >= 8:
            base_score = 8
        elif cpu_count >= 4:
            base_score = 6
        elif cpu_count >= 2:
            base_score = 4
        else:
            base_score = 2

        # Adjust for current load
        # High load reduces capability
        if cpu_percent > 80:
            load_penalty = 3
        elif cpu_percent > 60:
            load_penalty = 2
        elif cpu_percent > 40:
            load_penalty = 1
        else:
            load_penalty = 0

        # Bonus for hardware encoders
        encoders = detect_encoders()
        encoder_bonus = 0
        if encoders.get('vaapi') or encoders.get('v4l2m2m') or encoders.get('rkmpp'):
            encoder_bonus = 2

        # Calculate final score
        score = base_score - load_penalty + encoder_bonus

        # Clamp to 1-10
        return max(1, min(10, score))

    except Exception as e:
        logger.warning(f"CPU capability estimation failed: {e}")
        return 5  # Default middle value


def get_cpu_load() -> float:
    """Get current CPU load percentage."""
    try:
        return psutil.cpu_percent(interval=0.1)
    except Exception:
        return 50.0


def get_memory_info() -> Dict[str, int]:
    """Get memory information in MB."""
    try:
        mem = psutil.virtual_memory()
        return {
            'total_mb': mem.total // (1024 * 1024),
            'available_mb': mem.available // (1024 * 1024),
            'used_percent': mem.percent,
        }
    except Exception:
        return {'total_mb': 0, 'available_mb': 0, 'used_percent': 0}


def check_ffmpeg_available() -> bool:
    """Check if FFmpeg is available."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def check_v4l2_utils_available() -> bool:
    """Check if v4l2-ctl is available."""
    try:
        result = subprocess.run(
            ["v4l2-ctl", "--version"],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False
