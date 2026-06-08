import unittest
from unittest.mock import patch

from daemon.web_ui import routes
from daemon.web_ui.app import create_app


class MoonrakerRouteRegistrationTests(unittest.TestCase):
    def test_route_registration_helper_passes_stream_extra_data(self):
        extra_data = {"ravens_perch": {"camera_id": "4"}}

        with (
            patch("daemon.web_ui.routes.moonraker_available", return_value=True),
            patch("daemon.web_ui.routes.get_system_ip", return_value="printer.local"),
            patch("daemon.web_ui.routes.build_stream_url", return_value="webrtc-url"),
            patch("daemon.web_ui.routes.build_snapshot_url", return_value="snapshot-url"),
            patch("daemon.web_ui.routes.build_stream_extra_data", return_value=extra_data) as build_extra,
            patch("daemon.web_ui.routes.register_camera", return_value=(True, "uid-4", None)) as register,
            patch("daemon.web_ui.routes.update_camera") as update,
        ):
            routes._register_camera_with_moonraker(
                4,
                "USB Camera",
                {"rotation": 180},
            )

        build_extra.assert_called_once_with("4", "printer.local")
        register.assert_called_once_with(
            "4",
            "USB Camera",
            "webrtc-url",
            "snapshot-url",
            rotation=180,
            extra_data=extra_data,
        )
        update.assert_called_once_with(4, moonraker_uid="uid-4")

    def test_rename_reregistration_passes_stream_extra_data(self):
        app = create_app()
        extra_data = {"ravens_perch": {"camera_id": "4"}}

        with (
            app.test_request_context(
                "/cameras/4/rename",
                method="POST",
                data={"friendly_name": "New Camera"},
            ),
            patch(
                "daemon.web_ui.routes.get_camera_by_id",
                return_value={
                    "id": 4,
                    "friendly_name": "Old Camera",
                    "moonraker_uid": "old-uid",
                },
            ),
            patch("daemon.web_ui.routes.update_camera") as update,
            patch("daemon.web_ui.routes.add_log"),
            patch("daemon.web_ui.routes.moonraker_available", return_value=True),
            patch("daemon.web_ui.routes.unregister_moonraker_camera") as unregister,
            patch("daemon.web_ui.routes.get_system_ip", return_value="printer.local"),
            patch("daemon.web_ui.routes.build_stream_url", return_value="webrtc-url"),
            patch("daemon.web_ui.routes.build_snapshot_url", return_value="snapshot-url"),
            patch("daemon.web_ui.routes.get_camera_settings", return_value={"rotation": 270}),
            patch("daemon.web_ui.routes.build_stream_extra_data", return_value=extra_data) as build_extra,
            patch("daemon.web_ui.routes.register_camera", return_value=(True, "new-uid", None)) as register,
            patch("daemon.web_ui.routes.flash"),
            patch("daemon.web_ui.routes.redirect", return_value="redirected"),
            patch("daemon.web_ui.routes.url_for", return_value="/cameras/4"),
        ):
            response = routes.rename_camera(4)

        self.assertEqual(response, "redirected")
        unregister.assert_called_once_with("old-uid")
        build_extra.assert_called_once_with("4", "printer.local")
        register.assert_called_once_with(
            "4",
            "New Camera",
            "webrtc-url",
            "snapshot-url",
            rotation=270,
            extra_data=extra_data,
        )
        update.assert_any_call(4, friendly_name="New Camera")
        update.assert_any_call(4, moonraker_uid="new-uid")


if __name__ == "__main__":
    unittest.main()
