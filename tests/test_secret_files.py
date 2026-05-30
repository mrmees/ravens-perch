import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from daemon.secret_files import write_secret_file


class SecretFileTests(unittest.TestCase):
    def test_write_secret_file_uses_restrictive_create_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret_file = Path(tmp) / "secret"

            with patch("daemon.secret_files.os.open", wraps=os.open) as open_mock:
                write_secret_file(secret_file, "secret-value\n")

            self.assertEqual(secret_file.read_text(encoding="utf-8"), "secret-value\n")
            self.assertEqual(secret_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(open_mock.call_args.args[2], 0o600)

    def test_auth_config_uses_secret_writer_for_password_and_env(self):
        from daemon.web_ui import auth_config

        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "web-auth.env"
            password_file = Path(tmp) / "web-ui-password"

            with patch.object(auth_config, "write_secret_file", wraps=write_secret_file) as writer:
                auth_config.save_web_auth_credentials(
                    "camera_admin",
                    "secret-pass",
                    config_file=config_file,
                    password_file=password_file,
                )

            written_paths = {call.args[0] for call in writer.call_args_list}
            self.assertEqual(written_paths, {password_file, config_file})

    def test_snapshot_token_uses_secret_writer(self):
        from daemon import snapshot_access

        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "snapshot-token"
            snapshot_access._cached_snapshot_tokens.clear()

            with patch.object(snapshot_access, "write_secret_file", wraps=write_secret_file) as writer:
                token = snapshot_access.get_snapshot_token(token_file=token_file)

            self.assertTrue(token)
            writer.assert_called_once()
            self.assertEqual(writer.call_args.args[0], token_file)

    def test_flask_secret_uses_secret_writer(self):
        from daemon.web_ui import app as web_app

        with tempfile.TemporaryDirectory() as tmp:
            secret_file = Path(tmp) / "web-ui-secret"

            with (
                patch.object(web_app, "_SECRET_KEY_FILE", secret_file),
                patch.object(web_app, "write_secret_file", wraps=write_secret_file) as writer,
            ):
                secret = web_app._get_secret_key()

            self.assertTrue(secret)
            writer.assert_called_once()
            self.assertEqual(writer.call_args.args[0], secret_file)


if __name__ == "__main__":
    unittest.main()
