from __future__ import annotations

from pathlib import Path

from .models import SUPPORTED_EXTENSIONS, SUPPORTED_IMAGE_EXTENSIONS


def discover_videos(folder: Path, video_a: Path | None = None) -> list[Path]:
    """Return supported video files in deterministic filename order."""
    folder = Path(folder)
    excluded = Path(video_a).resolve() if video_a else None
    videos: list[Path] = []

    for path in folder.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if excluded and path.resolve() == excluded:
            continue
        videos.append(path)

    return sorted(videos, key=lambda item: item.name.lower())


def discover_images(folder: Path) -> list[Path]:
    """Return supported image files in deterministic filename order."""
    folder = Path(folder)
    images: list[Path] = []

    for path in folder.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            continue
        images.append(path)

    return sorted(images, key=lambda item: item.name.lower())


def discover_media(folder: Path) -> list[Path]:
    """Return supported image + video files (sticker sources) in deterministic order."""
    folder = Path(folder)
    media: list[Path] = []

    for path in folder.iterdir():
        if not path.is_file():
            continue
        if (
            path.suffix.lower() not in SUPPORTED_EXTENSIONS
            and path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS
        ):
            continue
        media.append(path)

    return sorted(media, key=lambda item: item.name.lower())


def _make_output_path(
    source: Path,
    output_dir: Path,
    used_paths: set[Path] | None,
    suffix: str,
) -> Path:
    output_dir = Path(output_dir)
    used_paths = used_paths if used_paths is not None else set()
    stem = f"{Path(source).stem}{suffix}"
    candidate = output_dir / f"{stem}.mp4"
    counter = 1

    while candidate.exists() or candidate in used_paths:
        candidate = output_dir / f"{stem}_{counter}.mp4"
        counter += 1

    used_paths.add(candidate)
    return candidate


def make_output_path(video_b: Path, output_dir: Path, used_paths: set[Path] | None = None) -> Path:
    return _make_output_path(video_b, output_dir, used_paths, "_合成")


def make_sticker_output_path(video: Path, output_dir: Path, used_paths: set[Path] | None = None) -> Path:
    return _make_output_path(video, output_dir, used_paths, "_贴片")
