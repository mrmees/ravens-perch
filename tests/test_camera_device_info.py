import tempfile
import unittest
from pathlib import Path

from daemon.camera_manager import _find_v4l_symlink_for_device


class CameraDeviceInfoTests(unittest.TestCase):
    def test_find_v4l_symlink_for_device_matches_resolved_real_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_device = root / "video0"
            real_device.touch()
            link_dir = root / "by-path"
            link_dir.mkdir()
            matching_link = link_dir / "pci-1-index0"
            matching_link.symlink_to(real_device)

            self.assertEqual(
                _find_v4l_symlink_for_device(str(real_device), link_dir),
                str(matching_link),
            )

    def test_find_v4l_symlink_for_device_returns_none_without_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_device = root / "video0"
            other_device = root / "video1"
            real_device.touch()
            other_device.touch()
            link_dir = root / "by-path"
            link_dir.mkdir()
            (link_dir / "pci-2-index0").symlink_to(other_device)

            self.assertIsNone(_find_v4l_symlink_for_device(str(real_device), link_dir))

    def test_find_v4l_symlink_for_device_returns_none_for_missing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_device = root / "video0"
            real_device.touch()

            self.assertIsNone(
                _find_v4l_symlink_for_device(str(real_device), root / "missing")
            )

    def test_find_v4l_symlink_for_device_ignores_broken_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_device = root / "video0"
            real_device.touch()
            link_dir = root / "by-path"
            link_dir.mkdir()
            (link_dir / "pci-1-index0").symlink_to(root / "missing-video")

            self.assertIsNone(_find_v4l_symlink_for_device(str(real_device), link_dir))


if __name__ == "__main__":
    unittest.main()
