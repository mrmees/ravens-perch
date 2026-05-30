import unittest
from unittest.mock import patch

from daemon.bandwidth import get_camera_bandwidth_stats


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
        self.assertNotIn("usb", stats)

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


if __name__ == "__main__":
    unittest.main()
