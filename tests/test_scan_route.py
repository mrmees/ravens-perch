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
            patch("daemon.web_ui.routes.probe_capabilities", return_value={"mjpeg": {"640x480": [30]}}),
            patch("daemon.web_ui.routes.save_camera_capabilities"),
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

    def test_ignore_camera_route_ignores_migrated_serial_camera_by_identity_key(self):
        app = create_app()

        with (
            app.test_request_context("/cameras/4/ignore", method="POST"),
            patch(
                "daemon.web_ui.routes.get_camera_by_id",
                return_value={
                    "id": 4,
                    "connected": False,
                    "friendly_name": "Legacy Camera",
                    "hardware_id": "LegacyCam-ABC123",
                    "identity_key": "serial:LegacyCam:ABC123",
                    "moonraker_uid": None,
                },
            ),
            patch("daemon.web_ui.routes.ignore_camera") as ignore_camera,
            patch(
                "daemon.web_ui.routes.delete_camera_completely",
                return_value=(True, "serial:LegacyCam:ABC123"),
            ) as delete_camera_completely,
            patch("daemon.web_ui.routes.add_log"),
            patch("daemon.web_ui.routes.flash"),
            patch("daemon.web_ui.routes.redirect", return_value="redirected"),
            patch("daemon.web_ui.routes.url_for", return_value="/cameras/"),
        ):
            response = routes.ignore_camera_route(4)

        self.assertEqual(response, "redirected")
        ignore_camera.assert_called_once_with(
            "serial:LegacyCam:ABC123",
            "Legacy Camera",
            "Ignored by user",
        )
        delete_camera_completely.assert_called_once_with(4)

    def test_ignore_camera_route_rejects_usb_path_camera_without_delete_or_ignore(self):
        app = create_app()

        with (
            app.test_request_context("/cameras/4/ignore", method="POST"),
            patch(
                "daemon.web_ui.routes.get_camera_by_id",
                return_value={
                    "id": 4,
                    "connected": False,
                    "friendly_name": "USB Port Camera",
                    "hardware_id": "usb-path:USB Camera:pci-1-index0",
                    "identity_key": "usb-path:USB Camera:pci-1-index0",
                    "identity_strategy": "usb_path",
                    "moonraker_uid": None,
                },
            ),
            patch("daemon.web_ui.routes.ignore_camera") as ignore_camera,
            patch("daemon.web_ui.routes.delete_camera_completely") as delete_camera_completely,
            patch("daemon.web_ui.routes.flash") as flash,
            patch("daemon.web_ui.routes.redirect", return_value="redirected"),
            patch("daemon.web_ui.routes.url_for", return_value="/cameras/"),
        ):
            response = routes.ignore_camera_route(4)

        self.assertEqual(response, "redirected")
        ignore_camera.assert_not_called()
        delete_camera_completely.assert_not_called()
        flash.assert_called_once()
        self.assertEqual(flash.call_args.args[1], "error")

    def test_delete_camera_with_ignore_does_not_ignore_usb_path_camera(self):
        app = create_app()

        with (
            app.test_request_context("/cameras/4/delete", method="POST", data={"also_ignore": "true"}),
            patch(
                "daemon.web_ui.routes.get_camera_by_id",
                return_value={
                    "id": 4,
                    "connected": False,
                    "friendly_name": "USB Port Camera",
                    "hardware_id": "usb-path:USB Camera:pci-1-index0",
                    "identity_key": "usb-path:USB Camera:pci-1-index0",
                    "identity_strategy": "usb_path",
                    "moonraker_uid": None,
                },
            ),
            patch("daemon.web_ui.routes.ignore_camera") as ignore_camera,
            patch(
                "daemon.web_ui.routes.delete_camera_completely",
                return_value=(True, "usb-path:USB Camera:pci-1-index0"),
            ) as delete_camera_completely,
            patch("daemon.web_ui.routes.add_log"),
            patch("daemon.web_ui.routes.flash") as flash,
            patch("daemon.web_ui.routes.redirect", return_value="redirected"),
            patch("daemon.web_ui.routes.url_for", return_value="/cameras/"),
        ):
            response = routes.delete_camera(4)

        self.assertEqual(response, "redirected")
        delete_camera_completely.assert_called_once_with(4)
        ignore_camera.assert_not_called()
        flash.assert_called_once()
        self.assertEqual(flash.call_args.args[1], "success")


if __name__ == "__main__":
    unittest.main()
