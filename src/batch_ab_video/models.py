from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi"}
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass(frozen=True)
class OutputPreset:
    label: str
    width: int
    height: int
    fps: int

    @property
    def display_name(self) -> str:
        return f"{self.label} ({self.width}x{self.height} {self.fps}fps)"


@dataclass(frozen=True)
class BitratePreset:
    label: str
    mbps: int

    @property
    def display_name(self) -> str:
        return f"{self.label} ({self.mbps} Mbps)"

    @property
    def ffmpeg_value(self) -> str:
        return f"{self.mbps}M"

    @property
    def ffmpeg_buffer_value(self) -> str:
        return f"{self.mbps * 2}M"


OUTPUT_PRESETS: tuple[OutputPreset, ...] = (
    OutputPreset("竖屏 1080x1920", 1080, 1920, 30),
    OutputPreset("横屏 1920x1080", 1920, 1080, 30),
    OutputPreset("方屏 1080x1080", 1080, 1080, 30),
    OutputPreset("横屏 1280x720", 1280, 720, 30),
)


BITRATE_PRESETS: tuple[BitratePreset, ...] = (
    BitratePreset("高码率", 20),
    BitratePreset("中码率", 15),
    BitratePreset("低码率", 8),
)


class TaskStatus(str, Enum):
    PENDING = "等待中"
    RUNNING = "处理中"
    SUCCESS = "完成"
    FAILED = "失败"
    CANCELED = "已取消"


class StickerLayout(str, Enum):
    TOP_BOTTOM = "top_bottom"
    LEFT_RIGHT = "left_right"


@dataclass(frozen=True)
class BatchConfig:
    video_a: Path
    folder_b: Path
    output_dir: Path
    preset: OutputPreset
    bitrate: BitratePreset


@dataclass(frozen=True)
class VideoTask:
    index: int
    total: int
    video_b: Path
    output_path: Path

    @property
    def input_path(self) -> Path:
        return self.video_b

    @property
    def asset_label(self) -> str:
        return ""


@dataclass(frozen=True)
class StickerConfig:
    video_folder: Path
    top_source: Path
    bottom_source: Path
    output_dir: Path
    preset: OutputPreset
    bitrate: BitratePreset
    random_seed: int | None = None
    layout: StickerLayout = StickerLayout.TOP_BOTTOM


@dataclass(frozen=True)
class StickerTask:
    index: int
    total: int
    video_path: Path
    top_asset: Path
    bottom_asset: Path
    output_path: Path

    @property
    def input_path(self) -> Path:
        return self.video_path

    @property
    def asset_label(self) -> str:
        return f"{self.top_asset.name} / {self.bottom_asset.name}"


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_audio: bool


@dataclass(frozen=True)
class TaskResult:
    task: VideoTask | StickerTask
    status: TaskStatus
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status == TaskStatus.SUCCESS
