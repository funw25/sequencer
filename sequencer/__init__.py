"""PC 端多通道音序器上位机骨架。

分层约定（重要）：
    model   —— 纯数据：音符、通道、工程
    core    —— 时间换算与播放引擎（产生 NoteEvent）
    io      —— 工程 JSON 存取
    output  —— 输出适配层（调试日志 / 串口）
    ui      —— PySide6 界面

音序编辑器不直接操作串口，只产生 NoteEvent，再由 output 层决定怎么发。
"""

__version__ = "0.1.0"
