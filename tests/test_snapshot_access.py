import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daemon.web_ui.app import create_app
from daemon.web_ui.auth_config import save_web_auth_credentials
from daemon.moonraker_client import build_snapshot_url


def _auth_header(username: str, password: str):
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


class SnapshotAccessTests(unittest.TestCase):
    def test_moonraker_snapshot_url_includes_public_snapshot_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "snapshot-token"
            token_file.write_text("snapshot-secret\n", encoding="utf-8")

            env = {"RAVENS_PERCH_SNAPSHOT_TOKEN_FILE": str(token_file)}
            with patch.dict("os.environ", env, clear=False):
                self.assertEqual(
                    build_snapshot_url("12", "printer.local"),
                    "http://printer.local/cameras/snapshot/12.jpg?token=snapshot-secret",
                )

    def test_snapshot_token_allows_moonraker_snapshot_when_basic_auth_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_file = tmp_path / "web-auth.env"
            token_file = tmp_path / "snapshot-token"
            save_web_auth_credentials(
                "camera_admin",
                "secret-pass",
                config_file=config_file,
                password_file=tmp_path / "web-ui-password",
            )
            token_file.write_text("snapshot-secret\n", encoding="utf-8")

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

                self.assertEqual(client.get("/cameras/snapshot/1.jpg").status_code, 401)

                response = client.get("/cameras/snapshot/1.jpg?token=snapshot-secret")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, b"jpeg-data")

    def test_authenticated_user_can_still_load_snapshot_without_token(self):
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
                "RAVENS_PERCH_SNAPSHOT_TOKEN_FILE": str(tmp_path / "snapshot-token"),
            }
            with (
                patch.dict("os.environ", env, clear=False),
                patch("daemon.web_ui.routes.get_camera_by_id", return_value={"id": 1, "connected": True}),
                patch("daemon.web_ui.routes.grab_snapshot", return_value=b"jpeg-data"),
            ):
                app = create_app()
                client = app.test_client()

                response = client.get(
                    "/cameras/snapshot/1.jpg",
                    headers=_auth_header("camera_admin", "secret-pass"),
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, b"jpeg-data")


if __name__ == "__main__":
    unittest.main()
