import unittest
from unittest.mock import Mock, patch

from daemon import snapshot_server
from daemon import stream_manager


class SnapshotCacheTests(unittest.TestCase):
    def tearDown(self):
        snapshot_server.clear_cache()

    def test_snapshot_uses_latest_cached_frame_without_regrabbing(self):
        with patch("daemon.snapshot_server.time.time", return_value=1000.0):
            snapshot_server._cache.put("1", b"latest-jpeg", 640, 480)

        with (
            patch("daemon.snapshot_server.time.time", return_value=1001.0),
            patch("daemon.snapshot_server.grab_frame_av", return_value=None) as grab_av,
            patch("daemon.snapshot_server.grab_frame_ffmpeg", return_value=None) as grab_ffmpeg,
        ):
            self.assertEqual(snapshot_server.grab_snapshot("1"), b"latest-jpeg")

        grab_av.assert_not_called()
        grab_ffmpeg.assert_not_called()

    def test_stream_start_starts_snapshot_cache_worker(self):
        with (
            patch("daemon.stream_manager.ensure_stream_path", return_value=(True, None)),
            patch("daemon.stream_manager._replace_ffmpeg_process", return_value=(True, None)),
            patch("daemon.stream_manager._start_snapshot_cache") as start_cache,
        ):
            success, error = stream_manager.add_or_update_stream("7", "ffmpeg -i /dev/video0 out")

        self.assertTrue(success)
        self.assertIsNone(error)
        start_cache.assert_called_once_with("7")

    def test_stream_remove_stops_snapshot_cache_worker(self):
        client = Mock()
        client.api_request.return_value = (True, {}, None)

        with (
            patch("daemon.stream_manager.get_client", return_value=client),
            patch("daemon.stream_manager._stop_managed_process"),
            patch("daemon.stream_manager._stop_snapshot_cache") as stop_cache,
            patch("daemon.stream_manager._reset_input_bandwidth_sample") as reset_sample,
        ):
            success, error = stream_manager.remove_stream("7")

        self.assertTrue(success)
        self.assertIsNone(error)
        stop_cache.assert_called_once_with("7")
        reset_sample.assert_called_once_with("7")

    def test_start_camera_stream_schedules_one_time_input_bandwidth_sample(self):
        settings = {
            "format": "mjpeg",
            "resolution": "640x480",
            "framerate": 10,
        }

        with (
            patch("daemon.stream_manager.add_or_update_stream", return_value=(True, None)),
            patch("daemon.stream_manager._schedule_input_bandwidth_sample") as schedule_sample,
        ):
            success, error = stream_manager.start_camera_stream("/dev/video0", "7", settings)

        self.assertTrue(success)
        self.assertIsNone(error)
        schedule_sample.assert_called_once_with("7", settings)

    def test_background_refresh_does_not_spawn_ffmpeg_fallback(self):
        class OneShotStop:
            def __init__(self):
                self.waited = False

            def is_set(self):
                return self.waited

            def wait(self, _timeout):
                self.waited = True
                return True

        with (
            patch("daemon.snapshot_server.grab_frame_av", return_value=None),
            patch("daemon.snapshot_server.grab_frame_ffmpeg") as grab_ffmpeg,
        ):
            snapshot_server._snapshot_refresh_loop("1", OneShotStop())

        grab_ffmpeg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
