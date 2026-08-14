from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from batch_ab_video.ffmpeg_tools import FFmpegError, FFmpegTools, _parse_progress
from batch_ab_video.logging_utils import write_export_log
from batch_ab_video.models import (
    BITRATE_PRESETS,
    OUTPUT_PRESETS,
    BatchConfig,
    MediaInfo,
    StickerConfig,
    StickerLayout,
    TaskResult,
    TaskStatus,
    StickerTask,
    VideoTask,
)
from batch_ab_video.processor import BatchProcessor, _build_balanced_asset_pairs
from batch_ab_video.paths import (
    discover_images,
    discover_media,
    discover_videos,
    make_output_path,
    make_sticker_output_path,
)


class PathTests(unittest.TestCase):
    def test_discover_videos_filters_and_sorts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            video_a = folder / "a.mp4"
            video_a.write_text("", encoding="utf-8")
            (folder / "z.mov").write_text("", encoding="utf-8")
            (folder / "A.avi").write_text("", encoding="utf-8")
            (folder / "note.txt").write_text("", encoding="utf-8")

            videos = discover_videos(folder, video_a)

        self.assertEqual([path.name for path in videos], ["A.avi", "z.mov"])

    def test_discover_media_returns_images_and_videos(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "a.mp4").write_text("", encoding="utf-8")
            (folder / "b.png").write_text("", encoding="utf-8")
            (folder / "note.txt").write_text("", encoding="utf-8")

            media = discover_media(folder)

        self.assertEqual([path.name for path in media], ["a.mp4", "b.png"])

    def test_make_output_path_uses_b_name_and_avoids_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            existing = folder / "demo_合成.mp4"
            existing.write_text("", encoding="utf-8")
            used: set[Path] = set()

            first = make_output_path(Path("demo.mov"), folder, used)
            second = make_output_path(Path("demo.mov"), folder, used)

        self.assertEqual(first.name, "demo_合成_1.mp4")
        self.assertEqual(second.name, "demo_合成_2.mp4")

    def test_make_sticker_output_path_uses_video_name_and_avoids_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            existing = folder / "demo_贴片.mp4"
            existing.write_text("", encoding="utf-8")
            used: set[Path] = set()

            first = make_sticker_output_path(Path("demo.mov"), folder, used)
            second = make_sticker_output_path(Path("demo.mov"), folder, used)

        self.assertEqual(first.name, "demo_贴片_1.mp4")
        self.assertEqual(second.name, "demo_贴片_2.mp4")

    def test_discover_images_filters_and_sorts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / "b.jpg").write_text("", encoding="utf-8")
            (folder / "A.png").write_text("", encoding="utf-8")
            (folder / "note.txt").write_text("", encoding="utf-8")

            images = discover_images(folder)

        self.assertEqual([path.name for path in images], ["A.png", "b.jpg"])


