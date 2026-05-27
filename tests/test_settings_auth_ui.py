import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_TEMPLATE = ROOT / "daemon/web_ui/templates/settings.html"
ROUTES_PY = ROOT / "daemon/web_ui/routes.py"


class SettingsAuthUITests(unittest.TestCase):
    def test_settings_page_has_authentication_form(self):
        template = SETTINGS_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("Authentication", template)
        self.assertIn("cameras.update_auth_settings", template)
        self.assertIn('name="auth_username"', template)
        self.assertIn('name="auth_password"', template)
        self.assertIn('name="auth_password_confirm"', template)

    def test_settings_auth_route_exists(self):
        routes = ROUTES_PY.read_text(encoding="utf-8")

        self.assertIn("@bp.route('/settings/auth', methods=['POST'])", routes)
        self.assertIn("def update_auth_settings", routes)


if __name__ == "__main__":
    unittest.main()
