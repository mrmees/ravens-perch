import base64
import tempfile
import unittest
from pathlib import Path
from typing import Dict
from unittest.mock import patch

from daemon.web_ui.app import create_app
from daemon.web_ui import routes
from daemon.web_ui.auth_config import (
    WebAuthConfigError,
    load_web_auth_config,
    save_web_auth_credentials,
)


def _auth_header(username: str, password: str) -> Dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


class WebAuthConfigTests(unittest.TestCase):
    def test_save_and_load_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "web-auth.env"
            password_file = Path(tmp) / "web-ui-password"

            save_web_auth_credentials(
                "camera_admin",
                "secret-pass",
                config_file=config_file,
                password_file=password_file,
            )

            config = load_web_auth_config(config_file=config_file)

            self.assertTrue(config.enabled)
            self.assertEqual(config.username, "camera_admin")
            self.assertEqual(config.password, "secret-pass")
            self.assertEqual(password_file.read_text(encoding="utf-8").strip(), "secret-pass")
            self.assertIn("RAVENS_PERCH_WEB_AUTH_USERNAME=camera_admin", config_file.read_text())

    def test_rejects_invalid_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(WebAuthConfigError):
                save_web_auth_credentials(
                    "bad user",
                    "secret-pass",
                    config_file=Path(tmp) / "web-auth.env",
                    password_file=Path(tmp) / "web-ui-password",
                )

    def test_app_uses_updated_auth_file_without_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "web-auth.env"
            password_file = Path(tmp) / "web-ui-password"
            save_web_auth_credentials("old_user", "old-pass", config_file=config_file, password_file=password_file)

            env = {
                "RAVENS_PERCH_SECRET_KEY": "test-secret-key",
                "RAVENS_PERCH_WEB_AUTH_ENV_FILE": str(config_file),
            }
            with patch.dict("os.environ", env, clear=False):
                app = create_app()

                @app.route("/protected-test")
                def protected_test():
                    return "ok"

                client = app.test_client()
                self.assertEqual(client.get("/protected-test", headers=_auth_header("old_user", "old-pass")).status_code, 200)

                save_web_auth_credentials("new_user", "new-pass", config_file=config_file, password_file=password_file)

                self.assertEqual(client.get("/protected-test", headers=_auth_header("old_user", "old-pass")).status_code, 401)
                self.assertEqual(client.get("/protected-test", headers=_auth_header("new_user", "new-pass")).status_code, 200)

    def test_settings_auth_route_updates_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "web-auth.env"
            env = {
                "RAVENS_PERCH_SECRET_KEY": "test-secret-key",
                "RAVENS_PERCH_WEB_AUTH_ENV_FILE": str(config_file),
            }
            form = {
                "auth_username": "settings_user",
                "auth_password": "settings-pass",
                "auth_password_confirm": "settings-pass",
            }

            def fake_render_template(_template_name, **context):
                return context["message"]

            with (
                patch.dict("os.environ", env, clear=False),
                patch.object(routes, "add_log"),
                patch.object(routes, "render_template", side_effect=fake_render_template),
            ):
                app = create_app()
                with app.test_request_context("/cameras/settings/auth", method="POST", data=form, headers={"HX-Request": "true"}):
                    response = routes.update_auth_settings()

            self.assertIn("Authentication settings saved.", response)
            config = load_web_auth_config(config_file=config_file)
            self.assertEqual(config.username, "settings_user")
            self.assertEqual(config.password, "settings-pass")


if __name__ == "__main__":
    unittest.main()
