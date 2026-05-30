import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_TEMPLATE = ROOT / "daemon/web_ui/templates/dashboard.html"
CAMERA_CARD_TEMPLATE = ROOT / "daemon/web_ui/templates/partials/camera_card.html"


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


if __name__ == "__main__":
    unittest.main()
