# Identical USB Camera Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support multiple identical USB cameras, including missing-serial and duplicate-serial devices, by resolving stable identity from serials when unique and `/dev/v4l/by-path` when needed.

**Architecture:** Add a focused identity module that owns device identity decisions, then update probing, database persistence, daemon monitoring, manual scan, and UI behavior to use canonical `identity_key`. Startup, polling, hotplug, and manual scan all resolve a batch of detected devices before connecting cameras.

**Tech Stack:** Python dataclasses, SQLite migrations, Flask/Jinja templates, `unittest`, `unittest.mock`, V4L2/udev subprocess probing.

---

## File Structure

- Create `daemon/camera_identity.py`: owns identity dataclasses, legacy hardware ID helper, batch resolver, rejection reasons, and default generated names.
- Modify `daemon/camera_manager.py`: import shared identity dataclasses, enrich `get_device_info()` with real path/by-path/by-id metadata, resolve batches in `CameraMonitor`, and keep rejected device tracking compatible with resolved identities.
- Modify `daemon/db.py`: add canonical identity columns, migrate existing rows, add identity-key lookup/create helpers, and move ignore lookups to identity keys.
- Modify `daemon/main.py`: process `ResolvedDevice` objects instead of raw `DeviceInfo`, look up cameras by `identity_key`, create rows with identity metadata, and preserve current stream/Moonraker behavior.
- Modify `daemon/web_ui/routes.py`: resolve scan batches, use identity-key DB helpers, prevent ignore for USB-port cameras, and pass identity metadata when creating rows.
- Modify `daemon/web_ui/templates/partials/camera_card.html`: show concise USB-port identity indicator for port-identified cameras.
- Modify `daemon/web_ui/templates/camera_detail.html`: show identity strategy/by-path/by-id metadata and hide Ignore for port-identified cameras.
- Modify `daemon/web_ui/templates/dashboard.html`: update rejected-camera copy so unsupported by-path failures are described accurately.
- Modify `raven_settings_example.yml`: remove the obsolete statement that duplicate name/serial cameras are rejected because they are unsupported.
- Create `tests/test_camera_identity.py`: resolver unit tests.
- Create `tests/test_camera_device_info.py`: symlink metadata tests for V4L2 probing helpers.
- Create `tests/test_camera_identity_db.py`: DB migration and identity helper tests.
- Modify `tests/test_moonraker_reconcile.py`: update daemon connect tests for `ResolvedDevice`.
- Modify `tests/test_scan_route.py`: update manual scan tests for batch identity and add duplicate/no-serial coverage.
- Create `tests/test_camera_monitor_identity.py`: monitor batch-resolution tests for startup and hotplug.
- Modify `tests/test_dashboard_diagnostics_ui.py`: template checks for USB-port identity indicator and hidden Ignore action.

## Task 1: Add Identity Resolver

**Files:**
- Create: `daemon/camera_identity.py`
- Create: `tests/test_camera_identity.py`

- [ ] **Step 1: Write resolver tests**

Create `tests/test_camera_identity.py` with:

```python
import unittest

from daemon.camera_identity import (
    IDENTITY_SERIAL,
    IDENTITY_USB_PATH,
    REJECTION_NO_STABLE_PATH,
    DeviceInfo,
    legacy_hardware_id,
    resolve_device_identities,
)


def device(path, name="C270 HD WEBCAM", serial=None, by_path=None, by_id=None):
    return DeviceInfo(
        path=path,
        hardware_name=name,
        serial_number=serial,
        hardware_id=legacy_hardware_id(name, serial),
        real_path=path,
        by_path=by_path,
        by_id=by_id,
    )


class CameraIdentityTests(unittest.TestCase):
    def test_unique_serial_uses_serial_identity(self):
        resolved = resolve_device_identities([
            device("/dev/video0", serial="ABC123", by_path="/dev/v4l/by-path/pci-1-index0"),
            device("/dev/video2", name="LifeCam", serial="XYZ789", by_path="/dev/v4l/by-path/pci-2-index0"),
        ])

        self.assertEqual(resolved[0].identity_strategy, IDENTITY_SERIAL)
        self.assertEqual(resolved[0].identity_key, "serial:C270 HD WEBCAM:ABC123")
        self.assertEqual(resolved[0].hardware_id, "serial:C270 HD WEBCAM:ABC123")
        self.assertEqual(resolved[0].friendly_name, "C270 HD WEBCAM")
        self.assertFalse(resolved[0].is_rejected)

    def test_missing_serial_uses_usb_path_identity_and_usb_name_prefix(self):
        resolved = resolve_device_identities([
            device("/dev/video0", by_path="/dev/v4l/by-path/pci-0000:00:14.0-usb-0:1.3:1.0-video-index0"),
        ])

        self.assertEqual(resolved[0].identity_strategy, IDENTITY_USB_PATH)
        self.assertEqual(
            resolved[0].identity_key,
            "usb-path:C270 HD WEBCAM:pci-0000:00:14.0-usb-0:1.3:1.0-video-index0",
        )
        self.assertEqual(resolved[0].friendly_name, "USB: C270 HD WEBCAM")
        self.assertFalse(resolved[0].is_rejected)

    def test_duplicate_serial_uses_usb_path_for_each_duplicate(self):
        resolved = resolve_device_identities([
            device("/dev/video0", serial="DUP", by_path="/dev/v4l/by-path/pci-1-index0"),
            device("/dev/video2", serial="DUP", by_path="/dev/v4l/by-path/pci-2-index0"),
        ])

        self.assertEqual([item.identity_strategy for item in resolved], [IDENTITY_USB_PATH, IDENTITY_USB_PATH])
        self.assertEqual(
            [item.identity_key for item in resolved],
            [
                "usb-path:C270 HD WEBCAM:pci-1-index0",
                "usb-path:C270 HD WEBCAM:pci-2-index0",
            ],
        )
        self.assertEqual([item.friendly_name for item in resolved], ["USB: C270 HD WEBCAM", "USB: C270 HD WEBCAM"])

    def test_missing_serial_without_by_path_is_rejected(self):
        resolved = resolve_device_identities([device("/dev/video0")])

        self.assertTrue(resolved[0].is_rejected)
        self.assertIsNone(resolved[0].identity_key)
        self.assertEqual(resolved[0].rejection_reason, REJECTION_NO_STABLE_PATH)

    def test_duplicate_serial_without_by_path_is_rejected(self):
        resolved = resolve_device_identities([
            device("/dev/video0", serial="DUP", by_path="/dev/v4l/by-path/pci-1-index0"),
            device("/dev/video2", serial="DUP", by_path=None),
        ])

        self.assertFalse(resolved[0].is_rejected)
        self.assertTrue(resolved[1].is_rejected)
        self.assertEqual(resolved[1].rejection_reason, REJECTION_NO_STABLE_PATH)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run resolver tests to verify failure**

Run:

```bash
python -m pytest tests/test_camera_identity.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'daemon.camera_identity'`.

- [ ] **Step 3: Implement identity module**

Create `daemon/camera_identity.py` with:

```python
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
    """A device after identity resolution."""
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
    """Resolve stable identity for a batch of currently detected devices."""
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
```

- [ ] **Step 4: Run resolver tests to verify pass**

Run:

```bash
python -m pytest tests/test_camera_identity.py -v
```

Expected: PASS, 5 tests passed.

- [ ] **Step 5: Commit identity resolver**

Run:

```bash
git add daemon/camera_identity.py tests/test_camera_identity.py
git commit -m "feat: add USB camera identity resolver"
```

## Task 2: Enrich Device Probing With V4L Symlink Metadata

**Files:**
- Modify: `daemon/camera_manager.py`
- Create: `tests/test_camera_device_info.py`

- [ ] **Step 1: Write symlink helper tests**

Create `tests/test_camera_device_info.py` with:

```python
import tempfile
import unittest
from pathlib import Path

