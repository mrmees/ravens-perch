import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_TEMPLATE = ROOT / "daemon/web_ui/templates/dashboard.html"
CAMERA_CARD_TEMPLATE = ROOT / "daemon/web_ui/templates/partials/camera_card.html"
STYLE_CSS = ROOT / "daemon/web_ui/static/css/style.css"


class DashboardDiagnosticsUITests(unittest.TestCase):
    def test_dashboard_card_shows_source_and_output_diagnostics(self):
        template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")

        self.assertNotIn("USB est:", template)
        self.assertNotIn("mbit_per_second", template)
        self.assertNotIn('data-type="usb"', template)
        self.assertIn("Per viewer:", template)
        self.assertIn("Out est:", template)
        self.assertIn("Source:", template)
        self.assertIn('data-type="output"', template)
        self.assertIn('data-type="source"', template)

    def test_dashboard_shows_shared_usb2_warning_copy(self):
        template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("USB 2.0 controller", template)
        self.assertIn("Klipper connection issues", template)
        self.assertIn("printer/controller", template)
        self.assertIn("Move one camera or the printer connection", template)

    def test_dashboard_places_usb_warning_after_normal_content(self):
        template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")

        self.assertGreater(
            template.index('class="usb-warning-section"'),
            template.index('class="info-panel"'),
        )

    def test_relevant_camera_cards_show_usb_warning_icon(self):
        dashboard = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
        partial = CAMERA_CARD_TEMPLATE.read_text(encoding="utf-8")

        for template in (dashboard, partial):
            self.assertIn("usb-warning-badge", template)
            self.assertIn("usb_topology_warning", template)
            self.assertIn("USB 2.0 topology warning", template)

    def test_usb_warning_panel_can_be_dismissed_without_removing_card_icon(self):
        template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("data-usb-warning-panel", template)
        self.assertIn("data-usb-warning-dismiss", template)
        self.assertIn("ravens-perch-usb-warning-dismissed", template)
        self.assertIn("localStorage", template)
        self.assertIn("usb-warning-badge", template)

    def test_usb_warning_has_mobile_footer_spacing(self):
        styles = STYLE_CSS.read_text(encoding="utf-8")

        self.assertIn(".usb-warning-section", styles)
        self.assertIn("margin: 1.5rem 0 4.5rem;", styles)
        self.assertIn("@media (max-width: 600px)", styles)
        self.assertIn("margin-bottom: 6rem;", styles)

    def test_rejected_camera_help_describes_stable_identity_requirements(self):
        template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("could not be identified safely", template)
        self.assertIn("volatile /dev/video paths", template)
        self.assertIn("Scan Cameras again", template)

    def test_camera_card_shows_usb_port_identity_badge(self):
        partial = CAMERA_CARD_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("identity_strategy == 'usb_path'", partial)
        self.assertIn("USB port", partial)
        self.assertIn("identity-badge", partial)

    def test_camera_detail_hides_ignore_for_usb_path_cameras(self):
        detail = (ROOT / "daemon/web_ui/templates/camera_detail.html").read_text(encoding="utf-8")

        self.assertIn("camera.identity_strategy != 'usb_path'", detail)
        self.assertIn("By-path", detail)
        self.assertIn("settings follow this USB port", detail)


if __name__ == "__main__":
    unittest.main()
