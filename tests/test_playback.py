"""播放引擎测试：事件展平、排序、时间换算、play / pause / stop。"""

import pytest

from sequencer.core.playback import (
    EVENT_NOTE_OFF,
    EVENT_NOTE_ON,
    NoteEvent,
    PlaybackEngine,
    audible_channels,
    collect_events,
    project_duration_seconds,
)
from sequencer.model.project import Project

GRID = 4
BPM = 120.0  # 一拍 0.5s，一格 0.125s


class _RecordingOutput:
    """记录收到的事件的假输出适配器。"""

    def __init__(self):
        self.events = []

    def send(self, event):
        self.events.append(event)

    def describe(self):
        return "测试输出"


class _FakeClock:
    """可控的单调时钟，替换 time.monotonic 以得到确定的测试。"""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = _FakeClock()
    monkeypatch.setattr("sequencer.core.playback.time.monotonic", fake)
    return fake


def make_project() -> Project:
    """两个通道：ch0 音高 60 从第 0 格起 4 格；ch1 音高 62 从第 2 格起 2 格。"""
    project = Project(bpm=BPM, grid_step=GRID)
    project.add_channel("主音")
    project.add_channel("贝斯")
    project.create_note(0, 60, 0, 4)
    project.create_note(1, 62, 2, 2)
    return project


# ---------- collect_events ----------


def test_collect_events_pairs_on_and_off_with_time():
    events = collect_events(make_project())
    assert [(e.kind, e.channel_index, e.pitch) for e in events] == [
        (EVENT_NOTE_ON, 0, 60),
        (EVENT_NOTE_ON, 1, 62),
        (EVENT_NOTE_OFF, 0, 60),
        (EVENT_NOTE_OFF, 1, 62),
    ]
    assert [e.time_seconds for e in events] == pytest.approx([0.0, 0.25, 0.5, 0.5])


def test_collect_events_is_sorted_by_time():
    events = collect_events(make_project())
    times = [e.time_seconds for e in events]
    assert times == sorted(times)


def test_note_off_comes_before_note_on_at_same_time():
    """同一时刻先结束再开始，避免同音高被自己的 NOTE_ON 打断。"""
    project = Project(bpm=BPM, grid_step=GRID)
    project.add_channel()
    project.create_note(0, 60, 0, 4)  # 第 4 格结束
    project.create_note(0, 60, 4, 4)  # 同一时刻开始，同音高
    events = collect_events(project)
    assert [e.kind for e in events] == [
        EVENT_NOTE_ON,
        EVENT_NOTE_OFF,
        EVENT_NOTE_ON,
        EVENT_NOTE_OFF,
    ]
    assert events[1].time_seconds == events[2].time_seconds


def test_events_carry_channel_pitch_and_time():
    """播放事件必须能回答：哪个通道、哪个音高、何时开始、何时停止。"""
    events = collect_events(make_project())
    starts = {
        (e.channel_index, e.pitch): e.time_seconds for e in events if e.kind == EVENT_NOTE_ON
    }
    stops = {
        (e.channel_index, e.pitch): e.time_seconds for e in events if e.kind == EVENT_NOTE_OFF
    }
    assert starts[(0, 60)] == 0.0
    assert stops[(0, 60)] == pytest.approx(0.5)
    assert starts[(1, 62)] == pytest.approx(0.25)
    assert stops[(1, 62)] == pytest.approx(0.5)


def test_pitch_zero_produces_no_events():
    project = Project(bpm=BPM, grid_step=GRID)
    project.add_channel()
    project.create_note(0, 0, 0, 4)  # 音高 0 = 休止/无音
    assert collect_events(project) == []


def test_collect_events_from_mid_song_skips_missed_note_on():
    events = collect_events(make_project(), start_step=3)
    # ch0/60 起步于第 0 格、结束于第 4 格：它跨越了起播点，NOTE_ON 已经错过，
    # 只保留 NOTE_OFF；ch1/62 起步于第 2 格，同样已经错过 NOTE_ON。
    assert [(e.kind, e.channel_index, e.pitch) for e in events] == [
        (EVENT_NOTE_OFF, 0, 60),
        (EVENT_NOTE_OFF, 1, 62),
    ]
    assert [e.time_seconds for e in events] == pytest.approx([0.5, 0.5])


def test_collect_events_from_earlier_start_keeps_note_on():
    """起播点落在音符开始之前时，NOTE_ON 仍然要发出来。"""
    events = collect_events(make_project(), start_step=2)
    assert [(e.kind, e.channel_index, e.pitch) for e in events] == [
        (EVENT_NOTE_ON, 1, 62),
        (EVENT_NOTE_OFF, 0, 60),
        (EVENT_NOTE_OFF, 1, 62),
    ]
    assert [e.time_seconds for e in events] == pytest.approx([0.25, 0.5, 0.5])