from daemon.camera_manager import _find_v4l_symlink_for_device


class CameraDeviceInfoTests(unittest.TestCase):
    def test_find_v4l_symlink_for_device_matches_resolved_real_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_device = root / "video0"
            real_device.touch()
            link_dir = root / "by-path"
            link_dir.mkdir()
            matching_link = link_dir / "pci-1-index0"
            matching_link.symlink_to(real_device)

            self.assertEqual(
                _find_v4l_symlink_for_device(str(real_device), link_dir),
                str(matching_link),
            )

    def test_find_v4l_symlink_for_device_returns_none_without_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_device = root / "video0"
            other_device = root / "video1"
            real_device.touch()
            other_device.touch()
            link_dir = root / "by-path"
            link_dir.mkdir()
            (link_dir / "pci-2-index0").symlink_to(other_device)

            self.assertIsNone(_find_v4l_symlink_for_device(str(real_device), link_dir))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run symlink tests to verify failure**

Run:

```bash
python -m pytest tests/test_camera_device_info.py -v
```

Expected: FAIL with `ImportError` for `_find_v4l_symlink_for_device`.

- [ ] **Step 3: Move `DeviceInfo` import and add symlink helper**

In `daemon/camera_manager.py`, replace the local `DeviceInfo` dataclass with an import:

```python
from .camera_identity import DeviceInfo, legacy_hardware_id, resolve_device_identities
```

Remove this block:

```python
@dataclass
class DeviceInfo:
    """Camera device information."""
    path: str
    hardware_name: str
    serial_number: Optional[str]
    hardware_id: str
```

Add this helper near `get_device_info()`:

```python
def _find_v4l_symlink_for_device(device_path: str, symlink_dir: Path) -> Optional[str]:
    """Return the first symlink in symlink_dir that resolves to device_path."""
    try:
        real_device = Path(device_path).resolve()
        if not symlink_dir.exists():
            return None

        for link in sorted(symlink_dir.iterdir()):
            try:
                if link.resolve() == real_device:
                    return str(link)
            except OSError:
                continue
    except OSError as e:
        logger.debug(f"Failed to resolve V4L symlink for {device_path}: {e}")
    return None
```

- [ ] **Step 4: Enrich `get_device_info()` return value**

In `get_device_info()`, replace hardware ID generation and return construction with:

```python
        hardware_id = legacy_hardware_id(hardware_name, serial_number)
        real_path = str(Path(device_path).resolve())
        by_path = _find_v4l_symlink_for_device(device_path, Path("/dev/v4l/by-path"))
        by_id = _find_v4l_symlink_for_device(device_path, Path("/dev/v4l/by-id"))

        return DeviceInfo(
            path=device_path,
            hardware_name=hardware_name,
            serial_number=serial_number,
            hardware_id=hardware_id,
            real_path=real_path,
            by_path=by_path,
            by_id=by_id,
        )
```

- [ ] **Step 5: Run symlink and resolver tests**

Run:

```bash
python -m pytest tests/test_camera_device_info.py tests/test_camera_identity.py -v
```

Expected: PASS for both files.

- [ ] **Step 6: Commit probing metadata**

Run:

