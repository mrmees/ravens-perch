import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daemon.web_ui.app import create_app
from daemon.web_ui.auth_config import save_web_auth_credentials


def _auth_header(username: str, password: str):
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _set_csrf(client, token: str = "test-csrf-token") -> str:
    with client.session_transaction() as session:
        session["_csrf_token"] = token
    return token


class WebSecurityTests(unittest.TestCase):
    def test_post_without_csrf_token_is_rejected(self):
        app = create_app()
        client = app.test_client()

        response = client.post("/cameras/redetect-encoders", headers={"HX-Request": "true"})

        self.assertEqual(response.status_code, 400)

    def test_post_with_csrf_token_is_allowed(self):
        app = create_app()
        client = app.test_client()
        token = _set_csrf(client)

        with (
            patch("daemon.web_ui.routes.clear_encoder_cache"),
            patch("daemon.web_ui.routes.detect_encoders", return_value={"libx264": True}),
            patch("daemon.web_ui.routes.add_log"),
        ):
            response = client.post(
                "/cameras/redetect-encoders",
                headers={"HX-Request": "true", "X-CSRFToken": token},
            )

        self.assertEqual(response.status_code, 200)

    def test_basic_auth_protects_routes_but_health_stays_public(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_file = tmp_path / "web-auth.env"
            save_web_auth_credentials(
                "camera_admin",
                "secret-pass",
                config_file=config_file,
                password_file=tmp_path / "web-ui-password",
            )

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

                self.assertEqual(client.get("/protected-test").status_code, 401)
                self.assertEqual(client.get("/cameras/api/health").status_code, 200)
                self.assertEqual(client.get("/protected-test", headers=_auth_header("camera_admin", "secret-pass")).status_code, 200)

    def test_snapshot_token_does_not_bypass_non_snapshot_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_file = tmp_path / "web-auth.env"
            token_file = tmp_path / "snapshot-token"
            token_file.write_text("snapshot-secret\n", encoding="utf-8")
            save_web_auth_credentials(
                "camera_admin",
                "secret-pass",
                config_file=config_file,
                password_file=tmp_path / "web-ui-password",
            )

            env = {
                "RAVENS_PERCH_SECRET_KEY": "test-secret-key",
                "RAVENS_PERCH_WEB_AUTH_ENV_FILE": str(config_file),
                "RAVENS_PERCH_SNAPSHOT_TOKEN_FILE": str(token_file),
            }
            with patch.dict("os.environ", env, clear=False):
                app = create_app()
                client = app.test_client()

                self.assertEqual(client.get("/cameras/settings?token=snapshot-secret").status_code, 401)

    def test_security_headers_are_added_to_responses(self):
        app = create_app()
        client = app.test_client()

        response = client.get("/cameras/api/health")

        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(response.headers["Referrer-Policy"], "same-origin")

    def test_snapshot_token_response_is_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_file = tmp_path / "web-auth.env"
            token_file = tmp_path / "snapshot-token"
            token_file.write_text("snapshot-secret\n", encoding="utf-8")
            save_web_auth_credentials(
                "camera_admin",
                "secret-pass",
                config_file=config_file,
                password_file=tmp_path / "web-ui-password",
            )

            env = {
                "RAVENS_PERCH_SECRET_KEY": "test-secret-key",
                "RAVENS_PERCH_WEB_AUTH_ENV_FILE": str(config_file),
                "RAVENS_PERCH_SNAPSHOT_TOKEN_FILE": str(token_file),
            }
            with (
                patch.dict("os.environ", env, clear=False),
                patch("daemon.web_ui.routes.get_camera_by_id", return_value={"id": 1, "connected": True}),
                patch("daemon.web_ui.routes.grab_snapshot", return_value=b"jpeg-data"),
            ):
                app = create_app()
                client = app.test_client()
                response = client.get("/cameras/snapshot/1.jpg?token=snapshot-secret")

            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response.headers["Cache-Control"])


if __name__ == "__main__":
    unittest.main()
