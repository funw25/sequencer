"""输出适配器接口。

只定义这一个接口，不做多层抽象。
"""

from __future__ import annotations


class OutputAdapter:
    """音符事件的输出端。

    子类至少要实现 send()。open() / close() 视需要覆盖，
    默认什么都不做，方便只打日志的适配器（如 DebugOutput）。
    """

    #: 显示给用户看的名字。
    name = "output"

    def open(self) -> None:
        """准备输出通道（例如打开串口）。默认无操作。"""

    def close(self) -> None:
        """关闭输出通道。默认无操作。"""

    @property
    def is_open(self) -> bool:
        """输出通道是否已就绪。默认视为总是就绪。"""
        return True

    def send(self, event) -> None:
        """发送一个 NoteEvent。子类必须实现。"""
        raise NotImplementedError("输出适配器必须实现 send(event)")

    def describe(self) -> str:
        """一句话描述当前状态，显示在主窗口状态栏。"""
        return f"{self.name}：{'已就绪' if self.is_open else '未就绪'}"
