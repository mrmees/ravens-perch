import shlex
import unittest

from daemon.stream_manager import build_ffmpeg_command


class StreamManagerConfigTests(unittest.TestCase):
    def test_ffmpeg_command_uses_one_second_keyframe_interval(self):
        command = build_ffmpeg_command(
            "/dev/video0",
            {
                "format": "mjpeg",
                "resolution": "640x480",
                "framerate": 15,
                "bitrate": "1M",
            },
            "7",
        )
        args = shlex.split(command)

        self.assertEqual(args[args.index("-g") + 1], "15")

    def test_ffmpeg_command_preserves_fractional_framerate(self):
        command = build_ffmpeg_command(
            "/dev/video0",
            {
                "format": "mjpeg",
                "resolution": "1280x720",
                "framerate": 7.5,
                "bitrate": "1M",
            },
            "7",
        )
        args = shlex.split(command)

        self.assertEqual(args[args.index("-framerate") + 1], "7.5")
        self.assertEqual(args[args.index("-g") + 1], "7.5")


if __name__ == "__main__":
    unittest.main()
