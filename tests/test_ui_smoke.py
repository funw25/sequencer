"""界面冒烟测试：无显示器环境下用 Qt 的 offscreen 平台跑。

覆盖：主窗口能建起来、通道能增删、钢琴卷帘能把音符变成图元、
播放能产出事件并写进日志、工程项目能存取。
不做截图对比，也不测具体像素。
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="没有安装 PySide6，跳过界面测试")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QGraphicsSimpleTextItem,
    QMenu,
    QMessageBox,
    QToolButton,
)

from sequencer.core.playback import EVENT_NOTE_ON  # noqa: E402
from sequencer.io.project_io import load_project, save_project  # noqa: E402
from sequencer.model.project import Project  # noqa: E402
from sequencer.ui.main_window import MainWindow, format_time  # noqa: E402
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


def test_play_pause_is_one_toggle(window):
    """播放与暂停共用一个按键：图标与文字随状态切换。"""
    assert window.play_pause_action.text() == "播放"
    window.project.create_note(0, 60, 0, 4)

    window.play()
    assert window.engine.is_playing is True
    assert window.play_pause_action.text() == "暂停"

    window.toggle_play()  # 再点一次 = 暂停
    assert window.engine.is_playing is False
    assert window.play_pause_action.text() == "播放"

    window.toggle_play()  # 又点一次 = 继续播
    assert window.engine.is_playing is True
    window.stop()
    assert window.play_pause_action.text() == "播放"


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


# ---------- 循环播放 ----------


def test_loop_action_toggles_engine(window):
    assert window.engine.loop_enabled is False
    window.loop_action.setChecked(True)  # 工具栏与菜单共用同一个动作
    assert window.engine.loop_enabled is True

    window.loop_action.trigger()  # 再点一次 = 取消勾选
    assert window.engine.loop_enabled is False
    assert window.loop_action.isChecked() is False


def test_play_with_loop_keeps_loop_flag(window):
    """播放不改变循环开关；循环本身的绕回行为在 test_playback.py 里测。"""
    window.project.create_note(0, 60, 0, 4)
    window.set_loop_enabled(True)
    window.play()
    assert window.engine.loop_enabled is True
    assert window.loop_action.isChecked() is True
    window.stop()


# ---------- 撤销与重做 ----------


def test_undo_actions_start_disabled(window):
    assert window.undo_action.isEnabled() is False
    assert window.redo_action.isEnabled() is False


def test_undo_restores_removed_channel(window):
    window.add_channel()
    assert len(window.project.channels) == 2

    window.channel_list.setCurrentRow(1)
    window.remove_channel()
    assert len(window.project.channels) == 1

    window.undo()
    assert len(window.project.channels) == 2
    assert window.undo_action.isEnabled() is True  # 「添加通道」还能再撤

    window.redo()
    assert len(window.project.channels) == 1


def test_undo_restores_note_edit(window):
    """钢琴卷帘的一整次鼠标编辑只记一步。"""
    window.piano_roll.editStarted.emit()
    window.project.create_note(0, 60, 0, 4)
    window.piano_roll.editFinished.emit()
    assert len(window.project.channels[0]) == 1

    window.undo()
    assert len(window.project.channels[0]) == 0
    assert window.undo_action.isEnabled() is False
    assert window.redo_action.isEnabled() is True

    window.redo()
    assert len(window.project.channels[0]) == 1
    assert window.undo_action.isEnabled() is True
    assert window.redo_action.isEnabled() is False


def test_edit_without_change_adds_no_history(window):
    window.piano_roll.editStarted.emit()
    window.piano_roll.editFinished.emit()
    assert window.history.can_undo is False
    assert window.undo_action.isEnabled() is False


def test_loading_project_clears_history(window, tmp_path):
    window.add_channel()
    assert window.history.can_undo is True

    path = os.path.join(tmp_path, "history.json")
    save_project(window.project, path)
    window._load_project_object(load_project(path), path)

    assert window.history.can_undo is False
    assert window.undo_action.isEnabled() is False


def test_mouse_drag_creates_note_and_one_undo_step(qapp):
    """真的发鼠标事件：空白处拖出一个音符，撤销应把它整条去掉。"""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win = MainWindow()
    win.resize(1000, 700)
    win.show()
    view = win.piano_roll
    pitch = 31  # 与初始视角中心同高，保证落在视口内
    y = view._pitch_to_y(pitch)

    start = view.mapFromScene(STEP_WIDTH_PX * 4, y)
    end = view.mapFromScene(STEP_WIDTH_PX * 6, y)
    QTest.mousePress(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    QTest.mouseMove(view.viewport(), end)
    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end
    )

    assert len(win.project.channels[0]) == 1
    note = win.project.channels[0].notes[0]
    assert (note.pitch, note.start, note.duration) == (pitch, 4, 2)
    assert win.history.can_undo is True

    win.undo()
    assert len(win.project.channels[0]) == 0
    win.close()


# ---------- 多通道显示 ----------


def test_switching_channel_keeps_notes(window):
    """切通道不丢音符：音符存在各自的通道里，切回来还在。"""
    window.project.create_note(0, 60, 0, 4)
    window.add_channel()
    window.channel_list.setCurrentRow(1)
    assert len(window.piano_roll._note_items) == 0  # 新通道本身是空的

    window.channel_list.setCurrentRow(0)
    assert len(window.project.channels[0]) == 1
    assert len(window.piano_roll._note_items) == 1


def test_other_channels_are_drawn_as_ghosts(window):
    """非当前通道的音符画成幽灵音符，只作对照，点不到。"""
    window.project.create_note(0, 60, 0, 4)
    other = window.project.add_channel("贝斯")
    other.create_note(40, 0, 8)
    window.piano_roll.channel_index = 0
    window.piano_roll.rebuild()

    assert len(window.piano_roll._note_items) == 1
    assert len(window.piano_roll._ghost_items) == 1
    ghost_rect = window.piano_roll._note_rect(other.notes[0])
    assert window.piano_roll._note_at(ghost_rect.center()) is None


def test_ruler_labels_are_seconds_and_bar_beat(window):
    """时间轴上排是秒数，下排是「小节.拍」。"""
    view = window.piano_roll
    assert view.ruler_text(0) == ("0.0s", "1.1")
    assert view.ruler_text(4) == ("0.5s", "1.2")  # 120 BPM、1/4 拍一格
    assert view.ruler_text(16)[1] == "2.1"  # 4/4 拍：16 格一小节


# ---------- 通道右键菜单的动作 ----------


def test_duplicate_channel_copies_notes(window):
    window.project.create_note(0, 60, 0, 4)
    window.duplicate_channel(0)
    assert len(window.project.channels) == 2
    assert [note.pitch for note in window.project.channels[1]] == [60]

    window.undo()
    assert len(window.project.channels) == 1


def test_move_channel_reorders_and_renumbers(window):
    window.add_channel()
    window.project.channel_at(0).name = "甲"
    window.project.channel_at(1).name = "乙"

    window.move_channel(0, 1)
    assert [channel.name for channel in window.project.channels] == ["乙", "甲"]
    assert [channel.index for channel in window.project.channels] == [0, 1]

    window.move_channel(0, -1)  # 已在顶部，越界不动
    assert [channel.name for channel in window.project.channels] == ["乙", "甲"]


def test_clear_channel_notes_asks_first(window, monkeypatch):
    window.project.create_note(0, 60, 0, 4)
    monkeypatch.setattr(
        "sequencer.ui.main_window.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    window.clear_channel_notes(0)
    assert len(window.project.channels[0]) == 0

    window.undo()
    assert len(window.project.channels[0]) == 1


def test_clear_channel_notes_can_be_cancelled(window, monkeypatch):
    window.project.create_note(0, 60, 0, 4)
    monkeypatch.setattr(
        "sequencer.ui.main_window.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    window.clear_channel_notes(0)
    assert len(window.project.channels[0]) == 1


# ---------- 拖动反馈 ----------


def test_drag_shows_readout_and_hides_it_on_release(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    win = MainWindow()
    win.resize(1000, 700)
    win.show()
    view = win.piano_roll
    y = view._pitch_to_y(31)
    start = view.mapFromScene(STEP_WIDTH_PX * 4, y)
    end = view.mapFromScene(STEP_WIDTH_PX * 6, y)

    QTest.mousePress(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start
    )
    QTest.mouseMove(view.viewport(), end)
    assert view._readout.isVisible()
    assert "第 4 格" in view._readout.text()
    assert "长 2 格" in view._readout.text()
    assert (view._drag_pitch, view._drag_end_step) == (31, 6)  # 辅助线跟着音符

    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end
    )
    assert not view._readout.isVisible()
    assert view._mode == "none"
    win.close()


# ---------- 工具栏、时间显示与光标读数 ----------


def test_format_time():
    assert format_time(0.0) == "00:00:000"
    assert format_time(1.25) == "00:01:250"
    assert format_time(61.5) == "01:01:500"
    assert format_time(-1.0) == "00:00:000"


def test_toolbar_carries_the_transport_actions(window):
    assert window.play_pause_action in window.toolbar.actions()
    assert window.loop_action in window.toolbar.actions()
    assert window.time_label.text() == "00:00:000"


def test_file_actions_live_in_the_menu_not_the_toolbar(window):
    toolbar_texts = [action.text() for action in window.toolbar.actions()]
    for text in ("新建", "打开...", "保存"):
        assert text not in toolbar_texts

    menu_texts = [
        action.text()
        for menu in window.menuBar().findChildren(QMenu)
        for action in menu.actions()
    ]
    for text in ("新建", "打开...", "保存"):
        assert text in menu_texts


def test_help_menu_holds_the_usage_notes(window):
    """界面里不放说明文字，说明收在「帮助 → 使用说明」。"""
    menu_texts = [
        action.text()
        for menu in window.menuBar().findChildren(QMenu)
        for action in menu.actions()
    ]
    assert "使用说明" in menu_texts


def test_log_view_colours_come_from_the_palette(window):
    """事件日志配色跟随主题：深色模式深底白字，浅色模式白底黑字。"""
    from PySide6.QtGui import QPalette

    style = window.log_view.styleSheet()
    palette = window.palette()
    assert palette.color(QPalette.ColorRole.Text).name() in style
    assert palette.color(QPalette.ColorRole.Base).name() in style
    assert "Consolas" in style


def test_toolbar_clock_colour_comes_from_the_palette(window):
    """播放时间也要写明颜色，否则某些主题下会与底色同色。"""
    from PySide6.QtGui import QPalette

    style = window.time_label.styleSheet()
    assert window.palette().color(QPalette.ColorRole.WindowText).name() in style


def test_loop_icon_uses_the_system_refresh_icon(window):
    """循环用系统自带刷新图标，颜色保持原样；选中底色是中性灰，不与其撞色。"""
    from PySide6.QtWidgets import QStyle

    expected = window.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
    actual = window.loop_action.icon().pixmap(16, 16).toImage()
    reference = expected.pixmap(16, 16).toImage()
    assert actual == reference
    assert "QToolButton:checked" in window.toolbar.styleSheet()


def test_time_label_follows_the_engine(window):
    window.project.create_note(0, 60, 0, 4)
    window.play()
    window._timer.stop()  # 冻住定时器，避免读表竞态
    window._update_time_label()
    assert window.time_label.text() == format_time(window.engine.elapsed_seconds)

    window.stop()
    assert window.time_label.text() == "00:00:000"


def test_cursor_readout_shows_pitch_bar_and_seconds(window):
    window.piano_roll.cursorMoved.emit(60, 16)
    text = window.position_label.text()
    assert "C5" in text
    assert "小节 2.1" in text
    assert "第 16 格" in text

    window.piano_roll.cursorLeft.emit()
    assert window.position_label.text() == ""


def test_playhead_moved_signal_seeks_the_engine(window):
    window.project.create_note(0, 60, 0, 8)
    window.piano_roll.playheadMoved.emit(6)
    assert window.engine.elapsed_steps == 6
    assert window.piano_roll._playhead_step == 6
    assert window.time_label.text() == format_time(window.engine.elapsed_seconds)


def test_dragging_on_the_ruler_moves_the_playhead(qapp):
    """时间轴固定在视口顶部：在上面按下并拖动 = 定位播放头。"""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    win = MainWindow()
    win.resize(1000, 700)
    win.show()
    win.project.create_note(0, 60, 0, 8)
    win.refresh_channels()
    win.piano_roll.rebuild()

    view = win.piano_roll
    press = QPoint(int(STEP_WIDTH_PX * 4), int(RULER_HEIGHT_PX / 2))
    target = QPoint(int(STEP_WIDTH_PX * 6), int(RULER_HEIGHT_PX / 2))
    QTest.mousePress(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press
    )
    QTest.mouseMove(view.viewport(), target)
    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target
    )

    assert view._playhead_step == 6
    assert win.engine.elapsed_steps == 6
    assert win.project.channels[0].notes[0].start == 0  # 没有误改音符
    win.close()


def test_grabbing_the_playhead_line_in_the_grid_moves_it(qapp):
    """网格区里贴着播放头那根线按下，就是拖播放头，不该画出音符。"""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    win = MainWindow()
    win.resize(1000, 700)
    win.show()
    win.project.create_note(0, 60, 0, 8)
    win.refresh_channels()
    view = win.piano_roll
    view.set_playhead_step(6)

    y = int(RULER_HEIGHT_PX + 40)
    press = QPoint(int(STEP_WIDTH_PX * 6), y)
    target = QPoint(int(STEP_WIDTH_PX * 10), y)
    QTest.mousePress(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press
    )
    QTest.mouseMove(view.viewport(), target)
    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target
    )

    assert len(win.project.channels[0]) == 1  # 没有新增音符
    assert view._playhead_step == 10
    win.close()


def test_scrubbing_shows_a_time_readout(qapp):
    """拖播放头时旁边要有位置读数。"""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    win = MainWindow()
    win.resize(1000, 700)
    win.show()
    win.project.create_note(0, 60, 0, 8)
    win.refresh_channels()
    view = win.piano_roll

    press = QPoint(int(STEP_WIDTH_PX * 4), int(RULER_HEIGHT_PX / 2))
    QTest.mousePress(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press
    )
    assert view._readout.isVisible()
    assert "小节 1.2" in view._readout.text()
    assert "0.500s" in view._readout.text()

    QTest.mouseRelease(
        view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, press
    )
    assert not view._readout.isVisible()
    win.close()


def test_control_row_widgets_are_wide_enough(qapp):
    """控件宽度不能小于最小需要，否则字会被挤掉（拍号分子曾因此看不见）。"""
    win = MainWindow()
    win.resize(1400, 820)
    win.show()
    qapp.processEvents()
    for widget in (
        win.bpm_input,
        win.numerator_input,
        win.denominator_input,
        win.grid_input,
        win.zoom_in_button,
        win.zoom_out_button,
    ):
        assert widget.width() >= widget.minimumSizeHint().width()
    win.close()


# ---------- 通道静音与独奏 ----------


def test_channel_row_has_mute_and_solo_buttons(window):
    row = window.channel_list.itemWidget(window.channel_list.item(0))
    assert row is not None
    assert {button.text() for button in row.findChildren(QToolButton)} == {"M", "S"}


def test_mute_and_solo_go_through_history(window):
    window.set_channel_muted(0, True)
    assert window.project.channels[0].muted is True

    window.set_channel_soloed(0, True)
    assert window.project.channels[0].soloed is True

    window.undo()
    assert window.project.channels[0].soloed is False
    window.undo()
    assert window.project.channels[0].muted is False


def test_mute_button_state_survives_refresh(window):
    window.set_channel_muted(0, True)
    window.refresh_channels()
    row = window.channel_list.itemWidget(window.channel_list.item(0))
    checked = [button.text() for button in row.findChildren(QToolButton) if button.isChecked()]
    assert checked == ["M"]


def test_clicking_a_channel_row_still_selects_it(qapp):
    """行里放了按钮之后，点行的空白处仍然要能选中该通道。"""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    win = MainWindow()
    win.resize(600, 400)
    win.show()
    win.add_channel()
    win.channel_list.setCurrentRow(0)
    assert win.channel_list.currentRow() == 0

    row = win.channel_list.itemWidget(win.channel_list.item(1))
    QTest.mouseClick(
        row, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(10, 5)
    )
    assert win.channel_list.currentRow() == 1
    win.close()