```bash
git add daemon/camera_manager.py tests/test_camera_device_info.py
git commit -m "feat: capture V4L camera identity links"
```

## Task 3: Add Database Identity Columns And Helpers

**Files:**
- Modify: `daemon/db.py`
- Create: `tests/test_camera_identity_db.py`

- [ ] **Step 1: Write DB identity tests**

Create `tests/test_camera_identity_db.py` with:

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import daemon.db as db


class CameraIdentityDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "ravens-perch.db"
        self.database_patch = patch.object(db, "DATABASE_PATH", self.db_path)
        self.data_dir_patch = patch.object(db, "DATA_DIR", self.db_path.parent)
        self.database_patch.start()
        self.data_dir_patch.start()
        db.close_thread_connection()
        db.init_db()

    def tearDown(self):
        db.close_thread_connection()
        self.data_dir_patch.stop()
        self.database_patch.stop()
        self.tmp.cleanup()

    def test_create_camera_persists_identity_metadata(self):
        camera_id = db.create_camera(
            hardware_name="C270 HD WEBCAM",
            serial_number=None,
            friendly_name="USB: C270 HD WEBCAM",
            device_path="/dev/video0",
            identity_key="usb-path:C270 HD WEBCAM:pci-1-index0",
            identity_strategy="usb_path",
            by_path="/dev/v4l/by-path/pci-1-index0",
            by_id=None,
            reported_serial_number=None,
        )

        camera = db.get_camera_by_identity_key("usb-path:C270 HD WEBCAM:pci-1-index0")
        self.assertEqual(camera["id"], camera_id)
        self.assertEqual(camera["hardware_id"], "usb-path:C270 HD WEBCAM:pci-1-index0")
        self.assertEqual(camera["identity_key"], "usb-path:C270 HD WEBCAM:pci-1-index0")
        self.assertEqual(camera["identity_strategy"], "usb_path")
        self.assertEqual(camera["by_path"], "/dev/v4l/by-path/pci-1-index0")
        self.assertIsNone(camera["reported_serial_number"])

    def test_existing_legacy_rows_are_backfilled(self):
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO cameras (hardware_id, hardware_name, serial_number, friendly_name)
                VALUES ('LegacyCam', 'LegacyCam', NULL, 'Legacy Camera')
                """
            )
            conn.commit()

        db.init_db()

        camera = db.get_camera_by_identity_key("LegacyCam")
        self.assertEqual(camera["identity_strategy"], "legacy")
        self.assertEqual(camera["reported_serial_number"], None)

    def test_ignore_camera_uses_identity_key(self):
        db.ignore_camera("serial:C270 HD WEBCAM:ABC123", "C270 HD WEBCAM", "Ignored by user")

        self.assertTrue(db.is_camera_ignored("serial:C270 HD WEBCAM:ABC123"))
        self.assertFalse(db.is_camera_ignored("serial:C270 HD WEBCAM:OTHER"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run DB tests to verify failure**

Run:

```bash
python -m pytest tests/test_camera_identity_db.py -v
```

Expected: FAIL with missing `get_camera_by_identity_key` or missing columns.

- [ ] **Step 3: Add camera and ignored-camera columns to initial schema**

In `daemon/db.py`, update the `CREATE TABLE IF NOT EXISTS cameras` SQL to include:

```sql
                identity_key TEXT UNIQUE,
                identity_strategy TEXT DEFAULT 'legacy',
                by_path TEXT,
                by_id TEXT,
                reported_serial_number TEXT,
```

Place these columns after `hardware_id TEXT UNIQUE NOT NULL`.

Update `CREATE TABLE IF NOT EXISTS ignored_cameras` to include:

```sql
                identity_key TEXT UNIQUE,
```

Place it after `hardware_id TEXT UNIQUE NOT NULL`.

- [ ] **Step 4: Add migration helper and backfill**

In `init_db()` after the `camera_settings` migration block, add:

```python
        cursor.execute("PRAGMA table_info(cameras)")
        camera_columns = {row['name'] for row in cursor.fetchall()}
        camera_new_columns = [
            ("identity_key", "TEXT"),
            ("identity_strategy", "TEXT DEFAULT 'legacy'"),
            ("by_path", "TEXT"),
            ("by_id", "TEXT"),
            ("reported_serial_number", "TEXT"),
        ]
        for col_name, col_def in camera_new_columns:
            if col_name not in camera_columns:
                cursor.execute(f"ALTER TABLE cameras ADD COLUMN {col_name} {col_def}")
                logger.info(f"Added column {col_name} to cameras")

        cursor.execute("""
            UPDATE cameras
            SET identity_key = hardware_id
            WHERE identity_key IS NULL OR identity_key = ''
        """)
        cursor.execute("""
            UPDATE cameras
            SET identity_strategy = 'legacy'
            WHERE identity_strategy IS NULL OR identity_strategy = ''
        """)
        cursor.execute("""
            UPDATE cameras
            SET reported_serial_number = serial_number
            WHERE reported_serial_number IS NULL AND serial_number IS NOT NULL
        """)
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cameras_identity_key ON cameras(identity_key)")

        cursor.execute("PRAGMA table_info(ignored_cameras)")
        ignored_columns = {row['name'] for row in cursor.fetchall()}
        if "identity_key" not in ignored_columns:
            cursor.execute("ALTER TABLE ignored_cameras ADD COLUMN identity_key TEXT")
            logger.info("Added column identity_key to ignored_cameras")
        cursor.execute("""
            UPDATE ignored_cameras
            SET identity_key = hardware_id
            WHERE identity_key IS NULL OR identity_key = ''
        """)
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ignored_cameras_identity_key ON ignored_cameras(identity_key)")
```

- [ ] **Step 5: Add identity-key DB helpers and update create**

Add after `get_camera_by_hardware_id()`:

```python
def get_camera_by_identity_key(identity_key: str) -> Optional[Dict]:
    """Lookup camera by canonical identity key."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cameras WHERE identity_key = ?", (identity_key,))
        row = cursor.fetchone()
        return dict(row) if row else None
