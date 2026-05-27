from pathlib import Path
import unittest


INSTALL_SH = Path(__file__).resolve().parents[1] / "install.sh"


def _configure_web_auth_body() -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("configure_web_auth() {")
    end = text.index("\n}\n\n# Create Python virtual environment", start) + 3
    return text[start:end]


class InstallAuthPromptTests(unittest.TestCase):
    def test_basic_auth_setup_prompts_for_username_and_password(self):
        body = _configure_web_auth_body()

        self.assertIn("Web UI username", body)
        self.assertIn("Web UI password", body)
        self.assertIn("Confirm Web UI password", body)
        self.assertIn("RAVENS_PERCH_WEB_AUTH_USERNAME=${username}", body)

    def test_basic_auth_setup_does_not_generate_hidden_password(self):
        body = _configure_web_auth_body()

        self.assertNotIn("secrets.token_urlsafe", body)
        self.assertNotIn("Web UI password already exists", body)


if __name__ == "__main__":
    unittest.main()
