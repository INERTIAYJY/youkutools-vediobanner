from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from batch_ab_video.gui import MainWindow
from batch_ab_video.models import StickerLayout


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = MainWindow()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()

    def test_sticker_modes_expose_the_correct_presets_and_layout(self) -> None:
        self.window.mode_combo.setCurrentIndex(1)
        self.assertEqual(
            [self.window.sticker_preset_combo.itemData(index).width for index in range(2)],
            [1080, 1080],
        )

        self.window.mode_combo.setCurrentIndex(2)
        presets = [self.window.sticker_preset_combo.itemData(index) for index in range(2)]
        self.assertTrue(all(preset.width > preset.height for preset in presets))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            videos = root / "videos"
            output = root / "output"
            videos.mkdir()
            output.mkdir()
            (videos / "portrait.mp4").write_text("", encoding="utf-8")
            left = root / "left.png"
            right = root / "right.png"
            left.write_text("", encoding="utf-8")
            right.write_text("", encoding="utf-8")
            self.window.sticker_video_folder_edit.setText(str(videos))
            self.window.top_asset_edit.setText(str(left))
            self.window.bottom_asset_edit.setText(str(right))
            self.window.sticker_output_dir_edit.setText(str(output))

            config = self.window._sticker_config_from_ui()

        self.assertIsNotNone(config)
        assert config is not None
        self.assertEqual(config.layout, StickerLayout.LEFT_RIGHT)

    def test_rejects_an_output_directory_that_matches_the_input_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video_a = root / "a.mp4"
            video_a.write_text("", encoding="utf-8")
            self.window.video_a_edit.setText(str(video_a))
            self.window.folder_b_edit.setText(str(root))
            self.window.output_dir_edit.setText(str(root))
            messages: list[str] = []
            self.window._warn = messages.append  # type: ignore[method-assign]

            config = self.window._concat_config_from_ui()

        self.assertIsNone(config)
        self.assertTrue(any("不能与导出目录相同" in message for message in messages))

    def test_running_state_locks_all_configuration_controls(self) -> None:
        self.window._set_running_state(True)
        self.assertFalse(self.window.config_stack.isEnabled())
        self.assertFalse(self.window.mode_combo.isEnabled())
        self.window._set_running_state(False)
        self.assertTrue(self.window.config_stack.isEnabled())
        self.assertTrue(self.window.mode_combo.isEnabled())


if __name__ == "__main__":
    unittest.main()
