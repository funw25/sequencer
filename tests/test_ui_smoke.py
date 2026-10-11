"""界面冒烟测试：无显示器环境下用 Qt 的 offscreen 平台跑。

覆盖：主窗口能建起来、通道能增删、钢琴卷帘能把音符变成图元、
播放能产出事件并写进日志、工程项目能存取。
不做截图对比，也不测具体像素。
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="没有安装 PySide6，跳过界面测试")

from PySide6.QtWidgets import QApplication  # noqa: E402

from sequencer.core.playback import EVENT_NOTE_ON  # noqa: E402
from sequencer.io.project_io import load_project, save_project  # noqa: E402
from sequencer.model.project import Project  # noqa: E402
from sequencer.ui.main_window import MainWindow  # noqa: E402
from sequencer.ui.piano_roll import (  # noqa: E402
    NOTE_HEIGHT_PX,
    RULER_HEIGHT_PX,
    STEP_WIDTH_PX,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp):
    win = MainWindow()
    yield win
    win.close()


def test_window_builds_with_one_default_channel(window):
    assert window.windowTitle().startswith("多通道音序器")
    assert len(window.project.channels) == 1
    assert window.channel_list.count() == 1
    assert window.piano_roll.channel is window.project.channels[0]


def test_add_and_remove_channel_updates_panel(window):
    window.add_channel()
    assert window.channel_list.count() == 2
    assert len(window.project.channels) == 2

    window.channel_list.setCurrentRow(1)
    window.remove_channel()
    assert len(window.project.channels) == 1
    assert window.channel_list.count() == 1


def test_creating_note_adds_scene_item(window):
    window.project.create_note(0, 60, 0, 4)
    window.piano_roll.rebuild()
    assert len(window.piano_roll._note_items) == 1
    note = window.project.channels[0].notes[0]
    rect = window.piano_roll._note_rect(note)
    assert rect.left() == 0.0
    assert rect.width() == pytest.approx(4 * STEP_WIDTH_PX - 1.0)


def test_coordinate_helpers_are_consistent(window):
    view = window.piano_roll
    for pitch in (0, 1, 30, 62, 63):
        assert view._y_to_pitch(view._pitch_to_y(pitch)) == pitch
    assert view._pitch_to_y(63) == pytest.approx(RULER_HEIGHT_PX)
    assert view._pitch_to_y(62) - view._pitch_to_y(63) == pytest.approx(NOTE_HEIGHT_PX)
    assert view._x_to_step(0.0) == 0
    assert view._x_to_step(STEP_WIDTH_PX * 3) == 3
    assert view._x_to_step(STEP_WIDTH_PX * 3.4) == 3
    assert view._x_to_step(STEP_WIDTH_PX * 3.6) == 4


def test_grid_step_change_rebuilds_scene(window):
    window.grid_input.setCurrentIndex(window.grid_input.findData(8))
    assert window.project.grid_step == 8
    assert len(window.piano_roll.scene().items()) > 0


def test_bpm_input_writes_through_to_project(window):
    window.bpm_input.setValue(140.0)
    assert window.project.bpm == pytest.approx(140.0)


def test_play_writes_events_into_log(window):
    window.project.create_note(0, 60, 0, 4)
    window.piano_roll.rebuild()
    window.play()
    text = window.log_view.toPlainText()
    assert "开始" in text
    assert "通道 0-60音" in text
    window.stop()
    assert window.piano_roll._playhead_step == 0


def test_pause_and_stop_do_not_crash(window):
    window.project.create_note(0, 60, 0, 4)
    window.play()
    window.pause()
    window.stop()
    assert window.play_button.isEnabled()
    assert not window.pause_button.isEnabled()


def test_play_to_empty_project_is_safe(qapp):
    win = MainWindow(Project())
    win.play()  # 没有通道时不崩
    assert win.engine.is_playing is False
    win.close()


def test_project_file_round_trip_through_window(window, tmp_path):
    window.project.create_note(0, 60, 0, 4)
    path = os.path.join(tmp_path, "win.json")
    save_project(window.project, path)

    loaded = load_project(path)
    window._load_project_object(loaded, path)
    assert window.project.channels[0].notes[0].pitch == 60
    assert window.windowTitle().endswith("win.json")
    assert window.channel_list.count() == len(window.project.channels)


def test_playhead_item_moves():
    win = MainWindow()
    win.piano_roll.set_playhead_step(7)
    assert win.piano_roll._playhead_step == 7
    win.piano_roll.reset_playhead()
    assert win.piano_roll._playhead_step == 0
    win.close()


def test_note_event_from_engine_is_note_on(window):
    window.project.create_note(0, 60, 0, 1)
    window.engine.play()
    events = window.engine.events
    assert events and events[0].kind == EVENT_NOTE_ON
