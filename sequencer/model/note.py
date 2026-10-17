"""音符数据类。

坐标语义（全工程统一，不要在别处另立一套）：
    start    —— 起始位置，单位是「网格步进的整数个数」，从 0 开始计数。
    duration —— 持续长度，单位同上，至少 1 步。
    两者都是整数，不使用浮点秒。秒数只在 core.timing 里换算。

音高语义：
    0 表示休止或无音；1~63 是有效音高，其中 1 号是 C2（65Hz），与《音高表.xlsx》一致。
    频率映射由 CH32V 硬件决定，PC 端不做音高到频率的换算，这里只保存音高编号。
"""

from __future__ import annotations

MIN_PITCH = 0
MAX_PITCH = 63


class NoteError(ValueError):
    """音符字段非法。校验失败一律抛出本异常，不做静默修正。"""


class Note:
    """一个音符：音高 + 开始位置 + 持续时间。

    同一通道内允许音符时间重叠，数据层不做任何重叠检查。
    """

    __slots__ = ("pitch", "start", "duration")

    def __init__(self, pitch: int, start: int, duration: int) -> None:
        self.pitch = _validate_int("pitch", pitch, MIN_PITCH, MAX_PITCH)
        self.start = _validate_int("start", start, 0, None)
        self.duration = _validate_int("duration", duration, 1, None)

    @property
    def end(self) -> int:
        """结束位置（网格整数），等于 start + duration。"""
        return self.start + self.duration

    def copy(self) -> "Note":
        """浅拷贝一份音符，供编辑操作在不破坏原对象的前提下试算。"""
        return Note(self.pitch, self.start, self.duration)

    def to_dict(self) -> dict:
        """转成可 JSON 序列化的字典。"""
        return {"pitch": self.pitch, "start": self.start, "duration": self.duration}

    @classmethod
    def from_dict(cls, data: dict) -> "Note":
        """从字典还原音符；缺字段或字段非法都会显式报错。"""
        if not isinstance(data, dict):
            raise NoteError(f"音符数据必须是字典，实际是 {type(data).__name__}")
        missing = [k for k in ("pitch", "start", "duration") if k not in data]
        if missing:
            raise NoteError(f"音符数据缺少字段：{', '.join(missing)}")
        return cls(data["pitch"], data["start"], data["duration"])

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Note):
            return NotImplemented
        return (self.pitch, self.start, self.duration) == (
            other.pitch,
            other.start,
            other.duration,
        )

    def __hash__(self) -> int:
        return hash((self.pitch, self.start, self.duration))

    def __repr__(self) -> str:
        return f"Note(pitch={self.pitch}, start={self.start}, duration={self.duration})"


def _validate_int(name: str, value, low: int, high: int | None) -> int:
    """校验一个整数字段；bool 会被拒绝（它是 int 的子类，但不是音序里的合法值）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise NoteError(f"{name} 必须是整数，实际是 {value!r}（{type(value).__name__}）")
    if value < low:
        raise NoteError(f"{name} 不能小于 {low}，实际是 {value}")
    if high is not None and value > high:
        raise NoteError(f"{name} 不能大于 {high}，实际是 {value}")
    return value
