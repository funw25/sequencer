"""音高编号与音名的对应。

规则来自《音高表.xlsx》：1 号音是 C2（65Hz），63 号是 D7（2349Hz）；
卷帘与事件日志只标 Cn，其余音名由位置推出。
"""

import pytest

from sequencer.output.debug_output import pitch_name
from sequencer.ui.piano_roll import _degree, _octave


@pytest.mark.parametrize(
    ("pitch", "expected"),
    [
        (1, "C2"),
        (13, "C3"),
        (25, "C4"),
        (37, "C5"),
        (49, "C6"),
        (61, "C7"),
        (10, "A2"),
        (63, "D7"),
        (0, "休止"),
    ],
)
def test_pitch_name_follows_the_table(pitch, expected):
    assert pitch_name(pitch) == expected


def test_piano_roll_marks_only_c_rows():
    c_rows = [pitch for pitch in range(1, 64) if _degree(pitch) == 0]
    assert c_rows == [1, 13, 25, 37, 49, 61]
    assert [_octave(pitch) for pitch in c_rows] == [2, 3, 4, 5, 6, 7]
