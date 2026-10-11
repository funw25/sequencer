"""时间换算测试：网格 <-> 节拍 <-> 秒，以及网格吸附。"""

import pytest

from sequencer.core.timing import (
    beats_to_steps,
    seconds_per_step,
    seconds_to_step,
    snap_seconds_to_step,
    snap_to_grid,
    step_to_seconds,
    steps_to_beats,
)


def test_seconds_per_step_at_120bpm_quarter_grid():
    # 120 BPM -> 一拍 0.5s；grid_step=4 -> 一格 0.125s
    assert seconds_per_step(120.0, 4) == pytest.approx(0.125)


def test_seconds_per_step_scales_with_grid():
    assert seconds_per_step(120.0, 1) == pytest.approx(0.5)
    assert seconds_per_step(120.0, 2) == pytest.approx(0.25)
    assert seconds_per_step(120.0, 8) == pytest.approx(0.0625)
    assert seconds_per_step(120.0, 16) == pytest.approx(0.03125)


def test_seconds_per_step_scales_with_bpm():
    assert seconds_per_step(60.0, 4) == pytest.approx(0.25)
    assert seconds_per_step(240.0, 4) == pytest.approx(0.0625)


@pytest.mark.parametrize("bpm,grid_step", [(0, 4), (-1, 4), (120, 0), (120, -4)])
def test_seconds_per_step_rejects_bad_input(bpm, grid_step):
    with pytest.raises(ValueError):
        seconds_per_step(bpm, grid_step)


def test_step_to_seconds_and_back():
    assert step_to_seconds(8, 120.0, 4) == pytest.approx(1.0)
    assert seconds_to_step(1.0, 120.0, 4) == pytest.approx(8.0)
    assert seconds_to_step(step_to_seconds(37, 137.5, 8), 137.5, 8) == pytest.approx(37.0)


def test_step_to_seconds_zero_is_zero():
    assert step_to_seconds(0, 120.0, 4) == 0.0


def test_time_helpers_reject_negative():
    with pytest.raises(ValueError):
        step_to_seconds(-1, 120.0, 4)
    with pytest.raises(ValueError):
        seconds_to_step(-0.5, 120.0, 4)


def test_steps_to_beats_and_back():
    assert steps_to_beats(4, 4) == pytest.approx(1.0)
    assert steps_to_beats(8, 16) == pytest.approx(0.5)
    assert beats_to_steps(1.0, 4) == pytest.approx(4.0)
    assert beats_to_steps(0.25, 8) == pytest.approx(2.0)


def test_one_bar_at_four_four_is_four_beats():
    # 4/4 一小节 4 拍，grid_step=4 -> 16 格
    assert beats_to_steps(4, 4) == 16
    assert step_to_seconds(16, 120.0, 4) == pytest.approx(2.0)


@pytest.mark.parametrize(
    "value,expected",
    [(0.0, 0), (0.4, 0), (0.5, 1), (0.6, 1), (1.49, 1), (1.5, 2), (7.5, 8), (-3.0, 0)],
)
def test_snap_to_grid(value, expected):
    assert snap_to_grid(value) == expected


def test_snap_seconds_to_step_rounds_to_nearest_grid():
    # 120 BPM、grid_step=4：一格 0.125s
    assert snap_seconds_to_step(0.06, 120.0, 4) == 0
    assert snap_seconds_to_step(0.07, 120.0, 4) == 1
    assert snap_seconds_to_step(0.5, 120.0, 4) == 4


def test_grid_step_change_changes_step_seconds_not_beat_length():
    """同一拍在不同网格密度下都是 0.5s，只是被切成的格子数不同。"""
    assert step_to_seconds(4, 120.0, 4) == pytest.approx(0.5)
    assert step_to_seconds(8, 120.0, 8) == pytest.approx(0.5)
    assert step_to_seconds(16, 120.0, 16) == pytest.approx(0.5)
