"""
USB topology helpers for camera diagnostics.
"""
import logging
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

USB2_HIGH_SPEED_MBIT = 480


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _read_speed_mbps(path: Path) -> Optional[int]:
    value = _read_text(path)
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _find_root_usb_bus(device_sysfs_path: Path) -> Optional[Path]:
    try:
        resolved = device_sysfs_path.resolve(strict=True)
    except OSError:
        return None

    for path in (resolved, *resolved.parents):
        if re.fullmatch(r"usb\d+", path.name) and (path / "busnum").exists():
            return path

    return None


def get_camera_usb_bus_info(
    device_path: str,
    video4linux_root: Path = Path("/sys/class/video4linux"),
) -> Optional[Dict]:
    """Return root USB bus metadata for a V4L2 device, or None when unknown."""
    if not device_path:
        return None

    video_name = Path(device_path).name
    device_sysfs = video4linux_root / video_name / "device"
    root_bus = _find_root_usb_bus(device_sysfs)
    if not root_bus:
        return None

    speed_mbps = _read_speed_mbps(root_bus / "speed")
    busnum = _read_text(root_bus / "busnum")

    return {
        "bus": root_bus.name,
        "busnum": busnum,
        "root_path": str(root_bus),
        "speed_mbps": speed_mbps,
        "product": _read_text(root_bus / "product"),
        "manufacturer": _read_text(root_bus / "manufacturer"),
    }


def get_shared_usb2_camera_warnings(
    cameras: Iterable[Dict],
    video4linux_root: Path = Path("/sys/class/video4linux"),
) -> List[Dict]:
    """Find enabled connected cameras sharing the same USB 2.0 root bus."""
    grouped: Dict[str, Dict] = {}

    for camera in cameras:
        if not camera.get("connected") or not camera.get("enabled", True):
            continue

        bus_info = get_camera_usb_bus_info(
            camera.get("device_path"),
            video4linux_root=video4linux_root,
        )
        if not bus_info:
            logger.debug("USB topology unavailable for camera %s", camera.get("id"))
            continue

        if bus_info.get("speed_mbps") != USB2_HIGH_SPEED_MBIT:
            continue

        key = bus_info["root_path"]
        group = grouped.setdefault(
            key,
            {
                "bus": bus_info["bus"],
                "busnum": bus_info.get("busnum"),
                "speed_mbps": bus_info.get("speed_mbps"),
                "product": bus_info.get("product"),
                "manufacturer": bus_info.get("manufacturer"),
                "cameras": [],
                "camera_names": [],
                "camera_ids": [],
            },
        )

        camera_name = (
            camera.get("friendly_name")
            or camera.get("hardware_name")
            or f"Camera {camera.get('id')}"
        )
        group["cameras"].append(camera)
        group["camera_names"].append(camera_name)
        group["camera_ids"].append(camera.get("id"))

    warnings = [group for group in grouped.values() if len(group["cameras"]) > 1]
    warnings.sort(key=lambda warning: warning.get("bus") or "")
    return warnings
