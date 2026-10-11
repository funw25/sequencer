"""串口输出占位实现。

现状：串口消息格式尚未确定，所以这里不发明任何二进制协议。
encode_event() 只定义签名并显式抛 NotImplementedError，
等协议定下来后，只改这一个方法（或换成真正的实现）即可。

依赖：pyserial（import serial）。没装时给出明确提示。
"""

from __future__ import annotations

from .base import OutputAdapter

try:  # pragma: no cover - 取决于环境是否装了 pyserial
    import serial

    _SERIAL_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover
    serial = None
    _SERIAL_IMPORT_ERROR = exc


class SerialOutputError(RuntimeError):
    """串口未连接、未打开，或消息格式尚未实现。"""


class SerialOutput(OutputAdapter):
    """按 pyserial 打开串口并发送音符事件。

    port 为 None 时视为「未连接」：构造不报错，但 send() 会给出明确异常，
    这样界面可以先挂着一个未配置的串口输出而不炸掉。
    """

    name = "串口输出"

    def __init__(
        self, port: str | None = None, baudrate: int = 115200, timeout: float = 0.0
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None

    # ---------- 生命周期 ----------

    def open(self) -> None:
        """打开串口。未配置端口、pyserial 缺失或打开失败都会抛 SerialOutputError。"""
        if self._serial is not None and self._serial.is_open:
            return
        if self.port is None or self.port == "":
            raise SerialOutputError(
                "串口未配置：请先指定端口（SerialOutput(port=...)），当前没有任何端口被打开"
            )
        if serial is None:
            raise SerialOutputError(f"pyserial 不可用：{_SERIAL_IMPORT_ERROR}")
        try:
            self._serial = serial.Serial(self.port, self.baudrate, timeout=self.timeout)
        except Exception as exc:
            raise SerialOutputError(f"打开串口 {self.port} 失败：{exc}") from exc

    def close(self) -> None:
        """关闭串口。未打开时静默返回。"""
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    @property
    def is_open(self) -> bool:
        return self._serial is not None and bool(self._serial.is_open)

    # ---------- 发送 ----------

    def encode_event(self, event) -> bytes:
        """把 NoteEvent 编码成串口字节。消息格式待定，暂不实现。

        等硬件协议确定后，在这里实现即可，不需要改播放引擎或界面。
        """
        raise NotImplementedError(
            "串口消息格式尚未确定，encode_event() 还没实现。"
            "现在请使用 DebugOutput（事件日志）查看播放结果。"
        )

    def send(self, event) -> None:
        """编码并写串口。任何前置条件不满足都抛 SerialOutputError。"""
        if not self.is_open:
            raise SerialOutputError(
                f"串口未打开（端口 {self.port!r}），无法发送事件 {event!r}；请先调用 open()"
            )
        payload = self.encode_event(event)
        if not isinstance(payload, (bytes, bytearray)):
            raise SerialOutputError(
                f"encode_event() 必须返回 bytes，实际是 {type(payload).__name__}"
            )
        self._serial.write(bytes(payload))

    def describe(self) -> str:
        if self.port and self.is_open:
            return f"{self.name}：{self.port} 已打开 @ {self.baudrate}"
        if self.port:
            return f"{self.name}：{self.port} 未打开"
        return f"{self.name}：未配置端口（消息格式待定）"
