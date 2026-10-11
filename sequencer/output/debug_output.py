"""调试输出：把音符事件格式化后写进界面侧栏的事件日志。

串口协议没定之前，播放事件统一走这里，便于核对时间点是否正确。
"""

from __future__ import annotations

from ..core.playback import EVENT_NOTE_ON
from .base import OutputAdapter

#: 音高编号 -> 便于肉眼核对的音名。只用于日志显示，不参与发音。
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def pitch_name(pitch: int) -> str:
    """把音高编号转成便于核对的音名（仅显示用，硬件映射另定）。"""
    if pitch <= 0:
        return "休止"
    if pitch > 127:
        return "?"
    octave, index = divmod(pitch, 12)
    return f"{_NOTE_NAMES[index]}{octave}"


class DebugOutput(OutputAdapter):
    """把事件交给界面日志的回调函数。

    emit 是一个接受字符串的函数，例如主窗口的事件日志追加方法。
    不传 emit 时退化为 print，便于命令行排查。
    """

    name = "调试输出"

    def __init__(self, emit=None, show_pitch_name: bool = True) -> None:
        self._emit = emit or print
        self.show_pitch_name = show_pitch_name

    def send(self, event) -> None:
        """格式化一条事件并写日志。"""
        self._emit(self.format_event(event))

    def format_event(self, event) -> str:
        """把事件格式化成人能读的一行。

        格式示例：``  1.250s  开始  通道 2-62音``

        通道号使用数据下标，从 0 开始。界面上的通道列表显示为 1、2、3，
        两处相差 1，核对时以此处的下标为准。
        """
        kind = "开始" if event.kind == EVENT_NOTE_ON else "结束"
        text = (
            f"{event.time_seconds:8.3f}s  {kind}"
            f"  通道 {event.channel_index}-{event.pitch}音"
        )
        if self.show_pitch_name:
            text += f"（{pitch_name(event.pitch)}）"
        return text
