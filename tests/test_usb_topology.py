import tempfile
import unittest
from pathlib import Path

from daemon.usb_topology import get_shared_usb2_camera_warnings


class UsbTopologyWarningTests(unittest.TestCase):
    def _make_camera_sysfs(self, root: Path, video_name: str, bus_name: str, speed: str) -> None:
        class_dir = root / "class" / "video4linux" / video_name
        class_dir.mkdir(parents=True)

        root_bus = root / "devices" / "platform" / "controller" / bus_name
        device_slot = int(video_name.replace("video", "")) + 1
        device_name = f"{bus_name[3:]}-{device_slot}"
        camera_interface = root_bus / device_name / f"{device_name}:1.0"
        camera_interface.mkdir(parents=True)
        (root_bus / "speed").write_text(speed, encoding="utf-8")
        (root_bus / "busnum").write_text(bus_name[3:], encoding="utf-8")
        (root_bus / "product").write_text("EHCI Host Controller", encoding="utf-8")

        (class_dir / "device").symlink_to(camera_interface)

    def test_warns_when_multiple_enabled_cameras_share_usb2_root_bus(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._make_camera_sysfs(root, "video0", "usb6", "480")
            self._make_camera_sysfs(root, "video1", "usb6", "480")

            warnings = get_shared_usb2_camera_warnings(
                [
                    {
                        "id": 1,
                        "friendly_name": "Toolhead",
                        "device_path": "/dev/video0",
                        "connected": True,
                        "enabled": True,
                    },
                    {
                        "id": 2,
                        "friendly_name": "Chamber",
                        "device_path": "/dev/video1",
                        "connected": True,
                        "enabled": True,
                    },
                ],
                video4linux_root=root / "class" / "video4linux",
            )

        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["bus"], "usb6")
        self.assertEqual(warnings[0]["speed_mbps"], 480)
        self.assertEqual(warnings[0]["camera_names"], ["Toolhead", "Chamber"])

    def test_ignores_disabled_cameras_and_usb3_buses(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._make_camera_sysfs(root, "video0", "usb6", "480")
            self._make_camera_sysfs(root, "video1", "usb6", "480")
            self._make_camera_sysfs(root, "video2", "usb10", "5000")
            self._make_camera_sysfs(root, "video3", "usb10", "5000")

            warnings = get_shared_usb2_camera_warnings(
                [
                    {
                        "id": 1,
                        "friendly_name": "Enabled USB2",
                        "device_path": "/dev/video0",
                        "connected": True,
                        "enabled": True,
                    },
                    {
                        "id": 2,
                        "friendly_name": "Disabled USB2",
                        "device_path": "/dev/video1",
                        "connected": True,
                        "enabled": False,
                    },
                    {
                        "id": 3,
                        "friendly_name": "USB3 A",
                        "device_path": "/dev/video2",
                        "connected": True,
                        "enabled": True,
                    },
                    {
                        "id": 4,
                        "friendly_name": "USB3 B",
                        "device_path": "/dev/video3",
                        "connected": True,
                        "enabled": True,
                    },
                ],
                video4linux_root=root / "class" / "video4linux",
            )

        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
