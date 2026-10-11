"""钢琴卷帘编辑区：QGraphicsView / QGraphicsScene 实现。

坐标系（scene 坐标，单位是像素）：
    x = 网格整数 * STEP_WIDTH_PX     —— 横轴是以节拍为基础的离散时间网格
    y = RULER_HEIGHT_PX + (MAX_PITCH - 音高) * NOTE_HEIGHT_PX
    刻意不用 scale(1, -1) 翻转视图：翻转会让所有文字倒过来；
    这里改成在算 y 的时候减一下，滚轮和滚动条的行为都保持正常。

本模块只负责显示与鼠标编辑，不碰串口、不碰播放时钟。
音符集合始终指向选中的那个 Channel，编辑直接改 Channel 里的 Note 对象。
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import (
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from ..model.note import MAX_PITCH, Note
from ..model.project import Project

STEP_WIDTH_PX = 24.0  # 一个网格的宽度
NOTE_HEIGHT_PX = 12.0  # 一个音高行的高度
RULER_HEIGHT_PX = 16.0  # 顶部刻度条高度
RESIZE_EDGE_PX = 6.0  # 距右边缘多少像素内算「拖长度」
MIN_VISIBLE_STEPS = 64  # 内容不足时的最小可见网格数
GRID_LINE = "#3C3C3C"
GRID_LINE_BEAT = "#6E6E6E"
GRID_BACK = "#1E1E1E"
RULER_BACK = "#2A2A2A"
PLAYHEAD_COLOR = "#FF4C4C"
LABEL_COLOR = "#B0B0B0"
NOTE_BORDER = "#101010"


class PianoRollView(QGraphicsView):
    """钢琴卷帘视图。

    交互：左键在空白处按下拖动 = 新建音符；左键按住已有音符拖动 = 移动（含改音高）；
    按住音符右边缘拖动 = 改长度。全部结果吸附到整数网格。
    """

    notesChanged = Signal()  # 音符被增删改后发出，主窗口用来刷新状态栏

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
        self._playhead: QGraphicsRectItem | None = None
        self._playhead_step = 0
        self._max_steps = MIN_VISIBLE_STEPS

        # 鼠标拖动状态
        self._mode = "none"
        self._drag_note: Note | None = None
        self._drag_grab_offset_steps = 0.0  # 按下点相对音符起始的网格偏移（浮点）

        self._rebuild_scene()

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
        self._playhead = None
        self._max_steps = max(MIN_VISIBLE_STEPS, self.project.length_in_steps() + 16)
        self._draw_grid()
        self._draw_playhead()
        self._sync_notes()
        self._scene.setSceneRect(self._scene_rect())

    def _scene_rect(self) -> QRectF:
        height = RULER_HEIGHT_PX + (MAX_PITCH + 1) * NOTE_HEIGHT_PX
        return QRectF(0.0, 0.0, self._max_steps * STEP_WIDTH_PX, height)

    def _draw_grid(self) -> None:
        """画顶部刻度条、网格竖线、每拍加粗线、音高行和少量标签。"""
        width = self._max_steps * STEP_WIDTH_PX
        height = RULER_HEIGHT_PX + (MAX_PITCH + 1) * NOTE_HEIGHT_PX

        ruler = QGraphicsRectItem(QRectF(0.0, 0.0, width, RULER_HEIGHT_PX))
        ruler.setBrush(QBrush(QColor(RULER_BACK)))
        ruler.setPen(QPen(Qt.PenStyle.NoPen))
        ruler.setZValue(-2)
        self._scene.addItem(ruler)

        grid_step = self.project.grid_step
        step_pen = QPen(QColor(GRID_LINE))
        step_pen.setCosmetic(True)
        beat_pen = QPen(QColor(GRID_LINE_BEAT))
        beat_pen.setCosmetic(True)
        for step in range(self._max_steps + 1):
            x = step * STEP_WIDTH_PX
            line = self._scene.addLine(x, RULER_HEIGHT_PX, x, height, step_pen)
            line.setZValue(-1)
            if step % grid_step == 0:
                # 每一拍一条加粗线，并在刻度条上标出拍号
                beat_line = self._scene.addLine(x, RULER_HEIGHT_PX, x, height, beat_pen)
                beat_line.setZValue(-1)
                label = QGraphicsSimpleTextItem(str(step // grid_step))
                label.setBrush(QBrush(QColor(LABEL_COLOR)))
                label.setPos(x + 2.0, 1.0)
                label.setZValue(-1)
                self._scene.addItem(label)

        row_pen = QPen(QColor(GRID_LINE))
        row_pen.setCosmetic(True)
        for pitch in range(MAX_PITCH + 1):
            y = self._pitch_to_y(pitch)
            line = self._scene.addLine(0.0, y, width, y, row_pen)
            line.setZValue(-1)
            if pitch in (0, 12, 24, 36, 48, 63):
                label = QGraphicsSimpleTextItem(str(pitch))
                label.setBrush(QBrush(QColor(LABEL_COLOR)))
                label.setPos(2.0, y + 1.0)
                label.setZValue(-1)
                self._scene.addItem(label)

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
        self._playhead.setRect(
            QRectF(step * STEP_WIDTH_PX - 1.0, 0.0, 2.0, self._scene_rect().height())
        )

    # ---------- 音符图元 ----------

    def _sync_notes(self) -> None:
        """让场景里的音符图元与当前通道的音符集合保持一致。"""
        channel = self.channel
        wanted = {} if channel is None else {id(note): note for note in channel}
        for key in list(self._note_items):
            if key not in wanted:
                self._scene.removeItem(self._note_items.pop(key))
        for key, note in wanted.items():
            if key not in self._note_items:
                item = QGraphicsRectItem()
                item.setZValue(1)
                self._scene.addItem(item)
                self._note_items[key] = item
            self._update_item(note)
            self._note_items[key].setToolTip(
                f"音高 {note.pitch}｜开始 {note.start}｜长度 {note.duration}"
            )

    def _update_item(self, note: Note) -> None:
        item = self._note_items.get(id(note))
        if item is None:
            return
        item.setRect(self._note_rect(note))
        color = QColor(self.channel.color) if self.channel else QColor("#4C8BF5")
        item.setBrush(QBrush(color))
        pen = QPen(QColor(NOTE_BORDER))
        pen.setCosmetic(True)
        item.setPen(pen)

    def _note_rect(self, note: Note) -> QRectF:
        return QRectF(
            note.start * STEP_WIDTH_PX,
            self._pitch_to_y(note.pitch),
            max(1.0, note.duration * STEP_WIDTH_PX - 1.0),
            NOTE_HEIGHT_PX - 1.0,
        )

    @staticmethod
    def _pitch_to_y(pitch: int) -> float:
        """音高 -> 场景 y。音高越大越靠上。"""
        return RULER_HEIGHT_PX + (MAX_PITCH - pitch) * NOTE_HEIGHT_PX

    @staticmethod
    def _y_to_pitch(y: float) -> int:
        """场景 y -> 音高，并夹到 0..MAX_PITCH。"""
        pitch = MAX_PITCH - int((y - RULER_HEIGHT_PX) // NOTE_HEIGHT_PX)
        return max(0, min(MAX_PITCH, pitch))

    @staticmethod
    def _x_to_step(x: float) -> int:
        """场景 x -> 最近的整数网格（吸附）。"""
        if x <= 0:
            return 0
        return int(x / STEP_WIDTH_PX + 0.5)

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
        pos = self.mapToScene(event.position().toPoint())
        note = self._note_at(pos)
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
        self.notesChanged.emit()

    def mouseMoveEvent(self, event) -> None:
        pos = self.mapToScene(event.position().toPoint())
        if self._mode == "none":
            self._update_cursor(pos)
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
        self.notesChanged.emit()

    def mouseReleaseEvent(self, event) -> None:
        if self._mode != "none":
            self._mode = "none"
            self._drag_note = None
            self._sync_notes()
            self.notesChanged.emit()
            return
        super().mouseReleaseEvent(event)

    def _update_cursor(self, pos) -> None:
        """悬停时给出提示光标：右边缘可拖长度，音符内部可拖动。"""
        note = self._note_at(pos)
        if note is None:
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif pos.x() >= self._note_rect(note).right() - RESIZE_EDGE_PX:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)

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
