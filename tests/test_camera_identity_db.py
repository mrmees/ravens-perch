import sqlite3
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

    def _reset_database_file(self):
        db.close_thread_connection()
        for path in (
            self.db_path,
            Path(f"{self.db_path}-wal"),
            Path(f"{self.db_path}-shm"),
        ):
            if path.exists():
                path.unlink()

    def _create_pre_task3_cameras_table(self):
        self._reset_database_file()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE cameras (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hardware_id TEXT UNIQUE NOT NULL,
                    hardware_name TEXT NOT NULL,
                    serial_number TEXT,
                    friendly_name TEXT,
                    device_path TEXT,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP,
                    connected BOOLEAN DEFAULT FALSE,
                    enabled BOOLEAN DEFAULT TRUE,
                    moonraker_uid TEXT
                )
            """)
            conn.commit()

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

    def test_pre_task3_serial_camera_rows_migrate_to_serial_identity(self):
        self._create_pre_task3_cameras_table()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cameras (hardware_id, hardware_name, serial_number, friendly_name)
                VALUES ('LegacyCam-ABC123', 'LegacyCam', 'ABC123', 'Legacy Camera')
                """
            )
            conn.commit()

        db.init_db()

        camera = db.get_camera_by_identity_key("serial:LegacyCam:ABC123")
        self.assertEqual(camera["hardware_id"], "LegacyCam-ABC123")
        self.assertEqual(camera["identity_key"], "serial:LegacyCam:ABC123")
        self.assertEqual(camera["identity_strategy"], "serial")
        self.assertEqual(camera["reported_serial_number"], "ABC123")

    def test_migration_reports_duplicate_camera_identity_keys_before_index_creation(self):
        self._create_pre_task3_cameras_table()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cameras (hardware_id, hardware_name, serial_number, friendly_name)
                VALUES ('LegacyCam-A', 'LegacyCam', 'ABC123', 'Legacy Camera A')
                """
            )
            conn.execute(
                """
                INSERT INTO cameras (hardware_id, hardware_name, serial_number, friendly_name)
                VALUES ('LegacyCam-B', 'LegacyCam', 'ABC123', 'Legacy Camera B')
                """
            )
            conn.commit()

        with self.assertRaisesRegex(
            RuntimeError,
            "Duplicate identity_key values in cameras: serial:LegacyCam:ABC123",
        ):
            db.init_db()

    def test_pre_task3_no_serial_camera_rows_remain_legacy(self):
        self._create_pre_task3_cameras_table()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cameras (hardware_id, hardware_name, serial_number, friendly_name)
                VALUES ('LegacyCam', 'LegacyCam', NULL, 'Legacy Camera')
                """
            )
            conn.commit()

        db.init_db()

        camera = db.get_camera_by_identity_key("LegacyCam")
        self.assertEqual(camera["identity_key"], "LegacyCam")
        self.assertEqual(camera["identity_strategy"], "legacy")
        self.assertEqual(camera["reported_serial_number"], None)

    def test_pre_task3_ignored_camera_rows_backfill_identity_key(self):
        self._reset_database_file()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE ignored_cameras (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hardware_id TEXT UNIQUE NOT NULL,
                    hardware_name TEXT,
                    reason TEXT,
                    ignored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                """
                INSERT INTO ignored_cameras (hardware_id, hardware_name, reason)
                VALUES ('serial:LegacyCam:ABC123', 'LegacyCam', 'Ignored before migration')
                """
            )
            conn.commit()

        db.init_db()

        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT identity_key FROM ignored_cameras WHERE hardware_id = ?",
                ("serial:LegacyCam:ABC123",),
            ).fetchone()
        self.assertEqual(row["identity_key"], "serial:LegacyCam:ABC123")
        self.assertTrue(db.is_camera_ignored("serial:LegacyCam:ABC123"))

    def test_migration_reports_duplicate_ignored_identity_keys_before_index_creation(self):
        self._reset_database_file()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE ignored_cameras (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    hardware_id TEXT UNIQUE NOT NULL,
                    identity_key TEXT,
                    hardware_name TEXT,
                    reason TEXT,
                    ignored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                """
                INSERT INTO ignored_cameras (hardware_id, identity_key, hardware_name)
                VALUES ('LegacyCam-A', 'duplicate-identity', 'Legacy Camera A')
                """
            )
            conn.execute(
                """
                INSERT INTO ignored_cameras (hardware_id, identity_key, hardware_name)
                VALUES ('LegacyCam-B', 'duplicate-identity', 'Legacy Camera B')
                """
            )
            conn.commit()

        with self.assertRaisesRegex(
            RuntimeError,
            "Duplicate identity_key values in ignored_cameras: duplicate-identity",
        ):
            db.init_db()

    def test_old_create_camera_signature_with_serial_preserves_legacy_hardware_id(self):
        camera_id = db.create_camera(
            "LegacyCam",
            "ABC123",
            device_path="/dev/video0",
        )

        camera = db.get_camera_by_identity_key("serial:LegacyCam:ABC123")
        self.assertEqual(camera["id"], camera_id)
        self.assertEqual(camera["identity_key"], "serial:LegacyCam:ABC123")
        self.assertEqual(camera["identity_strategy"], "serial")
        self.assertEqual(camera["hardware_id"], "LegacyCam-ABC123")

    def test_old_create_camera_signature_with_serial_can_be_found_by_legacy_hardware_id(self):
        camera_id = db.create_camera(
            "LegacyCam",
            "ABC123",
            device_path="/dev/video0",
        )

        camera = db.get_camera_by_hardware_id("LegacyCam-ABC123")
        self.assertEqual(camera["id"], camera_id)
        self.assertEqual(camera["identity_key"], "serial:LegacyCam:ABC123")

    def test_old_create_camera_signature_with_serial_reconnect_preserves_settings(self):
        camera_id = db.create_camera(
            "LegacyCam",
            "ABC123",
            device_path="/dev/video0",
        )
        db.save_camera_settings(
            camera_id,
            {
                "resolution": "1920x1080",
                "framerate": 15,
            },
        )

        reconnected_id = db.create_camera(
            "LegacyCam",
            "ABC123",
            device_path="/dev/video3",
        )

        camera = db.get_camera_by_identity_key("serial:LegacyCam:ABC123")
        settings = db.get_camera_settings(camera_id)
        self.assertEqual(reconnected_id, camera_id)
        self.assertEqual(camera["id"], camera_id)
        self.assertEqual(camera["hardware_id"], "LegacyCam-ABC123")
        self.assertEqual(camera["identity_strategy"], "serial")
        self.assertEqual(camera["device_path"], "/dev/video3")
        self.assertEqual(settings["resolution"], "1920x1080")
        self.assertEqual(settings["framerate"], 15)

    def test_create_camera_falls_back_to_hardware_id_after_legacy_unique_collision(self):
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO cameras (
                    hardware_id, identity_key, identity_strategy, hardware_name,
                    serial_number, friendly_name
                )
                VALUES (
                    'LegacyCam-ABC123', 'legacy-row-before-identity-migration',
                    'legacy', 'LegacyCam', 'ABC123', 'Legacy Camera'
                )
                """
            )
            existing_id = cursor.lastrowid
            conn.commit()

        camera_id = db.create_camera(
            "LegacyCam",
            "ABC123",
            device_path="/dev/video2",
        )

        camera = db.get_camera_by_hardware_id("LegacyCam-ABC123")
        self.assertEqual(camera_id, existing_id)
        self.assertEqual(camera["id"], existing_id)
        self.assertEqual(camera["device_path"], "/dev/video2")

    def test_old_create_camera_signature_without_serial_reconnects_legacy_identity(self):
        camera_id = db.create_camera(
            "NoSerialCam",
            None,
            device_path="/dev/video0",
        )

        camera = db.get_camera_by_identity_key("NoSerialCam")
        self.assertEqual(camera["id"], camera_id)
        self.assertEqual(camera["identity_key"], "NoSerialCam")
        self.assertEqual(camera["identity_strategy"], "legacy")
        self.assertEqual(camera["hardware_id"], "NoSerialCam")
        self.assertEqual(camera["device_path"], "/dev/video0")

        reconnected_id = db.create_camera(
            "NoSerialCam",
            None,
            device_path="/dev/video2",
        )

        camera = db.get_camera_by_identity_key("NoSerialCam")
        with db.get_connection() as conn:
            camera_count = conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
        self.assertEqual(reconnected_id, camera_id)
        self.assertEqual(camera["device_path"], "/dev/video2")
        self.assertEqual(camera_count, 1)

    def test_old_create_camera_signature_reconnects_migrated_serial_row(self):
        self._create_pre_task3_cameras_table()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cameras (hardware_id, hardware_name, serial_number, friendly_name)
                VALUES ('LegacyCam-ABC123', 'LegacyCam', 'ABC123', 'Legacy Camera')
                """
            )
            conn.commit()

        db.init_db()
        migrated = db.get_camera_by_identity_key("serial:LegacyCam:ABC123")

        reconnected_id = db.create_camera(
            "LegacyCam",
            "ABC123",
            device_path="/dev/video2",
        )

        camera = db.get_camera_by_identity_key("serial:LegacyCam:ABC123")
        with db.get_connection() as conn:
            camera_count = conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0]
        self.assertEqual(reconnected_id, migrated["id"])
        self.assertEqual(camera["id"], migrated["id"])
        self.assertEqual(camera["device_path"], "/dev/video2")
        self.assertEqual(camera_count, 1)

    def test_create_camera_reconnects_by_identity_key_and_refreshes_metadata(self):
        first_id = db.create_camera(
            hardware_name="C270 HD WEBCAM",
            serial_number=None,
            device_path="/dev/video0",
            identity_key="serial:C270 HD WEBCAM:ABC123",
            identity_strategy="serial",
            by_path="/dev/v4l/by-path/pci-1-index0",
            reported_serial_number="ABC123",
        )

        second_id = db.create_camera(
            hardware_name="C270 HD WEBCAM",
            serial_number=None,
            device_path="/dev/video2",
            identity_key="serial:C270 HD WEBCAM:ABC123",
            identity_strategy="serial",
            by_path="/dev/v4l/by-path/pci-2-index0",
            by_id="/dev/v4l/by-id/usb-C270-HD-WEBCAM-ABC123",
            reported_serial_number="ABC123",
        )

        camera = db.get_camera_by_identity_key("serial:C270 HD WEBCAM:ABC123")
        self.assertEqual(second_id, first_id)
        self.assertEqual(camera["device_path"], "/dev/video2")
        self.assertEqual(camera["by_path"], "/dev/v4l/by-path/pci-2-index0")
        self.assertEqual(camera["by_id"], "/dev/v4l/by-id/usb-C270-HD-WEBCAM-ABC123")
        self.assertEqual(camera["reported_serial_number"], "ABC123")

    def test_ignore_camera_uses_identity_key(self):
        db.ignore_camera("serial:C270 HD WEBCAM:ABC123", "C270 HD WEBCAM", "Ignored by user")

        self.assertTrue(db.is_camera_ignored("serial:C270 HD WEBCAM:ABC123"))
        self.assertFalse(db.is_camera_ignored("serial:C270 HD WEBCAM:OTHER"))

    def test_delete_camera_completely_returns_identity_key_for_ignore_list(self):
        self._create_pre_task3_cameras_table()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO cameras (hardware_id, hardware_name, serial_number, friendly_name)
                VALUES ('LegacyCam-ABC123', 'LegacyCam', 'ABC123', 'Legacy Camera')
                """
            )
            conn.commit()

        db.init_db()
        camera = db.get_camera_by_identity_key("serial:LegacyCam:ABC123")

        success, deleted_identity_key = db.delete_camera_completely(camera["id"])
        db.ignore_camera(deleted_identity_key, "LegacyCam", "Deleted by user")

        self.assertTrue(success)
        self.assertEqual(deleted_identity_key, "serial:LegacyCam:ABC123")
        self.assertTrue(db.is_camera_ignored("serial:LegacyCam:ABC123"))


if __name__ == "__main__":
    unittest.main()
