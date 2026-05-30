import unittest
from types import SimpleNamespace
from unittest.mock import patch

from daemon.web_ui import routes
from daemon.web_ui.app import create_app


class ScanRouteTests(unittest.TestCase):
    def test_scan_restarts_stream_for_existing_reconnected_camera(self):
        app = create_app()
        recorded_framerates = []
        device = SimpleNamespace(
            path="/dev/video2",
            hardware_name="USB Camera",
            serial_number="abc",
            hardware_id="usb:abc",
        )

        with (
            app.test_request_context("/cameras/scan", method="POST"),
            patch("daemon.web_ui.routes.find_video_devices", return_value=["/dev/video2"]),
            patch("daemon.web_ui.routes.get_device_info", return_value=device),
            patch("daemon.web_ui.routes.is_camera_ignored", return_value=False),
            patch(
                "daemon.web_ui.routes.get_camera_by_hardware_id",
                return_value={
                    "id": 4,
                    "connected": False,
                    "device_path": None,
                    "friendly_name": "USB Camera",
                    "enabled": True,
                },
            ),
            patch("daemon.web_ui.routes.mark_camera_connected"),
            patch(
                "daemon.web_ui.routes.get_camera_with_settings",
                return_value={
                    "id": 4,
                    "connected": True,
                    "device_path": "/dev/video2",
                    "friendly_name": "USB Camera",
                    "enabled": True,
                    "settings": {
                        "framerate": 30,
                        "standby_enabled": True,
                        "standby_framerate": 5,
                    },
                },
            ),
            patch(
                "daemon.web_ui.routes.get_print_monitor",
                return_value=SimpleNamespace(effective_state="standby"),
            ),
            patch("daemon.web_ui.routes.start_camera_stream", return_value=(True, None)) as start_stream,
            patch("daemon.web_ui.routes.moonraker_available", return_value=False),
            patch("daemon.web_ui.routes.flash"),
            patch("daemon.web_ui.routes.redirect", return_value="redirected"),
            patch("daemon.web_ui.routes.url_for", return_value="/cameras/"),
        ):
            routes.set_effective_framerate_callback(
                lambda camera_id, settings: recorded_framerates.append((camera_id, settings["framerate"]))
            )
            try:
                response = routes.scan_cameras()
            finally:
                routes.set_effective_framerate_callback(None)

        self.assertEqual(response, "redirected")
        start_stream.assert_called_once_with(
            "/dev/video2",
            "4",
            {
                "framerate": 5,
                "standby_enabled": True,
                "standby_framerate": 5,
            },
            SimpleNamespace(effective_state="standby"),
        )
        self.assertEqual(recorded_framerates, [(4, 5)])


if __name__ == "__main__":
    unittest.main()