```

Replace `create_camera()` signature and identity setup with:

```python
def create_camera(hardware_name: str, serial_number: Optional[str],
                  friendly_name: Optional[str] = None,
                  device_path: Optional[str] = None,
                  identity_key: Optional[str] = None,
                  identity_strategy: str = "legacy",
                  by_path: Optional[str] = None,
                  by_id: Optional[str] = None,
                  reported_serial_number: Optional[str] = None) -> int:
    """Create a new camera record. Returns the camera ID.

    If camera with same identity_key already exists, returns existing ID.
    """
    if identity_key is None:
        identity_key = f"{hardware_name}-{serial_number}" if serial_number else hardware_name
        identity_strategy = "legacy"
    hardware_id = identity_key
    if reported_serial_number is None:
        reported_serial_number = serial_number
    if not friendly_name:
        friendly_name = hardware_name
```

Update the insert and existing-row select inside `create_camera()`:

```python
        cursor.execute("""
            INSERT OR IGNORE INTO cameras (
                hardware_id, identity_key, identity_strategy, hardware_name,
                serial_number, reported_serial_number, by_path, by_id,
                friendly_name, device_path, connected, last_seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
        """, (
            hardware_id, identity_key, identity_strategy, hardware_name,
            serial_number, reported_serial_number, by_path, by_id,
            friendly_name, device_path,
        ))

        if cursor.rowcount == 0:
            cursor.execute("SELECT id FROM cameras WHERE identity_key = ?", (identity_key,))
            camera_id = cursor.fetchone()[0]
            cursor.execute("""
                UPDATE cameras
                SET connected = 1,
                    device_path = ?,
                    identity_strategy = ?,
                    by_path = ?,
                    by_id = ?,
                    reported_serial_number = ?,
                    last_seen = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (device_path, identity_strategy, by_path, by_id, reported_serial_number, camera_id))
```

- [ ] **Step 6: Update ignore helpers to use identity keys**

Replace ignore helper implementations with:

```python
def is_camera_ignored(identity_key: str) -> bool:
    """Check if an identity key is in the ignore list."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM ignored_cameras WHERE identity_key = ?",
            (identity_key,)
        )
        return cursor.fetchone() is not None


def ignore_camera(identity_key: str, hardware_name: str = None, reason: str = None) -> bool:
    """Add a serial-identified camera to the ignore list."""
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO ignored_cameras (hardware_id, identity_key, hardware_name, reason)
                VALUES (?, ?, ?, ?)
            """, (identity_key, identity_key, hardware_name, reason))
            conn.commit()
            logger.info(f"Added camera to ignore list: {identity_key}")
            return True
        except sqlite3.IntegrityError:
            return True


def unignore_camera(identity_key: str) -> bool:
    """Remove an identity key from the ignore list."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM ignored_cameras WHERE identity_key = ?",
            (identity_key,)
        )
        conn.commit()
        return cursor.rowcount > 0
```

- [ ] **Step 7: Run DB tests**

Run:

```bash
python -m pytest tests/test_camera_identity_db.py -v
```

Expected: PASS, 3 tests passed.

- [ ] **Step 8: Commit DB identity schema**

Run:

```bash
git add daemon/db.py tests/test_camera_identity_db.py
git commit -m "feat: persist camera identity keys"
```

## Task 4: Update Daemon Connection Logic

**Files:**
- Modify: `daemon/main.py`
- Modify: `tests/test_moonraker_reconcile.py`

- [ ] **Step 1: Update daemon connect test to use `ResolvedDevice`**

In `tests/test_moonraker_reconcile.py`, add imports:

```python
from daemon.camera_identity import IDENTITY_SERIAL, DeviceInfo, ResolvedDevice
```

In `test_camera_connect_records_effective_standby_framerate`, replace `device = SimpleNamespace(...)` with:

```python
        device = ResolvedDevice(
            device=DeviceInfo(
                path="/dev/video0",
                hardware_name="USB Camera",
                serial_number="abc",
                hardware_id="USB Camera-abc",
                real_path="/dev/video0",
                by_path="/dev/v4l/by-path/pci-1-index0",
                by_id="/dev/v4l/by-id/usb-camera-index0",
            ),
            identity_key="serial:USB Camera:abc",
            identity_strategy=IDENTITY_SERIAL,
            hardware_id="serial:USB Camera:abc",
            friendly_name="USB Camera",
        )
```

Replace patches:

```python
            patch("daemon.main.db.is_camera_ignored", return_value=False),
            patch(
                "daemon.main.db.get_camera_by_identity_key",
                return_value={
                    "id": 3,
                    "connected": False,
                    "device_path": None,
                    "friendly_name": "USB Camera",
                },
            ),
