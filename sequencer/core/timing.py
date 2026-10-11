"""时间换算：网格整数 <-> 节拍 <-> 秒。

术语与单位（全工程统一）：
    网格整数 / step —— 最小的离散时间单位，整数。音符的 start / duration 用它。
    节拍 / beat     —— 以四分音符为一拍。1 拍 = grid_step 个网格。
    秒 / second     —— 浮点，只在播放时换算出来，不写进数据模型。

换算链：
    1 拍秒数   = 60 / bpm
    1 网格秒数 = 60 / bpm / grid_step
    seconds_per_step(bpm, grid_step) 是唯一的换算入口，其它函数都基于它。

拍号说明：拍号（如 3/4）目前不参与时长换算，BPM 一律按四分音符为一拍；
拍号字段保留在工程里，供界面显示和后续小节线使用。
"""

from __future__ import annotations


def seconds_per_step(bpm: float, grid_step: int) -> float:
    """一个网格对应多少秒。bpm 无效时抛 ValueError，不静默返回 0。"""
    bpm = float(bpm)
    grid_step = int(grid_step)
    if bpm <= 0:
        raise ValueError(f"bpm 必须大于 0，实际是 {bpm}")
    if grid_step <= 0:
        raise ValueError(f"grid_step 必须大于 0，实际是 {grid_step}")
    return 60.0 / bpm / grid_step


def step_to_seconds(step: int, bpm: float, grid_step: int) -> float:
    """网格整数 -> 秒。step 可以是 0 或正数。"""
    if step < 0:
        raise ValueError(f"step 不能为负，实际是 {step}")
    return step * seconds_per_step(bpm, grid_step)


def seconds_to_step(seconds: float, bpm: float, grid_step: int) -> float:
    """秒 -> 网格（浮点，不取整）。需要整数请配合 snap_to_grid()。"""
    if seconds < 0:
        raise ValueError(f"seconds 不能为负，实际是 {seconds}")
    return seconds / seconds_per_step(bpm, grid_step)


def steps_to_beats(steps: int, grid_step: int) -> float:
    """网格整数 -> 拍（浮点）。"""
    if grid_step <= 0:
        raise ValueError(f"grid_step 必须大于 0，实际是 {grid_step}")
    return steps / grid_step


def beats_to_steps(beats: float, grid_step: int) -> float:
    """拍 -> 网格（浮点，不取整）。"""
    if grid_step <= 0:
        raise ValueError(f"grid_step 必须大于 0，实际是 {grid_step}")
    return beats * grid_step


def snap_to_grid(value: float) -> int:
    """把浮点网格值吸附到最近的整数网格。

    0.5 向上取整（round-half-up）；负数结果一律夹到 0，
    避免出现负的开始位置。
    """
    if value < 0:
        return 0
    return int(value + 0.5)


def snap_seconds_to_step(seconds: float, bpm: float, grid_step: int) -> int:
    """把秒吸附到最近的整数网格。播放头/拖动预览用得上。"""
    return snap_to_grid(seconds_to_step(seconds, bpm, grid_step))
