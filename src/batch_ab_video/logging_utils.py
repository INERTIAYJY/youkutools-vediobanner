from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .models import TaskResult

MESSAGE_LIMIT = 500


def write_export_log(output_dir: Path, results: list[TaskResult]) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = output_dir / f"export_log_{stamp}.csv"
    counter = 1
    while log_path.exists():
        log_path = output_dir / f"export_log_{stamp}_{counter}.csv"
        counter += 1

    _write_log(log_path, results)
    _write_log(output_dir / "export_log.csv", results)
    return log_path


def _write_log(log_path: Path, results: list[TaskResult]) -> None:
    with log_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time", "index", "input", "assets", "output", "status", "message"])
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for result in results:
            message = (result.message or "").replace("\n", " ").strip()[:MESSAGE_LIMIT]
            writer.writerow(
                [
                    now,
                    result.task.index,
                    str(result.task.input_path),
                    result.task.asset_label,
                    str(result.task.output_path),
                    result.status.value,
                    message,
                ]
            )
