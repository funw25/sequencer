"""串口输出：把播放事件编码成字节并发送。

编码方式为初步对接用的最简方案，与硬件端口头约定：
    1 个字节表示一个音符。
    bit7-6  通道号，0 或 1
    bit5-0  音高，1 至 63；填 0 表示该通道停止发声

硬件侧的振荡器固件接收中断尚未写好，所以本模块的实际收发未经验证。
编码集中在 encode_event()，协议正式定稿后只改这一个函数。

参数默认值取自 2026-10-11 硬件侧交接确认：CH340、115200、8 数据位、无校验。
注意固件源码 V203/OSC_Version_1.1/UART.c 中写的是 9600 且为仅发送模式，
波特率最终取值待硬件侧确认，因此波特率做成可配置。

依赖：pyserial（import serial）。没装时给出明确提示。
"""

from __future__ import annotations

from ..core.playback import EVENT_NOTE_ON

try:  # pragma: no cover - 取决于环境是否装了 pyserial
    import serial
    from serial.tools import list_ports

    _SERIAL_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover
    serial = None
    list_ports = None
    _SERIAL_IMPORT_ERROR = exc

#: 默认波特率。待硬件侧确认，见模块说明。
DEFAULT_BAUDRATE = 115200

#: 可选波特率，界面下拉框用。9600 是固件源码里的取值，列出来备用。
BAUDRATE_CHOICES = (9600, 19200, 38400, 57600, 115200)

#: 通道号在字节中的位置：高 2 位。
CHANNEL_SHIFT = 6

#: 通道号与音高的取值上限。
MAX_CHANNEL_BITS = 3
MAX_PITCH_BITS = 63


class SerialOutputError(RuntimeError):
    """串口未连接、未打开，或数据不合法。"""


def encode_note(channel: int, pitch: int) -> int:
    """把一个音符编码成 1 个字节。

    channel 取 0 至 3，pitch 取 0 至 63。pitch 为 0 表示该通道停止发声。
    取值非法时抛 SerialOutputError，不做静默截断。
    """
    if not 0 <= channel <= MAX_CHANNEL_BITS:
        raise SerialOutputError(f"通道号必须在 0 至 {MAX_CHANNEL_BITS} 之间，实际是 {channel}")
    if not 0 <= pitch <= MAX_PITCH_BITS:
        raise SerialOutputError(f"音高必须在 0 至 {MAX_PITCH_BITS} 之间，实际是 {pitch}")
    return (channel << CHANNEL_SHIFT) | pitch


def scan_ports() -> list[str]:
    """列出当前可用的串口设备名。pyserial 缺失时返回空列表。"""
    if list_ports is None:
        return []
    return [port.device for port in list_ports.comports()]


class SerialOutput:
    """按 pyserial 打开串口并发送音符字节。

    port 为 None 时视为未连接：构造不报错，open() 与 send() 抛出明确异常，
    这样界面可以先挂着一个未配置的串口输出而不崩。
    """

    name = "串口输出"

    def __init__(self, port: str | None = None, baudrate: int = DEFAULT_BAUDRATE) -> None:
        self.port = port
        self.baudrate = baudrate
        self._serial = None
        self.sent_count = 0

    # ---------- 生命周期 ----------

    def open(self) -> None:
        """打开串口。未配置端口、pyserial 缺失或打开失败都抛 SerialOutputError。"""
        if self._serial is not None and self._serial.is_open:
            return
        if not self.port:
            raise SerialOutputError("串口未配置：请先选择端口，再点打开")
        if serial is None:
            raise SerialOutputError(f"pyserial 不可用：{_SERIAL_IMPORT_ERROR}")
        try:
            self._serial = serial.Serial(self.port, self.baudrate, timeout=0)
        except Exception as exc:
            raise SerialOutputError(f"打开串口 {self.port} 失败：{exc}") from exc
        self.sent_count = 0
        # 起始字节：让设备把两个通道都置为静音，避免上次残留的发声状态。
        self._write(bytes([0x00, 0x40]))

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
        """把一个播放事件编码成 1 个字节。

        音符开始按通道与音高编码；音符结束只发送通道号，音高位填 0，即停止该通道。
        """
        if event.kind == EVENT_NOTE_ON:
            return bytes([encode_note(event.channel_index, event.pitch)])
        return bytes([encode_note(event.channel_index, 0)])

    def send(self, event) -> None:
        """编码并写串口。未打开时抛 SerialOutputError。"""
        if not self.is_open:
            raise SerialOutputError(
                f"串口未打开（端口 {self.port!r}），无法发送事件；请先点打开"
            )
        self._write(self.encode_event(event))

    def send_note(self, channel: int, pitch: int) -> int:
        """直接发送一个音符字节，返回实际发送的字节值。用于初步对接测试。"""
        if not self.is_open:
            raise SerialOutputError(f"串口未打开（端口 {self.port!r}），无法发送测试音符")
        value = encode_note(channel, pitch)
        self._write(bytes([value]))
        return value

    def _write(self, payload: bytes) -> None:
        try:
            self._serial.write(payload)
        except Exception as exc:
            raise SerialOutputError(f"写入串口 {self.port} 失败：{exc}") from exc
        self.sent_count += len(payload)

    def describe(self) -> str:
        if self.port and self.is_open:
            return f"{self.name}：{self.port} 已打开 @ {self.baudrate}，已发 {self.sent_count} 字节"
        if self.port:
            return f"{self.name}：{self.port} 未打开"
        return f"{self.name}：未选择端口"