class FFmpegCommandTests(unittest.TestCase):
    def test_build_concat_command_normalizes_video_adds_silent_audio_and_uses_bitrate(self) -> None:
        tools = FFmpegTools(Path("ffmpeg"), Path("ffprobe"))
        config = BatchConfig(
            video_a=Path("a.mp4"),
            folder_b=Path("b"),
            output_dir=Path("out"),
            preset=OUTPUT_PRESETS[0],
            bitrate=BITRATE_PRESETS[1],
        )
        task = VideoTask(index=1, total=1, video_b=Path("b/demo.mp4"), output_path=Path("out/demo_合成.mp4"))

        command = tools.build_concat_command(
            config=config,
            task=task,
            info_a=MediaInfo(duration=1.0, has_audio=False),
            info_b=MediaInfo(duration=2.0, has_audio=True),
        )
        command_text = " ".join(command)

        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000", command_text)
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=decrease", command_text)
        self.assertIn("concat=n=2:v=1:a=1", command_text)
        self.assertIn("setpts=PTS-STARTPTS", command_text)
        self.assertIn("asetpts=PTS-STARTPTS", command_text)
        self.assertIn("-b:v 15M", command_text)
        self.assertIn("-maxrate 15M", command_text)
        self.assertIn("-bufsize 30M", command_text)
        self.assertNotIn("-crf", command)
        self.assertEqual(command[-1], str(task.output_path))

    def test_build_landscape_sticker_command_places_images_on_both_sides(self) -> None:
        tools = FFmpegTools(Path("ffmpeg"), Path("ffprobe"))
        config = StickerConfig(
            video_folder=Path("videos"),
            top_source=Path("left.png"),
            bottom_source=Path("right.png"),
            output_dir=Path("out"),
            preset=OUTPUT_PRESETS[1],
            bitrate=BITRATE_PRESETS[1],
            layout=StickerLayout.LEFT_RIGHT,
        )
        task = StickerTask(
            index=1,
            total=1,
            video_path=Path("videos/portrait.mp4"),
            top_asset=Path("left.png"),
            bottom_asset=Path("right.png"),
            output_path=Path("out/portrait_贴片.mp4"),
        )

        command = tools.build_sticker_command(
            config,
            task,
            MediaInfo(duration=2.0, has_audio=True),
        )
        command_text = " ".join(command)

        self.assertIn("scale=608:1080:force_original_aspect_ratio=decrease", command_text)
        self.assertIn("scale=656:1080:force_original_aspect_ratio=increase", command_text)
        self.assertIn("overlay=656:0", command_text)
        self.assertIn("overlay=1264:0", command_text)

    def test_concat_refuses_to_overwrite_an_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "existing.mp4"
            output.write_text("keep", encoding="utf-8")
            config = BatchConfig(Path("a.mp4"), root, root, OUTPUT_PRESETS[0], BITRATE_PRESETS[0])
            task = VideoTask(index=1, total=1, video_b=Path("b.mp4"), output_path=output)

            with self.assertRaisesRegex(FFmpegError, "拒绝覆盖"):
                FFmpegTools(Path("ffmpeg"), Path("ffprobe")).concat(
                    config,
                    task,
                    MediaInfo(duration=1.0, has_audio=True),
                    MediaInfo(duration=1.0, has_audio=True),
                )
            self.assertEqual(output.read_text(encoding="utf-8"), "keep")

    def test_concat_cancel_before_start_leaves_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / "out.mp4"
            config = BatchConfig(Path("a.mp4"), root, root, OUTPUT_PRESETS[0], BITRATE_PRESETS[0])
            task = VideoTask(index=1, total=1, video_b=Path("b.mp4"), output_path=output)

            with self.assertRaisesRegex(FFmpegError, "已取消"):
                FFmpegTools(Path("ffmpeg"), Path("ffprobe")).concat(
                    config,
                    task,
                    MediaInfo(duration=1.0, has_audio=True),
                    MediaInfo(duration=1.0, has_audio=True),
                    cancel_check=lambda: True,
                )

            self.assertFalse(output.exists())
            self.assertEqual([p.name for p in root.iterdir() if ".partial" in p.name], [])

    def test_build_sticker_command_overlays_images_and_outputs_no_audio(self) -> None:
        tools = FFmpegTools(Path("ffmpeg"), Path("ffprobe"))
        config = StickerConfig(
            video_folder=Path("videos"),
            top_source=Path("top.png"),
            bottom_source=Path("bottom.png"),
            output_dir=Path("out"),
            preset=OUTPUT_PRESETS[0],
            bitrate=BITRATE_PRESETS[2],
        )
        task = StickerTask(
            index=1,
            total=1,
            video_path=Path("videos/demo.mp4"),
            top_asset=Path("top/1.png"),
            bottom_asset=Path("bottom/1.png"),
            output_path=Path("out/demo_贴片.mp4"),
        )

        command = tools.build_sticker_command(
            config=config,
            task=task,
            info=MediaInfo(duration=2.0, has_audio=False),
        )
        command_text = " ".join(command)

        self.assertIn("-loop 1", command_text)
        self.assertNotIn("anullsrc", command_text)
        self.assertNotIn("-c:a", command_text)
        self.assertIn(" -an ", command_text)
        self.assertIn("color=c=black:s=1080x1920", command_text)
        self.assertIn("scale=1080:608:force_original_aspect_ratio=decrease", command_text)
        self.assertIn("pad=1080:608:(ow-iw)/2:(oh-ih)/2", command_text)
        self.assertIn("scale=1080:656:force_original_aspect_ratio=increase", command_text)
        self.assertIn("crop=1080:656", command_text)
        self.assertNotIn("crop=1080:608", command_text)
        self.assertIn("overlay=(W-w)/2:", command_text)
        self.assertIn("-b:v 8M", command_text)
        self.assertEqual(command[-1], str(task.output_path))

    def test_build_sticker_command_loops_video_assets_and_outputs_no_audio(self) -> None:
        tools = FFmpegTools(Path("ffmpeg"), Path("ffprobe"))
        config = StickerConfig(
            video_folder=Path("videos"),
            top_source=Path("top.mp4"),
            bottom_source=Path("bottom.png"),
            output_dir=Path("out"),
            preset=OUTPUT_PRESETS[0],
            bitrate=BITRATE_PRESETS[1],
        )
        task = StickerTask(
            index=1,
            total=1,
            video_path=Path("videos/demo.mp4"),
            top_asset=Path("top/loop.mp4"),
            bottom_asset=Path("bottom/1.png"),
            output_path=Path("out/demo_贴片.mp4"),
        )

        command = tools.build_sticker_command(
            config,
            task,
            MediaInfo(duration=5.0, has_audio=True),
        )
        command_text = " ".join(command)

        self.assertIn("-stream_loop -1", command_text)
        self.assertIn(str(Path("top/loop.mp4")), command_text)
        self.assertIn("-loop 1 -t 5.000", command_text)
        self.assertIn(str(Path("bottom/1.png")), command_text)
        self.assertIn(" -an ", command_text)
        self.assertNotIn("-c:a", command_text)
        self.assertNotIn("anullsrc", command_text)
        self.assertEqual(command[-1], str(task.output_path))

    def test_parse_progress_handles_ffmpeg_progress_keys(self) -> None:
        self.assertEqual(_parse_progress("out_time_ms=500000", 10.0), 5.0)
        self.assertEqual(_parse_progress("out_time_us=2500000", 10.0), 25.0)
        self.assertEqual(_parse_progress("progress=end", 10.0), 100.0)
        self.assertIsNone(_parse_progress("frame=100", 10.0))
        self.assertIsNone(_parse_progress("out_time_ms=abc", 10.0))
        self.assertEqual(_parse_progress("out_time_ms=999999999", 1.0), 100.0)
        self.assertEqual(_parse_progress("out_time_ms=-100", 10.0), 0.0)


