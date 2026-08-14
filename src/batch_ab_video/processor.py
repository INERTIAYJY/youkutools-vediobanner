from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .ffmpeg_tools import FFmpegError, FFmpegTools
from .logging_utils import write_export_log
from .models import BatchConfig, StickerConfig, StickerTask, TaskResult, TaskStatus, VideoTask
from .paths import discover_media, discover_videos, make_output_path, make_sticker_output_path


TaskCallback = Callable[[VideoTask | StickerTask, TaskStatus, float, str], None]
SummaryCallback = Callable[[list[TaskResult], Path], None]
TaskProcessor = Callable[[VideoTask | StickerTask], None]


class BatchProcessor:
    def __init__(self, tools: FFmpegTools | None = None) -> None:
        self.tools = tools

    def _get_tools(self) -> FFmpegTools:
        if self.tools is None:
            self.tools = FFmpegTools.discover()
        return self.tools

    def build_tasks(self, config: BatchConfig) -> list[VideoTask]:
        videos = discover_videos(config.folder_b, config.video_a)
        used_paths: set[Path] = set()
        total = len(videos)
        return [
            VideoTask(
                index=index,
                total=total,
                video_b=video,
                output_path=make_output_path(video, config.output_dir, used_paths),
            )
            for index, video in enumerate(videos, start=1)
        ]

    def build_sticker_tasks(self, config: StickerConfig) -> list[StickerTask]:
        videos = discover_videos(config.video_folder)
        top_assets = _resolve_sticker_source(config.top_source)
        bottom_assets = _resolve_sticker_source(config.bottom_source)
        asset_pairs = _build_balanced_asset_pairs(
            top_assets,
            bottom_assets,
            len(videos),
            config.random_seed,
        )
        if not asset_pairs:
            return []

        used_paths: set[Path] = set()
        total = len(videos)
        tasks: list[StickerTask] = []
        for index, video in enumerate(videos, start=1):
            top_asset, bottom_asset = asset_pairs[index - 1]
            tasks.append(
                StickerTask(
                    index=index,
                    total=total,
                    video_path=video,
                    top_asset=top_asset,
                    bottom_asset=bottom_asset,
                    output_path=make_sticker_output_path(video, config.output_dir, used_paths),
                )
            )
        return tasks

    def run(
        self,
        config: BatchConfig,
        tasks: list[VideoTask],
        cancel_event: threading.Event,
        pause_event: threading.Event,
        on_task_update: TaskCallback | None = None,
        on_summary: SummaryCallback | None = None,
    ) -> list[TaskResult]:
        try:
            tools = self._get_tools()
            info_a = tools.probe_media(config.video_a)
        except FFmpegError as exc:
            return self._finish_startup_failure(config, tasks, str(exc), on_task_update, on_summary)

        def process_task(task: VideoTask) -> None:
            info_b = tools.probe_media(task.video_b)
            tools.concat(
                config=config,
                task=task,
                info_a=info_a,
                info_b=info_b,
                on_progress=lambda progress, current=task: _notify(
                    on_task_update,
                    current,
                    TaskStatus.RUNNING,
                    progress,
                    "处理中",
                ),
                cancel_check=cancel_event.is_set,
            )

        return self._run_tasks(
            config,
            tasks,
            cancel_event,
            pause_event,
            on_task_update,
            on_summary,
            process_task,
        )

    def run_sticker(
        self,
        config: StickerConfig,
        tasks: list[StickerTask],
        cancel_event: threading.Event,
        pause_event: threading.Event,
        on_task_update: TaskCallback | None = None,
        on_summary: SummaryCallback | None = None,
    ) -> list[TaskResult]:
        try:
            tools = self._get_tools()
        except FFmpegError as exc:
            return self._finish_startup_failure(config, tasks, str(exc), on_task_update, on_summary)

        def process_task(task: StickerTask) -> None:
            info = tools.probe_media(task.video_path)
            tools.render_sticker(
                config=config,
                task=task,
                info=info,
                on_progress=lambda progress, current=task: _notify(
                    on_task_update,
                    current,
                    TaskStatus.RUNNING,
                    progress,
                    "处理中",
                ),
                cancel_check=cancel_event.is_set,
            )

        return self._run_tasks(
            config,
            tasks,
            cancel_event,
            pause_event,
            on_task_update,
            on_summary,
            process_task,
        )

    def _run_tasks(
        self,
        config: BatchConfig | StickerConfig,
        tasks: list[VideoTask | StickerTask],
        cancel_event: threading.Event,
        pause_event: threading.Event,
        on_task_update: TaskCallback | None,
        on_summary: SummaryCallback | None,
        process_task: TaskProcessor,
    ) -> list[TaskResult]:
        results: list[TaskResult] = []
        for task in tasks:
            if cancel_event.is_set():
                results.append(TaskResult(task, TaskStatus.CANCELED, "任务已取消"))
                _notify(on_task_update, task, TaskStatus.CANCELED, 0, "任务已取消")
                continue

            while pause_event.is_set() and not cancel_event.is_set():
                time.sleep(0.2)

            if cancel_event.is_set():
                results.append(TaskResult(task, TaskStatus.CANCELED, "任务已取消"))
                _notify(on_task_update, task, TaskStatus.CANCELED, 0, "任务已取消")
                continue

            _notify(on_task_update, task, TaskStatus.RUNNING, 0, "开始处理")
            try:
                process_task(task)
            except FFmpegError as exc:
                if cancel_event.is_set():
                    results.append(TaskResult(task, TaskStatus.CANCELED, "任务已取消"))
                    _notify(on_task_update, task, TaskStatus.CANCELED, 0, "任务已取消")
                    continue
                message = str(exc).strip() or "FFmpeg 处理失败"
                results.append(TaskResult(task, TaskStatus.FAILED, message))
                _notify(on_task_update, task, TaskStatus.FAILED, 0, message)
                continue
            except Exception as exc:  # noqa: BLE001 - UI needs a readable per-file failure.
                message = str(exc).strip() or exc.__class__.__name__
                results.append(TaskResult(task, TaskStatus.FAILED, message))
                _notify(on_task_update, task, TaskStatus.FAILED, 0, message)
                continue

            # A cancel may have landed right after ffmpeg finished; honor it
            # instead of reporting a success that the user tried to stop.
            if cancel_event.is_set():
                task.output_path.unlink(missing_ok=True)
                results.append(TaskResult(task, TaskStatus.CANCELED, "任务已取消"))
                _notify(on_task_update, task, TaskStatus.CANCELED, 0, "任务已取消")
                continue

            results.append(TaskResult(task, TaskStatus.SUCCESS, "导出完成"))
            _notify(on_task_update, task, TaskStatus.SUCCESS, 100, "导出完成")

        log_path = write_export_log(config.output_dir, results)
        if on_summary:
            on_summary(results, log_path)
        return results

    def cancel(self) -> None:
        if self.tools is not None:
            self.tools.cancel_current()

    def _finish_startup_failure(
        self,
        config: BatchConfig | StickerConfig,
        tasks: list[VideoTask] | list[StickerTask],
        message: str,
        on_task_update: TaskCallback | None,
        on_summary: SummaryCallback | None,
    ) -> list[TaskResult]:
        detail = message.strip() or "无法启动 FFmpeg 处理"
        results = [TaskResult(task, TaskStatus.FAILED, detail) for task in tasks]
        for task in tasks:
            _notify(on_task_update, task, TaskStatus.FAILED, 0, detail)
        log_path = write_export_log(config.output_dir, results)
        if on_summary:
            on_summary(results, log_path)
        return results


