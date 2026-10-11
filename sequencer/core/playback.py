"""播放引擎：把工程展平成按时间排序的事件序列，并按时间推进产生事件。

架构要点：
    音序编辑器不直接操作串口。本模块只产生统一的 NoteEvent，
    交给 output 层的适配器（DebugOutput 写日志 / SerialOutput 发串口）处理。

事件语义：
    NoteEvent(kind, channel_index, pitch, time_seconds)
    kind 取 EVENT_NOTE_ON / EVENT_NOTE_OFF 两个常量。
    拿到一段事件就能回答「哪个通道、哪个音高、什么时候开始、什么时候停止」。

同一时刻的排序规则（确定性，必须保持）：
    1) 时间升序
    2) 同一时刻 NOTE_OFF 排在 NOTE_ON 前面，避免同音高的音被自己的 NOTE_ON 打断
    3) 再按通道下标升序
    4) 再按音高升序

播放位置：
    时间取自 time.monotonic()，不依赖定时器的时间精度，定时器只负责「多久看一眼」。
    QTimer 由界面层持有并调用 step()，因此本模块不依赖 PySide6，
    没有图形环境也能测试。暂停会为正在发声的音符补发 NOTE_OFF，
    恢复播放时再从当前位置重新发 NOTE_ON，避免悬挂音。
"""

from __future__ import annotations

import time

from ..model.project import Project
from .timing import step_to_seconds

EVENT_NOTE_ON = "NOTE_ON"
EVENT_NOTE_OFF = "NOTE_OFF"

# 同一时刻的排序权重：NOTE_OFF 优先。
_KIND_RANK = {EVENT_NOTE_OFF: 0, EVENT_NOTE_ON: 1}


class NoteEvent:
    """一次音符事件。time_seconds 是相对工程起点的秒数（浮点，仅运行时使用）。"""

    __slots__ = ("kind", "channel_index", "pitch", "time_seconds")

    def __init__(self, kind: str, channel_index: int, pitch: int, time_seconds: float) -> None:
        if kind not in _KIND_RANK:
            raise ValueError(
                f"未知事件类型：{kind!r}，只支持 {EVENT_NOTE_ON} / {EVENT_NOTE_OFF}"
            )
        self.kind = kind
        self.channel_index = channel_index
        self.pitch = pitch
        self.time_seconds = float(time_seconds)

    @property
    def is_note_on(self) -> bool:
        return self.kind == EVENT_NOTE_ON

    def copy(self) -> "NoteEvent":
        return NoteEvent(self.kind, self.channel_index, self.pitch, self.time_seconds)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, NoteEvent):
            return NotImplemented
        return (
            self.kind,
            self.channel_index,
            self.pitch,
            self.time_seconds,
        ) == (
            other.kind,
            other.channel_index,
            other.pitch,
            other.time_seconds,
        )

    def __repr__(self) -> str:
        return (
            f"NoteEvent({self.kind}, ch={self.channel_index}, "
            f"pitch={self.pitch}, t={self.time_seconds:.4f}s)"
        )


def collect_events(project: Project, start_step: int = 0) -> list[NoteEvent]:
    """把工程展平成按时间排序的事件列表。

    start_step 之后的音符会给出 NOTE_ON / NOTE_OFF 成对事件；
    跨越 start_step 的音符只给 NOTE_OFF（它的 NOTE_ON 在过去，已经错过），
    这样既不会漏掉结束，也不会产生没有对应的开始。
    """
    events: list[NoteEvent] = []
    bpm = project.bpm
    grid_step = project.grid_step
    for channel in project.channels:
        for note in channel:
            if note.pitch == 0:
                # 音高 0 表示休止或无音，不产生事件。
                continue
            if note.end <= start_step:
                continue
            events.append(
                NoteEvent(
                    EVENT_NOTE_OFF,
                    channel.index,
                    note.pitch,
                    step_to_seconds(note.end, bpm, grid_step),
                )
            )
            if note.start >= start_step:
                events.append(
                    NoteEvent(
                        EVENT_NOTE_ON,
                        channel.index,
                        note.pitch,
                        step_to_seconds(note.start, bpm, grid_step),
                    )
                )
    events.sort(
        key=lambda e: (
            e.time_seconds,
            _KIND_RANK[e.kind],
            e.channel_index,
            e.pitch,
        )
    )
    return events


def project_duration_seconds(project: Project) -> float:
    """工程最后一个音符结束的秒数；没有音符时返回 0.0。"""
    last = max(
        (note.end for channel in project.channels for note in channel if note.pitch != 0),
        default=0,
    )
    if not last:
        return 0.0
    return step_to_seconds(last, project.bpm, project.grid_step)


