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


if __name__ == "__main__":
    unittest.main()