```

- [ ] **Step 2: Run updated daemon test to verify failure**

Run:

```bash
python -m pytest tests/test_moonraker_reconcile.py::MoonrakerReconcileTests::test_camera_connect_records_effective_standby_framerate -v
```

Expected: FAIL because `_on_camera_connected()` still expects raw `DeviceInfo` fields.

- [ ] **Step 3: Update imports and `_on_camera_connected()` signature**

In `daemon/main.py`, replace the camera manager import of `DeviceInfo` with `ResolvedDevice` from identity:

```python
from .camera_identity import IDENTITY_SERIAL, ResolvedDevice
```

Keep the existing camera manager import for monitor/probing functions without `DeviceInfo`.

Change the method signature:

```python
    def _on_camera_connected(self, resolved_device: ResolvedDevice):
        """Handle camera connection event."""
        device_info = resolved_device.device
        logger.info(f"Camera connected: {device_info.hardware_name} at {device_info.path}")
```

- [ ] **Step 4: Replace identity lookup and create logic**

Inside `_on_camera_connected()`, replace the ignore and lookup block with:

```python
            if resolved_device.is_rejected:
                add_rejected_camera(
                    device_path=device_info.path,
                    hardware_name=device_info.hardware_name,
                    hardware_id=device_info.hardware_id,
                    reason=resolved_device.rejection_reason or "Unsupported camera",
                    identity_key=resolved_device.identity_key,
                    identity_strategy=resolved_device.identity_strategy,
                )
                return

            if resolved_device.identity_strategy == IDENTITY_SERIAL and db.is_camera_ignored(resolved_device.identity_key):
                logger.info(f"Camera {device_info.hardware_name} is ignored, skipping")
                return

            camera = db.get_camera_by_identity_key(resolved_device.identity_key)
```

Delete the duplicate-hardware-id rejection block because the resolver prevents that collision.

In the new-camera branch, replace `db.create_camera(...)` with:

```python
                camera_id = db.create_camera(
                    hardware_name=device_info.hardware_name,
                    serial_number=device_info.serial_number,
                    friendly_name=resolved_device.friendly_name,
                    device_path=device_info.path,
                    identity_key=resolved_device.identity_key,
                    identity_strategy=resolved_device.identity_strategy,
                    by_path=device_info.by_path,
                    by_id=device_info.by_id,
                    reported_serial_number=device_info.serial_number,
                )
```

- [ ] **Step 5: Run daemon connect test**

Run:

```bash
python -m pytest tests/test_moonraker_reconcile.py::MoonrakerReconcileTests::test_camera_connect_records_effective_standby_framerate -v
```

Expected: PASS.

- [ ] **Step 6: Commit daemon connection update**

Run:

```bash
git add daemon/main.py tests/test_moonraker_reconcile.py
git commit -m "feat: connect cameras by identity key"
```

## Task 5: Resolve CameraMonitor Batches

**Files:**
- Modify: `daemon/camera_manager.py`
- Create: `tests/test_camera_monitor_identity.py`

- [ ] **Step 1: Write monitor startup and hotplug tests**

Create `tests/test_camera_monitor_identity.py` with:

```python
import unittest
from unittest.mock import patch

from daemon.camera_identity import IDENTITY_USB_PATH, DeviceInfo, legacy_hardware_id
from daemon.camera_manager import CameraMonitor


def device(path, by_path):
    name = "C270 HD WEBCAM"
    return DeviceInfo(
        path=path,
        hardware_name=name,
        serial_number=None,
        hardware_id=legacy_hardware_id(name, None),
        real_path=path,
        by_path=by_path,
        by_id=None,
    )


class CameraMonitorIdentityTests(unittest.TestCase):
    def test_scan_existing_resolves_batch_before_connecting(self):
        connected = []
        monitor = CameraMonitor(on_connect=connected.append, on_disconnect=lambda _path: None)

        with (
            patch("daemon.camera_manager.find_video_devices", return_value=["/dev/video0", "/dev/video2"]),
            patch(
                "daemon.camera_manager.get_device_info",
                side_effect=[
                    device("/dev/video0", "/dev/v4l/by-path/pci-1-index0"),
                    device("/dev/video2", "/dev/v4l/by-path/pci-2-index0"),
                ],
            ),
        ):
            monitor.scan_existing()

        self.assertEqual(len(connected), 2)
        self.assertEqual([item.identity_strategy for item in connected], [IDENTITY_USB_PATH, IDENTITY_USB_PATH])
        self.assertEqual(set(monitor._known_devices.values()), {item.identity_key for item in connected})

    def test_scan_current_devices_dispatches_new_identity_once(self):
        connected = []
        monitor = CameraMonitor(on_connect=connected.append, on_disconnect=lambda _path: None)

        with (
            patch("daemon.camera_manager.find_video_devices", return_value=["/dev/video0"]),
            patch(
                "daemon.camera_manager.get_device_info",
                return_value=device("/dev/video0", "/dev/v4l/by-path/pci-1-index0"),
            ),
        ):
            monitor._scan_current_devices()
            monitor._scan_current_devices()

        self.assertEqual(len(connected), 1)
        self.assertIn("/dev/video0", monitor._known_devices)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run monitor tests to verify failure**

Run:

```bash
python -m pytest tests/test_camera_monitor_identity.py -v
```

Expected: FAIL because `CameraMonitor` still dispatches raw `DeviceInfo` and lacks `_scan_current_devices()`.

- [ ] **Step 3: Update monitor known-device tracking**

In `CameraMonitor.__init__`, change the callback type and known-device comment:

```python
        on_connect: Callable[[ResolvedDevice], None],
```

and:

```python
        self._known_devices: Dict[str, str] = {}  # path -> identity_key
```

Import `ResolvedDevice` with the existing identity import:

```python
from .camera_identity import DeviceInfo, ResolvedDevice, legacy_hardware_id, resolve_device_identities
```

