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
