from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .ffmpeg_tools import FFmpegError
from .models import (
    BITRATE_PRESETS,
    OUTPUT_PRESETS,
    SUPPORTED_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS,
    BatchConfig,
    StickerConfig,
    StickerLayout,
    StickerTask,
    TaskResult,
    TaskStatus,
    VideoTask,
)
from .processor import BatchProcessor
from .paths import discover_media


class DropPathLineEdit(QLineEdit):
    dropped = Signal(str)

    def __init__(self, drop_kind: str, placeholder: str) -> None:
        super().__init__()
        self.drop_kind = drop_kind
        self.setAcceptDrops(True)
        self.setPlaceholderText(placeholder)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._accepted_path(event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        path = self._accepted_path(event.mimeData().urls())
        if not path:
            event.ignore()
            return
        self.setText(path)
        self.dropped.emit(path)
        event.acceptProposedAction()

    def _accepted_path(self, urls: list[QUrl]) -> str:
        if not urls:
            return ""
        path = Path(urls[0].toLocalFile())
        if self.drop_kind == "video":
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
                return str(path)
            return ""
        if self.drop_kind == "media_source":
            if path.is_file() and (
                path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                or path.suffix.lower() in SUPPORTED_EXTENSIONS
            ):
                return str(path)
            if path.is_dir():
                return str(path)
            return ""
        if self.drop_kind == "folder":
            return str(path) if path.is_dir() else ""
        return str(path)


class BatchWorker(QThread):
    task_update = Signal(int, str, float, str)
    finished_summary = Signal(int, int, int, str)
    failed_startup = Signal(str)

    def __init__(self, config: BatchConfig | StickerConfig, tasks: list[VideoTask | StickerTask]) -> None:
        super().__init__()
        self.config = config
        self.tasks = tasks
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.processor = BatchProcessor()

    def run(self) -> None:
        try:
            if isinstance(self.config, StickerConfig):
                self.processor.run_sticker(
                    config=self.config,
                    tasks=self.tasks,
                    cancel_event=self.cancel_event,
                    pause_event=self.pause_event,
                    on_task_update=self._on_task_update,
                    on_summary=self._on_summary,
                )
            else:
                self.processor.run(
                    config=self.config,
                    tasks=self.tasks,
                    cancel_event=self.cancel_event,
                    pause_event=self.pause_event,
                    on_task_update=self._on_task_update,
                    on_summary=self._on_summary,
                )
        except FFmpegError as exc:
            self.failed_startup.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - keep GUI from crashing.
            self.failed_startup.emit(str(exc))

    def cancel(self) -> None:
        self.cancel_event.set()
        self.processor.cancel()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self.pause_event.set()
        else:
            self.pause_event.clear()

    def _on_task_update(self, task: VideoTask | StickerTask, status: TaskStatus, progress: float, message: str) -> None:
        self.task_update.emit(task.index - 1, status.value, progress, message)

    def _on_summary(self, results: list[TaskResult], log_path: Path) -> None:
        success = sum(1 for result in results if result.ok)
        failed = sum(1 for result in results if result.status == TaskStatus.FAILED)
        canceled = sum(1 for result in results if result.status == TaskStatus.CANCELED)
        self.finished_summary.emit(success, failed, canceled, str(log_path))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("批量视频导出工具")
        self.resize(1180, 840)
        self.setMinimumSize(820, 640)
        self.worker: BatchWorker | None = None
        self.tasks: list[VideoTask | StickerTask] = []
        self.current_config: BatchConfig | StickerConfig | None = None
        self.row_progress: dict[int, float] = {}
        self._tasks_consumed = False
        self._close_requested = False
        self._build_ui()
        self._apply_style()
        self._on_mode_changed()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        header_panel = QFrame()
        header_panel.setObjectName("HeaderPanel")
        header_layout = QHBoxLayout(header_panel)
        header_layout.setContentsMargins(20, 16, 20, 16)
        header_layout.setSpacing(16)
        heading_layout = QVBoxLayout()
        heading_layout.setSpacing(3)
        eyebrow = QLabel("VIDEO WORKSPACE")
        eyebrow.setObjectName("Eyebrow")
        heading_layout.addWidget(eyebrow)
        title = QLabel("批量视频导出")
        title.setObjectName("Title")
        heading_layout.addWidget(title)
        subtitle = QLabel("快速组合素材、统一导出规格，并实时掌握每一项任务状态。")
        subtitle.setObjectName("Subtitle")
        heading_layout.addWidget(subtitle)
        header_layout.addLayout(heading_layout)
        header_layout.addStretch(1)
        engine_badge = QLabel("本地处理  ·  FFmpeg")
        engine_badge.setObjectName("EngineBadge")
        engine_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_layout.addWidget(engine_badge)
        self._apply_elevation(header_panel)
        layout.addWidget(header_panel)

        mode_panel = QFrame()
        mode_panel.setObjectName("ModePanel")
        mode_layout = QHBoxLayout(mode_panel)
        mode_layout.setContentsMargins(16, 12, 16, 12)
        mode_layout.setSpacing(12)
        mode_copy = QVBoxLayout()
        mode_copy.setSpacing(2)
        mode_title = QLabel("01  选择工作流")
        mode_title.setObjectName("SectionTitle")
        mode_copy.addWidget(mode_title)
        mode_hint = QLabel("切换模式后，按需填入素材与导出参数。")
        mode_hint.setObjectName("SectionHint")
        mode_copy.addWidget(mode_hint)
        mode_layout.addLayout(mode_copy)
        mode_layout.addSpacing(8)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("视频 A + 批量 B 拼接", "concat")
        self.mode_combo.addItem("竖版贴片（上贴片 + 中间视频 + 下贴片）", "sticker_vertical")
        self.mode_combo.addItem("横版贴片（左贴片 + 中间视频 + 右贴片）", "sticker_landscape")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.mode_combo.setMinimumWidth(220)
        mode_layout.addWidget(self.mode_combo, 1)
        self._apply_elevation(mode_panel)
        layout.addWidget(mode_panel)

        self.config_stack = QStackedWidget()
        self.config_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.config_stack)
        self._build_concat_panel()
        self._build_sticker_panel()

        action_panel = QFrame()
        action_panel.setObjectName("ActionPanel")
        action_row = QHBoxLayout(action_panel)
        action_row.setContentsMargins(16, 12, 16, 12)
        action_row.setSpacing(10)
        action_copy = QVBoxLayout()
        action_copy.setSpacing(2)
        action_title = QLabel("02  生成与导出")
        action_title.setObjectName("SectionTitle")
        action_copy.addWidget(action_title)
        self.action_hint = QLabel("先扫描任务，确认列表后再开始导出。")
        self.action_hint.setObjectName("SectionHint")
        action_copy.addWidget(self.action_hint)
        action_row.addLayout(action_copy)
        action_row.addStretch(1)

        self.scan_button = QPushButton("扫描")
        self.scan_button.setObjectName("SecondaryButton")
        self.scan_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
        self.scan_button.clicked.connect(self.scan_tasks)
        action_row.addWidget(self.scan_button)

        self.start_button = QPushButton("开始导出")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.start_button.clicked.connect(self.start_batch)
        action_row.addWidget(self.start_button)

        self.pause_button = QPushButton("暂停")
        self.pause_button.setObjectName("SecondaryButton")
        self.pause_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        self.pause_button.setCheckable(True)
        self.pause_button.clicked.connect(self.toggle_pause)
        self.pause_button.setEnabled(False)
        self.pause_button.setToolTip("暂停会在当前视频导出完成后生效")
        action_row.addWidget(self.pause_button)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("DangerButton")
        self.cancel_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.cancel_button.clicked.connect(self.cancel_batch)
        self.cancel_button.setEnabled(False)
        action_row.addWidget(self.cancel_button)
        self._apply_elevation(action_panel)
        layout.addWidget(action_panel)

        progress_panel = QFrame()
        progress_panel.setObjectName("ProgressPanel")
        progress_layout = QVBoxLayout(progress_panel)
        progress_layout.setContentsMargins(16, 12, 16, 14)
        progress_layout.setSpacing(9)
        progress_header = QHBoxLayout()
        progress_title = QLabel("总进度")
        progress_title.setObjectName("SectionTitle")
        progress_header.addWidget(progress_title)
        progress_header.addStretch(1)
        self.progress_value_label = QLabel("0%")
        self.progress_value_label.setObjectName("ProgressValue")
        progress_header.addWidget(self.progress_value_label)
        progress_layout.addLayout(progress_header)
        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setValue(0)
        self.overall_progress.setTextVisible(False)
        progress_layout.addWidget(self.overall_progress)
        self._apply_elevation(progress_panel)
        layout.addWidget(progress_panel)

        table_panel = QFrame()
        table_panel.setObjectName("QueuePanel")
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(16, 14, 16, 16)
        table_layout.setSpacing(12)
        layout.addWidget(table_panel, 1)

        table_heading = QHBoxLayout()
        queue_title = QLabel("03  任务队列")
        queue_title.setObjectName("SectionTitle")
        table_heading.addWidget(queue_title)
        table_heading.addStretch(1)
        self.queue_summary_label = QLabel("等待扫描")
        self.queue_summary_label.setObjectName("QueueSummary")
        table_heading.addWidget(self.queue_summary_label)
        table_layout.addLayout(table_heading)

        self.table = QTableWidget(0, 7)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        table_layout.addWidget(self.table)
        self._apply_elevation(table_panel)

        status_panel = QFrame()
        status_panel.setObjectName("StatusPanel")
        status_layout = QHBoxLayout(status_panel)
        status_layout.setContentsMargins(14, 9, 14, 9)
        status_layout.setSpacing(9)
        status_dot = QLabel()
        status_dot.setObjectName("StatusDot")
        status_dot.setFixedSize(8, 8)
        status_layout.addWidget(status_dot)
        self.status_label = QLabel("请选择处理模式和素材。")
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label, 1)
        self._apply_elevation(status_panel)
        layout.addWidget(status_panel)

        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        self.addAction(exit_action)

    def _build_concat_panel(self) -> None:
        panel = QFrame()
        panel.setObjectName("ConfigPanel")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        panel_layout = QGridLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setHorizontalSpacing(10)
        panel_layout.setVerticalSpacing(8)
        panel_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        panel_layout.setColumnMinimumWidth(0, 72)
        self._apply_elevation(panel)
        self.config_stack.addWidget(panel)

        self.video_a_edit = DropPathLineEdit("video", "拖入或选择视频 A")
        self.folder_b_edit = DropPathLineEdit("folder", "拖入或选择 B 视频文件夹")
        self.output_dir_edit = DropPathLineEdit("folder", "拖入或选择导出目录")
        self.preset_combo = self._preset_combo()
        self.bitrate_combo = self._bitrate_combo()

        for widget in (self.video_a_edit, self.folder_b_edit, self.output_dir_edit):
            widget.dropped.connect(self._clear_tasks)
            widget.textChanged.connect(self._clear_tasks)
        self.preset_combo.currentIndexChanged.connect(self._clear_tasks)
        self.bitrate_combo.currentIndexChanged.connect(self._clear_tasks)

        panel_layout.addWidget(self._field_label("视频 A"), 0, 0)
        panel_layout.addWidget(self.video_a_edit, 0, 1)
        panel_layout.addWidget(self._browse_button("选择视频", self._choose_video_a), 0, 2)
        panel_layout.addWidget(self._field_label("B 文件夹"), 1, 0)
        panel_layout.addWidget(self.folder_b_edit, 1, 1)
        panel_layout.addWidget(self._browse_button("选择文件夹", self._choose_folder_b), 1, 2)
        panel_layout.addWidget(self._field_label("导出目录"), 2, 0)
        panel_layout.addWidget(self.output_dir_edit, 2, 1)
        panel_layout.addWidget(self._browse_button("选择目录", self._choose_output_dir), 2, 2)
        panel_layout.addWidget(self._field_label("导出规格"), 3, 0)
        panel_layout.addWidget(self.preset_combo, 3, 1)
        panel_layout.addWidget(self._field_label("视频码率"), 4, 0)
        panel_layout.addWidget(self.bitrate_combo, 4, 1)

    def _build_sticker_panel(self) -> None:
        panel = QFrame()
        panel.setObjectName("ConfigPanel")
        panel_layout = QGridLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setHorizontalSpacing(10)
        panel_layout.setVerticalSpacing(8)
        panel_layout.setColumnMinimumWidth(0, 86)
        self._apply_elevation(panel)
        self.config_stack.addWidget(panel)

        self.sticker_video_folder_edit = DropPathLineEdit("folder", "拖入或选择待处理视频文件夹")
        self.top_asset_edit = DropPathLineEdit("media_source", "拖入或选择贴片 A / 上贴片（图或视频）")
        self.bottom_asset_edit = DropPathLineEdit("media_source", "拖入或选择贴片 B / 下贴片（图或视频）")
        self.sticker_output_dir_edit = DropPathLineEdit("folder", "拖入或选择导出目录")
        self.sticker_preset_combo = self._preset_combo()
        self.sticker_bitrate_combo = self._bitrate_combo()

        for widget in (
            self.sticker_video_folder_edit,
            self.top_asset_edit,
            self.bottom_asset_edit,
            self.sticker_output_dir_edit,
        ):
            widget.dropped.connect(self._clear_tasks)
            widget.textChanged.connect(self._clear_tasks)
        self.sticker_preset_combo.currentIndexChanged.connect(self._clear_tasks)
        self.sticker_bitrate_combo.currentIndexChanged.connect(self._clear_tasks)

        panel_layout.addWidget(self._field_label("视频文件夹"), 0, 0)
        panel_layout.addWidget(self.sticker_video_folder_edit, 0, 1)
        panel_layout.addWidget(self._browse_button("选择文件夹", self._choose_sticker_video_folder), 0, 2)
        self.sticker_top_label = self._field_label("贴片 A / 上贴片")
        self.sticker_bottom_label = self._field_label("贴片 B / 下贴片")
        panel_layout.addWidget(self.sticker_top_label, 1, 0)
        panel_layout.addWidget(self.top_asset_edit, 1, 1)
        panel_layout.addWidget(self._browse_button("选择贴片素材/文件夹", self._choose_top_asset_source), 1, 2)
        panel_layout.addWidget(self.sticker_bottom_label, 2, 0)
        panel_layout.addWidget(self.bottom_asset_edit, 2, 1)
        panel_layout.addWidget(self._browse_button("选择贴片素材/文件夹", self._choose_bottom_asset_source), 2, 2)
        panel_layout.addWidget(self._field_label("导出目录"), 3, 0)
        panel_layout.addWidget(self.sticker_output_dir_edit, 3, 1)
        panel_layout.addWidget(self._browse_button("选择目录", self._choose_sticker_output_dir), 3, 2)
        panel_layout.addWidget(self._field_label("导出规格"), 4, 0)
        panel_layout.addWidget(self.sticker_preset_combo, 4, 1)
        panel_layout.addWidget(self._field_label("视频码率"), 5, 0)
        panel_layout.addWidget(self.sticker_bitrate_combo, 5, 1)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget#Root {
                background: #eef2f7;
                color: #1d2736;
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 13px;
            }
            QFrame#HeaderPanel, QFrame#ModePanel, QFrame#ActionPanel,
            QFrame#ProgressPanel, QFrame#ConfigPanel, QFrame#QueuePanel,
            QFrame#StatusPanel {
                background: rgba(255, 255, 255, 232);
                border: 1px solid rgba(255, 255, 255, 245);
                border-radius: 14px;
            }
            QLabel#Eyebrow {
                color: #3478c7;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#Title {
                color: #182230;
                font-size: 25px;
                font-weight: 700;
            }
            QLabel#Subtitle, QLabel#SectionHint, QLabel#StatusLabel {
                color: #657487;
                font-size: 12px;
            }
            QLabel#EngineBadge, QLabel#QueueSummary {
                background: rgba(238, 246, 255, 210);
                border: 1px solid #c8ddf6;
                border-radius: 10px;
                color: #286bb2;
                font-size: 12px;
                font-weight: 600;
                padding: 6px 10px;
            }
            QLabel#FieldLabel {
                color: #344255;
                font-weight: 600;
            }
            QLabel#SectionTitle {
                color: #263446;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#ProgressValue {
                color: #1671cf;
                font-size: 17px;
                font-weight: 700;
            }
            QLabel#StatusDot {
                background: #34c759;
                border-radius: 4px;
            }
            QLineEdit, QComboBox {
                min-height: 32px;
                padding: 3px 10px;
                border: 1px solid #d8e0ea;
                border-radius: 8px;
                background: rgba(255, 255, 255, 220);
                color: #233043;
                selection-background-color: #b8d9ff;
            }
            QLineEdit::placeholder { color: #98a4b3; }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #007aff;
                background: #ffffff;
            }
            QComboBox::drop-down { border: 0; width: 28px; }
            QComboBox QAbstractItemView {
                background: #ffffff;
                border: 1px solid #d4dce7;
                color: #1f2a38;
                selection-background-color: #dcebff;
                outline: 0;
            }
            QPushButton {
                min-height: 32px;
                padding: 3px 10px;
                border-radius: 8px;
                font-weight: 600;
            }
            QPushButton#PrimaryButton {
                background: #007aff;
                color: #ffffff;
                border: 1px solid #0071e8;
            }
            QPushButton#PrimaryButton:hover { background: #0a84ff; }
            QPushButton#PrimaryButton:pressed { background: #0065d1; }
            QPushButton#SecondaryButton {
                background: rgba(248, 250, 253, 230);
                color: #2c5e98;
                border: 1px solid #d4ddea;
            }
            QPushButton#SecondaryButton:hover {
                background: #edf6ff;
                border-color: #b8d2ed;
            }
            QPushButton#SecondaryButton:checked {
                background: #dceeff;
                border-color: #8dc0f3;
                color: #125da8;
            }
            QPushButton#DangerButton {
                background: rgba(255, 250, 250, 220);
                color: #d64f4a;
                border: 1px solid #f0d0cf;
            }
            QPushButton#DangerButton:hover { background: #fff0ef; }
            QPushButton:disabled {
                background: #f0f3f7;
                color: #a3aebb;
                border: 1px solid #e1e6ed;
            }
            QProgressBar {
                min-height: 10px;
                border: 0;
                border-radius: 5px;
                background: #e4eaf2;
                text-align: center;
            }
            QProgressBar::chunk {
                border-radius: 5px;
                background: #007aff;
            }
            QTableWidget {
                background: rgba(251, 253, 255, 220);
                alternate-background-color: #f5f8fc;
                border: 1px solid #e5eaf0;
                border-radius: 8px;
                color: #334155;
                selection-background-color: #dcebff;
                selection-color: #182230;
            }
            QHeaderView::section {
                background: #f1f5f9;
                color: #627184;
                border: 0;
                border-bottom: 1px solid #dde5ee;
                padding: 8px 6px;
                font-weight: 700;
            }
            QTableWidget::item {
                padding: 7px 6px;
                border-bottom: 1px solid #edf1f5;
            }
            QTableWidget::item:selected { background: #dcebff; }
            QScrollBar:vertical {
                background: transparent;
                width: 9px;
                margin: 3px;
            }
            QScrollBar::handle:vertical {
                background: #c5cfdb;
                border-radius: 4px;
                min-height: 28px;
            }
            QScrollBar::handle:vertical:hover { background: #aebdce; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            """
        )

    @staticmethod
    def _apply_elevation(widget: QWidget) -> None:
        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(48, 71, 96, 28))
        widget.setGraphicsEffect(shadow)

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("FieldLabel")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return label

    def _browse_button(self, tooltip: str, handler: Callable[[], None]) -> QPushButton:
        button = QPushButton("浏览…")
        button.setObjectName("SecondaryButton")
        button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        button.setToolTip(tooltip)
        button.setFixedWidth(88)
        button.clicked.connect(handler)
        return button

    def _preset_combo(self) -> QComboBox:
        combo = QComboBox()
        for preset in OUTPUT_PRESETS:
            combo.addItem(preset.display_name, preset)
        return combo

    def _bitrate_combo(self) -> QComboBox:
        combo = QComboBox()
        for bitrate in BITRATE_PRESETS:
            combo.addItem(bitrate.display_name, bitrate)
        return combo

    def _choose_video_a(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频 A",
            "",
            "Video Files (*.mp4 *.mov *.mkv *.avi)",
        )
        if path:
            self.video_a_edit.setText(path)

    def _choose_folder_b(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 B 视频文件夹")
        if path:
            self.folder_b_edit.setText(path)

    def _choose_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if path:
            self.output_dir_edit.setText(path)

    def _choose_sticker_video_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择视频文件夹")
        if path:
            self.sticker_video_folder_edit.setText(path)

    def _choose_top_asset_source(self) -> None:
        path = self._choose_sticker_source("选择贴片 A 素材（图片或视频）或文件夹")
        if path:
            self.top_asset_edit.setText(path)

    def _choose_bottom_asset_source(self) -> None:
        path = self._choose_sticker_source("选择贴片 B 素材（图片或视频）或文件夹")
        if path:
            self.bottom_asset_edit.setText(path)

    def _choose_sticker_source(self, title: str) -> str:
        choice = QMessageBox.question(
            self,
            title,
            "选择单个贴片素材（图片或视频）？\n点击“是”选择素材文件，点击“否”选择文件夹。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if choice == QMessageBox.StandardButton.Yes:
            path, _ = QFileDialog.getOpenFileName(
                self,
                title,
                "",
                "Media Files (*.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.mov *.mkv *.avi)",
            )
            return path
        if choice == QMessageBox.StandardButton.No:
            return QFileDialog.getExistingDirectory(self, title)
        return ""

    def _choose_sticker_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if path:
            self.sticker_output_dir_edit.setText(path)

    def _config_from_ui(self) -> BatchConfig | StickerConfig | None:
        if self._is_sticker_mode():
            return self._sticker_config_from_ui()
        return self._concat_config_from_ui()

    def _concat_config_from_ui(self) -> BatchConfig | None:
        video_a_text = self.video_a_edit.text().strip()
        folder_b_text = self.folder_b_edit.text().strip()
        output_dir_text = self.output_dir_edit.text().strip()
        video_a = Path(video_a_text)
        folder_b = Path(folder_b_text)
        output_dir = Path(output_dir_text)
        preset = self.preset_combo.currentData()
        bitrate = self.bitrate_combo.currentData()

        if not video_a_text or not video_a.is_file():
            self._warn("请选择有效的视频 A 文件。")
            return None
        if video_a.suffix.lower() not in SUPPORTED_EXTENSIONS:
            self._warn("视频 A 格式必须是 mp4、mov、mkv 或 avi。")
            return None
        if not folder_b_text or not folder_b.is_dir():
            self._warn("请选择有效的 B 视频文件夹。")
            return None
        if not output_dir_text:
            self._warn("请选择导出目录。")
            return None
        if _same_directory(folder_b, output_dir):
            self._warn("B 视频文件夹不能与导出目录相同，以免将已导出视频再次处理。")
            return None
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._warn(f"无法创建导出目录：{exc}")
            return None

        return BatchConfig(video_a=video_a, folder_b=folder_b, output_dir=output_dir, preset=preset, bitrate=bitrate)

    def _sticker_config_from_ui(self) -> StickerConfig | None:
        video_folder_text = self.sticker_video_folder_edit.text().strip()
        top_source_text = self.top_asset_edit.text().strip()
        bottom_source_text = self.bottom_asset_edit.text().strip()
        output_dir_text = self.sticker_output_dir_edit.text().strip()
        video_folder = Path(video_folder_text)
        top_source = Path(top_source_text)
        bottom_source = Path(bottom_source_text)
        output_dir = Path(output_dir_text)

        if not video_folder_text or not video_folder.is_dir():
            self._warn("请选择有效的视频文件夹。")
            return None
        if not self._valid_sticker_source(top_source):
            self._warn("请选择有效的贴片 A 素材（图片或视频）或包含素材的文件夹。")
            return None
        if not self._valid_sticker_source(bottom_source):
            self._warn("请选择有效的贴片 B 素材（图片或视频）或包含素材的文件夹。")
            return None
        if not output_dir_text:
            self._warn("请选择导出目录。")
            return None
        if _same_directory(video_folder, output_dir):
            self._warn("视频文件夹不能与导出目录相同，以免将已导出视频再次处理。")
            return None
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._warn(f"无法创建导出目录：{exc}")
            return None

        return StickerConfig(
            video_folder=video_folder,
            top_source=top_source,
            bottom_source=bottom_source,
            output_dir=output_dir,
            preset=self.sticker_preset_combo.currentData(),
            bitrate=self.sticker_bitrate_combo.currentData(),
            layout=(StickerLayout.LEFT_RIGHT if self._is_landscape_sticker_mode() else StickerLayout.TOP_BOTTOM),
        )

    def _valid_sticker_source(self, source: Path) -> bool:
        if source.is_file():
            return (
                source.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                or source.suffix.lower() in SUPPORTED_EXTENSIONS
            )
        if source.is_dir():
            return bool(discover_media(source))
        return False

    def _sticker_source_count(self, source: Path) -> int:
        if source.is_file() and (
            source.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
            or source.suffix.lower() in SUPPORTED_EXTENSIONS
        ):
            return 1
        if source.is_dir():
            return len(discover_media(source))
        return 0

    def scan_tasks(self) -> None:
        config = self._config_from_ui()
        if config is None:
            return
        self._load_tasks(config)

    def start_batch(self) -> None:
        config = self._config_from_ui()
        if config is None:
            return
        if self.tasks and self.current_config == config and not self._tasks_consumed:
            self._populate_task_table()
        elif not self._load_tasks(config):
            self._warn("没有可处理的任务。")
            return

        self.worker = BatchWorker(config, self.tasks)
        self.worker.task_update.connect(self.update_task_row)
        self.worker.finished_summary.connect(self.finish_batch)
        self.worker.failed_startup.connect(self.fail_startup)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

        self._tasks_consumed = True
        self._set_running_state(True)
        self.queue_summary_label.setText(f"正在处理 {len(self.tasks)} 项")
        self.action_hint.setText("正在导出；可暂停后续任务，或安全取消当前批次。")
        self.status_label.setText("正在导出。")

    def _load_tasks(self, config: BatchConfig | StickerConfig) -> bool:
        try:
            processor = BatchProcessor()
            if isinstance(config, StickerConfig):
                self.tasks = processor.build_sticker_tasks(config)
            else:
                self.tasks = processor.build_tasks(config)
        except Exception as exc:  # noqa: BLE001
            self._warn(str(exc))
            return False

        self.current_config = config
        self._tasks_consumed = False
        self._populate_task_table()
        self.queue_summary_label.setText(f"已生成 {len(self.tasks)} 项")
        self.action_hint.setText("任务已就绪，确认列表后点击“开始导出”。")
        return bool(self.tasks)

    def _populate_task_table(self) -> None:
        self.row_progress.clear()
        self.table.setRowCount(len(self.tasks))
        for row, task in enumerate(self.tasks):
            self._set_item(row, 0, str(task.index))
            self._set_item(row, 1, str(task.input_path))
            self._set_item(row, 2, task.asset_label)
            self._set_item(row, 3, str(task.output_path))
            self._set_item(row, 4, TaskStatus.PENDING.value)
            self._set_item(row, 5, "0%")
            self._set_item(row, 6, "")

        self.overall_progress.setValue(0)
        self.progress_value_label.setText("0%")
        if isinstance(self.current_config, StickerConfig):
            top_count = self._sticker_source_count(self.current_config.top_source)
            bottom_count = self._sticker_source_count(self.current_config.bottom_source)
            self.status_label.setText(
                (
                    f"已扫描到 {len(self.tasks)} 个视频，左贴片 {top_count} 个素材，右贴片 {bottom_count} 个素材，"
                    f"可组合 {top_count * bottom_count} 组，已随机分散分配。重新扫描会刷新组合。"
                    if self.current_config.layout == StickerLayout.LEFT_RIGHT
                    else f"已扫描到 {len(self.tasks)} 个视频，上贴片 {top_count} 个素材，下贴片 {bottom_count} 个素材，"
                    f"可组合 {top_count * bottom_count} 组，已随机分散分配。重新扫描会刷新组合。"
                )
            )
        else:
            self.status_label.setText(f"已扫描到 {len(self.tasks)} 个 B 视频。")

    def toggle_pause(self) -> None:
        if not self.worker:
            return
        paused = self.pause_button.isChecked()
        self.worker.set_paused(paused)
        self.pause_button.setText("继续" if paused else "暂停")
        self.status_label.setText("已请求暂停，当前视频完成后生效。" if paused else "继续处理。")

    def cancel_batch(self) -> None:
        if not self.worker:
            return
        self.status_label.setText("正在取消当前任务。")
        self.worker.cancel()
        self.cancel_button.setEnabled(False)

    def update_task_row(self, row: int, status: str, progress: float, message: str) -> None:
        self.row_progress[row] = progress
        self._set_item(row, 4, status)
        self._set_item(row, 5, f"{progress:.0f}%")
        self._set_item(row, 6, message.splitlines()[0][:160] if message else "")
        self._update_overall_progress()

    def finish_batch(self, success: int, failed: int, canceled: int, log_path: str) -> None:
        self.status_label.setText(
            f"导出完成：成功 {success} 个，失败 {failed} 个，取消 {canceled} 个。日志：{log_path}"
        )
        self.queue_summary_label.setText(f"已完成 · 成功 {success} / 失败 {failed}")
        self.action_hint.setText("本批次已结束；调整素材或参数后可重新扫描。")
        QMessageBox.information(
            self,
            "导出完成",
            f"成功：{success}\n失败：{failed}\n取消：{canceled}\n日志：{log_path}",
        )

    def fail_startup(self, message: str) -> None:
        self._warn(message)
        self.status_label.setText("任务启动失败。")
        self.queue_summary_label.setText("启动失败")
        self.action_hint.setText("请检查素材与 FFmpeg 配置后重试。")

    def _worker_finished(self) -> None:
        self._set_running_state(False)
        self.worker = None
        if self._close_requested:
            self.close()

    def _clear_tasks(self) -> None:
        if self.worker is not None:
            return
        self.tasks = []
        self.current_config = None
        self._tasks_consumed = False
        self.row_progress.clear()
        self.table.setRowCount(0)
        self.overall_progress.setValue(0)
        self.progress_value_label.setText("0%")
        if hasattr(self, "queue_summary_label"):
            self.queue_summary_label.setText("等待扫描")

    def _set_running_state(self, running: bool) -> None:
        self.scan_button.setEnabled(not running)
        self.start_button.setEnabled(not running)
        self.mode_combo.setEnabled(not running)
        self.config_stack.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.pause_button.setChecked(False)
        self.pause_button.setText("暂停")
        self.cancel_button.setEnabled(running)

    def _update_overall_progress(self) -> None:
        if not self.tasks:
            self.overall_progress.setValue(0)
            self.progress_value_label.setText("0%")
            return
        total = sum(self.row_progress.get(row, 0.0) for row in range(len(self.tasks)))
        value = round(total / len(self.tasks))
        self.overall_progress.setValue(value)
        self.progress_value_label.setText(f"{value}%")

    def _set_item(self, row: int, column: int, value: str) -> None:
        item = self.table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            if column in {0, 4, 5}:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, column, item)
        item.setText(value)

    def _on_mode_changed(self) -> None:
        if not hasattr(self, "config_stack"):
            return
        sticker_mode = self._is_sticker_mode()
        self.config_stack.setCurrentIndex(1 if sticker_mode else 0)
        if sticker_mode:
            landscape = self._is_landscape_sticker_mode()
            self.sticker_top_label.setText("贴片 A / 左贴片" if landscape else "贴片 A / 上贴片")
            self.sticker_bottom_label.setText("贴片 B / 右贴片" if landscape else "贴片 B / 下贴片")
            self.top_asset_edit.setPlaceholderText(
                "拖入或选择贴片 A / 左贴片（图或视频）" if landscape else "拖入或选择贴片 A / 上贴片（图或视频）"
            )
            self.bottom_asset_edit.setPlaceholderText(
                "拖入或选择贴片 B / 右贴片（图或视频）" if landscape else "拖入或选择贴片 B / 下贴片（图或视频）"
            )
            self._set_sticker_preset_options(landscape)
        # Calculate after applying the mode's fields, so all rows remain visible.
        content_height = self.config_stack.currentWidget().sizeHint().height()
        self.config_stack.setFixedHeight(content_height)
        self._set_table_headers()
        self._clear_tasks()
        self.status_label.setText(
            "请选择视频文件夹、贴片 A、贴片 B 和导出目录。"
            if sticker_mode
            else "请选择视频 A、B 视频文件夹和导出目录。"
        )

    def _set_table_headers(self) -> None:
        if self._is_sticker_mode():
            assets = "左右贴片" if self._is_landscape_sticker_mode() else "上下贴片"
            self.table.setHorizontalHeaderLabels(["#", "视频", assets, "输出文件", "状态", "进度", "说明"])
        else:
            self.table.setHorizontalHeaderLabels(["#", "B 视频", "素材", "输出文件", "状态", "进度", "说明"])

    def _is_sticker_mode(self) -> bool:
        return self.mode_combo.currentData() in {"sticker_vertical", "sticker_landscape"}

    def _is_landscape_sticker_mode(self) -> bool:
        return self.mode_combo.currentData() == "sticker_landscape"

    def _set_sticker_preset_options(self, landscape: bool) -> None:
        current = self.sticker_preset_combo.currentData()
        allowed = [
            preset
            for preset in OUTPUT_PRESETS
            if (preset.width > preset.height) == landscape
        ]
        self.sticker_preset_combo.blockSignals(True)
        self.sticker_preset_combo.clear()
        for preset in allowed:
            self.sticker_preset_combo.addItem(preset.display_name, preset)
        if current in allowed:
            self.sticker_preset_combo.setCurrentIndex(allowed.index(current))
        self.sticker_preset_combo.blockSignals(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        worker = self.worker
        if worker is not None and worker.isRunning():
            if not self._close_requested:
                answer = QMessageBox.question(
                    self,
                    "确认关闭",
                    "仍有视频正在导出。关闭会取消当前任务，是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    event.ignore()
                    return
                self._close_requested = True
                self.status_label.setText("正在取消任务，完成后自动关闭。")
                worker.cancel()
            event.ignore()
            return
        super().closeEvent(event)

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "提示", message or "未知错误")


def _same_directory(first: Path, second: Path) -> bool:
    try:
        return first.resolve() == second.resolve()
    except OSError:
        return False


def run_app() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()
