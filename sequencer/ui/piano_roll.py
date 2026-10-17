"""钢琴卷帘编辑区：QGraphicsView / QGraphicsScene 实现。

坐标系（scene 坐标，单位是像素）：
    x = 网格整数 * STEP_WIDTH_PX     —— 横轴是以节拍为基础的离散时间网格
    y = RULER_HEIGHT_PX + (MAX_PITCH - 音高) * NOTE_HEIGHT_PX
    刻意不用 scale(1, -1) 翻转视图：翻转会让所有文字倒过来；
    这里改成在算 y 的时候减一下，滚轮和滚动条的行为都保持正常。

顶部时间轴分两行：上排是小节号，下排是小节起点的秒数，拍用短刻度标出。

本模块只负责显示与鼠标编辑，不碰串口、不碰播放时钟。
音符集合始终指向选中的那个 Channel，编辑直接改 Channel 里的 Note 对象。
其他通道的音符以半透明色画出（幽灵音符），只作对照，不参与点击与编辑。

拖动建音符、移动或改长度时，画两条虚线辅助线并显示读数气泡（音名、起始格、长度），
松手后消失。

一次鼠标编辑（新建、移动、改长度）在开始时发 editStarted、结束时发 editFinished，
主窗口用这两个信号在编辑前存一份快照，编辑后决定是否记进撤销栈。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QLabel,
    QMenu,
)

from ..model.note import MAX_PITCH, Note
from ..model.project import Project
from ..output.debug_output import pitch_name

STEP_WIDTH_PX = 24.0  # 一个网格的宽度
NOTE_HEIGHT_PX = 14.0  # 一个音高行的高度
LOWEST_PITCH = 1  # 卷帘最底一行。音高表里第 1 号是 C2（65Hz），0 号是休止，不上卷帘
RULER_HEIGHT_PX = 36.0  # 顶部时间轴高度：两排文字，同时是拖动播放头的可点区域
RESIZE_EDGE_PX = 6.0  # 距右边缘多少像素内算「拖长度」
MIN_VISIBLE_STEPS = 64  # 内容不足时的最小可见网格数
KEYBOARD_WIDTH_PX = 58.0  # 左侧固定键盘列的宽度，绘制在视口坐标下
NOTE_LABEL_MIN_WIDTH_PX = 28.0  # 音符窄于此宽度就不写音名，避免糊成一团
PLAYHEAD_GRAB_PX = 4.0  # 网格区里距播放头多少像素内算「抓住了播放头」

GRID_BACK = "#151517"
GRID_LINE = "#2A2A2F"
GRID_LINE_BEAT = "#4A4A53"
GRID_LINE_BAR = "#90909C"
ROW_LINE = "#232327"
ROW_BLACK = "#1C1C20"
OCTAVE_LINE = "#43434C"
RULER_BACK = "#26262B"
RULER_HOVER = "#33333B"
RULER_TEXT = "#F2F2F6"
RULER_TIME = "#C6C6CE"
RULER_TICK = "#9A9AA6"
KEYBOARD_BACK = "#191A1D"
KEYBOARD_WHITE = "#B4B4BC"
KEYBOARD_BLACK = "#232327"
KEYBOARD_LINE = "#0C0C0E"
KEYBOARD_OCTAVE = "#6E6E78"
KEYBOARD_TEXT = "#101014"
PLAYHEAD_COLOR = "#FF5555"
NOTE_BORDER = "#0B0B0D"
NOTE_SELECTED_BORDER = "#FFFFFF"
NOTE_TEXT = "#101014"
GHOST_ALPHA = 70
GUIDE_COLOR = "#FFD166"
READOUT_STYLE = (
    "QLabel { background: #FFD166; color: #17171A; padding: 2px 7px;"
    " border-radius: 3px; font-size: 11px; }"
)

#: 一个八度内黑键相对于 Cn 的偏移。Cn 落在音高 1、13、25…（见 LOWEST_PITCH）。
_BLACK_KEY_OFFSETS = (1, 3, 6, 8, 10)


def _degree(pitch: int) -> int:
    """音高在八度内的位置，0 表示这一行的音名是 Cn。"""
    return (pitch - LOWEST_PITCH) % 12


def _rows() -> int:
    """卷帘一共有多少行音高：C2 到最高音。"""
    return MAX_PITCH - LOWEST_PITCH + 1


def _octave(pitch: int) -> int:
    """音高所在的八度数：C2 在第 2 个八度，与音高表一致。"""
    return (pitch - LOWEST_PITCH) // 12 + 2


class PianoRollView(QGraphicsView):
    """钢琴卷帘视图。

    交互：左键在空白处按下拖动 = 新建音符；左键按住已有音符拖动 = 移动（含改音高）；
    按住音符右边缘拖动 = 改长度。全部结果吸附到整数网格。
    """

    notesChanged = Signal()  # 音符被增删改后发出，主窗口用来刷新状态栏
    editStarted = Signal()  # 一次鼠标编辑开始，此时还没改动音符
    editFinished = Signal()  # 一次鼠标编辑结束
    cursorMoved = Signal(int, int)  # 鼠标在卷帘上移动：(音高, 网格)
    cursorLeft = Signal()  # 鼠标离开卷帘
    playheadMoved = Signal(int)  # 在时间轴上点击或拖动，参数为网格位置

    def __init__(self, project: Project, parent=None) -> None:
        super().__init__(parent)
        self.project = project
        self.channel_index = 0 if project.channels else -1

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setBackgroundBrush(QBrush(QColor(GRID_BACK)))
        self.setMouseTracking(True)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)

        self._note_items: dict[int, QGraphicsRectItem] = {}  # id(Note) -> 图元
        self._note_labels: dict[int, QGraphicsSimpleTextItem] = {}  # id(Note) -> 音名
        self._ghost_items: list[QGraphicsRectItem] = []  # 其他通道的音符图元
        self._playhead: QGraphicsRectItem | None = None
        self._playhead_step = 0
        self._max_steps = MIN_VISIBLE_STEPS

        # 鼠标拖动状态
        self._mode = "none"
        self._drag_note: Note | None = None
        self._drag_grab_offset_steps = 0.0  # 按下点相对音符起始的网格偏移（浮点）
        self._drag_pitch = 0  # 拖动中的音高，横向辅助线用
        self._drag_end_step = 0  # 拖动中的结束格，纵向辅助线用
        self._ruler_hover = False  # 鼠标是否停在时间轴上，用来提亮整条
        self._playhead_hover = False  # 鼠标是否压在播放头那根线上，用来加粗
        self._selected_note: Note | None = None  # 当前选中的音符，Delete 键删它
        # 键盘焦点：不设的话 Delete 键到不了这里
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._readout = QLabel(self.viewport())
        self._readout.setStyleSheet(READOUT_STYLE)
        self._readout.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._readout.hide()

        self._rebuild_scene()
        self.center_on_notes()

    def center_on_notes(self) -> None:
        """把视口落在工程实际用到的音域中部。

        没有音符时落在中音区。否则打开工程会看到一片空网格，
        音符被留在视口外的音区里。
        """
        pitches = [
            note.pitch
            for channel in self.project.channels
            for note in channel
            if note.pitch != 0
        ]
        middle = (min(pitches) + max(pitches)) // 2 if pitches else MAX_PITCH // 2
        self.centerOn(0.0, self._pitch_to_y(middle))

    # ---------- 左侧键盘列与拖动辅助线 ----------

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        """画固定在视口上的两层：左侧钢琴键盘、顶部时间轴，最后是拖动辅助线。

        painter 的坐标系是场景坐标，带滚动偏移，因此先把原点平移回视口左上角，
        键盘与时间轴就固定在边上，不随滚动移动。辅助线属于场景内容，画的时候减掉偏移。
        """
        super().drawForeground(painter, rect)
        offset = self.mapToScene(0, 0)
        view_width = self.viewport().width()
        view_height = self.viewport().height()
        painter.save()
        painter.translate(offset)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # 先铺满键盘列背景，避免场景内容从键盘下面透出来
        painter.fillRect(
            QRectF(0.0, 0.0, KEYBOARD_WIDTH_PX, view_height), QColor(KEYBOARD_BACK)
        )

        label_font = painter.font()
        label_font.setPointSize(7)
        for pitch in range(LOWEST_PITCH, MAX_PITCH + 1):
            y = self._pitch_to_y(pitch)
            if y + NOTE_HEIGHT_PX < 0 or y > view_height:
                continue
            # 每一行先铺满白键底板，黑键再压在上面。这样黑键底部始终是白色，
            # 看上去才像钢琴键盘，而不是一条黑一条白交替的色块。
            painter.fillRect(
                QRectF(0.0, y, KEYBOARD_WIDTH_PX, NOTE_HEIGHT_PX),
                QColor(KEYBOARD_WHITE),
            )
            if _degree(pitch) in _BLACK_KEY_OFFSETS:
                # 黑键短，靠键盘右侧，贴近真实键盘的排列
                painter.fillRect(
                    QRectF(
                        KEYBOARD_WIDTH_PX * 0.38,
                        y,
                        KEYBOARD_WIDTH_PX * 0.62,
                        NOTE_HEIGHT_PX,
                    ),
                    QColor(KEYBOARD_BLACK),
                )
            painter.setPen(QPen(QColor(KEYBOARD_LINE), 0))
            painter.drawLine(0.0, y, KEYBOARD_WIDTH_PX, y)
            # 每个八度起始（Cn 那一行）画一条分隔线并标注音名，其余行不写字
            if _degree(pitch) == 0:
                painter.setPen(QPen(QColor(KEYBOARD_OCTAVE), 1))
                painter.drawLine(0.0, y, KEYBOARD_WIDTH_PX, y)
                painter.setPen(QPen(QColor(KEYBOARD_TEXT), 0))
                painter.setFont(label_font)
                painter.drawText(
                    QRectF(4.0, y, KEYBOARD_WIDTH_PX - 6.0, NOTE_HEIGHT_PX),
                    int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                    f"C{_octave(pitch)}",
                )

        painter.setPen(QPen(QColor(KEYBOARD_LINE), 1))
        painter.drawLine(KEYBOARD_WIDTH_PX, 0.0, KEYBOARD_WIDTH_PX, view_height)
        painter.drawLine(0.0, 0.0, 0.0, view_height)

        # 时间轴画在键盘列之后，覆盖整幅宽度，包括键盘列上方那一段
        self._draw_ruler(painter, offset, view_width)

        if self._mode in ("create", "move", "resize"):
            # 纵向线标音符结束位置，横向线标音高，与正在编辑的音符对齐
            guide = QPen(QColor(GUIDE_COLOR), 1, Qt.PenStyle.DashLine)
            guide.setCosmetic(True)
            painter.setPen(guide)
            x = self._drag_end_step * STEP_WIDTH_PX - offset.x()
            painter.drawLine(x, RULER_HEIGHT_PX, x, view_height)
            y = self._pitch_to_y(self._drag_pitch) - offset.y()
            painter.drawLine(KEYBOARD_WIDTH_PX, y, view_width, y)

        painter.restore()

    def ruler_text(self, step: int) -> tuple[str, str]:
        """时间轴在某一步上的两排文字：(小节起点的秒数, 小节.拍)。

        绘制与测试共用，避免两处各写一套换算。
        """
        grid_step = self.project.grid_step
        steps_per_bar = grid_step * self.project.time_signature_numerator
        seconds = step * 60.0 / self.project.bpm / grid_step
        bar = step // steps_per_bar + 1
        beat = (step % steps_per_bar) // grid_step + 1
        return f"{seconds:.1f}s", f"{bar}.{beat}"

    def _draw_ruler(self, painter: QPainter, offset, view_width: float) -> None:
        """画固定在视口顶部的时间轴：上排小节起点秒数，下排小节.拍。

        鼠标停在上面时整条提亮，提示这里可以按住拖动。
        最左端留给「秒 / 小节.拍」两行说明，落在说明下面的刻度文字不再画，
        否则会和说明叠在一起。
        """
        painter.fillRect(
            QRectF(0.0, 0.0, view_width, RULER_HEIGHT_PX),
            QColor(RULER_HOVER if self._ruler_hover else RULER_BACK),
        )
        painter.setPen(QPen(QColor(GRID_LINE_BAR), 1))
        painter.drawLine(0.0, RULER_HEIGHT_PX, view_width, RULER_HEIGHT_PX)

        grid_step = self.project.grid_step
        steps_per_bar = grid_step * self.project.time_signature_numerator
        show_every_beat = grid_step * STEP_WIDTH_PX >= 34.0

        bar_font = painter.font()
        bar_font.setPointSize(8)
        time_font = painter.font()
        time_font.setPointSize(8)
        align = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        first_step = max(0, int(offset.x() // STEP_WIDTH_PX) - 1)
        last_step = min(self._max_steps, int((offset.x() + view_width) // STEP_WIDTH_PX) + 1)

        for step in range(first_step, last_step + 1):
            if step % grid_step:
                continue
            is_bar = step % steps_per_bar == 0
            if not is_bar and not show_every_beat:
                continue
            x = step * STEP_WIDTH_PX - offset.x()
            if x > view_width:
                break
            painter.setPen(QPen(QColor(RULER_TICK), 1))
            painter.drawLine(
                QPointF(x, 0.0 if is_bar else RULER_HEIGHT_PX * 0.5),
                QPointF(x, RULER_HEIGHT_PX),
            )
            if x < KEYBOARD_WIDTH_PX:
                # 落在左端说明区里，文字不画，免得和「秒 / 小节.拍」重叠
                continue
            seconds_text, beat_text = self.ruler_text(step)
            if is_bar:
                painter.setFont(time_font)
                painter.setPen(QPen(QColor(RULER_TIME), 0))
                painter.drawText(QRectF(x + 4.0, 2.0, 70.0, 13.0), align, seconds_text)
            painter.setFont(bar_font)
            painter.setPen(QPen(QColor(RULER_TEXT), 0))
            painter.drawText(
                QRectF(x + 4.0, RULER_HEIGHT_PX - 16.0, 70.0, 14.0), align, beat_text
            )

        # 最左端标出上下两排数字各是什么，这两行落在键盘列上方
        painter.setFont(bar_font)
        painter.setPen(QPen(QColor(RULER_TIME), 0))
        painter.drawText(QRectF(6.0, 2.0, KEYBOARD_WIDTH_PX - 10.0, 13.0), align, "秒")
        painter.setPen(QPen(QColor(RULER_TEXT), 0))
        painter.drawText(
            QRectF(6.0, RULER_HEIGHT_PX - 16.0, KEYBOARD_WIDTH_PX - 10.0, 14.0),
            align,
            "小节.拍",
        )

    @staticmethod
    def _in_keyboard(pos) -> bool:
        """视口坐标是否落在左侧键盘列内。"""
        return pos.x() < KEYBOARD_WIDTH_PX

    @staticmethod
    def _in_ruler(pos) -> bool:
        """视口坐标是否落在顶部时间轴上。时间轴固定在视口上，用视口坐标判断。"""
        return pos.y() < RULER_HEIGHT_PX and pos.x() >= KEYBOARD_WIDTH_PX

    def _in_playhead(self, viewport_pos, scene_pos) -> bool:
        """是否抓住了播放头那根线。

        顶部时间轴整条都能拖；网格区里只有贴着播放头线的几像素算抓住，
        免得和「空白处拖动 = 新建音符」抢操作。
        """
        if viewport_pos.y() < RULER_HEIGHT_PX or viewport_pos.x() < KEYBOARD_WIDTH_PX:
            return False
        return abs(scene_pos.x() - self._playhead_step * STEP_WIDTH_PX) <= PLAYHEAD_GRAB_PX

    # ---------- 场景构建 ----------

    @property
    def channel(self):
        """当前编辑的通道；工程里没有通道时返回 None。"""
        if 0 <= self.channel_index < len(self.project.channels):
            return self.project.channels[self.channel_index]
        return None

    def set_channel_index(self, index: int) -> None:
        """切换正在编辑的通道。"""
        self.channel_index = index
        self.rebuild()

    def rebuild(self) -> None:
        """按当前工程重建整个场景（网格 + 音符 + 播放头）。"""
        self._rebuild_scene()

    def _rebuild_scene(self) -> None:
        self._scene.clear()
        self._note_items.clear()
        self._note_labels.clear()
        self._ghost_items = []
        self._playhead = None
        self._max_steps = max(MIN_VISIBLE_STEPS, self.project.length_in_steps() + 16)
        self._draw_grid()
        self._draw_playhead()
        self._sync_notes()
        self._scene.setSceneRect(self._scene_rect())

    def _scene_rect(self) -> QRectF:
        height = RULER_HEIGHT_PX + _rows() * NOTE_HEIGHT_PX
        return QRectF(0.0, 0.0, self._max_steps * STEP_WIDTH_PX, height)

    def _draw_grid(self) -> None:
        """画网格竖线、每拍加粗线、音高行与八度线。

        时间轴不在这里画：它固定在视口顶部，见 _draw_ruler()。
        """
        width = self._max_steps * STEP_WIDTH_PX
        height = RULER_HEIGHT_PX + _rows() * NOTE_HEIGHT_PX

        grid_step = self.project.grid_step
        # 一小节的网格数。拍号分子为每小节的拍数，分母目前不参与换算。
        steps_per_bar = grid_step * self.project.time_signature_numerator

        step_pen = QPen(QColor(GRID_LINE))
        step_pen.setCosmetic(True)
        beat_pen = QPen(QColor(GRID_LINE_BEAT))
        beat_pen.setCosmetic(True)
        bar_pen = QPen(QColor(GRID_LINE_BAR))
        bar_pen.setCosmetic(True)

        for step in range(self._max_steps + 1):
            x = step * STEP_WIDTH_PX
            line = self._scene.addLine(x, RULER_HEIGHT_PX, x, height, step_pen)
            line.setZValue(-1)
            if step % grid_step:
                continue
            is_bar = steps_per_bar > 0 and step % steps_per_bar == 0
            # 小节线最亮，拍线次之，网格线最暗
            line = self._scene.addLine(
                x, RULER_HEIGHT_PX, x, height, bar_pen if is_bar else beat_pen
            )
            line.setZValue(-1)

        row_pen = QPen(QColor(ROW_LINE))
        row_pen.setCosmetic(True)
        octave_pen = QPen(QColor(OCTAVE_LINE))
        octave_pen.setCosmetic(True)
        for pitch in range(LOWEST_PITCH, MAX_PITCH + 1):
            y = self._pitch_to_y(pitch)
            # 黑键行用更暗的底色，便于在深色网格里辨认音高位置
            if _degree(pitch) in _BLACK_KEY_OFFSETS:
                band = QGraphicsRectItem(QRectF(0.0, y, width, NOTE_HEIGHT_PX))
                band.setBrush(QBrush(QColor(ROW_BLACK)))
                band.setPen(QPen(Qt.PenStyle.NoPen))
                band.setZValue(-1.5)
                self._scene.addItem(band)
            if _degree(pitch) != 0:
                line = self._scene.addLine(0.0, y, width, y, row_pen)
                line.setZValue(-1)
            # 八度起始处画一条略亮的横线，音名由左侧键盘列标示
            octave_line = self._scene.addLine(0.0, y, width, y, octave_pen)
            octave_line.setZValue(-1)

    def _draw_playhead(self) -> None:
        item = QGraphicsRectItem(QRectF(0.0, 0.0, 2.0, self._scene_rect().height()))
        item.setBrush(QBrush(QColor(PLAYHEAD_COLOR)))
        item.setPen(QPen(Qt.PenStyle.NoPen))
        item.setZValue(10)
        self._scene.addItem(item)
        self._playhead = item
        self._position_playhead(self._playhead_step)

    def _position_playhead(self, step: int) -> None:
        if self._playhead is None:
            return
        # 鼠标压在播放头上时画粗一点，提示这根线可以拖
        width = 6.0 if self._playhead_hover else 2.0
        self._playhead.setRect(
            QRectF(
                step * STEP_WIDTH_PX - width / 2.0,
                0.0,
                width,
                self._scene_rect().height(),
            )
        )

    # ---------- 音符图元 ----------

    def _sync_notes(self) -> None:
        """让场景里的音符图元与当前通道一致，并重画其他通道的幽灵音符。"""
        channel = self.channel
        wanted = {} if channel is None else {id(note): note for note in channel}
        # 选中的音符不在当前通道里了（切通道、撤销、删除）就清掉选中
        if self._selected_note is not None and id(self._selected_note) not in wanted:
            self._selected_note = None
        for key in list(self._note_items):
            if key not in wanted:
                self._note_labels.pop(key, None)
                self._scene.removeItem(self._note_items.pop(key))
        for key, note in wanted.items():
            if key not in self._note_items:
                item = QGraphicsRectItem()
                item.setZValue(1)
                self._scene.addItem(item)
                label = QGraphicsSimpleTextItem(item)
                label.setBrush(QBrush(QColor(NOTE_TEXT)))
                label.setZValue(2)
                self._note_labels[key] = label
                self._note_items[key] = item
            label_font = self._note_labels[key].font()
            label_font.setPointSize(7)
            self._note_labels[key].setFont(label_font)
            self._note_items[key].setToolTip(
                f"音高 {note.pitch}｜开始 {note.start}｜长度 {note.duration}"
            )
            self._update_item(note)
        self._sync_ghosts(channel)

    def _sync_ghosts(self, active) -> None:
        """把其他通道的音符画成半透明色，用于对照；不参与点击与编辑。"""
        for item in self._ghost_items:
            self._scene.removeItem(item)
        self._ghost_items = []
        for channel in self.project.channels:
            if channel is active:
                continue
            fill = QColor(channel.color)
            fill.setAlpha(GHOST_ALPHA)
            border = QColor(channel.color)
            border.setAlpha(GHOST_ALPHA + 60)
            pen = QPen(border)
            pen.setCosmetic(True)
            for note in channel:
                if note.pitch == 0:
                    continue
                item = QGraphicsRectItem(self._note_rect(note))
                item.setBrush(QBrush(fill))
                item.setPen(pen)
                item.setZValue(0)
                item.setToolTip(f"{channel.name}｜{pitch_name(note.pitch)}")
                self._scene.addItem(item)
                self._ghost_items.append(item)

    def _update_item(self, note: Note) -> None:
        item = self._note_items.get(id(note))
        if item is None:
            return
        rect = self._note_rect(note)
        item.setRect(rect)
        color = QColor(self.channel.color) if self.channel else QColor("#4C8BF5")
        if note is self._drag_note:
            color = color.lighter(135)
        item.setBrush(QBrush(color))
        # 正在拖动的音符用亮边框标出，便于确认操作对象
        border = (
            NOTE_SELECTED_BORDER
            if note is self._drag_note or note is self._selected_note
            else NOTE_BORDER
        )
        pen = QPen(QColor(border))
        pen.setCosmetic(True)
        item.setPen(pen)

        label = self._note_labels.get(id(note))
        if label is not None:
            label.setText(pitch_name(note.pitch))
            label.setVisible(
                note.pitch != 0 and rect.width() >= NOTE_LABEL_MIN_WIDTH_PX
            )
            label.setPos(rect.left() + 3.0, rect.top() + 1.0)

    def _note_rect(self, note: Note) -> QRectF:
        return QRectF(
            note.start * STEP_WIDTH_PX,
            self._pitch_to_y(note.pitch),
            max(1.0, note.duration * STEP_WIDTH_PX - 1.0),
            NOTE_HEIGHT_PX - 2.0,
        )

    @staticmethod
    def _pitch_to_y(pitch: int) -> float:
        """音高 -> 场景 y。音高越大越靠上。"""
        return RULER_HEIGHT_PX + (MAX_PITCH - pitch) * NOTE_HEIGHT_PX

    @staticmethod
    def _y_to_pitch(y: float) -> int:
        """场景 y -> 音高，并夹到 LOWEST_PITCH..MAX_PITCH。"""
        pitch = MAX_PITCH - int((y - RULER_HEIGHT_PX) // NOTE_HEIGHT_PX)
        return max(LOWEST_PITCH, min(MAX_PITCH, pitch))

    @staticmethod
    def _x_to_step(x: float) -> int:
        """场景 x -> 最近的整数网格（吸附）。"""
        if x <= 0:
            return 0
        return int(x / STEP_WIDTH_PX + 0.5)

    @staticmethod
    def _x_to_cell(x: float) -> int:
        """场景 x -> 光标所在的网格（向下取整，读数用）。"""
        if x <= 0:
            return 0
        return int(x / STEP_WIDTH_PX)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._set_ruler_hover(False)
        self._set_playhead_hover(False)
        self.cursorLeft.emit()
        super().leaveEvent(event)

    def _set_ruler_hover(self, hover: bool) -> None:
        """鼠标进出时间轴时提亮或还原整条，提示这里是可拖动的。"""
        if hover == self._ruler_hover:
            return
        self._ruler_hover = hover
        self.viewport().update()

    # ---------- 选中与删除 ----------

    def _select_note(self, note: Note | None) -> None:
        """选中一个音符，它的边框会变亮；传 None 表示取消选中。"""
        if note is self._selected_note:
            return
        previous = self._selected_note
        self._selected_note = note
        for candidate in (previous, note):
            if candidate is not None:
                self._update_item(candidate)

    @property
    def selected_note(self) -> Note | None:
        return self._selected_note

    def delete_selected_note(self) -> bool:
        """删除选中的音符，返回是否真的删了。

        通过 editStarted / editFinished 走一次完整的编辑，主窗口据此记进撤销栈。
        """
        note = self._selected_note
        channel = self.channel
        if note is None or channel is None or note not in channel:
            return False
        self.editStarted.emit()
        channel.remove_note(note)
        self._selected_note = None
        self._sync_notes()
        self.viewport().update()
        self.notesChanged.emit()
        self.editFinished.emit()
        return True

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if self.delete_selected_note():
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """右键音符：删除它。空白处不弹菜单。"""
        pos = self.mapToScene(event.pos())
        note = self._note_at(pos)
        if note is None:
            event.ignore()
            return
        self._select_note(note)
        menu = QMenu(self)
        menu.addAction("删除音符", self.delete_selected_note)
        menu.exec(event.globalPos())

    # ---------- 拖动读数 ----------

    def _remember_drag_target(self, note: Note) -> None:
        """记下拖动中的音高与结束格，供 drawForeground 画辅助线。"""
        self._drag_pitch = note.pitch
        self._drag_end_step = note.start + note.duration

    def _show_readout(self, note: Note, viewport_pos) -> None:
        """在光标旁显示音名与长度，松手后隐藏。"""
        beats = note.duration / self.project.grid_step
        self._readout.setText(
            f"{pitch_name(note.pitch)}｜第 {note.start} 格｜长 {note.duration} 格"
            f"（{beats:g} 拍）"
        )
        self._place_readout(viewport_pos)

    def _show_time_readout(self, step: int, viewport_pos) -> None:
        """拖动播放头时在光标旁显示当前位置：小节.拍 与秒数。"""
        _, beat_text = self.ruler_text(step)
        seconds = step * 60.0 / self.project.bpm / self.project.grid_step
        self._readout.setText(f"小节 {beat_text}｜{seconds:.3f}s")
        self._place_readout(viewport_pos)

    def _place_readout(self, viewport_pos) -> None:
        """把读数气泡挪到光标旁，越界时夹回视口内。"""
        self._readout.adjustSize()
        x = min(
            max(viewport_pos.x() + 14, 0),
            max(0, self.viewport().width() - self._readout.width()),
        )
        y = viewport_pos.y() - self._readout.height() - 10
        if y < 0:
            y = viewport_pos.y() + 18
        self._readout.move(x, y)
        self._readout.show()

    def _hide_readout(self) -> None:
        self._readout.hide()

    # ---------- 鼠标交互 ----------

    def _note_at(self, scene_pos) -> Note | None:
        """找出鼠标位置下的音符；重叠时取最上面（最后加入）的一个。"""
        channel = self.channel
        if channel is None:
            return None
        hit = None
        for note in channel:
            if self._note_rect(note).contains(scene_pos):
                hit = note
        return hit

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self.channel is None:
            super().mousePressEvent(event)
            return
        if self._in_keyboard(event.position()):
            # 左侧键盘列只用于定位，不受编辑操作影响
            return
        pos = self.mapToScene(event.position().toPoint())
        if self._in_ruler(event.position()) or self._in_playhead(event.position(), pos):
            # 抓住播放头：顶部时间轴任意位置，或网格区里贴着那根线
            self._mode = "scrub"
            self._scrub_to(pos.x(), event.position())
            return
        note = self._note_at(pos)
        # 先发信号再改数据：主窗口在这时存下「编辑前」的快照。
        self.editStarted.emit()
        if note is not None:
            if pos.x() >= self._note_rect(note).right() - RESIZE_EDGE_PX:
                self._mode = "resize"
            else:
                self._mode = "move"
                self._drag_grab_offset_steps = pos.x() / STEP_WIDTH_PX - note.start
            self._drag_note = note
        else:
            # 空白处按下：新建一个长度为 1 步的音符，后续拖动改长度和音高。
            note = self.channel.create_note(
                self._y_to_pitch(pos.y()), self._x_to_step(pos.x()), 1
            )
            self._sync_notes()  # 为新音符补上图元
            self._mode = "create"
            self._drag_note = note
        self._select_note(note)
        self._remember_drag_target(note)
        self._show_readout(note, event.position().toPoint())
        self.viewport().update()
        self.notesChanged.emit()

    def mouseMoveEvent(self, event) -> None:
        pos = self.mapToScene(event.position().toPoint())
        self._set_ruler_hover(self._in_ruler(event.position()))
        self._set_playhead_hover(
            self._mode == "scrub" or self._in_playhead(event.position(), pos)
        )
        if self._mode == "scrub":
            self._scrub_to(pos.x(), event.position())
            self._update_cursor(event.position())
            super().mouseMoveEvent(event)
            return
        if self._mode == "none":
            if not self._in_ruler(event.position()):
                self.cursorMoved.emit(self._y_to_pitch(pos.y()), self._x_to_cell(pos.x()))
            self._update_cursor(event.position())
            super().mouseMoveEvent(event)
            return
        note = self._drag_note
        if note is None:
            return
        if self._mode == "resize":
            end = max(note.start + 1, self._x_to_step(pos.x()))
            note.duration = end - note.start
        elif self._mode == "move":
            grabbed_x = pos.x() - self._drag_grab_offset_steps * STEP_WIDTH_PX
            note.start = self._x_to_step(grabbed_x)
            note.pitch = self._y_to_pitch(pos.y())
        else:  # create
            end = max(note.start + 1, self._x_to_step(pos.x()))
            note.duration = end - note.start
            note.pitch = self._y_to_pitch(pos.y())
        self._update_item(note)
        self._remember_drag_target(note)
        self._show_readout(note, event.position().toPoint())
        self.viewport().update()
        self.notesChanged.emit()

    def mouseReleaseEvent(self, event) -> None:
        if self._mode == "scrub":
            self._mode = "none"
            self._hide_readout()
            self.viewport().update()
            return
        if self._mode != "none":
            self._mode = "none"
            self._drag_note = None
            self._hide_readout()
            self._sync_notes()
            self.viewport().update()
            self.notesChanged.emit()
            self.editFinished.emit()
            return
        super().mouseReleaseEvent(event)

    def _scrub_to(self, scene_x: float, viewport_pos=None) -> None:
        """把播放头挪到时间轴上对应的网格，并通知主窗口。

        传了光标位置就在旁边显示此刻的时间读数。
        """
        step = self._x_to_step(scene_x)
        self.set_playhead_step(step)
        self.playheadMoved.emit(step)
        if viewport_pos is not None:
            self._show_time_readout(step, viewport_pos)

    def _update_cursor(self, viewport_pos) -> None:
        """悬停时给出提示光标：时间轴与播放头可拖动，右边缘可拖长度，音符内部可拖动。"""
        pos = self.mapToScene(viewport_pos.toPoint())
        if self._in_ruler(viewport_pos) or self._in_playhead(viewport_pos, pos):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            return
        note = self._note_at(pos)
        if note is None:
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif pos.x() >= self._note_rect(note).right() - RESIZE_EDGE_PX:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)

    def _set_playhead_hover(self, hover: bool) -> None:
        """鼠标压在播放头上时把它画粗，离开还原。"""
        if hover == self._playhead_hover:
            return
        self._playhead_hover = hover
        self._position_playhead(self._playhead_step)
        self.viewport().update()

    # ---------- 播放头 ----------

    def set_playhead_step(self, step: int) -> None:
        """移动播放头到指定网格（不改变场景结构）。"""
        if step < 0:
            step = 0
        self._playhead_step = step
        self._position_playhead(step)

    def reset_playhead(self) -> None:
        """播放头回到起点。"""
        self.set_playhead_step(0)

    # ---------- 视图辅助 ----------

    def zoom_in(self) -> None:
        self.scale(1.25, 1.0)

    def zoom_out(self) -> None:
        self.scale(0.8, 1.0)