class PlaybackEngine:
    """播放引擎：play / pause / stop，step() 由界面定时器驱动。

    output 是任一实现了 send(event) 的对象（见 output.base.OutputAdapter）。
    output 为 None 时只产生事件不发出，便于测试和只预览界面。
    """

    def __init__(self, project: Project, output=None, on_event=None) -> None:
        self.project = project
        self.output = output
        self.on_event = on_event
        self._events: list[NoteEvent] = []
        self._next_index = 0
        self._accumulated = 0.0  # 已播完的秒数（暂停或播完时落账）
        self._started_at: float | None = None  # 本次播放段的单调时钟起点
        self._playing = False
        self._start_step = 0

    # ---------- 状态查询 ----------

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def start_step(self) -> int:
        """本段播放从哪个网格位置起播。"""
        return self._start_step

    @property
    def elapsed_seconds(self) -> float:
        """相对工程起点的当前秒数。"""
        return self._position_seconds()

    @property
    def elapsed_steps(self) -> int:
        """当前播放位置的网格整数（向下取整），界面播放头用。"""
        per_step = self._seconds_per_step()
        if per_step <= 0:
            return 0
        return int(self._position_seconds() / per_step)

    @property
    def events(self) -> list[NoteEvent]:
        """本段播放的事件序列副本。"""
        return list(self._events)

    def duration_seconds(self) -> float:
        """当前工程的总时长（秒）。"""
        return project_duration_seconds(self.project)

    # ---------- 控制 ----------

    def play(self, start_step: int | None = None) -> list[NoteEvent]:
        """开始或恢复播放。

        start_step 传 None 时：暂停后继续从暂停位置播；停止或播完则从 0 开始。
        返回本次调用立即产生的事件（通常是起点上的事件）。
        """
        if self._playing:
            return []
        if start_step is None:
            start_step = self._start_step if self._accumulated > 0 else 0
        if start_step < 0:
            raise ValueError(f"start_step 不能为负，实际是 {start_step}")
        self._start_step = start_step
        self._events = collect_events(self.project, start_step=start_step)
        self._next_index = 0
        self._started_at = time.monotonic()
        self._playing = True
        return self.step()

    def pause(self) -> list[NoteEvent]:
        """暂停并保留位置。为正在发声的音符补发 NOTE_OFF，避免悬挂音。"""
        if not self._playing:
            return []
        released = self._release_sounding()
        self._accumulated = self._position_seconds()
        self._playing = False
        self._started_at = None
        return released

    def stop(self) -> list[NoteEvent]:
        """停止并回到工程起点。为正在发声的音符补发 NOTE_OFF。"""
        released = self._release_sounding()
        self._playing = False
        self._started_at = None
        self._accumulated = 0.0
        self._next_index = 0
        self._events = []
        self._start_step = 0
        return released

    def step(self) -> list[NoteEvent]:
        """推进到当前时刻，返回本次新产生的事件。"""
        if not self._playing:
            return []
        now = self._position_seconds()
        ready: list[NoteEvent] = []
        while self._next_index < len(self._events):
            event = self._events[self._next_index]
            if event.time_seconds > now:
                break
            ready.append(event.copy())
            self._next_index += 1
        self._dispatch(ready)
        if self._next_index >= len(self._events):
            # 事件放完就落账并停下，省得界面一直空转。
            self._accumulated = now
            self._playing = False
            self._started_at = None
        return ready

    # ---------- 内部 ----------

    def _seconds_per_step(self) -> float:
        return step_to_seconds(1, self.project.bpm, self.project.grid_step)

    def _position_seconds(self) -> float:
        if not self._playing or self._started_at is None:
            return self._accumulated
        return self._accumulated + (time.monotonic() - self._started_at)

    def _release_sounding(self) -> list[NoteEvent]:
        """为已发 NOTE_ON 但还没发 NOTE_OFF 的音符补发 NOTE_OFF。"""
        sounding: dict[tuple[int, int], int] = {}
        for event in self._events[: self._next_index]:
            key = (event.channel_index, event.pitch)
            if event.kind == EVENT_NOTE_ON:
                sounding[key] = sounding.get(key, 0) + 1
            else:
                sounding[key] = max(0, sounding.get(key, 0) - 1)
        released_at = self._position_seconds()
        pending: list[NoteEvent] = []
        for (channel_index, pitch), count in sounding.items():
            for _ in range(count):
                pending.append(NoteEvent(EVENT_NOTE_OFF, channel_index, pitch, released_at))
        self._dispatch(pending)
        return pending

    def _dispatch(self, events: list[NoteEvent]) -> None:
        """把事件交给输出适配层和界面回调。"""
        for event in events:
            if self.output is not None:
                self.output.send(event)
            if self.on_event is not None:
                self.on_event(event)
