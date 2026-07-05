import unittest
from unittest.mock import patch

from daemon.camera_identity import IDENTITY_USB_PATH, DeviceInfo, legacy_hardware_id
from daemon.camera_manager import (
    CameraMonitor,
    add_rejected_camera,
    clear_rejected_cameras,
    get_rejected_cameras,
)


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
    def tearDown(self):
        clear_rejected_cameras()

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

    def test_rejected_camera_includes_identity_metadata(self):
        add_rejected_camera(
            device_path="/dev/video0",
            hardware_name="C270 HD WEBCAM",
            hardware_id="C270 HD WEBCAM",
            reason="No stable USB port path available",
            identity_key=None,
            identity_strategy=None,
        )

        rejected = get_rejected_cameras()

        self.assertEqual(rejected[0]["identity_key"], None)
        self.assertEqual(rejected[0]["identity_strategy"], None)


if __name__ == "__main__":
    unittest.main()
