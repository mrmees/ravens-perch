import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_TEMPLATE = ROOT / "daemon/web_ui/templates/dashboard.html"


class DashboardDiagnosticsUITests(unittest.TestCase):
    def test_dashboard_card_shows_source_and_output_diagnostics(self):
        template = DASHBOARD_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("USB est:", template)
        self.assertIn("mbit_per_second", template)
        self.assertIn("Mbit/s", template)
        self.assertNotIn("MB/s", template)
        self.assertIn("Per viewer:", template)
        self.assertIn("Out est:", template)
        self.assertIn("Source:", template)
        self.assertIn('data-type="output"', template)
        self.assertIn('data-type="source"', template)


if __name__ == "__main__":
    unittest.main()