class StickerTaskTests(unittest.TestCase):
    def test_build_sticker_tasks_uses_single_image_pair_for_every_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            videos = root / "videos"
            out = root / "out"
            for folder in (videos, out):
                folder.mkdir()
            top_image = root / "top.png"
            bottom_image = root / "bottom.png"
            for name in ("01.mp4", "02.mp4", "03.mp4"):
                (videos / name).write_text("", encoding="utf-8")
            top_image.write_text("", encoding="utf-8")
            bottom_image.write_text("", encoding="utf-8")
            config = StickerConfig(videos, top_image, bottom_image, out, OUTPUT_PRESETS[0], BITRATE_PRESETS[0])

            tasks = BatchProcessor(FFmpegTools(Path("ffmpeg"), Path("ffprobe"))).build_sticker_tasks(config)

        self.assertEqual([task.video_path.name for task in tasks], ["01.mp4", "02.mp4", "03.mp4"])
        self.assertEqual([task.top_asset.name for task in tasks], ["top.png", "top.png", "top.png"])
        self.assertEqual([task.bottom_asset.name for task in tasks], ["bottom.png", "bottom.png", "bottom.png"])

    def test_build_sticker_tasks_balances_single_top_with_batch_bottom(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            videos = root / "videos"
            bottom = root / "bottom"
            out = root / "out"
            for folder in (videos, bottom, out):
                folder.mkdir()
            top_image = root / "top.png"
            top_image.write_text("", encoding="utf-8")
            for name in ("01.mp4", "02.mp4", "03.mp4"):
                (videos / name).write_text("", encoding="utf-8")
            for name in ("1.png", "2.png"):
                (bottom / name).write_text("", encoding="utf-8")
            config = StickerConfig(videos, top_image, bottom, out, OUTPUT_PRESETS[0], BITRATE_PRESETS[0])

            tasks = BatchProcessor(FFmpegTools(Path("ffmpeg"), Path("ffprobe"))).build_sticker_tasks(config)

        self.assertEqual([task.top_asset.name for task in tasks], ["top.png", "top.png", "top.png"])
        self.assertEqual(set(task.bottom_asset.name for task in tasks), {"1.png", "2.png"})
        self.assertEqual(len({tasks[0].bottom_asset.name, tasks[1].bottom_asset.name}), 2)

    def test_build_sticker_tasks_uses_balanced_random_pairs_for_batch_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            videos = root / "videos"
            top = root / "top"
            bottom = root / "bottom"
            out = root / "out"
            for folder in (videos, top, bottom, out):
                folder.mkdir()
            for name in ("01.mp4", "02.mp4", "03.mp4", "04.mp4"):
                (videos / name).write_text("", encoding="utf-8")
            for name in ("A.png", "B.png", "C.png", "D.png"):
                (top / name).write_text("", encoding="utf-8")
            for name in ("1.png", "2.png", "3.png", "4.png", "5.png"):
                (bottom / name).write_text("", encoding="utf-8")
            config = StickerConfig(videos, top, bottom, out, OUTPUT_PRESETS[0], BITRATE_PRESETS[0])

            tasks = BatchProcessor(FFmpegTools(Path("ffmpeg"), Path("ffprobe"))).build_sticker_tasks(config)

        self.assertEqual(len(tasks), 4)
        self.assertEqual(len({task.top_asset.name for task in tasks}), 4)
        self.assertEqual(len({task.bottom_asset.name for task in tasks}), 4)
        self.assertNotEqual([task.top_asset.name for task in tasks], ["A.png", "A.png", "A.png", "A.png"])

    def test_build_sticker_tasks_changes_batch_pairs_when_seed_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            videos = root / "videos"
            top = root / "top"
            bottom = root / "bottom"
            out = root / "out"
            for folder in (videos, top, bottom, out):
                folder.mkdir()
            for name in ("01.mp4", "02.mp4", "03.mp4", "04.mp4", "05.mp4", "06.mp4"):
                (videos / name).write_text("", encoding="utf-8")
            for name in ("A.png", "B.png", "C.png", "D.png"):
                (top / name).write_text("", encoding="utf-8")
            for name in ("1.png", "2.png", "3.png", "4.png", "5.png"):
                (bottom / name).write_text("", encoding="utf-8")
            processor = BatchProcessor(FFmpegTools(Path("ffmpeg"), Path("ffprobe")))

            first = processor.build_sticker_tasks(
                StickerConfig(videos, top, bottom, out, OUTPUT_PRESETS[0], BITRATE_PRESETS[0], random_seed=101)
            )
            second = processor.build_sticker_tasks(
                StickerConfig(videos, top, bottom, out, OUTPUT_PRESETS[0], BITRATE_PRESETS[0], random_seed=202)
            )

        first_pairs = [(task.top_asset.name, task.bottom_asset.name) for task in first]
        second_pairs = [(task.top_asset.name, task.bottom_asset.name) for task in second]
        self.assertNotEqual(first_pairs, second_pairs)
        self.assertGreaterEqual(len({pair[0] for pair in first_pairs}), 4)
        self.assertGreaterEqual(len({pair[1] for pair in first_pairs}), 5)

    def test_build_sticker_tasks_returns_empty_for_empty_image_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            videos = root / "videos"
            top = root / "top"
            bottom = root / "bottom"
            out = root / "out"
            for folder in (videos, top, bottom, out):
                folder.mkdir()
            (videos / "01.mp4").write_text("", encoding="utf-8")
            (bottom / "1.png").write_text("", encoding="utf-8")
            config = StickerConfig(videos, top, bottom, out, OUTPUT_PRESETS[0], BITRATE_PRESETS[0])

            tasks = BatchProcessor(FFmpegTools(Path("ffmpeg"), Path("ffprobe"))).build_sticker_tasks(config)

        self.assertEqual(tasks, [])

    def test_build_balanced_asset_pairs_evenly_distributes_and_is_reproducible(self) -> None:
        tops = [Path(f"t{i}.png") for i in range(4)]
        bottoms = [Path(f"b{i}.png") for i in range(5)]

        pairs = _build_balanced_asset_pairs(tops, bottoms, 13, seed=7)

        self.assertEqual(len(pairs), 13)
        top_usage: dict[Path, int] = {}
        bottom_usage: dict[Path, int] = {}
        for top, bottom in pairs:
            top_usage[top] = top_usage.get(top, 0) + 1
            bottom_usage[bottom] = bottom_usage.get(bottom, 0) + 1

        self.assertLessEqual(max(top_usage.values()) - min(top_usage.values()), 1)
        self.assertLessEqual(max(bottom_usage.values()) - min(bottom_usage.values()), 1)
        again = _build_balanced_asset_pairs(tops, bottoms, 13, seed=7)
        self.assertEqual(pairs, again)


class LogTests(unittest.TestCase):
    def test_write_export_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            task = VideoTask(index=1, total=1, video_b=Path("b.mp4"), output_path=folder / "b_合成.mp4")
            log_path = write_export_log(folder, [TaskResult(task, TaskStatus.SUCCESS, "导出完成")])

            with log_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))

        self.assertEqual(rows[0], ["time", "index", "input", "assets", "output", "status", "message"])
        self.assertEqual(rows[1][5], "完成")

    def test_write_export_log_keeps_a_timestamped_history_file_and_latest_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            task = VideoTask(index=1, total=1, video_b=Path("b.mp4"), output_path=folder / "b_合成.mp4")
            history_path = write_export_log(folder, [TaskResult(task, TaskStatus.CANCELED, "任务已取消")])

            self.assertTrue(history_path.exists())
            self.assertTrue(history_path.name.startswith("export_log_"))
            self.assertTrue((folder / "export_log.csv").exists())

    def test_write_export_log_truncates_long_messages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            task = VideoTask(index=1, total=1, video_b=Path("b.mp4"), output_path=folder / "b_合成.mp4")
            log_path = write_export_log(folder, [TaskResult(task, TaskStatus.FAILED, "x" * 2000)])

            with log_path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))

        self.assertEqual(len(rows[1][6]), 500)


if __name__ == "__main__":
    unittest.main()
