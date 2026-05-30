from pathlib import Path
import unittest


INSTALL_SH = Path(__file__).resolve().parents[1] / "install.sh"


class InstallLifecycleTests(unittest.TestCase):
    def test_ravens_service_uses_wants_for_mediamtx_dependency(self):
        text = INSTALL_SH.read_text(encoding="utf-8")

        self.assertIn("Wants=mediamtx.service", text)
        self.assertNotIn("Requires=mediamtx.service", text)

    def test_success_message_does_not_advertise_unreachable_lan_port(self):
        text = INSTALL_SH.read_text(encoding="utf-8")

        self.assertNotIn("http://${ip}:8585/cameras/", text)
        self.assertIn("Local service", text)
        self.assertIn("nginx", text.lower())

    def test_nginx_port_detection_does_not_match_8080(self):
        text = INSTALL_SH.read_text(encoding="utf-8")

        self.assertNotIn('grep -q "listen 80"', text)
        self.assertIn("listen[[:space:]]+80", text)

    def test_install_verification_uses_web_auth_credentials_for_local_api(self):
        text = INSTALL_SH.read_text(encoding="utf-8")

        self.assertIn("ravens_perch_api_curl", text)
        self.assertIn("RAVENS_PERCH_WEB_AUTH_USERNAME", text)
        self.assertIn("RAVENS_PERCH_WEB_AUTH_PASSWORD_FILE", text)
        self.assertIn('ravens_perch_api_curl "/cameras/api/health"', text)
        self.assertIn('rp_cameras=$(ravens_perch_api_curl "/cameras/api/status"', text)
        self.assertNotIn('curl -s "http://127.0.0.1:8585/cameras/api/status"', text)

    def test_installer_prompts_for_moonraker_api_key_and_uses_helper(self):
        text = INSTALL_SH.read_text(encoding="utf-8")

        self.assertIn("configure_moonraker_api_key", text)
        self.assertIn("RAVENS_PERCH_MOONRAKER_API_KEY", text)
        self.assertIn("moonraker_curl", text)
        self.assertIn("X-Api-Key:", text)
        self.assertIn("Keeping existing Moonraker API key", text)
        self.assertNotIn('curl -s "http://127.0.0.1:7125/server/webcams/list"', text)


if __name__ == "__main__":
    unittest.main()