- [ ] **Step 4: Add batch scan method**

Add this method inside `CameraMonitor`:

```python
    def _scan_current_devices(self):
        """Resolve the current capture-device batch and dispatch new identities."""
        device_infos = []
        for device_path in find_video_devices():
            device_info = get_device_info(device_path)
            if device_info:
                device_infos.append(device_info)

        current_paths = {device.path for device in device_infos}
        for known_path in list(self._known_devices.keys()):
            if known_path not in current_paths:
                self._schedule_disconnect(known_path)

        for resolved in resolve_device_identities(device_infos):
            device_path = resolved.device.path
            current_identity = resolved.identity_key or f"rejected:{device_path}"
            known_identity = self._known_devices.get(device_path)
            if known_identity == current_identity:
                continue
            self._known_devices[device_path] = current_identity
            try:
                self.on_connect(resolved)
            except Exception as e:
                logger.error(f"Error in connect callback: {e}")
```

- [ ] **Step 5: Route polling, hotplug, and startup through batch scan**

Replace `_polling_monitor()` loop body with:

```python
            self._scan_current_devices()
            time.sleep(2)
```

In `_schedule_connect()` after USB/capture checks pass, replace the raw `get_device_info()` block with:

```python
            self._scan_current_devices()
```

Replace `scan_existing()` with:

```python
    def scan_existing(self):
        """Scan for existing cameras (call on startup)."""
        self._scan_current_devices()
```

- [ ] **Step 6: Run monitor tests**

Run:

```bash
python -m pytest tests/test_camera_monitor_identity.py -v
```

Expected: PASS, 2 tests passed.

- [ ] **Step 7: Commit monitor batch resolution**

Run:

```bash
git add daemon/camera_manager.py tests/test_camera_monitor_identity.py
git commit -m "feat: resolve camera monitor batches"
```

## Task 6: Update Manual Scan Route

**Files:**
- Modify: `daemon/web_ui/routes.py`
- Modify: `tests/test_scan_route.py`

- [ ] **Step 1: Add no-serial duplicate manual scan test**

Append this test to `ScanRouteTests` in `tests/test_scan_route.py`:

```python
    def test_scan_adds_two_same_model_no_serial_cameras_by_usb_path(self):
        app = create_app()

        def device(path, by_path):
            return SimpleNamespace(
                path=path,
                hardware_name="C270 HD WEBCAM",
                serial_number=None,
                hardware_id="C270 HD WEBCAM",
                real_path=path,
                by_path=by_path,
                by_id=None,
            )

        created = []

        def create_camera(**kwargs):
            created.append(kwargs)
            return len(created)

        with (
            app.test_request_context("/cameras/scan", method="POST"),
            patch("daemon.web_ui.routes.find_video_devices", return_value=["/dev/video0", "/dev/video2"]),
            patch(
                "daemon.web_ui.routes.get_device_info",
                side_effect=[
                    device("/dev/video0", "/dev/v4l/by-path/pci-1-index0"),
                    device("/dev/video2", "/dev/v4l/by-path/pci-2-index0"),
                ],
            ),
            patch("daemon.web_ui.routes.probe_capabilities", return_value={"mjpeg": {"640x480": [30]}}),
            patch("daemon.web_ui.routes.get_camera_by_identity_key", return_value=None),
            patch("daemon.web_ui.routes.create_camera", side_effect=create_camera),
            patch("daemon.web_ui.routes.save_camera_settings"),
            patch("daemon.web_ui.routes.save_camera_capabilities"),
            patch("daemon.web_ui.routes.auto_configure", return_value={"framerate": 30}),
            patch("daemon.web_ui.routes.get_all_cameras", return_value=[]),
            patch("daemon.web_ui.routes._start_camera_from_route", return_value=(True, None)),
            patch("daemon.web_ui.routes.get_camera_by_id", return_value={"friendly_name": "USB: C270 HD WEBCAM"}),
            patch("daemon.web_ui.routes._register_camera_with_moonraker"),
            patch("daemon.web_ui.routes.flash"),
            patch("daemon.web_ui.routes.redirect", return_value="redirected"),
            patch("daemon.web_ui.routes.url_for", return_value="/cameras/"),
        ):
            response = routes.scan_cameras()

        self.assertEqual(response, "redirected")
        self.assertEqual(len(created), 2)
        self.assertEqual(
            [camera["identity_key"] for camera in created],
            [
                "usb-path:C270 HD WEBCAM:pci-1-index0",
                "usb-path:C270 HD WEBCAM:pci-2-index0",
            ],
        )
        self.assertEqual([camera["friendly_name"] for camera in created], ["USB: C270 HD WEBCAM", "USB: C270 HD WEBCAM"])
```

- [ ] **Step 2: Update existing scan route test mocks**

In `test_scan_restarts_stream_for_existing_reconnected_camera`, add `real_path`, `by_path`, and `by_id` to `device`, and replace:

```python
            patch("daemon.web_ui.routes.is_camera_ignored", return_value=False),
            patch(
                "daemon.web_ui.routes.get_camera_by_hardware_id",
```

with:

```python
            patch("daemon.web_ui.routes.is_camera_ignored", return_value=False),
            patch(
                "daemon.web_ui.routes.get_camera_by_identity_key",
```

Set the mocked device identity-compatible values:

```python
            hardware_id="USB Camera-abc",
            real_path="/dev/video2",
            by_path="/dev/v4l/by-path/pci-1-index0",
            by_id="/dev/v4l/by-id/usb-camera-index0",
```