def _notify(
    callback: TaskCallback | None,
    task: VideoTask | StickerTask,
    status: TaskStatus,
    progress: float,
    message: str,
) -> None:
    if callback:
        callback(task, status, progress, message)


def _resolve_sticker_source(source: Path) -> list[Path]:
    source = Path(source)
    if source.is_file():
        return [source]
    if source.is_dir():
        return discover_media(source)
    return []


def _build_balanced_asset_pairs(
    top_assets: list[Path],
    bottom_assets: list[Path],
    total: int,
    seed: int | None = None,
) -> list[tuple[Path, Path]]:
    """Assign top/bottom sticker assets to tasks with even usage and random order.

    Each list is shuffled once and then cycled round-robin, so every asset is
    used within ±1 of an even share and consecutive videos only repeat a pair
    when a source list is smaller than the task count. Runs in O(total).
    """
    if total <= 0 or not top_assets or not bottom_assets:
        return []

    actual_seed = seed if seed is not None else random.SystemRandom().getrandbits(64)
    rng = random.Random(actual_seed)
    tops = list(top_assets)
    bottoms = list(bottom_assets)
    rng.shuffle(tops)
    rng.shuffle(bottoms)

    return [
        (tops[index % len(tops)], bottoms[index % len(bottoms)])
        for index in range(total)
    ]
