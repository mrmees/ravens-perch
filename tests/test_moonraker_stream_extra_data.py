import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from daemon.moonraker_client import build_stream_extra_data, register_camera


class MoonrakerStreamExtraDataTests(unittest.TestCase):
    def test_build_stream_extra_data_advertises_all_streams(self):
        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "snapshot-token"
            token_file.write_text("snapshot-secret\n", encoding="utf-8")

            env = {"RAVENS_PERCH_SNAPSHOT_TOKEN_FILE": str(token_file)}
            with patch.dict("os.environ", env, clear=False):
                self.assertEqual(
                    build_stream_extra_data("12", "printer.local"),
                    {
                        "ravens_perch": {
                            "schema_version": 1,
                            "camera_id": "12",
                            "path": "12",
                            "streams": {
                                "webrtc": {
                                    "url": "http://printer.local:8889/12/",
                                    "protocol": "webrtc",
                                },
                                "rtsp": {
                                    "url": "rtsp://printer.local:8554/12",
                                    "protocol": "rtsp",
                                },
                                "hls": {
                                    "url": "http://printer.local:8888/12/",
                                    "protocol": "hls",
                                },
                                "snapshot": {
                                    "url": (
                                        "http://printer.local/cameras/snapshot/12.jpg"
                                        "?token=snapshot-secret"
                                    ),
                                    "protocol": "http",
                                },
                            },
                        }
                    },
                )

    def test_register_camera_sends_extra_data_when_creating_webcam(self):
        client = Mock()
        client._request.return_value = (True, {"webcam": {"uid": "uid-12"}}, None)
        extra_data = {"ravens_perch": {"camera_id": "12"}}

        with (
            patch("daemon.moonraker_client.get_client", return_value=client),
            patch("daemon.moonraker_client.get_ravens_camera_by_name", return_value=None),
        ):
            success, uid, error = register_camera(
                "12",
                "Toolhead Camera",
                "http://printer.local:8889/12/",
                "http://printer.local/cameras/snapshot/12.jpg?token=snapshot-secret",
                extra_data=extra_data,
            )

        self.assertTrue(success)
        self.assertEqual(uid, "uid-12")
        self.assertIsNone(error)
        payload = client._request.call_args.kwargs["data"]
        self.assertEqual(payload["extra_data"], extra_data)


if __name__ == "__main__":
    unittest.main()
