import unittest
from unittest.mock import patch

from daemon.bandwidth import (
    get_camera_bandwidth_stats,
    reset_input_bandwidth_sample,
    sample_input_bandwidth_once,
)


class BandwidthStatsTests(unittest.TestCase):
    def tearDown(self):
        reset_input_bandwidth_sample("7")

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

    def test_compressed_usb_bandwidth_is_unavailable_without_one_time_sample(self):
        camera = {
            "id": 7,
            "settings": {
                "format": "h264",
                "resolution": "1280x720",
                "framerate": 30,
                "bitrate": "2M",
            },
        }

        with patch("daemon.bandwidth.get_mediamtx_stream_stats", return_value=None):
            stats = get_camera_bandwidth_stats(camera)

        self.assertFalse(stats["usb"]["available"])
        self.assertIsNone(stats["usb"]["mb_per_second"])

    def test_mjpeg_usb_bandwidth_uses_one_time_cached_frame_sample(self):
        camera = {
            "id": 7,
            "settings": {
                "format": "mjpeg",
                "resolution": "640x480",
                "framerate": 10,
                "bitrate": "2M",
            },
        }

        with (
            patch("daemon.bandwidth.get_mediamtx_stream_stats", return_value=None),
            patch(
                "daemon.bandwidth._input_bandwidth_samples",
                {"7": {"bytes_per_second": 250_000, "source": "snapshot_sample"}},
            ),
        ):
            stats = get_camera_bandwidth_stats(camera)

        self.assertTrue(stats["usb"]["available"])
        self.assertTrue(stats["usb"]["sampled"])
        self.assertEqual(stats["usb"]["mb_per_second"], 0.2)

    def test_mjpeg_one_time_sample_reads_latest_snapshot_size(self):
        with patch(
            "daemon.snapshot_server.get_cached_snapshot_info",
            return_value={"bytes": 25_000, "age_seconds": 1.0},
        ):
            sampled = sample_input_bandwidth_once("7", {"format": "mjpeg", "framerate": 10})

        self.assertTrue(sampled)

        with patch("daemon.bandwidth.get_mediamtx_stream_stats", return_value=None):
            stats = get_camera_bandwidth_stats(
                {
                    "id": 7,
                    "settings": {
                        "format": "mjpeg",
                        "resolution": "640x480",
                        "framerate": 10,
                    },
                }
            )

        self.assertTrue(stats["usb"]["available"])
        self.assertEqual(stats["usb"]["bytes_per_second"], 250_000)


if __name__ == "__main__":
    unittest.main()
