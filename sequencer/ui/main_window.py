"""主窗口：菜单、通道面板、钢琴卷帘编辑区、播放控制、BPM/网格输入、事件日志侧栏、串口面板。

播放链路：
    QTimer(20ms) -> PlaybackEngine.step() -> NoteEvent -> 输出适配层
    输出适配层两个：DebugOutput 写事件日志，SerialOutput 发串口。
界面层只产生事件，不直接操作串口。
"""

from __future__ import annotations

import math
import os

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.history import History
from ..core.playback import PlaybackEngine
from ..io.project_io import ProjectIOError, load_project, save_project
from ..model.project import ALLOWED_GRID_STEPS, Project
from ..output.debug_output import DebugOutput, pitch_name
from ..output.serial_output import (
    BAUDRATE_CHOICES,
    SerialOutput,
    SerialOutputError,
    scan_ports,
)
from .piano_roll import PianoRollView

#: 播放定时器间隔（毫秒）。20ms 视觉效果够用，实际时间点由引擎的单调时钟决定。
TICK_INTERVAL_MS = 20

#: 事件日志最多保留的行数，避免长时间播放把内存堆满。
MAX_LOG_LINES = 2000

#: 通道列表每行的高度（像素），放得下静音与独奏按钮。
CHANNEL_ROW_HEIGHT = 26

#: 三栏统一的边距与控件间距，保证各栏内容左右上下对齐。
PANEL_MARGINS = (8, 8, 8, 8)
PANEL_SPACING = 6

#: 控制行与工具栏里控件的统一高度。
CONTROL_HEIGHT = 26


def _panel_title(text: str) -> QLabel:
    """面板标题：加粗，高度与一行控件一致，便于三栏首行对齐。"""
    label = QLabel(text)
    font = label.font()
    font.setBold(True)
    label.setFont(font)
    label.setFixedHeight(CONTROL_HEIGHT)
    return label