- [ ] **Step 3: Run scan route tests to verify failure**

Run:

```bash
python -m pytest tests/test_scan_route.py -v
```

Expected: FAIL because routes still use raw hardware IDs and per-device identity.

- [ ] **Step 4: Update route imports**

In `daemon/web_ui/routes.py`, replace `get_camera_by_hardware_id` import with `get_camera_by_identity_key`, and add:

```python
from ..camera_identity import IDENTITY_SERIAL, resolve_device_identities
```

- [ ] **Step 5: Rewrite scan loop to resolve batch first**

In `scan_cameras()`, replace the loop setup with:

```python
        devices = find_video_devices()
        device_infos = []
        for device_path in devices:
            device_info = get_device_info(device_path)
            if device_info:
                device_infos.append(device_info)

        resolved_devices = resolve_device_identities(device_infos)
        added = 0
        updated = 0

        for resolved_device in resolved_devices:
            device_info = resolved_device.device
            device_path = device_info.path

            if resolved_device.is_rejected:
                add_rejected_camera(
                    device_path=device_path,
                    hardware_name=device_info.hardware_name,
                    hardware_id=device_info.hardware_id,
                    reason=resolved_device.rejection_reason or "Unsupported camera",
                    identity_key=resolved_device.identity_key,
                    identity_strategy=resolved_device.identity_strategy,
                )
                continue

            if resolved_device.identity_strategy == IDENTITY_SERIAL and is_camera_ignored(resolved_device.identity_key):
                continue

            existing = get_camera_by_identity_key(resolved_device.identity_key)
```

Remove the old duplicate-hardware-id rejection block.

Update the create call:

```python
            camera_id = create_camera(
                hardware_name=device_info.hardware_name,
                serial_number=device_info.serial_number,
                friendly_name=resolved_device.friendly_name,
                device_path=device_path,
                identity_key=resolved_device.identity_key,
                identity_strategy=resolved_device.identity_strategy,
                by_path=device_info.by_path,
                by_id=device_info.by_id,
                reported_serial_number=device_info.serial_number,
            )
```

- [ ] **Step 6: Run scan route tests**

Run:

```bash
python -m pytest tests/test_scan_route.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit manual scan update**

Run:

```bash
git add daemon/web_ui/routes.py tests/test_scan_route.py
git commit -m "feat: scan cameras by resolved identity"
```

## Task 7: Update Rejected Camera Tracking

**Files:**
- Modify: `daemon/camera_manager.py`
- Modify: `daemon/web_ui/templates/dashboard.html`

- [ ] **Step 1: Extend rejected-camera data shape**

In `daemon/camera_manager.py`, update `RejectedCamera`:

```python
@dataclass
class RejectedCamera:
    """Information about a camera that was rejected."""
    device_path: str
    hardware_name: str
    hardware_id: str
    reason: str
    existing_camera_id: Optional[int] = None
    identity_key: Optional[str] = None
    identity_strategy: Optional[str] = None
```

Update `add_rejected_camera()` signature:

```python
def add_rejected_camera(device_path: str, hardware_name: str, hardware_id: str,
                        reason: str, existing_camera_id: Optional[int] = None,
                        identity_key: Optional[str] = None,
                        identity_strategy: Optional[str] = None):
```

Set those fields in the constructor and include them in `get_rejected_cameras()`:

```python
                'identity_key': rc.identity_key,
                'identity_strategy': rc.identity_strategy,
```

- [ ] **Step 2: Update dashboard rejected copy**

In `daemon/web_ui/templates/dashboard.html`, replace the rejected cameras help paragraph with:

```html
    <p class="rejected-cameras-help">
        These cameras could not be identified safely. Ravens Perch will not use
        volatile /dev/video paths because they can change across reboots.
        Check the reason above, then reconnect the camera or run Scan Cameras again.
    </p>
```

- [ ] **Step 3: Run dashboard template tests**

Run:

```bash
python -m pytest tests/test_dashboard_diagnostics_ui.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit rejected-camera metadata**

Run:

```bash
git add daemon/camera_manager.py daemon/web_ui/templates/dashboard.html
git commit -m "feat: show camera identity rejection reasons"
```

## Task 8: Hide Ignore For USB-Port Cameras And Show Identity UI

**Files:**
- Modify: `daemon/web_ui/routes.py`
- Modify: `daemon/web_ui/templates/partials/camera_card.html`
- Modify: `daemon/web_ui/templates/camera_detail.html`
- Modify: `daemon/web_ui/static/css/style.css`
- Modify: `tests/test_dashboard_diagnostics_ui.py`

- [ ] **Step 1: Add template tests**

Append these tests to `DashboardDiagnosticsUITests` in `tests/test_dashboard_diagnostics_ui.py`:

```python
    def test_camera_card_shows_usb_port_identity_badge(self):
        partial = CAMERA_CARD_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("identity_strategy == 'usb_path'", partial)
        self.assertIn("USB port", partial)
        self.assertIn("identity-badge", partial)

    def test_camera_detail_hides_ignore_for_usb_path_cameras(self):
        detail = (ROOT / "daemon/web_ui/templates/camera_detail.html").read_text(encoding="utf-8")

        self.assertIn("camera.identity_strategy != 'usb_path'", detail)
        self.assertIn("By-path", detail)
        self.assertIn("settings follow this USB port", detail)
```

- [ ] **Step 2: Run UI tests to verify failure**

Run:

```bash
python -m pytest tests/test_dashboard_diagnostics_ui.py -v
```

Expected: FAIL because the new UI text is absent.

