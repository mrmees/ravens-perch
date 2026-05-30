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


def _find_usb_device_path(device_sysfs_path: Path) -> Optional[Path]:
    try:
        resolved = device_sysfs_path.resolve(strict=True)
    except OSError:
        return None

    for path in (resolved, *resolved.parents):
        if (path / "idVendor").exists() or (path / "product").exists():
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


def _serial_label(name: str, manufacturer: Optional[str], product: Optional[str]) -> str:
    details = " ".join(part for part in (manufacturer, product) if part)
    return f"{name} ({details})" if details else name


def get_usb_serial_devices(
    tty_root: Path = Path("/sys/class/tty"),
) -> List[Dict]:
    """Return USB serial devices grouped by root USB bus."""
    serial_devices: List[Dict] = []

    for tty_path in sorted([*tty_root.glob("ttyACM*"), *tty_root.glob("ttyUSB*")]):
        device_sysfs = tty_path / "device"
        root_bus = _find_root_usb_bus(device_sysfs)
        if not root_bus:
            continue

        speed_mbps = _read_speed_mbps(root_bus / "speed")
        if speed_mbps != USB2_HIGH_SPEED_MBIT:
            continue

        usb_device = _find_usb_device_path(device_sysfs)
        manufacturer = _read_text(usb_device / "manufacturer") if usb_device else None
        product = _read_text(usb_device / "product") if usb_device else None
        name = tty_path.name

        serial_devices.append(
            {
                "name": name,
                "device_path": f"/dev/{name}",
                "bus": root_bus.name,
                "busnum": _read_text(root_bus / "busnum"),
                "root_path": str(root_bus),
                "speed_mbps": speed_mbps,
                "manufacturer": manufacturer,
                "product": product,
                "label": _serial_label(name, manufacturer, product),
            }
        )

    return serial_devices


def get_shared_usb2_camera_warnings(
    cameras: Iterable[Dict],
    video4linux_root: Path = Path("/sys/class/video4linux"),
    tty_root: Path = Path("/sys/class/tty"),
) -> List[Dict]:
    """Find enabled connected cameras sharing USB 2.0 with cameras or serial devices."""
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
                "serial_devices": [],
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

    for serial_device in get_usb_serial_devices(tty_root=tty_root):
        group = grouped.get(serial_device["root_path"])
        if group:
            group["serial_devices"].append(serial_device)

    warnings = [
        group
        for group in grouped.values()
        if len(group["cameras"]) > 1 or group["serial_devices"]
    ]
    warnings.sort(key=lambda warning: warning.get("bus") or "")
    return warnings