def format_time(seconds: float) -> str:
    """把秒数格式化成「分:秒:毫秒」，例 00:01:250。"""
    total_ms = int(round(max(0.0, seconds) * 1000))
    minutes, rest = divmod(total_ms, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{minutes:02d}:{secs:02d}:{millis:03d}"


class ChannelRowWidget(QWidget):
    """通道列表里的一行。

    行内放了静音与独奏按钮，按钮要吃掉自己的点击，
    其余位置点一下等于选中这一行，所以整行的按下事件在这里发出。
    """

    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.clicked.emit()
        super().mousePressEvent(event)


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
        self.serial_output = SerialOutput(port=None)  # 未选端口，见串口面板
        self.engine = PlaybackEngine(self.project, output=self.debug_output)

        # 撤销历史：保存每次编辑前的工程快照，见 core/history.py。
        self.history = History()
        self._pending_snapshot: dict | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

        self._build_ui()
        self._build_menus()
        self.refresh_serial_ports()
        self.refresh_channels()
        self._refresh_history_actions()
        self._update_status()

    # ---------- 界面搭建 ----------

    def _build_ui(self) -> None:
        # 左侧：通道面板
        self.channel_list = QListWidget()
        self.channel_list.currentRowChanged.connect(self._on_channel_selected)
        # 通道的编辑操作集中在右键菜单里，见 _show_channel_menu
        self.channel_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.channel_list.customContextMenuRequested.connect(self._show_channel_menu)
        self.channel_list.setToolTip("右键通道：重命名、颜色、复制、移动、清空、删除")
        self.add_channel_button = QPushButton("添加通道")
        self.add_channel_button.clicked.connect(self.add_channel)
        self.remove_channel_button = QPushButton("删除通道")
        self.remove_channel_button.clicked.connect(self.remove_channel)

        channel_panel = QWidget()
        channel_layout = QVBoxLayout(channel_panel)
        channel_layout.setContentsMargins(*PANEL_MARGINS)
        channel_layout.setSpacing(PANEL_SPACING)
        channel_layout.addWidget(_panel_title("通道"))
        channel_layout.addWidget(self.channel_list)
        channel_layout.addWidget(self.add_channel_button)
        channel_layout.addWidget(self.remove_channel_button)
        channel_layout.addWidget(self._build_serial_panel())
        self.channel_list.setMinimumWidth(180)
        for button in (self.add_channel_button, self.remove_channel_button):
            button.setFixedHeight(CONTROL_HEIGHT)
            button.setMinimumWidth(96)

        # 中间：编辑区 + 播放控制 + 时间设置
        self.piano_roll = PianoRollView(self.project)
        self.piano_roll.notesChanged.connect(self._update_status)
        self.piano_roll.editStarted.connect(self._on_note_edit_started)
        self.piano_roll.editFinished.connect(self._on_note_edit_finished)
        self.piano_roll.cursorMoved.connect(self._on_cursor_moved)
        self.piano_roll.cursorLeft.connect(self._on_cursor_left)
        self.piano_roll.playheadMoved.connect(self.seek_to_step)

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

        # 只统一高度。宽度不能写死：下面这些控件的 minimumSizeHint 比拍脑袋的宽度大，
        # 压窄了文字会被挤掉（拍号分子是这样丢的），交给布局按各自需要给宽度。
        for widget in (
            self.bpm_input,
            self.numerator_input,
            self.denominator_input,
            self.grid_input,
            self.zoom_in_button,
            self.zoom_out_button,
        ):
            widget.setFixedHeight(CONTROL_HEIGHT)

        self.bpm_input.setToolTip("每分钟拍数")
        self.numerator_input.setToolTip("每小节的拍数")
        self.denominator_input.setToolTip("以几分音符为一拍")
        self.grid_input.setToolTip("一格等于几分之一拍")
        self.zoom_out_button.setToolTip("横向缩小")
        self.zoom_in_button.setToolTip("横向放大")

        control_row = QHBoxLayout()
        control_row.setContentsMargins(0, 0, 0, 0)
        control_row.setSpacing(6)
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

        # 界面里不放操作说明，说明统一收在「帮助 → 使用说明」里
        centre = QWidget()
        centre_layout = QVBoxLayout(centre)
        centre_layout.setContentsMargins(*PANEL_MARGINS)
        centre_layout.setSpacing(PANEL_SPACING)
        centre_layout.addLayout(control_row)
        centre_layout.addWidget(self.piano_roll, 1)

        # 右侧：事件日志
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(MAX_LOG_LINES)
        # 配色跟随主题：深色模式深底白字，浅色模式白底黑字。
        # 只写字体会让部分主题下前后景同色，所以这里把两个颜色都写明。
        palette = self.palette()
        text_color = palette.color(QPalette.ColorRole.Text).name()
        base_color = palette.color(QPalette.ColorRole.Base).name()
        self.log_view.setStyleSheet(
            "QPlainTextEdit { font-family: Consolas, 'Courier New', monospace;"
            f" font-size: 12px; color: {text_color}; background-color: {base_color}; }}"
        )
        self.clear_log_button = QPushButton("清空日志")
        self.clear_log_button.clicked.connect(self.log_view.clear)
        self.clear_log_button.setFixedHeight(CONTROL_HEIGHT)

        log_panel = QWidget()
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(*PANEL_MARGINS)
        log_layout.setSpacing(PANEL_SPACING)
        log_layout.addWidget(_panel_title("事件日志"))
        log_layout.addWidget(self.log_view, 1)
        log_layout.addWidget(self.clear_log_button)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(channel_panel)
        splitter.addWidget(centre)
        splitter.addWidget(log_panel)
        splitter.setSizes([230, 720, 320])
        self.setCentralWidget(splitter)

        self.status_label = QLabel()
        self.statusBar().addWidget(self.status_label)
        # 光标读数：鼠标在卷帘上移动时显示音名、小节.拍、网格与秒数
        self.position_label = QLabel()
        self.position_label.setMinimumWidth(260)
        self.statusBar().addPermanentWidget(self.position_label)
        self.statusBar().setContentsMargins(8, 2, 8, 2)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("工程(&F)")
        self.new_action = self._add_action(file_menu, "新建", self.new_project, "Ctrl+N")
        self.open_action = self._add_action(file_menu, "打开...", self.open_project, "Ctrl+O")
        self.save_action = self._add_action(file_menu, "保存", self.save_project_file, "Ctrl+S")
        self._add_action(file_menu, "另存为...", self.save_project_as)
        file_menu.addSeparator()
        self._add_action(file_menu, "退出", self.close, "Ctrl+Q")

        edit_menu = self.menuBar().addMenu("编辑(&E)")
        self.undo_action = self._add_action(edit_menu, "撤销", self.undo, "Ctrl+Z")
        self.redo_action = self._add_action(edit_menu, "重做", self.redo, "Ctrl+Shift+Z")
        self.redo_action.setShortcuts(["Ctrl+Shift+Z", "Ctrl+Y"])

        play_menu = self.menuBar().addMenu("播放(&P)")
        self.play_pause_action = self._add_action(
            play_menu, "播放", self.toggle_play, "Space"
        )
        self.play_pause_action.setToolTip("播放（空格）")
        self.stop_action = self._add_action(play_menu, "停止", self.stop)
        self.loop_action = self._add_action(play_menu, "循环播放", shortcut="Ctrl+L")
        self.loop_action.setCheckable(True)
        self.loop_action.setToolTip("循环播放整个工程")
        self.loop_action.toggled.connect(self.set_loop_enabled)

        help_menu = self.menuBar().addMenu("帮助(&H)")
        self._add_action(help_menu, "使用说明", self.show_usage, "F1")
        self._add_action(help_menu, "关于", self.show_about)

        self._build_toolbar()

    def _build_toolbar(self) -> None:
        """顶部工具栏：撤销重做、时间显示、播放控制。

        新建/打开/保存只留在「工程」菜单里，工具栏不再重复。
        """
        toolbar = QToolBar("主工具栏", self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setIconSize(QSize(16, 16))
        toolbar.setContentsMargins(6, 4, 6, 4)
        self.addToolBar(toolbar)
        self.toolbar = toolbar

        style = self.style()
        icons = (
            (self.undo_action, QStyle.StandardPixmap.SP_ArrowBack),
            (self.redo_action, QStyle.StandardPixmap.SP_ArrowForward),
            (self.stop_action, QStyle.StandardPixmap.SP_MediaStop),
        )
        for action, pixmap in icons:
            action.setIcon(style.standardIcon(pixmap))
        # 播放/暂停共用一个动作，图标与文字随状态切换，见 _set_playback_actions
        self.play_pause_action.setIcon(
            style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        )

        palette = self.palette()
        window_text = palette.color(QPalette.ColorRole.WindowText).name()
        # 循环用系统自带刷新图标，保留它原本的颜色
        self.loop_action.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        # 选中态底色取主题中性色，不再用系统那层蓝，蓝图标压在灰底上才看得清
        checked_bg = palette.color(QPalette.ColorRole.Mid).name()
        toolbar.setStyleSheet(
            f"QToolButton:checked {{ background: {checked_bg}; border-radius: 3px; }}"
        )

        toolbar.addAction(self.undo_action)
        toolbar.addAction(self.redo_action)
        toolbar.addSeparator()

        self.time_label = QLabel("00:00:000")
        self.time_label.setObjectName("timeLabel")
        # 颜色写死成主题色：只给字体大小的话，某些主题下会与底色同色而看不见
        self.time_label.setStyleSheet(
            "QLabel#timeLabel { font-family: Consolas, 'Courier New', monospace;"
            f" font-size: 15px; padding: 0 10px; color: {window_text}; }}"
        )
        self.time_label.setFixedHeight(CONTROL_HEIGHT)
        self.time_label.setToolTip("播放位置")
        toolbar.addWidget(self.time_label)
        toolbar.addSeparator()

        toolbar.addAction(self.play_pause_action)
        toolbar.addAction(self.stop_action)
        toolbar.addAction(self.loop_action)

    def _add_action(
        self, menu, text: str, slot=None, shortcut: str | None = None
    ) -> QAction:
        action = QAction(text, self)
        if slot is not None:
            action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(shortcut)
        menu.addAction(action)
        return action

    # ---------- 串口面板 ----------

    def _build_serial_panel(self) -> QWidget:
        """串口面板：选端口、选波特率、打开、发测试音。

        发送编码见 output/serial_output.py：1 个字节，高 2 位通道，低 6 位音高。
        本面板用于联调时验证链路。
        """
        self.port_input = QComboBox()
        self.refresh_ports_button = QPushButton("刷新")
        self.refresh_ports_button.clicked.connect(self.refresh_serial_ports)

        port_row = QHBoxLayout()
        port_row.addWidget(self.port_input, 1)
        port_row.addWidget(self.refresh_ports_button)

        self.baudrate_input = QComboBox()
        for value in BAUDRATE_CHOICES:
            self.baudrate_input.addItem(str(value), value)
        self.baudrate_input.setCurrentIndex(
            max(0, self.baudrate_input.findData(self.serial_output.baudrate))
        )

        self.serial_toggle_button = QPushButton("打开串口")
        self.serial_toggle_button.clicked.connect(self.toggle_serial)

        self.test_channel_input = QSpinBox()
        self.test_channel_input.setRange(0, 1)
        self.test_pitch_input = QSpinBox()
        self.test_pitch_input.setRange(0, 63)
        self.test_pitch_input.setValue(10)
        self.send_test_button = QPushButton("发测试音")
        self.send_test_button.clicked.connect(self.send_test_note)

        test_row = QHBoxLayout()
        test_row.setContentsMargins(0, 0, 0, 0)
        test_row.setSpacing(6)
        test_row.addWidget(QLabel("通道"))
        test_row.addWidget(self.test_channel_input)
        test_row.addWidget(QLabel("音高"))
        test_row.addWidget(self.test_pitch_input)
        test_row.addWidget(self.send_test_button)

        for widget in (
            self.port_input,
            self.refresh_ports_button,
            self.baudrate_input,
            self.serial_toggle_button,
            self.test_channel_input,
            self.test_pitch_input,
            self.send_test_button,
        ):
            widget.setFixedHeight(CONTROL_HEIGHT)

        panel = QGroupBox("串口")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(PANEL_SPACING)
        layout.addLayout(port_row)
        layout.addWidget(self.baudrate_input)
        layout.addWidget(self.serial_toggle_button)
        layout.addLayout(test_row)
        return panel

    def refresh_serial_ports(self) -> None:
        """扫描当前可用串口并填入下拉框，保留已选中的那个。"""
        current = self.port_input.currentText()
        ports = scan_ports()
        self.port_input.clear()
        self.port_input.addItems(ports)
        if current in ports:
            self.port_input.setCurrentText(current)
        self.serial_output.port = self.port_input.currentText() or None
        self._update_status()

    def toggle_serial(self) -> None:
        """打开或关闭串口。失败时在事件日志里给出原因。"""
        if self.serial_output.is_open:
            self.serial_output.close()
            self.append_event_log(f"串口已关闭：{self.serial_output.port}")
        else:
            self.serial_output.port = self.port_input.currentText() or None
            self.serial_output.baudrate = int(self.baudrate_input.currentData())
            try:
                self.serial_output.open()
            except SerialOutputError as exc:
                QMessageBox.warning(self, "打开串口失败", str(exc))
            else:
                self.append_event_log(
                    f"串口已打开：{self.serial_output.port} @ {self.serial_output.baudrate}"
                )
        self.serial_toggle_button.setText(
            "关闭串口" if self.serial_output.is_open else "打开串口"
        )
        self._update_status()

    def send_test_note(self) -> None:
        """发送一个测试音符字节。音高填 0 表示停止该通道。"""
        channel = int(self.test_channel_input.value())
        pitch = int(self.test_pitch_input.value())
        try:
            value = self.serial_output.send_note(channel, pitch)
        except SerialOutputError as exc:
            QMessageBox.warning(self, "发送失败", str(exc))
            return
        self.append_event_log(f"发送测试音字节 0x{value:02X}（通道 {channel} 音高 {pitch}）")
        self._update_status()

    # ---------- 通道操作 ----------

    def refresh_channels(self) -> None:
        """把工程的通道同步到左侧列表，并重建编辑区。

        每行是一个自建控件：颜色条 + 名称与音符数 + 静音/独奏按钮。
        """
        current = self.piano_roll.channel_index
        self.channel_list.blockSignals(True)
        self.channel_list.clear()
        for i, channel in enumerate(self.project.channels):
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, CHANNEL_ROW_HEIGHT))
            self.channel_list.addItem(item)
            self.channel_list.setItemWidget(item, self._channel_row(i, channel))
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

    def _channel_row(self, index: int, channel) -> QWidget:
        """通道列表里的一行：颜色条、名称、静音与独奏按钮。"""
        row = ChannelRowWidget()
        row.clicked.connect(lambda i=index: self.channel_list.setCurrentRow(i))
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 0, 4, 0)
        layout.setSpacing(6)

        strip = QLabel()
        strip.setFixedWidth(6)
        strip.setStyleSheet(f"background: {channel.color}; border-radius: 2px;")
        # 文字与色条不接鼠标，点它们等于点整行
        strip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        text = QLabel(f"{index + 1}. {channel.name}（{len(channel)} 音符）")
        text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        mute = QToolButton()
        mute.setText("M")
        mute.setCheckable(True)
        mute.setChecked(channel.muted)
        mute.setToolTip("静音：播放时跳过该通道")
        mute.setAutoRaise(True)
        mute.toggled.connect(lambda checked, i=index: self.set_channel_muted(i, checked))

        solo = QToolButton()
        solo.setText("S")
        solo.setCheckable(True)
        solo.setChecked(channel.soloed)
        solo.setToolTip("独奏：只播放独奏通道")
        solo.setAutoRaise(True)
        solo.toggled.connect(lambda checked, i=index: self.set_channel_soloed(i, checked))

        layout.addWidget(strip)
        layout.addWidget(text, 1)
        layout.addWidget(mute)
        layout.addWidget(solo)
        return row

    def set_channel_muted(self, index: int, muted: bool) -> None:
        """开或关一个通道的静音。只改播放时的取舍，不动音符。"""
        channel = self.project.channel_at(index)
        if channel.muted == bool(muted):
            return
        before = self._snapshot()
        channel.muted = bool(muted)
        self._record("静音通道" if muted else "取消静音", before)
        self._update_status()

    def set_channel_soloed(self, index: int, soloed: bool) -> None:
        """开或关一个通道的独奏。"""
        channel = self.project.channel_at(index)
        if channel.soloed == bool(soloed):
            return
        before = self._snapshot()
        channel.soloed = bool(soloed)
        self._record("独奏通道" if soloed else "取消独奏", before)
        self._update_status()

    def _on_channel_selected(self, row: int) -> None:
        if row < 0:
            return
        self.piano_roll.set_channel_index(row)
        self._update_status()

    def add_channel(self) -> None:
        before = self._snapshot()
        self.project.add_channel()
        self.channel_list.setCurrentRow(len(self.project.channels) - 1)
        self.refresh_channels()
        self._record("添加通道", before)

    def remove_channel(self) -> None:
        row = self.channel_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "删除通道", "当前没有选中的通道。")
            return
        before = self._snapshot()
        self.project.remove_channel(row)
        self.refresh_channels()
        self._record("删除通道", before)

    def rename_channel(self) -> None:
        row = self.channel_list.currentRow()
        if row < 0:
            return
        channel = self.project.channel_at(row)
        text, ok = QInputDialog.getText(self, "重命名通道", "通道名称：", text=channel.name)
        if ok and text and text != channel.name:
            before = self._snapshot()
            channel.name = text
            self.refresh_channels()
            self._record("重命名通道", before)

    # ---------- 通道右键菜单 ----------

    def _show_channel_menu(self, pos) -> None:
        """在通道列表上弹右键菜单。

        只保留本项目用得上的项：通道的增删改、颜色、顺序与清空。
        自动化、包络、渲染冻结、电平表这些依赖音频引擎的项不做。
        """
        item = self.channel_list.itemAt(pos)
        row = self.channel_list.row(item) if item is not None else -1
        if row >= 0:
            self.channel_list.setCurrentRow(row)

        menu = QMenu(self)
        if row >= 0:
            notes = len(self.project.channel_at(row))
            menu.addAction("重命名…", self.rename_channel)
            menu.addAction("颜色…", lambda: self.set_channel_color(row))
            menu.addSeparator()
            menu.addAction("复制通道", lambda: self.duplicate_channel(row))
            up = menu.addAction("上移", lambda: self.move_channel(row, -1))
            up.setEnabled(row > 0)
            down = menu.addAction("下移", lambda: self.move_channel(row, 1))
            down.setEnabled(row < len(self.project.channels) - 1)
            menu.addSeparator()
            clear = menu.addAction("清空该通道音符…", lambda: self.clear_channel_notes(row))
            clear.setEnabled(notes > 0)
            menu.addAction("删除该通道", self.remove_channel)
            menu.addSeparator()
        menu.addAction("添加通道", self.add_channel)
        menu.exec(self.channel_list.viewport().mapToGlobal(pos))

    def set_channel_color(self, row: int) -> None:
        """选一个通道颜色。颜色只用于显示，不进入播放逻辑。"""
        channel = self.project.channel_at(row)
        color = QColorDialog.getColor(QColor(channel.color), self, "通道颜色")
        if not color.isValid() or color.name() == channel.color:
            return
        before = self._snapshot()
        channel.color = color.name()
        self.refresh_channels()
        self._record("通道颜色", before)

    def duplicate_channel(self, row: int) -> None:
        """复制一个通道（含全部音符），副本排在末尾。"""
        before = self._snapshot()
        source = self.project.channel_at(row)
        clone = self.project.add_channel(name=f"{source.name} 副本", color=source.color)
        for note in source:
            clone.create_note(note.pitch, note.start, note.duration)
        self.piano_roll.channel_index = len(self.project.channels) - 1
        self.refresh_channels()
        self._record("复制通道", before)

    def move_channel(self, row: int, delta: int) -> None:
        """把通道上移或下移若干位。"""
        target = row + delta
        if not 0 <= target < len(self.project.channels):
            return
        before = self._snapshot()
        channels = list(self.project.channels)
        channels.insert(target, channels.pop(row))
        self.project.channels = channels  # setter 会按新顺序重排 index
        self.piano_roll.channel_index = target
        self.refresh_channels()
        self._record("移动通道", before)

    def clear_channel_notes(self, row: int) -> None:
        """清空一个通道的全部音符，清空前先确认。"""
        channel = self.project.channel_at(row)
        if not len(channel):
            return
        answer = QMessageBox.question(
            self, "清空通道", f"清空「{channel.name}」的全部 {len(channel)} 个音符？"
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        before = self._snapshot()
        channel.clear_notes()
        self.refresh_channels()
        self._record("清空通道音符", before)

    # ---------- 撤销与重做 ----------

    def _snapshot(self) -> dict:
        """当前工程的数据快照。"""
        return self.project.to_dict()

    def _record(self, label: str, before: dict) -> None:
        """把一次编辑记进撤销栈。before 是编辑前取好的快照。"""
        self.history.record(label, before)
        self._refresh_history_actions()

    def _on_note_edit_started(self) -> None:
        """鼠标按下、还没改动音符时存一份快照。"""
        self._pending_snapshot = self._snapshot()

    def _on_note_edit_finished(self) -> None:
        """鼠标松开：只有真的改了东西才记一步，空点不进撤销栈。"""
        before, self._pending_snapshot = self._pending_snapshot, None
        if before is None or before == self._snapshot():
            return
        self._record("编辑音符", before)

    def undo(self) -> None:
        entry = self.history.undo(self._snapshot())
        if entry is None:
            return
        label, snapshot = entry
        self._apply_snapshot(snapshot)
        self.append_event_log(f"撤销：{label}")

    def redo(self) -> None:
        entry = self.history.redo(self._snapshot())
        if entry is None:
            return
        label, snapshot = entry
        self._apply_snapshot(snapshot)
        self.append_event_log(f"重做：{label}")

    def _apply_snapshot(self, snapshot: dict) -> None:
        """用快照重建工程。保持文件路径、当前通道与界面输入一致。"""
        self.stop()
        project = Project.from_dict(snapshot)
        self.project = project
        self.engine.project = project
        self.piano_roll.project = project
        if not project.channels:
            self.piano_roll.channel_index = -1
        elif not 0 <= self.piano_roll.channel_index < len(project.channels):
            self.piano_roll.channel_index = 0
        self._sync_inputs(project)
        self.refresh_channels()
        self._refresh_history_actions()

    def _refresh_history_actions(self) -> None:
        self.undo_action.setEnabled(self.history.can_undo)
        self.redo_action.setEnabled(self.history.can_redo)

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
        self._set_playback_actions(playing=True)
        self._update_time_label()
        self._update_status()

    def pause(self) -> None:
        """暂停播放，保留播放位置。"""
        self.engine.pause()
        self._timer.stop()
        self._set_playback_actions(playing=False)
        self._update_time_label()
        self._update_status()

    def stop(self) -> None:
        """停止播放并让播放头回到起点。"""
        self.engine.stop()
        self._timer.stop()
        self.piano_roll.reset_playhead()
        self._set_playback_actions(playing=False)
        self._update_time_label()
        self._update_status()

    def set_loop_enabled(self, enabled: bool) -> None:
        """开关循环播放。

        循环范围为整个工程。播放中开关立即生效：关掉后本轮放完即停。
        """
        enabled = bool(enabled)
        self.engine.loop_enabled = enabled
        self.loop_action.blockSignals(True)
        self.loop_action.setChecked(enabled)
        self.loop_action.blockSignals(False)
        self.append_event_log("循环播放：开" if enabled else "循环播放：关")
        self._update_status()

    def _on_tick(self) -> None:
        """定时器回调：推进播放引擎，刷新播放头与时间显示。"""
        self.engine.step()
        self.piano_roll.set_playhead_step(self.engine.elapsed_steps)
        if not self.engine.is_playing:
            # 事件播完了，自动收尾。
            self._timer.stop()
            self._set_playback_actions(playing=False)
        self._update_time_label()
        self._update_status()

    def toggle_play(self) -> None:
        """播放与暂停共用一个入口：正在播就暂停，否则开始播。"""
        if self.engine.is_playing:
            self.pause()
        else:
            self.play()

    def _set_playback_actions(self, playing: bool) -> None:
        """播放键在两种状态间切换：停止时显示「播放」，播放中显示「暂停」。"""
        pixmap = (
            QStyle.StandardPixmap.SP_MediaPause
            if playing
            else QStyle.StandardPixmap.SP_MediaPlay
        )
        self.play_pause_action.setIcon(self.style().standardIcon(pixmap))
        self.play_pause_action.setText("暂停" if playing else "播放")
        self.play_pause_action.setToolTip("暂停（空格）" if playing else "播放（空格）")

    def seek_to_step(self, step: int) -> None:
        """时间轴上点击或拖动：把播放位置挪到该网格。

        播放中就从新位置接着放；停止状态下只移动位置。
        """
        self.engine.seek(step)
        self.piano_roll.set_playhead_step(step)
        self._update_time_label()
        self._update_status()

    def _update_time_label(self) -> None:
        self.time_label.setText(format_time(self.engine.elapsed_seconds))

    # ---------- 光标读数 ----------

    def _on_cursor_moved(self, pitch: int, step: int) -> None:
        """鼠标在卷帘上移动时，在状态栏显示光标位置。"""
        beats_per_bar = self.project.time_signature_numerator
        steps_per_bar = self.project.grid_step * beats_per_bar
        bar, rest = divmod(step, steps_per_bar)
        beat = rest // self.project.grid_step + 1
        seconds = step * 60.0 / self.project.bpm / self.project.grid_step
        self.position_label.setText(
            f"音高 {pitch_name(pitch)}｜小节 {bar + 1}.{beat}｜第 {step} 格｜{seconds:.3f}s"
        )

    def _on_cursor_left(self) -> None:
        self.position_label.clear()

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

        self._sync_inputs(project)

        # 换了工程就没有可撤销的旧编辑了。
        self.history.clear()
        self._pending_snapshot = None
        self._refresh_history_actions()

        self.log_view.clear()
        self.refresh_channels()
        # 打开工程后把视口对到实际用到的音域，否则可能只看到空网格
        self.piano_roll.center_on_notes()
        self._update_title()

    def _sync_inputs(self, project: Project) -> None:
        """把工程的 BPM、拍号、网格写回输入框。

        同步时屏蔽信号，避免把界面刷新当成改值再写回工程。
        """
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

    def _update_title(self) -> None:
        name = os.path.basename(self.project_path) if self.project_path else "未命名工程"
        self.setWindowTitle(f"多通道音序器 - {name}")

    # ---------- 状态栏 ----------

    def _update_status(self) -> None:
        channel = self.piano_roll.channel
        notes = len(channel) if channel is not None else 0
        state = "播放中" if self.engine.is_playing else "已停止"
        if self.engine.loop_enabled:
            state += "｜循环"
        self.status_label.setText(
            f"通道数 {len(self.project.channels)}｜当前通道音符 {notes}｜"
            f"工程长度 {self.project.length_in_steps()} 网格｜"
            f"{self.project.bpm:g} BPM｜"
            f"{self.project.time_signature_numerator}/"
            f"{self.project.time_signature_denominator}｜"
            f"网格 1/{self.project.grid_step} 拍｜{state}｜输出：{self.debug_output.describe()}"
        )

    def show_usage(self) -> None:
        """帮助 → 使用说明。界面里不放说明文字，都收在这里。"""
        QMessageBox.information(
            self,
            "使用说明",
            "编辑\n"
            "  空白处左键拖动：新建音符，横向拖动决定长度，纵向决定音高\n"
            "  音符上拖动：移动位置或改音高\n"
            "  音符右边缘拖动：改长度\n"
            "  以上操作全部吸附到整数网格\n\n"
            "播放\n"
            "  空格开始播放，工具栏可暂停、停止、循环\n"
            "  循环播放的范围是整个工程（Ctrl+L）\n"
            "  播放阶段不发声，结果体现在播放头位置与事件日志\n\n"
            "通道\n"
            "  右键通道：重命名、颜色、复制、上移下移、清空、删除\n"
            "  行尾 M 静音、S 独奏；独奏优先，静音始终生效\n\n"
            "事件日志\n"
            "  格式：时间  开始/结束  通道下标-音高音\n"
            "  通道下标从 0 起，与通道列表的显示序号相差 1\n\n"
            "音高\n"
            "  有效音高 1 至 63，音高 0 表示休止，不产生事件\n",
        )

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "关于",
            "多通道音序器上位机\n"
            "编辑器只产生 NOTE_ON / NOTE_OFF 事件，经输出适配层写出。",
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
