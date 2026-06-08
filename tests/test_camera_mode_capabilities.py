import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from daemon.camera_manager import probe_capabilities
from daemon.web_ui import routes


CAMERA_A_CAPABILITIES = {
    "mjpeg": {
        "800x600": [25],
        "640x400": [25, 20, 15, 10, 5],
    },
    "yuyv": {
        "800x600": [20],
    },
}

LIFECAM_CAPABILITIES = {
    "mjpeg": {
        "1280x720": [30, 20, 15, 10, 7.5],
    },
}


class CameraModeCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)

    def test_detail_page_uses_framerates_for_selected_format_and_resolution(self):
        camera = {
            "id": 4,
            "friendly_name": "CameraA",
            "connected": False,
            "enabled": True,
            "device_path": "/dev/video9",
            "settings": {
                "format": "mjpeg",
                "resolution": "800x600",
                "framerate": 25,
            },
        }

        with (
            self.app.test_request_context("/cameras/4"),
            patch.object(routes, "get_camera_with_settings", return_value=camera),
            patch.object(routes, "is_stream_active", return_value=False),
            patch.object(routes, "get_stream_urls", return_value={}),
            patch.object(routes, "_get_capability_map", return_value=CAMERA_A_CAPABILITIES),
            patch.object(routes, "detect_encoders", return_value={}),
            patch.object(routes, "get_system_ip", return_value="127.0.0.1"),
            patch.object(routes, "get_all_settings", return_value={}),
            patch.object(routes, "render_template", return_value="rendered") as render_template,
        ):
            response = routes.camera_detail(4)

        self.assertEqual(response, "rendered")
        context = render_template.call_args.kwargs
        self.assertEqual(context["resolutions"], ["800x600", "640x400"])
        self.assertEqual(context["framerates"], [25])

    def test_update_settings_coerces_unsupported_live_and_standby_framerates(self):
        camera = {
            "id": 4,
            "friendly_name": "CameraA",
            "connected": False,
            "enabled": True,
            "device_path": "/dev/video9",
        }
        current_settings = {
            "format": "mjpeg",
            "resolution": "800x600",
            "framerate": 25,
            "standby_enabled": False,
            "standby_framerate": None,
        }
        form = {
            "format": "mjpeg",
            "resolution": "800x600",
            "framerate": "10",
            "encoder": "libx264",
            "bitrate": "2M",
            "standby_enabled": "1",
            "standby_framerate": "5",
        }

        def fake_render_template(_template_name, **context):
            return context

        with (
            self.app.test_request_context("/cameras/4/settings", method="POST", data=form, headers={"HX-Request": "true"}),
            patch.object(routes, "get_camera_by_id", return_value=camera),
            patch.object(routes, "_get_capability_map", return_value=CAMERA_A_CAPABILITIES),
            patch.object(routes, "get_camera_settings", return_value=current_settings),
            patch.object(routes, "save_camera_settings") as save_camera_settings,
            patch.object(routes, "get_print_monitor", return_value=None),
            patch.object(routes, "add_log"),
            patch.object(routes, "render_template", side_effect=fake_render_template),
        ):
            response = routes.update_settings(4)

        saved_settings = save_camera_settings.call_args.args[1]
        self.assertEqual(saved_settings["framerate"], 25)
        self.assertEqual(saved_settings["standby_framerate"], 25)
        self.assertIn("10 fps is not available for MJPEG 800x600", response["validation_notes"][0])
        self.assertIn("5 fps is not available for MJPEG 800x600", response["validation_notes"][1])

    def test_update_settings_preserves_fractional_framerate(self):
        camera = {
            "id": 1,
            "friendly_name": "LifeCam",
            "connected": False,
            "enabled": True,
            "device_path": "/dev/video2",
        }
        current_settings = {
            "format": "mjpeg",
            "resolution": "1280x720",
            "framerate": 15,
            "standby_enabled": False,
            "standby_framerate": None,
        }
        form = {
            "format": "mjpeg",
            "resolution": "1280x720",
            "framerate": "7.5",
            "encoder": "libx264",
            "bitrate": "2M",
        }

        def fake_render_template(_template_name, **context):
            return context

        with (
            self.app.test_request_context("/cameras/1/settings", method="POST", data=form, headers={"HX-Request": "true"}),
            patch.object(routes, "get_camera_by_id", return_value=camera),
            patch.object(routes, "_get_capability_map", return_value=LIFECAM_CAPABILITIES),
            patch.object(routes, "get_camera_settings", return_value=current_settings),
            patch.object(routes, "save_camera_settings") as save_camera_settings,
            patch.object(routes, "get_print_monitor", return_value=None),
            patch.object(routes, "add_log"),
            patch.object(routes, "render_template", side_effect=fake_render_template),
        ):
            response = routes.update_settings(1)

        saved_settings = save_camera_settings.call_args.args[1]
        self.assertEqual(saved_settings["framerate"], 7.5)
        self.assertEqual(response["validation_notes"], [])

    def test_api_framerates_renders_fractional_option(self):
        with (
            self.app.test_request_context(
                "/cameras/api/framerates/1?format=mjpeg&resolution=1280x720&framerate=7.5",
                headers={"HX-Request": "true"},
            ),
            patch.object(routes, "_get_capability_map", return_value=LIFECAM_CAPABILITIES),
        ):
            response, status, _headers = routes.api_framerates(1)

        self.assertEqual(status, 200)
        self.assertIn('value="7.5" selected>7.5 fps</option>', response)

    def test_probe_capabilities_preserves_fractional_framerates(self):
        v4l2_output = """
        ioctl: VIDIOC_ENUM_FMT
            Type: Video Capture

            [0]: 'MJPG' (Motion-JPEG, compressed)
                Size: Discrete 1280x720
                    Interval: Discrete 0.067s (15.000 fps)
                    Interval: Discrete 0.133s (7.500 fps)
        """

        result = SimpleNamespace(returncode=0, stdout=v4l2_output)

        with patch("daemon.camera_manager.subprocess.run", return_value=result):
            capabilities = probe_capabilities("/dev/video2")

        self.assertEqual(capabilities["mjpeg"]["1280x720"], [15, 7.5])


if __name__ == "__main__":
    unittest.main()
