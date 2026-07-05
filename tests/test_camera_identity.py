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
        self.assertTrue(resolved[0].is_ignore_eligible)

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
        self.assertFalse(resolved[0].is_ignore_eligible)

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
        self.assertIsNone(resolved[0].identity_strategy)
        self.assertIsNone(resolved[0].identity_key)
        self.assertEqual(resolved[0].rejection_reason, REJECTION_NO_STABLE_PATH)

    def test_duplicate_serial_without_by_path_is_rejected(self):
        resolved = resolve_device_identities([
            device("/dev/video0", serial="DUP", by_path="/dev/v4l/by-path/pci-1-index0"),
            device("/dev/video2", serial="DUP", by_path=None),
        ])

        self.assertFalse(resolved[0].is_rejected)
        self.assertTrue(resolved[1].is_rejected)
        self.assertIsNone(resolved[1].identity_strategy)
        self.assertEqual(resolved[1].rejection_reason, REJECTION_NO_STABLE_PATH)


if __name__ == "__main__":
    unittest.main()
