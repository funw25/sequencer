"""主窗口：菜单、通道面板、钢琴卷帘编辑区、播放控制、BPM/网格输入、事件日志侧栏。

播放链路：
    QTimer(20ms) -> PlaybackEngine.step() -> NoteEvent -> 输出适配层
    输出适配层当前用 DebugOutput 写事件日志；SerialOutput 已留占位但不启用。
界面层只做这三件事，不直接操作串口。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core.playback import PlaybackEngine
from ..io.project_io import ProjectIOError, load_project, save_project
from ..model.project import ALLOWED_GRID_STEPS, Project
from ..output.debug_output import DebugOutput
from ..output.serial_output import SerialOutput
from .piano_roll import PianoRollView

#: 播放定时器间隔（毫秒）。20ms 视觉效果够用，实际时间点由引擎的单调时钟决定。
TICK_INTERVAL_MS = 20

#: 事件日志最多保留的行数，避免长时间播放把内存堆满。
MAX_LOG_LINES = 2000


class MainWindow(QMainWindow):
    """音序器主窗口。"""

    def __init__(self, project: Project | None = None) -> None:
        super().__init__()
        self.project = project or _default_project()
        self.project_path: str | None = None

        self.setWindowTitle("多通道音序器 - 未命名工程")
        self.resize(1200, 760)

        # 播放引擎：输出先接调试日志（写进侧栏）。
        self.debug_output = DebugOutput(emit=self.append_event_log)
        self.serial_output = SerialOutput(port=None)  # 占位，端口未配置
        self.engine = PlaybackEngine(self.project, output=self.debug_output)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

        self._build_ui()
        self._build_menus()
        self.refresh_channels()
        self._update_status()

    # ---------- 界面搭建 ----------

    def _build_ui(self) -> None:
        # 左侧：通道面板
        self.channel_list = QListWidget()
        self.channel_list.currentRowChanged.connect(self._on_channel_selected)
        self.add_channel_button = QPushButton("添加通道")
        self.add_channel_button.clicked.connect(self.add_channel)
        self.remove_channel_button = QPushButton("删除通道")
        self.remove_channel_button.clicked.connect(self.remove_channel)
        self.rename_channel_button = QPushButton("重命名通道")
        self.rename_channel_button.clicked.connect(self.rename_channel)

        channel_panel = QWidget()
        channel_layout = QVBoxLayout(channel_panel)
        channel_layout.addWidget(QLabel("通道（一个通道 = 一个模块/乐器）"))
        channel_layout.addWidget(self.channel_list)
        channel_layout.addWidget(self.add_channel_button)
        channel_layout.addWidget(self.remove_channel_button)
        channel_layout.addWidget(self.rename_channel_button)

        # 中间：编辑区 + 播放控制 + 时间设置
        self.piano_roll = PianoRollView(self.project)
        self.piano_roll.notesChanged.connect(self._update_status)

        self.play_button = QPushButton("播放")
        self.play_button.clicked.connect(self.play)
        self.pause_button = QPushButton("暂停")
        self.pause_button.clicked.connect(self.pause)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(self.stop)

        self.bpm_input = QDoubleSpinBox()
        self.bpm_input.setRange(1.0, 400.0)
        self.bpm_input.setDecimals(2)
        self.bpm_input.setSingleStep(1.0)
        self.bpm_input.setValue(self.project.bpm)
        self.bpm_input.valueChanged.connect(self._on_bpm_changed)

        self.numerator_input = QSpinBox()
        self.numerator_input.setRange(1, 32)
        self.numerator_input.setValue(self.project.time_signature_numerator)
        self.numerator_input.valueChanged.connect(self._on_time_signature_changed)

        self.denominator_input = QComboBox()
        for value in (1, 2, 4, 8, 16, 32):
            self.denominator_input.addItem(f"1/{value}", value)
        self.denominator_input.setCurrentIndex(
            max(0, self.denominator_input.findData(self.project.time_signature_denominator))
        )
        self.denominator_input.currentIndexChanged.connect(self._on_time_signature_changed)

        self.grid_input = QComboBox()
        for value in ALLOWED_GRID_STEPS:
            self.grid_input.addItem(f"1/{value} 拍", value)
        self.grid_input.setCurrentIndex(max(0, self.grid_input.findData(self.project.grid_step)))
        self.grid_input.currentIndexChanged.connect(self._on_grid_changed)

        self.zoom_in_button = QPushButton("放大")
        self.zoom_in_button.clicked.connect(self.piano_roll.zoom_in)
        self.zoom_out_button = QPushButton("缩小")
        self.zoom_out_button.clicked.connect(self.piano_roll.zoom_out)

        control_row = QHBoxLayout()
        control_row.addWidget(self.play_button)
        control_row.addWidget(self.pause_button)
        control_row.addWidget(self.stop_button)
        control_row.addSpacing(12)
        control_row.addWidget(QLabel("BPM"))
        control_row.addWidget(self.bpm_input)
        control_row.addSpacing(12)
        control_row.addWidget(QLabel("拍号"))
        control_row.addWidget(self.numerator_input)
        control_row.addWidget(QLabel("/"))
        control_row.addWidget(self.denominator_input)
        control_row.addSpacing(12)
        control_row.addWidget(QLabel("网格步进"))
        control_row.addWidget(self.grid_input)
        control_row.addSpacing(12)
        control_row.addWidget(self.zoom_out_button)
        control_row.addWidget(self.zoom_in_button)
        control_row.addStretch(1)

        self.hint_label = QLabel(
            "编辑：空白处左键拖动 = 新建音符；音符上拖动 = 移动/改音高；右边缘拖动 = 改长度。"
        )
        self.length_label = QLabel()

        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.addLayout(control_row)
        centre_layout.addWidget(self.piano_roll, 1)
        centre_layout.addWidget(self.hint_label)
        centre_layout.addWidget(self.length_label)

        # 右侧：事件日志
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(MAX_LOG_LINES)
        self.clear_log_button = QPushButton("清空日志")
        self.clear_log_button.clicked.connect(self.log_view.clear)

        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.addWidget(QLabel("事件日志（播放事件输出）"))
        log_layout.addWidget(self.log_view)
        log_layout.addWidget(self.clear_log_button)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(channel_panel)
        splitter.addWidget(centre)
        splitter.addWidget(log_panel)
        splitter.setSizes([220, 700, 300])
        self.setCentralWidget(splitter)

        self.status_label = QLabel()
        self.statusBar().addWidget(self.status_label)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("工程(&F)")
        self._add_action(file_menu, "新建", self.new_project, "Ctrl+N")
        self._add_action(file_menu, "打开...", self.open_project, "Ctrl+O")
        self._add_action(file_menu, "保存", self.save_project_file, "Ctrl+S")
        self._add_action(file_menu, "另存为...", self.save_project_as)
        file_menu.addSeparator()
        self._add_action(file_menu, "退出", self.close, "Ctrl+Q")

        play_menu = self.menuBar().addMenu("播放(&P)")
        self._add_action(play_menu, "播放", self.play)
        self._add_action(play_menu, "暂停", self.pause)
        self._add_action(play_menu, "停止", self.stop)

        help_menu = self.menuBar().addMenu("帮助(&H)")
        self._add_action(help_menu, "关于", self.show_about)

    def _add_action(self, menu, text: str, slot, shortcut: str | None = None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(shortcut)
        menu.addAction(action)
        return action

    # ---------- 通道操作 ----------

    def refresh_channels(self) -> None:
        """把工程的通道同步到左侧列表，并重建编辑区。"""
        current = self.piano_roll.channel_index
        self.channel_list.blockSignals(True)
        self.channel_list.clear()
        for i, channel in enumerate(self.project.channels):
            item = QListWidgetItem(f"{i + 1}. {channel.name}（{len(channel)} 音符）")
            item.setBackground(QColor(channel.color))
            self.channel_list.addItem(item)
        self.channel_list.blockSignals(False)
        if self.project.channels:
            if not 0 <= current < len(self.project.channels):
                current = 0
            self.channel_list.setCurrentRow(current)
            self.piano_roll.channel_index = current
        else:
            self.piano_roll.channel_index = -1
        self.piano_roll.rebuild()
        self._update_status()

    def _on_channel_selected(self, row: int) -> None:
        if row < 0:
            return
        self.piano_roll.set_channel_index(row)
        self._update_status()

    def add_channel(self) -> None:
        self.project.add_channel()
        self.channel_list.setCurrentRow(len(self.project.channels) - 1)
        self.refresh_channels()

    def remove_channel(self) -> None:
        row = self.channel_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "删除通道", "当前没有选中的通道。")
            return
        self.project.remove_channel(row)
        self.refresh_channels()

    def rename_channel(self) -> None:
        row = self.channel_list.currentRow()
        if row < 0:
            return
        channel = self.project.channel_at(row)
        text, ok = QInputDialog.getText(self, "重命名通道", "通道名称：", text=channel.name)
        if ok and text:
            channel.name = text
            self.refresh_channels()

    # ---------- 时间设置 ----------

    def _on_bpm_changed(self, value: float) -> None:
        self.project.bpm = float(value)
        self._update_status()

    def _on_time_signature_changed(self, _value=None) -> None:
        self.project.time_signature_numerator = int(self.numerator_input.value())
        self.project.time_signature_denominator = int(self.denominator_input.currentData())
        self.piano_roll.rebuild()
        self._update_status()

    def _on_grid_changed(self, _index: int) -> None:
        self.project.grid_step = int(self.grid_input.currentData())
        self.piano_roll.rebuild()
        self._update_status()

    # ---------- 播放控制 ----------

    def play(self) -> None:
        """开始播放。播放头从工程起点走，事件写入侧栏日志。"""
        if not self.project.channels:
            self._update_status()
            return
        self.engine.play(start_step=0)
        self.piano_roll.set_playhead_step(self.engine.start_step)
        self._timer.start()
        self._set_playback_buttons(playing=True)
        self._update_status()

    def pause(self) -> None:
        """暂停播放，保留播放位置。"""
        self.engine.pause()
        self._timer.stop()
        self._set_playback_buttons(playing=False)
        self._update_status()

    def stop(self) -> None:
        """停止播放并让播放头回到起点。"""
        self.engine.stop()
        self._timer.stop()
        self.piano_roll.reset_playhead()
        self._set_playback_buttons(playing=False)
        self._update_status()

    def _on_tick(self) -> None:
        """定时器回调：推进播放引擎，刷新播放头。"""
        self.engine.step()
        self.piano_roll.set_playhead_step(self.engine.elapsed_steps)
        if not self.engine.is_playing:
            # 事件播完了，自动收尾。
            self._timer.stop()
            self._set_playback_buttons(playing=False)
        self._update_status()

    def _set_playback_buttons(self, playing: bool) -> None:
        self.play_button.setEnabled(not playing)
        self.pause_button.setEnabled(playing)

    # ---------- 事件日志 ----------

    def append_event_log(self, text: str) -> None:
        """往侧栏追加一行事件。DebugOutput 的回调就是它。"""
        self.log_view.appendPlainText(text)

    # ---------- 工程文件 ----------

    def new_project(self) -> None:
        self._load_project_object(_default_project(), None)

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开工程", "", "音序工程 (*.json);;所有文件 (*)"
        )
        if not path:
            return
        try:
            project = load_project(path)
        except ProjectIOError as exc:
            QMessageBox.critical(self, "打开工程失败", str(exc))
            return
        self._load_project_object(project, path)
        self.append_event_log(f"已打开工程：{path}")

    def save_project_file(self) -> None:
        if not self.project_path:
            self.save_project_as()
            return
        self._save_to(self.project_path)

    def save_project_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "保存工程", "工程.json", "音序工程 (*.json)")
        if not path:
            return
        self._save_to(path)

    def _save_to(self, path: str) -> None:
        try:
            save_project(self.project, path)
        except ProjectIOError as exc:
            QMessageBox.critical(self, "保存工程失败", str(exc))
            return
        self.project_path = path
        self._update_title()
        self.append_event_log(f"已保存工程：{path}")

    def _load_project_object(self, project: Project, path: str | None) -> None:
        """把新工程换成当前工程，并重置界面上的播放状态和输入框。"""
        self.stop()
        self.project = project
        self.project_path = path
        self.engine.project = project
        self.piano_roll.project = project
        self.piano_roll.channel_index = 0 if project.channels else -1

        # 同步输入框时屏蔽信号，避免把「界面刷新」当成「用户改值」再写回工程。
        self.bpm_input.blockSignals(True)
        self.numerator_input.blockSignals(True)
        self.bpm_input.setValue(project.bpm)
        self.numerator_input.setValue(project.time_signature_numerator)
        self.denominator_input.setCurrentIndex(
            max(0, self.denominator_input.findData(project.time_signature_denominator))
        )
        self.grid_input.setCurrentIndex(
            max(0, self.grid_input.findData(project.grid_step))
        )
        self.bpm_input.blockSignals(False)
        self.numerator_input.blockSignals(False)

        self.log_view.clear()
        self.refresh_channels()
        self._update_title()

    def _update_title(self) -> None:
        name = os.path.basename(self.project_path) if self.project_path else "未命名工程"
        self.setWindowTitle(f"多通道音序器 - {name}")

    # ---------- 状态栏 ----------

    def _update_status(self) -> None:
        channel = self.piano_roll.channel
        notes = len(channel) if channel is not None else 0
        state = "播放中" if self.engine.is_playing else "已停止"
        self.status_label.setText(
            f"通道数 {len(self.project.channels)}｜当前通道音符 {notes}｜"
            f"工程长度 {self.project.length_in_steps()} 网格｜"
            f"{self.project.bpm:g} BPM｜"
            f"{self.project.time_signature_numerator}/"
            f"{self.project.time_signature_denominator}｜"
            f"网格 1/{self.project.grid_step} 拍｜{state}｜输出：{self.debug_output.describe()}"
        )
        self.length_label.setText(
            f"提示：音高 0 表示休止；串口输出为占位实现（{self.serial_output.describe()}）。"
        )

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "关于",
            "多通道音序器上位机骨架\n"
            "编辑器只产生 NOTE_ON / NOTE_OFF 事件，串口消息格式待定。",
        )

    # ---------- 生命周期 ----------

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._timer.stop()
        self.engine.stop()
        self.serial_output.close()
        super().closeEvent(event)


def _default_project() -> Project:
    """新建工程时给一个空通道，界面不至于空着。"""
    project = Project()
    project.add_channel()
    return project
