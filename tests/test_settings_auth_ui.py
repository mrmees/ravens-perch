import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_TEMPLATE = ROOT / "daemon/web_ui/templates/settings.html"
ROUTES_PY = ROOT / "daemon/web_ui/routes.py"
STYLE_CSS = ROOT / "daemon/web_ui/static/css/style.css"


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

    def test_password_fields_use_themed_form_input_styles(self):
        styles = STYLE_CSS.read_text(encoding="utf-8")

        self.assertIn('.form-group input[type="password"]', styles)

    def test_settings_about_section_can_open_full_raven_poem(self):
        template = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
        routes = ROUTES_PY.read_text(encoding="utf-8")

        self.assertIn("Read Full Poem", template)
        self.assertIn('id="raven-poem-modal"', template)
        self.assertIn("openRavenPoem", template)
        self.assertIn("closeRavenPoem", template)
        self.assertIn("raven_lines=RAVEN_LINES", routes)


if __name__ == "__main__":
    unittest.main()
