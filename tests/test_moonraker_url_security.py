import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daemon.moonraker_client import MoonrakerUrlError, validate_moonraker_url
from daemon.web_ui import routes
from daemon.web_ui.app import create_app


class MoonrakerUrlSecurityTests(unittest.TestCase):
    def test_allows_local_and_private_moonraker_urls(self):
        self.assertEqual(validate_moonraker_url("http://127.0.0.1:7125"), "http://127.0.0.1:7125")
        self.assertEqual(validate_moonraker_url("http://localhost:7125"), "http://localhost:7125")
        self.assertEqual(validate_moonraker_url("http://192.168.1.50:7125"), "http://192.168.1.50:7125")
        self.assertEqual(validate_moonraker_url("http://printer.local:7125"), "http://printer.local:7125")
        self.assertEqual(validate_moonraker_url("http://ender3:7125"), "http://ender3:7125")

    def test_rejects_public_moonraker_url_by_default(self):
        with self.assertRaises(MoonrakerUrlError):
            validate_moonraker_url("http://example.com:7125")

    def test_remote_moonraker_url_requires_explicit_override(self):
        with patch.dict("os.environ", {"RAVENS_PERCH_ALLOW_REMOTE_MOONRAKER": "1"}, clear=False):
            self.assertEqual(validate_moonraker_url("https://example.com:7125"), "https://example.com:7125")

    def test_rejects_urls_with_embedded_credentials(self):
        with self.assertRaises(MoonrakerUrlError):
            validate_moonraker_url("http://user:pass@127.0.0.1:7125")

    def test_settings_rejects_public_moonraker_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                "RAVENS_PERCH_SECRET_KEY": "test-secret-key",
                "RAVENS_PERCH_MOONRAKER_API_KEY_FILE": str(Path(tmp) / "moonraker-api-key"),
            }
            form = {"moonraker_url": "http://example.com:7125"}

            with (
                patch.dict("os.environ", env, clear=False),
                patch.object(routes, "set_setting") as set_setting,
                patch.object(routes, "render_template", side_effect=lambda _template, **context: context.get("message", "")),
            ):
                app = create_app()
                with app.test_request_context("/cameras/settings", method="POST", data=form, headers={"HX-Request": "true"}):
                    response = routes.update_global_settings()

            self.assertIn("Moonraker URL", response)
            set_setting.assert_not_called()

    def test_settings_allows_blank_moonraker_url_to_restore_auto_detection(self):
        form = {"moonraker_url": ""}

        with (
            patch.object(routes, "set_setting") as set_setting,
            patch.object(routes, "add_log"),
            patch.object(routes, "render_template", side_effect=lambda _template, **context: context.get("message", "Settings saved")),
        ):
            app = create_app()
            with app.test_request_context("/cameras/settings", method="POST", data=form, headers={"HX-Request": "true"}):
                response = routes.update_global_settings()

        self.assertIn("Settings saved", response)
        set_setting.assert_called_once_with("moonraker_url", "")


if __name__ == "__main__":
    unittest.main()