def test_overlapping_same_pitch_notes_are_not_merged():
    """同一通道同音高的重叠音符在数据层保留，事件层也不合并。"""
    project = Project(bpm=BPM, grid_step=GRID)
    project.add_channel()
    project.create_note(0, 60, 0, 8)
    project.create_note(0, 60, 2, 8)
    events = collect_events(project)
    assert sum(1 for e in events if e.kind == EVENT_NOTE_ON) == 2
    assert sum(1 for e in events if e.kind == EVENT_NOTE_OFF) == 2


def test_project_duration_seconds():
    assert project_duration_seconds(make_project()) == pytest.approx(0.5)
    assert project_duration_seconds(Project()) == 0.0


def test_note_event_rejects_unknown_kind():
    with pytest.raises(ValueError):
        NoteEvent("NOTE_MAYBE", 0, 60, 0.0)


# ---------- PlaybackEngine ----------


def test_engine_play_emits_events_through_output(clock):
    project = make_project()
    sink = _RecordingOutput()
    engine = PlaybackEngine(project, output=sink)

    engine.play()
    assert [e.kind for e in sink.events] == [EVENT_NOTE_ON]

    clock.advance(0.25)
    engine.step()
    assert [e.time_seconds for e in sink.events] == pytest.approx([0.0, 0.25])

    clock.advance(0.5)
    engine.step()
    assert [e.kind for e in sink.events] == [
        EVENT_NOTE_ON,
        EVENT_NOTE_ON,
        EVENT_NOTE_OFF,
        EVENT_NOTE_OFF,
    ]
    assert engine.is_playing is False  # 事件放完自动停


def test_engine_on_event_callback_receives_same_events(clock):
    project = make_project()
    received = []
    engine = PlaybackEngine(project, on_event=received.append)
    engine.play()
    clock.advance(1.0)
    engine.step()
    assert len(received) == 4


def test_engine_pause_keeps_position_and_resumes(clock):
    project = make_project()
    sink = _RecordingOutput()
    engine = PlaybackEngine(project, output=sink)

    engine.play()
    clock.advance(0.3)
    engine.step()
    released = engine.pause()
    assert engine.is_playing is False
    # 暂停时正在发声的音符（第 2 格已开始的 ch1/62 也在响）必须补发 NOTE_OFF
    assert sorted((e.kind, e.pitch) for e in released) == [
        (EVENT_NOTE_OFF, 60),
        (EVENT_NOTE_OFF, 62),
    ]

    position = engine.elapsed_seconds
    clock.advance(5.0)  # 暂停期间时间流逝不应推进位置
    assert engine.elapsed_seconds == pytest.approx(position)

    engine.play()  # 不带参数 = 从暂停位置继续
    clock.advance(1.0)
    engine.step()
    # 恢复后音高 60 会重新发 NOTE_ON（它还有尾巴没放完）
    assert any(e.kind == EVENT_NOTE_ON and e.pitch == 60 for e in sink.events)


def test_engine_stop_resets_to_start(clock):
    project = make_project()
    sink = _RecordingOutput()
    engine = PlaybackEngine(project, output=sink)

    engine.play()
    clock.advance(0.3)
    engine.step()
    engine.stop()
    assert engine.is_playing is False
    assert engine.elapsed_seconds == 0.0
    assert engine.events == []

    sink.events.clear()
    clock.advance(10.0)
    assert engine.step() == []  # 停止后 step 不再产生事件
    assert sink.events == []


def test_engine_pause_and_stop_are_idempotent():
    engine = PlaybackEngine(make_project())
    assert engine.pause() == []
    assert engine.stop() == []


def test_engine_play_rejects_negative_start_step():
    engine = PlaybackEngine(make_project())
    with pytest.raises(ValueError):
        engine.play(start_step=-1)


def test_engine_step_without_play_returns_empty():
    engine = PlaybackEngine(make_project())
    assert engine.step() == []


def test_engine_empty_project_stops_immediately():
    engine = PlaybackEngine(Project(bpm=BPM, grid_step=GRID))
    engine.play()
    assert engine.is_playing is False
    assert engine.duration_seconds() == 0.0


def test_engine_elapsed_steps_tracks_playhead(clock):
    project = make_project()
    engine = PlaybackEngine(project)
    engine.play()
    assert engine.elapsed_steps == 0
    clock.advance(0.25)  # 两格
    assert engine.elapsed_steps == 2
    clock.advance(0.125)  # 再一格
    assert engine.elapsed_steps == 3


# ---------- 循环播放 ----------


