"""工程数据类：BPM、拍号、网格步进、通道集合。

grid_step 语义（重要，定清楚）：
    grid_step = 每拍的网格份数。
    grid_step=1  -> 网格为 1/1 拍，一步等于一整拍
    grid_step=2  -> 1/2 拍
    grid_step=4  -> 1/4 拍
    grid_step=8  -> 1/8 拍
    grid_step=16 -> 1/16 拍
    一步的秒数 = 60 / bpm / grid_step。
    拍号目前不参与「一步多少秒」的换算（BPM 一律以四分音符为一拍），
    它保存在工程里供界面显示和后续导出使用。
"""

from __future__ import annotations

from .channel import CHANNEL_INDEX_BASE, Channel
from .note import NoteError

DEFAULT_BPM = 120.0
DEFAULT_TIME_SIGNATURE = (4, 4)  # (分子, 分母)
DEFAULT_GRID_STEP = 4  # 每拍 4 份，即 1/4 拍一格

# 允许的网格步进，界面下拉框只给这些值。
ALLOWED_GRID_STEPS = (1, 2, 4, 8, 16)


class ProjectError(ValueError):
    """工程字段非法。"""


class Project:
    """一个音序工程。"""

    def __init__(
        self,
        bpm: float = DEFAULT_BPM,
        time_signature_numerator: int = DEFAULT_TIME_SIGNATURE[0],
        time_signature_denominator: int = DEFAULT_TIME_SIGNATURE[1],
        grid_step: int = DEFAULT_GRID_STEP,
    ) -> None:
        self.bpm = _validate_bpm(bpm)
        self.time_signature_numerator = _validate_positive_int(
            "time_signature_numerator", time_signature_numerator
        )
        self.time_signature_denominator = _validate_positive_int(
            "time_signature_denominator", time_signature_denominator
        )
        self.grid_step = _validate_grid_step(grid_step)
        self._channels: list[Channel] = []

    # ---------- 通道管理 ----------

    @property
    def channels(self) -> list[Channel]:
        """通道列表。取用请走本属性。"""
        return self._channels

    @channels.setter
    def channels(self, value: list[Channel]) -> None:
        self._channels = list(value)
        self._renumber()

    def add_channel(self, name: str = "", color: str = "") -> Channel:
        """追加一个通道，编号依次递增。返回新通道。"""
        channel = Channel(index=len(self._channels), name=name, color=color)
        self._channels.append(channel)
        return channel

    def remove_channel(self, index: int) -> Channel:
        """按下标删除通道，并重排剩余通道编号。返回被删掉的通道。"""
        if not 0 <= index < len(self._channels):
            raise ProjectError(f"通道下标越界：{index}（当前通道数 {len(self._channels)}）")
        removed = self._channels.pop(index)
        self._renumber()
        return removed

    def channel_at(self, index: int) -> Channel:
        """按下标取通道。"""
        if not 0 <= index < len(self._channels):
            raise ProjectError(f"通道下标越界：{index}（当前通道数 {len(self._channels)}）")
        return self._channels[index]

    def _renumber(self) -> None:
        """通道增删后把 index 重新排成 0..n-1。"""
        for i, channel in enumerate(self._channels):
            channel.index = i

    def __len__(self) -> int:
        return len(self._channels)

    # ---------- 音符编辑辅助 ----------

    def add_note(self, channel_index: int, note) -> None:
        """往指定通道加音符。"""
        self.channel_at(channel_index).add_note(note)

    def create_note(self, channel_index: int, pitch: int, start: int, duration: int):
        """往指定通道新建音符。"""
        return self.channel_at(channel_index).create_note(pitch, start, duration)

    def length_in_steps(self) -> int:
        """全工程最后一个音符的结束位置（网格整数）；空工程返回 0。"""
        return max(
            (note.end for channel in self._channels for note in channel),
            default=0,
        )

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        """转成可 JSON 序列化的字典。"""
        return {
            "bpm": self.bpm,
            "time_signature_numerator": self.time_signature_numerator,
            "time_signature_denominator": self.time_signature_denominator,
            "grid_step": self.grid_step,
            "channels": [c.to_dict() for c in self._channels],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        """从字典还原工程。"""
        if not isinstance(data, dict):
            raise ProjectError(f"工程数据必须是字典，实际是 {type(data).__name__}")
        for key in ("bpm", "grid_step", "channels"):
            if key not in data:
                raise ProjectError(f"工程数据缺少字段：{key}")
        project = cls(
            bpm=data["bpm"],
            time_signature_numerator=data.get(
                "time_signature_numerator", DEFAULT_TIME_SIGNATURE[0]
            ),
            time_signature_denominator=data.get(
                "time_signature_denominator", DEFAULT_TIME_SIGNATURE[1]
            ),
            grid_step=data["grid_step"],
        )
        project.channels = [Channel.from_dict(item) for item in data["channels"]]
        return project

    def __repr__(self) -> str:
        return (
            f"Project(bpm={self.bpm}, "
            f"time_signature={self.time_signature_numerator}/"
            f"{self.time_signature_denominator}, "
            f"grid_step={self.grid_step}, channels={len(self._channels)})"
        )


def _validate_bpm(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectError(f"bpm 必须是数字，实际是 {value!r}")
    value = float(value)
    if value <= 0:
        raise ProjectError(f"bpm 必须大于 0，实际是 {value}")
    return value


def _validate_positive_int(name: str, value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProjectError(f"{name} 必须是正整数，实际是 {value!r}")
    return value


def _validate_grid_step(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProjectError(f"grid_step 必须是正整数，实际是 {value!r}")
    return value


__all__ = [
    "ALLOWED_GRID_STEPS",
    "CHANNEL_INDEX_BASE",
    "Channel",
    "DEFAULT_BPM",
    "DEFAULT_GRID_STEP",
    "DEFAULT_TIME_SIGNATURE",
    "NoteError",
    "Project",
    "ProjectError",
]