- [ ] **Step 3: Add camera card badge**

In `daemon/web_ui/templates/partials/camera_card.html`, below the camera resolution block, add:

```html
        {% if camera.identity_strategy == 'usb_path' %}
            <span class="identity-badge" title="Settings follow this USB port">USB port</span>
        {% endif %}
```

- [ ] **Step 4: Add detail identity metadata and hide Ignore**

In `daemon/web_ui/templates/camera_detail.html`, in the Device info list after serial, add:

```html
                            {% if camera.identity_strategy == 'usb_path' %}
                            <dt>Identity</dt><dd>USB port</dd>
                            <dt>By-path</dt><dd>{{ camera.by_path or 'Unavailable' }}</dd>
                            <dt>Note</dt><dd>Camera settings follow this USB port/topology, not the physical camera.</dd>
                            {% elif camera.identity_strategy == 'serial' %}
                            <dt>Identity</dt><dd>Serial</dd>
                            {% endif %}
                            {% if camera.by_id %}<dt>By-id</dt><dd>{{ camera.by_id }}</dd>{% endif %}
```

Wrap the Ignore form with:

```html
                        {% if camera.identity_strategy != 'usb_path' %}
                        <form action="{{ url_for('cameras.ignore_camera_route', camera_id=camera.id) }}" method="POST"
                              onsubmit="return confirm('Ignore this camera permanently?');">
                            {{ csrf_field() }}
                            <button type="submit" class="btn btn-danger btn-sm">Ignore</button>
                        </form>
                        {% endif %}
```

- [ ] **Step 5: Add badge CSS**

Append to `daemon/web_ui/static/css/style.css`:

```css
.identity-badge {
    display: inline-flex;
    align-items: center;
    width: fit-content;
    margin-top: 0.35rem;
    padding: 0.15rem 0.45rem;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    color: var(--text-muted);
    font-size: 0.75rem;
    line-height: 1.2;
}
```

- [ ] **Step 6: Reject ignore route for USB-port cameras**

In `ignore_camera_route()`, after loading `camera`, add:

```python
    if camera.get('identity_strategy') == 'usb_path':
        flash("USB-port identified cameras cannot be ignored", "error")
        return redirect(url_for('cameras.camera_detail', camera_id=camera_id))
```

Change identity value used for ignore:

```python
    identity_key = camera.get('identity_key') or camera.get('hardware_id')
```

and:

```python
    if identity_key:
        ignore_camera(identity_key, camera_name, "Ignored by user")
```

- [ ] **Step 7: Run UI tests**

Run:

```bash
python -m pytest tests/test_dashboard_diagnostics_ui.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit UI identity behavior**

Run:

```bash
git add daemon/web_ui/routes.py daemon/web_ui/templates/partials/camera_card.html daemon/web_ui/templates/camera_detail.html daemon/web_ui/static/css/style.css tests/test_dashboard_diagnostics_ui.py
git commit -m "feat: label USB-port identified cameras"
```

## Task 9: Update Delete-With-Ignore And Example Config

**Files:**
- Modify: `daemon/web_ui/routes.py`
- Modify: `raven_settings_example.yml`

- [ ] **Step 1: Update delete-with-ignore behavior**

In `delete_camera()`, before reading `also_ignore`, add:

```python
    can_ignore = camera.get('identity_strategy') != 'usb_path'
```

Replace:

```python
    also_ignore = request.form.get('also_ignore') == 'true'
```

with:

```python
    also_ignore = can_ignore and request.form.get('also_ignore') == 'true'
```

Replace `deleted_hardware_id` handling with identity naming:

```python
    success, deleted_identity_key = delete_camera_completely(camera_id)
```

and:

```python
        if also_ignore and deleted_identity_key:
            ignore_camera(deleted_identity_key, camera_name, "Deleted by user")
```

- [ ] **Step 2: Update `delete_camera_completely()` return naming**

In `daemon/db.py`, update the function docstring and value:

```python
    Returns: (success, identity_key) - identity_key for optional ignore list
```

and:

```python
    identity_key = camera.get('identity_key') or camera.get('hardware_id')
```

Return/log `identity_key` instead of `hardware_id`.

- [ ] **Step 3: Update example config warning**

In `raven_settings_example.yml`, replace the top NOTE block with:

```yaml
# NOTE: Cameras with reliable unique serial numbers are tracked by serial.
# Identical cameras with missing or duplicate serials are tracked by USB port
# when Linux exposes a stable /dev/v4l/by-path entry.
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
python -m pytest tests/test_camera_identity_db.py tests/test_scan_route.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit delete and docs update**

Run:

```bash
git add daemon/db.py daemon/web_ui/routes.py raven_settings_example.yml
git commit -m "fix: avoid ignoring USB-port cameras"
```

## Task 10: Final Integration Verification

**Files:**
- Verify: all modified Python, template, CSS, and YAML files

- [ ] **Step 1: Run full test suite**

Run:

```bash
python -m pytest -v
```

Expected: PASS for the full suite.

- [ ] **Step 2: Run whitespace check**

Run:

```bash
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 3: Inspect final changed files**

Run:

```bash
git status --short
```

Expected: only intentional uncommitted files if the executor has not committed Task 10 notes. No unrelated dirty files should be staged or modified.

- [ ] **Step 4: Leave final state clean**

If Task 10 produced cleanup edits, inspect `git status --short`, stage only the exact files changed by the cleanup, and commit them with:

```bash
git commit -m "test: verify identical USB camera identity"
```

If there are no cleanup edits, leave the repository without a final empty commit.
