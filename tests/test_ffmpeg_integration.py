from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from batch_ab_video.ffmpeg_tools import FFmpegTools
from batch_ab_video.models import BITRATE_PRESETS, OUTPUT_PRESETS, BatchConfig, StickerConfig, StickerLayout
from batch_ab_video.processor import BatchProcessor


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe not available")
class FFmpegIntegrationTests(unittest.TestCase):
    def test_processes_one_video_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            b_folder = root / "b"
            out_folder = root / "out"
            b_folder.mkdir()
            out_folder.mkdir()
            video_a = root / "a.mp4"
            video_b = b_folder / "b.mp4"

            _make_sample(video_a, "red", "440")
            _make_sample(video_b, "blue", "880")

            config = BatchConfig(video_a, b_folder, out_folder, _preset(1280, 720), BITRATE_PRESETS[2])
            processor = BatchProcessor(FFmpegTools.discover())
            tasks = processor.build_tasks(config)
            results = processor.run(
                config=config,
                tasks=tasks,
                cancel_event=_UnsetEvent(),
                pause_event=_UnsetEvent(),
            )

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].ok, results[0].message)
            self.assertTrue(results[0].task.output_path.exists())

    def test_processes_one_sticker_video_without_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video_folder = root / "videos"
            out_folder = root / "out"
            for folder in (video_folder, out_folder):
                folder.mkdir()
            video = video_folder / "clip.mp4"
            top_asset = root / "top.png"
            bottom_asset = root / "bottom.png"

            _make_sample_no_audio(video, "green")
            _make_image(top_asset, "yellow")
            _make_image(bottom_asset, "purple")

            config = StickerConfig(
                video_folder=video_folder,
                top_source=top_asset,
                bottom_source=bottom_asset,
                output_dir=out_folder,
                preset=_preset(1280, 720),
                bitrate=BITRATE_PRESETS[2],
            )
            processor = BatchProcessor(FFmpegTools.discover())
            tasks = processor.build_sticker_tasks(config)
            results = processor.run_sticker(
                config=config,
                tasks=tasks,
                cancel_event=_UnsetEvent(),
                pause_event=_UnsetEvent(),
            )
            output = results[0].task.output_path
            info = _probe(output)

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].ok, results[0].message)
            self.assertEqual(_video_stream_value(info, "width"), 1280)
            self.assertEqual(_video_stream_value(info, "height"), 720)
            self.assertFalse(any(stream.get("codec_type") == "audio" for stream in info["streams"]))
            self.assertAlmostEqual(float(info["format"]["duration"]), 0.4, delta=0.2)

    def test_processes_landscape_sticker_with_a_centered_portrait_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video_folder = root / "videos"
            out_folder = root / "out"
            for folder in (video_folder, out_folder):
                folder.mkdir()
            video = video_folder / "portrait.mp4"
            left_asset = root / "left.png"
            right_asset = root / "right.png"

            _make_sample_no_audio(video, "green", "320x480")
            _make_image(left_asset, "yellow")
            _make_image(right_asset, "purple")

            config = StickerConfig(
                video_folder=video_folder,
                top_source=left_asset,
                bottom_source=right_asset,
                output_dir=out_folder,
                preset=_preset(1280, 720),
                bitrate=BITRATE_PRESETS[2],
                layout=StickerLayout.LEFT_RIGHT,
            )
            processor = BatchProcessor(FFmpegTools.discover())
            tasks = processor.build_sticker_tasks(config)
            results = processor.run_sticker(config, tasks, _UnsetEvent(), _UnsetEvent())
            info = _probe(results[0].task.output_path)

            self.assertTrue(results[0].ok, results[0].message)
            self.assertEqual(_video_stream_value(info, "width"), 1280)
            self.assertEqual(_video_stream_value(info, "height"), 720)
            self.assertFalse(any(stream.get("codec_type") == "audio" for stream in info["streams"]))

    def test_processes_sticker_looping_a_short_video_asset_to_fill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video_folder = root / "videos"
            out_folder = root / "out"
            for folder in (video_folder, out_folder):
                folder.mkdir()
            video = video_folder / "clip.mp4"
            top_video = root / "top_loop.mp4"
            bottom_asset = root / "bottom.png"

            _make_sample_no_audio(video, "green", "320x240", duration="1.0")
            _make_sample_no_audio(top_video, "red", "320x180", duration="0.3")
            _make_image(bottom_asset, "purple")

            config = StickerConfig(
                video_folder=video_folder,
                top_source=top_video,
                bottom_source=bottom_asset,
                output_dir=out_folder,
                preset=OUTPUT_PRESETS[0],
                bitrate=BITRATE_PRESETS[2],
            )
            processor = BatchProcessor(FFmpegTools.discover())
            tasks = processor.build_sticker_tasks(config)
            results = processor.run_sticker(
                config=config,
                tasks=tasks,
                cancel_event=_UnsetEvent(),
                pause_event=_UnsetEvent(),
            )
            output = results[0].task.output_path
            info = _probe(output)

            self.assertEqual(len(results), 1)
            self.assertTrue(results[0].ok, results[0].message)
            self.assertEqual(_video_stream_value(info, "width"), 1080)
            self.assertEqual(_video_stream_value(info, "height"), 1920)
            self.assertFalse(any(stream.get("codec_type") == "audio" for stream in info["streams"]))
            # The output follows the main video length; the 0.3s top video loops to fill it.
            self.assertAlmostEqual(float(info["format"]["duration"]), 1.0, delta=0.3)

    def test_processes_sticker_at_vertical_720p_preset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            video_folder = root / "videos"
            out_folder = root / "out"
            for folder in (video_folder, out_folder):
                folder.mkdir()
            video = video_folder / "clip.mp4"
            top_asset = root / "top.png"
            bottom_asset = root / "bottom.png"

            _make_sample_no_audio(video, "green")
            _make_image(top_asset, "yellow")
            _make_image(bottom_asset, "purple")

            preset = next(p for p in OUTPUT_PRESETS if (p.width, p.height) == (720, 1280))
            config = StickerConfig(
                video_folder=video_folder,
                top_source=top_asset,
                bottom_source=bottom_asset,
                output_dir=out_folder,
                preset=preset,
                bitrate=BITRATE_PRESETS[2],
            )
            processor = BatchProcessor(FFmpegTools.discover())
            tasks = processor.build_sticker_tasks(config)
            results = processor.run_sticker(
                config=config,
                tasks=tasks,
                cancel_event=_UnsetEvent(),
                pause_event=_UnsetEvent(),
            )
            info = _probe(results[0].task.output_path)

            self.assertTrue(results[0].ok, results[0].message)
            self.assertEqual(_video_stream_value(info, "width"), 720)
            self.assertEqual(_video_stream_value(info, "height"), 1280)
            self.assertFalse(any(stream.get("codec_type") == "audio" for stream in info["streams"]))


class _UnsetEvent:
    def is_set(self) -> bool:
        return False


def _preset(width: int, height: int):
    return next(p for p in OUTPUT_PRESETS if (p.width, p.height) == (width, height))


def _make_sample(path: Path, color: str, frequency: str) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=320x240:d=0.4:r=30",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:duration=0.4",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)


def _make_sample_no_audio(path: Path, color: str, size: str = "320x240", duration: str = "0.4") -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={size}:d={duration}:r=30",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)


def _make_image(path: Path, color: str) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=640x180",
        "-frames:v",
        "1",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)


def _probe(path: Path) -> dict:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


def _video_stream_value(info: dict, key: str) -> int:
    for stream in info["streams"]:
        if stream.get("codec_type") == "video":
            return int(stream[key])
    raise AssertionError("no video stream")


if __name__ == "__main__":
    unittest.main()
