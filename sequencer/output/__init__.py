"""输出适配层：把 NoteEvent 送到某个去处。

音序编辑器只产生 NoteEvent，不关心它是写日志还是发串口。
换输出方式只需要换一个适配器，编辑器和播放引擎都不用改。
"""

from .base import OutputAdapter
from .debug_output import DebugOutput
from .serial_output import SerialOutput, SerialOutputError

__all__ = ["DebugOutput", "OutputAdapter", "SerialOutput", "SerialOutputError"]
