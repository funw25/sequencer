"""通道数据类：一个通道对应一个合成器模块或乐器。
通道编号基准待确认，集中在 CHANNEL_INDEX_BASE 定义。
改这一个常量即可切换 0 起编或 1 起编，其它代码不要硬编码编号含义。
"""

from __future__ import annotations

from .note import Note, NoteError

# 通道编号从几开始。待团队确认（0起编 /1起编），当前取 0。
# 注意：本项目里通道的「列表下标」始终从 0 开始，
# CHANNEL_INDEX_BASE 只影响对外展示和默认命名。
CHANNEL_INDEX_BASE = 0

# 通道默认配色（循环取用），只用于显示，不进入播放逻辑。
DEFAULT_COLORS = (
    "#4C8BF5",
    "#E8663D",
    "#2FA84F",
    "#B45BD6",
    "#D6A32F",
    "#3DBFBF",
    "#D6508C",
    "#7A8B99",
)


class Channel:
    """一个通道：编号、名称、颜色、音符集合。

    音符以 list 保存，允许重叠，也允许同一个音高在同一时间重复出现。
    增删查改都走本类的方法，外部不要直接改内部列表。
    """

    def __init__(self, index: int, name: str = "", color: str = "") -> None:
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise NoteError(f"通道下标必须是非负整数，实际是 {index!r}")
        self.index = index
        self.name = name or f"通道 {index + CHANNEL_INDEX_BASE}"
        self.color = color or DEFAULT_COLORS[index % len(DEFAULT_COLORS)]
        self._notes: list[Note] = []

    # ---------- 查询 ----------

    @property
    def notes(self) -> list[Note]:
        """返回音符列表的副本，防止外部绕过校验直接改内部结构。"""
        return list(self._notes)

    def __len__(self) -> int:
        return len(self._notes)

    def __iter__(self):
        return iter(self._notes)

    def add_note(self, note: Note) -> Note:
        """加入一个音符。允许与已有音符重叠，不做冲突检查。"""
        if not isinstance(note, Note):
            raise NoteError(f"只能加入 Note 对象，实际是 {type(note).__name__}")
        self._notes.append(note)
        return note

    def create_note(self, pitch: int, start: int, duration: int) -> Note:
        """按参数新建并加入音符，字段非法时 Note 构造器会抛错。"""
        return self.add_note(Note(pitch, start, duration))

    def remove_note(self, note: Note) -> None:
        """按对象删除音符；对象不在本通道时抛错而不是静默忽略。"""
        try:
            self._notes.remove(note)
        except ValueError:
            raise NoteError(f"音符不在通道 {self.index} 中：{note!r}") from None

    def remove_notes_at(self, pitch: int, start: int) -> int:
        """删除指定 (音高, 开始位置) 上的全部音符，返回删除个数。"""
        victims = [n for n in self._notes if n.pitch == pitch and n.start == start]
        for note in victims:
            self._notes.remove(note)
        return len(victims)

    def notes_at(self, pitch: int, start: int) -> list[Note]:
        """查出指定 (音高, 开始位置) 上的全部音符（重叠时可能多于一个）。"""
        return [n for n in self._notes if n.pitch == pitch and n.start == start]

    def clear_notes(self) -> None:
        """清空本通道的全部音符。"""
        self._notes.clear()

    def replace_notes(self, notes) -> None:
        """整体替换音符集合，用于从工程文件载入。"""
        self._notes = []
        for note in notes:
            self.add_note(note)

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        """转成可 JSON 序列化的字典。"""
        return {
            "index": self.index,
            "name": self.name,
            "color": self.color,
            "notes": [n.to_dict() for n in self._notes],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Channel":
        """从字典还原通道及其音符。"""
        if not isinstance(data, dict):
            raise NoteError(f"通道数据必须是字典，实际是 {type(data).__name__}")
        for key in ("index", "notes"):
            if key not in data:
                raise NoteError(f"通道数据缺少字段：{key}")
        channel = cls(
            index=data["index"],
            name=data.get("name", ""),
            color=data.get("color", ""),
        )
        channel.replace_notes(Note.from_dict(item) for item in data["notes"])
        return channel

    def __repr__(self) -> str:
        return f"Channel(index={self.index}, name={self.name!r}, notes={len(self._notes)})"
