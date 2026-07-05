import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from daemon.camera_identity import IDENTITY_SERIAL, DeviceInfo, ResolvedDevice
from daemon.main import RavensPerchDaemon


class MoonrakerReconcileTests(unittest.TestCase):
    def test_late_moonraker_availability_initializes_worker_and_queues_connected_cameras(self):
        daemon = RavensPerchDaemon()
        daemon.running = True
        monitor = Mock()

        with (
            patch.object(daemon, "_resolve_moonraker_url", return_value="http://127.0.0.1:7125"),
            patch.object(daemon, "_start_moonraker_worker") as start_worker,
            patch("daemon.main.init_monitor", return_value=monitor),
            patch("daemon.main.db.get_setting", return_value=5),
            patch("daemon.main.db.get_all_cameras", return_value=[]),
            patch(
                "daemon.main.db.get_all_cameras_with_settings",
                return_value=[
                    {
                        "id": 7,
                        "connected": True,
                        "enabled": True,
                        "moonraker_uid": None,
                        "friendly_name": "Toolhead Camera",
                        "settings": {"rotation": 90},
                    }
                ],
            ),
            patch("daemon.main.add_log"),
        ):
            daemon._ensure_moonraker_integration()

        self.assertEqual(daemon.moonraker_url, "http://127.0.0.1:7125")
        start_worker.assert_called_once()
        monitor.set_state_change_callback.assert_called_once_with(daemon._on_print_state_change)
        monitor.start.assert_called_once()
        self.assertEqual(daemon._moonraker_queue.get_nowait(), (7, "Toolhead Camera", 90))
        with self.assertRaises(queue.Empty):
            daemon._moonraker_queue.get_nowait()

    def test_camera_connect_records_effective_standby_framerate(self):
        daemon = RavensPerchDaemon()
        daemon.print_monitor = SimpleNamespace(status=SimpleNamespace(is_printing=False))
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

        with (
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
            patch("daemon.main.db.mark_camera_connected"),
            patch(
                "daemon.main.db.get_camera_with_settings",
                return_value={
                    "id": 3,
                    "enabled": True,
                    "friendly_name": "USB Camera",
                    "settings": {
                        "framerate": 30,
                        "standby_enabled": True,
                        "standby_framerate": 5,
                    },
                },
            ),
            patch("daemon.main.start_camera_stream", return_value=(True, None)),
            patch("daemon.main.add_log"),
        ):
            daemon._on_camera_connected(device)

        self.assertEqual(daemon._camera_framerates[3], 5)

    def test_registration_worker_skips_camera_that_disconnected_while_queued(self):
        daemon = RavensPerchDaemon()
        daemon.running = True
        daemon._queued_moonraker_camera_ids.add(11)
        daemon._moonraker_queue.put((11, "Old Camera", 0))

        def disconnected_camera(_camera_id):
            daemon.running = False
            return {
                "id": 11,
                "connected": False,
                "enabled": True,
                "friendly_name": "Old Camera",
                "settings": {},
            }

        with (
            patch(
                "daemon.main.db.get_camera_with_settings",
                side_effect=disconnected_camera,
            ),
            patch("daemon.main.register_camera") as register_camera,
            patch("daemon.main.time.sleep"),
        ):
            worker = threading.Thread(target=daemon._moonraker_registration_worker)
            worker.start()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        register_camera.assert_not_called()
        self.assertNotIn(11, daemon._queued_moonraker_camera_ids)

    def test_registration_worker_passes_stream_extra_data(self):
        daemon = RavensPerchDaemon()
        daemon.running = True
        daemon._queued_moonraker_camera_ids.add(7)
        daemon._moonraker_queue.put((7, "Queued Name", 0))
        extra_data = {"ravens_perch": {"camera_id": "7"}}

        def connected_camera(_camera_id):
            daemon.running = False
            return {
                "id": 7,
                "connected": True,
                "enabled": True,
                "friendly_name": "Toolhead Camera",
                "settings": {"rotation": 90},
            }

        with (
            patch("daemon.main.db.get_camera_with_settings", side_effect=connected_camera),
            patch("daemon.main.get_system_ip", return_value="printer.local"),
            patch("daemon.main.build_stream_url", return_value="webrtc-url"),
            patch("daemon.main.build_snapshot_url", return_value="snapshot-url"),
            patch("daemon.main.build_stream_extra_data", return_value=extra_data) as build_extra,
            patch("daemon.main.register_camera", return_value=(True, "uid-7", None)) as register,
            patch("daemon.main.db.update_camera"),
            patch("daemon.main.time.sleep"),
        ):
            worker = threading.Thread(target=daemon._moonraker_registration_worker)
            worker.start()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        build_extra.assert_called_once_with("7", "printer.local")
        register.assert_called_once_with(
            "7",
            "Toolhead Camera",
            "webrtc-url",
            "snapshot-url",
            rotation=90,
            extra_data=extra_data,
        )
        self.assertNotIn(7, daemon._queued_moonraker_camera_ids)


if __name__ == "__main__":
    unittest.main()
