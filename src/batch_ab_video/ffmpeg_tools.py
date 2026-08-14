from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from .models import (
    SUPPORTED_IMAGE_EXTENSIONS,
    BatchConfig,
    MediaInfo,
    StickerConfig,
    StickerLayout,
    StickerTask,
    VideoTask,
)

ProgressCallback = Callable[[float], None]
CancelCheck = Callable[[], bool]

PROBE_TIMEOUT_SECONDS = 30.0
AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNEL_LAYOUT = "stereo"
AUDIO_BITRATE = "192k"
PROGRESS_LINE_LIMIT = 40
ERROR_LINE_LIMIT = 12
ERROR_DETAIL_LIMIT = 2000


class FFmpegError(RuntimeError):
    pass


class FFmpegTools:
    def __init__(self, ffmpeg: Path, ffprobe: Path) -> None:
        self.ffmpeg = Path(ffmpeg)
        self.ffprobe = Path(ffprobe)
        self._current_process: subprocess.Popen[str] | None = None
        self._process_lock = threading.Lock()

    @classmethod
    def discover(cls) -> "FFmpegTools":
        return cls(
            ffmpeg=_find_executable("ffmpeg"),
            ffprobe=_find_executable("ffprobe"),
        )

    def probe_media(self, path: Path) -> MediaInfo:
        command = [
            str(self.ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type",
            "-of",
            "json",
            str(path),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=PROBE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise FFmpegError(f"读取媒体信息超时: {path}") from exc
        if completed.returncode != 0:
            raise FFmpegError(completed.stderr.strip() or f"ffprobe failed: {path}")

        try:
            payload = json.loads(completed.stdout)
            duration = float(payload.get("format", {}).get("duration") or 0)
            streams = payload.get("streams", [])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise FFmpegError(f"无法读取媒体信息: {path}") from exc

        if duration <= 0:
            raise FFmpegError(f"视频时长无效: {path}")

        return MediaInfo(
            duration=duration,
            has_audio=any(stream.get("codec_type") == "audio" for stream in streams),
        )

    def build_concat_command(
        self,
        config: BatchConfig,
        task: VideoTask,
        info_a: MediaInfo,
        info_b: MediaInfo,
    ) -> list[str]:
        preset = config.preset
        inputs = ["-i", str(config.video_a), "-i", str(task.video_b)]
        next_input_index = 2

        if info_a.has_audio:
            audio_a = "[0:a:0]"
        else:
            inputs.extend(
                [
                    "-f",
                    "lavfi",
                    "-t",
                    _seconds(info_a.duration),
                    "-i",
                    _silent_audio_source(),
                ]
            )
            audio_a = f"[{next_input_index}:a:0]"
            next_input_index += 1

        if info_b.has_audio:
            audio_b = "[1:a:0]"
        else:
            inputs.extend(
                [
                    "-f",
                    "lavfi",
                    "-t",
                    _seconds(info_b.duration),
                    "-i",
                    _silent_audio_source(),
                ]
            )
            audio_b = f"[{next_input_index}:a:0]"

        video_filter = (
            "setpts=PTS-STARTPTS,"
            f"scale={preset.width}:{preset.height}:force_original_aspect_ratio=decrease,"
            f"pad={preset.width}:{preset.height}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={preset.fps},format=yuv420p"
        )
        filter_complex = ";".join(
            [
                f"[0:v:0]{video_filter}[v0]",
                f"[1:v:0]{video_filter}[v1]",
                f"{audio_a}{_audio_filter()}[a0]",
                f"{audio_b}{_audio_filter()}[a1]",
                "[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]",
            ]
        )

        return [
            str(self.ffmpeg),
            "-hide_banner",
            "-y",
            "-nostats",
            "-progress",
            "pipe:1",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-b:v",
            config.bitrate.ffmpeg_value,
            "-maxrate",
            config.bitrate.ffmpeg_value,
            "-bufsize",
            config.bitrate.ffmpeg_buffer_value,
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(preset.fps),
            "-c:a",
            "aac",
            "-b:a",
            AUDIO_BITRATE,
            "-movflags",
            "+faststart",
            str(task.output_path),
        ]

    def concat(
        self,
        config: BatchConfig,
        task: VideoTask,
        info_a: MediaInfo,
        info_b: MediaInfo,
        on_progress: ProgressCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> None:
        task.output_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path = _temporary_output_path(task.output_path)
        command = self.build_concat_command(config, task, info_a, info_b)
        command[-1] = str(partial_path)
        total_duration = max(info_a.duration + info_b.duration, 0.01)
        try:
            self._run_ffmpeg(command, total_duration, on_progress, cancel_check)
            _promote_partial_output(partial_path, task.output_path)
        finally:
            partial_path.unlink(missing_ok=True)

    def build_sticker_command(
        self,
        config: StickerConfig,
        task: StickerTask,
        info: MediaInfo,
    ) -> list[str]:
        preset = config.preset
        duration = _seconds(info.duration)
        inputs = ["-i", str(task.video_path)]
        inputs.extend(_sticker_input_options(task.top_asset, duration))
        inputs.extend(_sticker_input_options(task.bottom_asset, duration))

        filter_complex = ";".join(_sticker_video_filters(config, info.duration))

        return [
            str(self.ffmpeg),
            "-hide_banner",
            "-y",
            "-nostats",
            "-progress",
            "pipe:1",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[outv]",
            "-t",
            duration,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-b:v",
            config.bitrate.ffmpeg_value,
            "-maxrate",
            config.bitrate.ffmpeg_value,
            "-bufsize",
            config.bitrate.ffmpeg_buffer_value,
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(preset.fps),
            "-movflags",
            "+faststart",
            str(task.output_path),
        ]

    def render_sticker(
        self,
        config: StickerConfig,
        task: StickerTask,
        info: MediaInfo,
        on_progress: ProgressCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> None:
        task.output_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path = _temporary_output_path(task.output_path)
        command = self.build_sticker_command(config, task, info)
        command[-1] = str(partial_path)
        try:
            self._run_ffmpeg(command, max(info.duration, 0.01), on_progress, cancel_check)
            _promote_partial_output(partial_path, task.output_path)
        finally:
            partial_path.unlink(missing_ok=True)

    def cancel_current(self) -> None:
        with self._process_lock:
            process = self._current_process
        if process is None or process.poll() is not None:
            return

        try:
            if os.name == "nt":
                process.terminate()
            else:
                process.send_signal(signal.SIGTERM)
        except OSError:
            return
        threading.Thread(target=_kill_after_timeout, args=(process,), daemon=True).start()

    def _run_ffmpeg(
        self,
        command: list[str],
        total_duration: float,
        on_progress: ProgressCallback | None,
        cancel_check: CancelCheck | None = None,
    ) -> None:
        if cancel_check is not None and cancel_check():
            raise FFmpegError("任务已取消")

        lines: list[str] = []
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        with self._process_lock:
            self._current_process = process

        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                if cancel_check is not None and cancel_check():
                    _terminate_process(process)
                    break
                line = raw_line.strip()
                if line:
                    lines.append(line)
                    lines = lines[-PROGRESS_LINE_LIMIT:]
                    progress = _parse_progress(line, total_duration)
                    if progress is not None and on_progress:
                        on_progress(progress)
            return_code = process.wait()
        finally:
            if process.stdout:
                process.stdout.close()
            with self._process_lock:
                if self._current_process is process:
                    self._current_process = None

        if return_code != 0:
            detail = "\n".join(lines[-ERROR_LINE_LIMIT:]).strip()[:ERROR_DETAIL_LIMIT]
            raise FFmpegError(detail or f"ffmpeg failed with exit code {return_code}")
        if on_progress:
            on_progress(100.0)


def _find_executable(name: str) -> Path:
    filename = f"{name}.exe" if os.name == "nt" else name
    search_roots = [
        Path(getattr(sys, "_MEIPASS", Path.cwd())),
        Path(sys.executable).resolve().parent,
        Path(__file__).resolve().parents[2],
        Path.cwd(),
    ]

    for root in search_roots:
        candidate = root / "tools" / "ffmpeg" / "bin" / filename
        if candidate.exists():
            return candidate

    found = shutil.which(name)
    if found:
        return Path(found)

    raise FFmpegError(f"找不到 {filename}，请放入 tools/ffmpeg/bin 或加入系统 PATH。")


def _seconds(value: float) -> str:
    return f"{max(value, 0.01):.3f}"


def _temporary_output_path(output_path: Path) -> Path:
    output_path = Path(output_path)
    if output_path.exists():
        raise FFmpegError(f"输出文件已存在，拒绝覆盖: {output_path}")
    token = uuid.uuid4().hex
    return output_path.with_name(f"{output_path.stem}.{token}.partial{output_path.suffix}")


def _promote_partial_output(partial_path: Path, output_path: Path) -> None:
    if not partial_path.is_file():
        raise FFmpegError(f"未生成临时输出文件: {partial_path}")
    if output_path.exists():
        raise FFmpegError(f"输出文件已存在，拒绝覆盖: {output_path}")
    partial_path.rename(output_path)


def _kill_after_timeout(process: subprocess.Popen[str], timeout_seconds: float = 3.0) -> None:
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass


def _terminate_process(process: subprocess.Popen[str]) -> None:
    try:
        process.terminate()
    except OSError:
        return
    threading.Thread(target=_kill_after_timeout, args=(process,), daemon=True).start()


def _silent_audio_source() -> str:
    return (
        f"anullsrc=channel_layout={AUDIO_CHANNEL_LAYOUT}:"
        f"sample_rate={AUDIO_SAMPLE_RATE}"
    )


def _audio_filter() -> str:
    return (
        f"aresample={AUDIO_SAMPLE_RATE},"
        f"aformat=sample_fmts=fltp:channel_layouts={AUDIO_CHANNEL_LAYOUT},"
        "asetpts=PTS-STARTPTS"
    )


def _sticker_input_options(asset: Path, duration: str) -> list[str]:
    """Input options for one sticker asset.

    Images are looped by the image2 demuxer for the main duration. Videos are
    looped infinitely via -stream_loop; the output -t bounds the total length,
    so a sticker video shorter than the main video repeats to fill it.
    """
    asset = Path(asset)
    if asset.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
        return ["-loop", "1", "-t", duration, "-i", str(asset)]
    return ["-stream_loop", "-1", "-i", str(asset)]


def _sticker_video_filters(config: StickerConfig, duration: float) -> list[str]:
    if config.layout == StickerLayout.LEFT_RIGHT:
        return _landscape_sticker_video_filters(config, duration)

    preset = config.preset
    width = preset.width
    height = preset.height
    top_h, video_h, bottom_h = _sticker_region_heights(width, height)
    video_h = max(1, height - top_h - bottom_h)
    duration_value = _seconds(duration)

    return [
        f"color=c=black:s={width}x{height}:d={duration_value}:r={preset.fps}[canvas]",
        (
            f"[0:v:0]setpts=PTS-STARTPTS,scale={width}:{video_h}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{video_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"fps={preset.fps},format=rgba[video]"
        ),
        (
            f"[1:v:0]setpts=PTS-STARTPTS,scale={width}:{top_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{top_h},"
            "format=rgba[top]"
        ),
        (
            f"[2:v:0]setpts=PTS-STARTPTS,scale={width}:{bottom_h}:force_original_aspect_ratio=increase,"
            f"crop={width}:{bottom_h},"
            "format=rgba[bottom]"
        ),
        f"[canvas][video]overlay=0:{top_h}:eof_action=pass[withvideo]",
        (
            "[withvideo][top]overlay=(W-w)/2:"
            f"({top_h}-h)/2:eof_action=pass[withtop]"
        ),
        (
            "[withtop][bottom]overlay=(W-w)/2:"
            f"{top_h + video_h}+({bottom_h}-h)/2:"
            "eof_action=pass,format=yuv420p[outv]"
        ),
    ]


def _landscape_sticker_video_filters(config: StickerConfig, duration: float) -> list[str]:
    preset = config.preset
    width = preset.width
    height = preset.height
    left_w, video_w, right_w = _sticker_region_widths(width, height)
    duration_value = _seconds(duration)

    return [
        f"color=c=black:s={width}x{height}:d={duration_value}:r={preset.fps}[canvas]",
        (
            f"[0:v:0]setpts=PTS-STARTPTS,scale={video_w}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={video_w}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,"
            f"fps={preset.fps},format=rgba[video]"
        ),
        (
            f"[1:v:0]setpts=PTS-STARTPTS,scale={left_w}:{height}:force_original_aspect_ratio=increase,"
            f"crop={left_w}:{height},format=rgba[left]"
        ),
        (
            f"[2:v:0]setpts=PTS-STARTPTS,scale={right_w}:{height}:force_original_aspect_ratio=increase,"
            f"crop={right_w}:{height},format=rgba[right]"
        ),
        "[canvas][left]overlay=0:0:eof_action=pass[withleft]",
        f"[withleft][video]overlay={left_w}:0:eof_action=pass[withvideo]",
        (
            f"[withvideo][right]overlay={left_w + video_w}:0:"
            "eof_action=pass,format=yuv420p[outv]"
        ),
    ]


def _sticker_region_heights(width: int, height: int) -> tuple[int, int, int]:
    target_video_h = _even_up(round(width * 9 / 16))
    if target_video_h > height - 4:
        target_video_h = _even(max(2, height - 4))

    remaining = max(4, height - target_video_h)
    top_h = _even(max(2, remaining // 2))
    bottom_h = max(2, height - target_video_h - top_h)
    bottom_h = _even(bottom_h)
    video_h = max(2, height - top_h - bottom_h)
    return top_h, video_h, bottom_h


def _sticker_region_widths(width: int, height: int) -> tuple[int, int, int]:
    target_video_w = _even(round(height * 9 / 16))
    target_video_w = min(target_video_w, _even(max(2, width - 4)))
    remaining = max(4, width - target_video_w)
    left_w = _even(max(2, remaining // 2))
    right_w = _even(max(2, width - target_video_w - left_w))
    video_w = max(2, width - left_w - right_w)
    return left_w, video_w, right_w


def _even(value: int) -> int:
    return max(2, value - value % 2)


def _even_up(value: int) -> int:
    return max(2, value + value % 2)


def _parse_progress(line: str, total_duration: float) -> float | None:
    if line.startswith("out_time_ms="):
        try:
            elapsed = int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    elif line.startswith("out_time_us="):
        try:
            elapsed = int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    elif line == "progress=end":
        return 100.0
    else:
        return None

    return max(0.0, min(100.0, elapsed / total_duration * 100))
