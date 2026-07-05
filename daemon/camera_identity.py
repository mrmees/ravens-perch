"""
Ravens Perch - Camera identity resolution.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

IDENTITY_SERIAL = "serial"
IDENTITY_USB_PATH = "usb_path"
IDENTITY_LEGACY = "legacy"

REJECTION_NO_STABLE_PATH = "No stable USB port path available"


@dataclass
class DeviceInfo:
    """Raw camera device facts collected from V4L2, sysfs, and udev links."""

    path: str
    hardware_name: str
    serial_number: Optional[str]
    hardware_id: str
    real_path: Optional[str] = None
    by_path: Optional[str] = None
    by_id: Optional[str] = None


@dataclass
class ResolvedDevice:
    """A device after identity resolution.

    hardware_id is the value to persist into the legacy unique DB column for
    new rows; DeviceInfo.hardware_id keeps the pre-identity legacy value.
    """

    device: DeviceInfo
    identity_key: Optional[str]
    identity_strategy: Optional[str]
    hardware_id: Optional[str]
    friendly_name: str
    rejection_reason: Optional[str] = None

    @property
    def is_rejected(self) -> bool:
        return self.rejection_reason is not None

    @property
    def is_ignore_eligible(self) -> bool:
        return self.identity_strategy == IDENTITY_SERIAL and not self.is_rejected


def legacy_hardware_id(hardware_name: str, serial_number: Optional[str]) -> str:
    """Return the pre-identity-key hardware ID format."""
    return f"{hardware_name}-{serial_number}" if serial_number else hardware_name


def _serial_group(device: DeviceInfo) -> Optional[Tuple[str, str]]:
    if not device.serial_number:
        return None
    return (device.hardware_name, device.serial_number)


def _by_path_basename(by_path: str) -> str:
    return Path(by_path).name


def _serial_identity(device: DeviceInfo) -> str:
    return f"serial:{device.hardware_name}:{device.serial_number}"


def _usb_path_identity(device: DeviceInfo) -> str:
    return f"usb-path:{device.hardware_name}:{_by_path_basename(device.by_path or '')}"


def _default_friendly_name(device: DeviceInfo, identity_strategy: str) -> str:
    if identity_strategy == IDENTITY_USB_PATH:
        return f"USB: {device.hardware_name}"
    return device.hardware_name


def resolve_device_identities(devices: Iterable[DeviceInfo]) -> List[ResolvedDevice]:
    """Resolve stable identity for a batch of currently detected devices.

    Resolution is intentionally batch-local. A duplicate-serial camera may
    resolve by serial when alone and by USB path when its twin is present; this
    module does not remember previously seen duplicate serials.
    """
    device_list = list(devices)
    serial_counts = Counter(group for group in (_serial_group(device) for device in device_list) if group)
    resolved: List[ResolvedDevice] = []

    for device in device_list:
        serial_group = _serial_group(device)
        if serial_group and serial_counts[serial_group] == 1:
            identity_key = _serial_identity(device)
            resolved.append(
                ResolvedDevice(
                    device=device,
                    identity_key=identity_key,
                    identity_strategy=IDENTITY_SERIAL,
                    hardware_id=identity_key,
                    friendly_name=_default_friendly_name(device, IDENTITY_SERIAL),
                )
            )
            continue

        if not device.by_path:
            resolved.append(
                ResolvedDevice(
                    device=device,
                    identity_key=None,
                    identity_strategy=None,
                    hardware_id=None,
                    friendly_name=device.hardware_name,
                    rejection_reason=REJECTION_NO_STABLE_PATH,
                )
            )
            continue

        identity_key = _usb_path_identity(device)
        resolved.append(
            ResolvedDevice(
                device=device,
                identity_key=identity_key,
                identity_strategy=IDENTITY_USB_PATH,
                hardware_id=identity_key,
                friendly_name=_default_friendly_name(device, IDENTITY_USB_PATH),
            )
        )

    return resolved