def test_engine_loop_replays_from_start_after_the_end(clock):
    project = make_project()  # 总长 0.5s
    sink = _RecordingOutput()
    engine = PlaybackEngine(project, output=sink)
    engine.loop_enabled = True

    engine.play()
    clock.advance(0.6)  # 越过工程结尾
    engine.step()

    assert engine.is_playing is True
    # 绕回起点：位置归零，并重新发出第 0 格的事件
    assert engine.elapsed_steps == 0
    assert sink.events[-1].kind == EVENT_NOTE_ON
    assert sink.events[-1].time_seconds == pytest.approx(0.0)


def test_engine_loop_keeps_playing_across_wraps(clock):
    project = make_project()
    sink = _RecordingOutput()
    engine = PlaybackEngine(project, output=sink)
    engine.loop_enabled = True
    engine.play()

    for _ in range(3):
        clock.advance(0.6)
        engine.step()

    assert engine.is_playing is True
    # 开场一次，之后每绕回一次再发一次
    starts = [e for e in sink.events if e.kind == EVENT_NOTE_ON and e.pitch == 60]
    assert len(starts) == 4


def test_engine_loop_off_stops_at_the_end(clock):
    project = make_project()
    engine = PlaybackEngine(project)
    engine.loop_enabled = True
    engine.play()
    clock.advance(0.1)
    engine.step()

    engine.loop_enabled = False  # 播放中关掉循环：本轮放完即停
    clock.advance(0.5)
    engine.step()
    assert engine.is_playing is False


def test_engine_loop_stops_when_there_is_nothing_to_play(clock):
    """全是不发声的音高 0：没有事件可放，绕回会空转，必须停下来。"""
    project = Project(bpm=BPM, grid_step=GRID)
    project.add_channel()
    project.create_note(0, 0, 0, 4)

    engine = PlaybackEngine(project)
    engine.loop_enabled = True
    engine.play()
    assert engine.is_playing is False


def test_engine_loop_pause_resumes_from_loop_start(clock):
    """绕回之后再暂停，恢复播放应落在循环起点那一段。"""
    project = make_project()
    engine = PlaybackEngine(project)
    engine.loop_enabled = True
    engine.play()
    clock.advance(0.6)
    engine.step()  # 这一步绕回起点

    engine.pause()
    clock.advance(0.05)
    engine.play()
    clock.advance(0.1)
    engine.step()
    assert engine.elapsed_seconds == pytest.approx(0.1)


# ---------- 静音与独奏 ----------


def test_muted_channel_produces_no_events():
    project = make_project()
    project.channels[0].muted = True
    events = collect_events(project)
    assert events
    assert all(event.channel_index == 1 for event in events)


def test_solo_limits_playback_to_soloed_channels():
    project = make_project()
    project.channels[1].soloed = True
    events = collect_events(project)
    assert events
    assert all(event.channel_index == 1 for event in events)


def test_mute_wins_over_solo_on_the_same_channel():
    project = make_project()
    project.channels[1].soloed = True
    project.channels[1].muted = True
    assert collect_events(project) == []


def test_all_channels_muted_gives_no_events():
    project = make_project()
    for channel in project.channels:
        channel.muted = True
    assert collect_events(project) == []


def test_audible_channels_reports_candidates():
    project = make_project()
    assert audible_channels(project) == list(project.channels)
    project.channels[0].muted = True
    assert audible_channels(project) == [project.channels[1]]

    project.channels[0].muted = False
    project.channels[0].soloed = True
    assert audible_channels(project) == [project.channels[0]]


# ---------- 定位（时间轴拖动） ----------


def test_engine_seek_moves_position_without_playing():
    engine = PlaybackEngine(make_project())
    engine.seek(2)
    assert engine.is_playing is False
    assert engine.elapsed_steps == 2


def test_engine_seek_while_playing_continues_from_there(clock):
    project = make_project()
    sink = _RecordingOutput()
    engine = PlaybackEngine(project, output=sink)
    engine.play()
    clock.advance(0.1)
    engine.step()

    released = engine.seek(0)  # 回到起点，先补发悬挂音的 NOTE_OFF
    assert engine.is_playing is True
    assert engine.elapsed_steps == 0
    assert any(event.kind == EVENT_NOTE_OFF for event in released)
    assert any(event.kind == EVENT_NOTE_ON for event in released)


def test_engine_seek_then_play_continues_from_new_position(clock):
    engine = PlaybackEngine(make_project())
    engine.play()
    clock.advance(0.1)
    engine.step()
    engine.pause()

    engine.seek(4)
    engine.play()  # 不带参数 = 从当前位置继续
    assert engine.start_step == 4


def test_engine_seek_rejects_negative_step():
    engine = PlaybackEngine(make_project())
    with pytest.raises(ValueError):
        engine.seek(-1)
