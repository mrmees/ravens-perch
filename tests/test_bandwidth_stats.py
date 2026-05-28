import unittest
from unittest.mock import patch

from daemon.bandwidth import (
    estimate_usb_bandwidth,
    get_camera_bandwidth_stats,
)


class BandwidthStatsTests(unittest.TestCase):
    def test_stats_include_source_state_and_total_output(self):
        camera = {
            "id": 7,
            "settings": {
                "format": "mjpeg",
                "resolution": "640x480",
                "framerate": 15,
                "bitrate": "2M",
            },
        }

        with patch(
            "daemon.bandwidth.get_mediamtx_stream_stats",
            return_value={"readers": 3, "ready": True, "source_ready": True},
        ):
            stats = get_camera_bandwidth_stats(camera)

        self.assertEqual(stats["source"]["state"], "ok")
        self.assertEqual(stats["source"]["label"], "OK")
        self.assertEqual(stats["output"]["mbps"], 6.0)
        self.assertEqual(stats["output"]["readers"], 3)

    def test_stats_report_waiting_source_when_path_is_not_ready(self):
        camera = {
            "id": 7,
            "settings": {
                "bitrate": "2M",
            },
        }

        with patch(
            "daemon.bandwidth.get_mediamtx_stream_stats",
            return_value={"readers": 0, "ready": False, "source_ready": False},
        ):
            stats = get_camera_bandwidth_stats(camera)

        self.assertEqual(stats["source"]["state"], "waiting")
        self.assertEqual(stats["source"]["label"], "Waiting")
        self.assertEqual(stats["output"]["mbps"], 0.0)

    def test_raw_usb_bandwidth_reports_calculated_mbit_per_second(self):
        usb = estimate_usb_bandwidth("yuyv", "1920x1080", 30)

        self.assertTrue(usb["available"])
        self.assertFalse(usb["is_estimate"])
        self.assertEqual(usb["method"], "calculated")
        self.assertEqual(usb["format_family"], "raw")
        self.assertEqual(usb["unit"], "Mbit/s")
        self.assertEqual(usb["mbit_per_second"], 995.3)

    def test_mjpeg_usb_bandwidth_uses_resolution_and_fps_lookup_table(self):
        usb = estimate_usb_bandwidth("mjpeg", "1280x720", 30)

        self.assertTrue(usb["available"])
        self.assertTrue(usb["is_estimate"])
        self.assertEqual(usb["method"], "lookup")
        self.assertEqual(usb["format_family"], "mjpeg")
        self.assertEqual(usb["resolution_bucket"], "720p")
        self.assertEqual(usb["framerate_bucket"], 30)
        self.assertEqual(usb["mbit_per_second"], 42.0)

    def test_encoded_usb_bandwidth_uses_encoded_lookup_table(self):
        usb = estimate_usb_bandwidth("h264", "1920x1080", 30)

        self.assertTrue(usb["available"])
        self.assertTrue(usb["is_estimate"])
        self.assertEqual(usb["method"], "lookup")
        self.assertEqual(usb["format_family"], "encoded")
        self.assertEqual(usb["resolution_bucket"], "1080p")
        self.assertEqual(usb["framerate_bucket"], 30)
        self.assertEqual(usb["mbit_per_second"], 20.0)


if __name__ == "__main__":
    unittest.main()
