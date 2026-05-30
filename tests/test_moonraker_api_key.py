import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daemon.web_ui.app import create_app
from daemon.web_ui import routes
from daemon.moonraker_client import MoonrakerClient
from daemon.moonraker_auth import (
    MoonrakerApiKeyError,
    clear_moonraker_api_key,
    moonraker_api_key_configured,
    moonraker_auth_headers,
    read_moonraker_api_key,
    save_moonraker_api_key,
)
from daemon.print_status import PrintStatusMonitor


class MoonrakerApiKeyConfigTests(unittest.TestCase):
    def test_save_load_and_clear_api_key_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "moonraker-api-key"

            save_moonraker_api_key("secret-key", key_file=key_file)

            self.assertEqual(read_moonraker_api_key(key_file=key_file), "secret-key")
            self.assertEqual(key_file.stat().st_mode & 0o777, 0o600)
            self.assertTrue(moonraker_api_key_configured(key_file=key_file))
            self.assertEqual(moonraker_auth_headers(key_file=key_file), {"X-Api-Key": "secret-key"})

            self.assertTrue(clear_moonraker_api_key(key_file=key_file))
            self.assertEqual(read_moonraker_api_key(key_file=key_file), "")
            self.assertFalse(moonraker_api_key_configured(key_file=key_file))

    def test_rejects_api_key_with_line_breaks(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(MoonrakerApiKeyError):
                save_moonraker_api_key("bad\nkey", key_file=Path(tmp) / "moonraker-api-key")

    def test_auth_headers_are_empty_without_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(moonraker_auth_headers(key_file=Path(tmp) / "missing"), {})


class MoonrakerApiKeyRequestTests(unittest.TestCase):
    def test_moonraker_client_sends_api_key_header(self):
        client = MoonrakerClient("http://moonraker.local")

        with (
            patch("daemon.moonraker_client.moonraker_auth_headers", return_value={"X-Api-Key": "secret"}),
            patch.object(client.session, "get") as get,
        ):
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"result": {"ok": True}}

            success, _, _ = client._request("/server/info")

        self.assertTrue(success)
        self.assertEqual(get.call_args.kwargs["headers"], {"X-Api-Key": "secret"})


class MoonrakerApiKeySettingsTests(unittest.TestCase):
    def test_settings_route_saves_moonraker_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "moonraker-api-key"
            env = {
                "RAVENS_PERCH_SECRET_KEY": "test-secret-key",
                "RAVENS_PERCH_MOONRAKER_API_KEY_FILE": str(key_file),
            }
            form = {
                "moonraker_url": "http://127.0.0.1:7125",
                "moonraker_api_key": "secret",
            }

            with (
                patch.dict("os.environ", env, clear=False),
                patch.object(routes, "add_log"),
                patch.object(routes, "set_setting"),
                patch.object(routes, "render_template", side_effect=lambda _template, **context: context.get("message", "Settings saved")),
            ):
                app = create_app()
                with app.test_request_context("/cameras/settings", method="POST", data=form, headers={"HX-Request": "true"}):
                    response = routes.update_global_settings()

            self.assertIn("Settings saved", response)
            self.assertEqual(read_moonraker_api_key(key_file=key_file), "secret")

    def test_settings_route_clears_moonraker_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "moonraker-api-key"
            save_moonraker_api_key("secret", key_file=key_file)
            env = {
                "RAVENS_PERCH_SECRET_KEY": "test-secret-key",
                "RAVENS_PERCH_MOONRAKER_API_KEY_FILE": str(key_file),
            }
            form = {
                "moonraker_url": "http://127.0.0.1:7125",
                "clear_moonraker_api_key": "1",
            }

            with (
                patch.dict("os.environ", env, clear=False),
                patch.object(routes, "add_log"),
                patch.object(routes, "set_setting"),
                patch.object(routes, "render_template", side_effect=lambda _template, **context: context.get("message", "Settings saved")),
            ):
                app = create_app()
                with app.test_request_context("/cameras/settings", method="POST", data=form, headers={"HX-Request": "true"}):
                    response = routes.update_global_settings()

            self.assertIn("Settings saved", response)
            self.assertEqual(read_moonraker_api_key(key_file=key_file), "")

    def test_print_status_sends_api_key_header(self):
        monitor = PrintStatusMonitor(moonraker_url="http://moonraker.local")

        with (
            patch("daemon.print_status.moonraker_auth_headers", return_value={"X-Api-Key": "secret"}),
            patch("daemon.print_status.requests.get") as get,
        ):
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"result": {"status": {}}}

            monitor._poll_status()

        self.assertEqual(get.call_args.kwargs["headers"], {"X-Api-Key": "secret"})


if __name__ == "__main__":
    unittest.main()
