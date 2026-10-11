"""数据模型层测试：Note / Channel / Project。"""

import pytest

from sequencer.model import (
    CHANNEL_INDEX_BASE,
    Channel,
    Note,
    NoteError,
    Project,
    ProjectError,
)


# ---------- Note ----------


def test_note_fields_and_end():
    note = Note(pitch=60, start=4, duration=3)
    assert (note.pitch, note.start, note.duration) == (60, 4, 3)
    assert note.end == 7


@pytest.mark.parametrize("pitch", [0, 1, 63])
def test_note_accepts_pitch_bounds(pitch):
    assert Note(pitch=pitch, start=0, duration=1).pitch == pitch


@pytest.mark.parametrize("pitch", [-1, 64, 1000])
def test_note_rejects_pitch_out_of_range(pitch):
    with pytest.raises(NoteError):
        Note(pitch=pitch, start=0, duration=1)


@pytest.mark.parametrize("duration", [0, -1])
def test_note_rejects_non_positive_duration(duration):
    with pytest.raises(NoteError):
        Note(pitch=60, start=0, duration=duration)


def test_note_rejects_negative_start():
    with pytest.raises(NoteError):
        Note(pitch=60, start=-1, duration=1)


@pytest.mark.parametrize("bad", [1.0, "60", None, True])
def test_note_rejects_non_integer_fields(bad):
    with pytest.raises(NoteError):
        Note(pitch=bad, start=0, duration=1)
    with pytest.raises(NoteError):
        Note(pitch=60, start=bad, duration=1)
    with pytest.raises(NoteError):
        Note(pitch=60, start=0, duration=bad)


def test_note_round_trip_dict():
    note = Note(pitch=42, start=8, duration=2)
    assert Note.from_dict(note.to_dict()) == note


def test_note_from_dict_reports_missing_field():
    with pytest.raises(NoteError):
        Note.from_dict({"pitch": 60, "start": 0})


def test_note_copy_is_independent():
    note = Note(pitch=60, start=0, duration=1)
    other = note.copy()
    other.duration = 5
    assert note.duration == 1


# ---------- Channel ----------


def test_channel_defaults_and_index_base_constant():
    channel = Channel(index=0)
    assert isinstance(CHANNEL_INDEX_BASE, int)
    assert channel.name  # 有默认名字
    assert channel.color.startswith("#")
    assert len(channel) == 0


def test_channel_add_and_query_notes():
    channel = Channel(index=0)
    note = channel.create_note(pitch=60, start=0, duration=4)
    assert channel.notes == [note]
    assert channel.notes_at(60, 0) == [note]
    assert channel.notes_at(61, 0) == []


def test_channel_allows_overlapping_notes():
    channel = Channel(index=0)
    a = channel.create_note(60, 0, 8)
    b = channel.create_note(60, 2, 8)  # 与 a 重叠，且同音高
    c = channel.create_note(62, 1, 1)  # 与 a 重叠，不同音高
    assert len(channel) == 3
    assert {a.start, b.start, c.start} == {0, 2, 1}
    assert len(channel.notes_at(60, 0)) == 1


def test_channel_notes_property_returns_copy():
    channel = Channel(index=0)
    channel.create_note(60, 0, 1)
    channel.notes.append(Note(61, 0, 1))  # 改副本不应影响内部集合
    assert len(channel) == 1


def test_channel_remove_note_and_missing_note():
    channel = Channel(index=0)
    note = channel.create_note(60, 0, 1)
    channel.remove_note(note)
    assert len(channel) == 0
    with pytest.raises(NoteError):
        channel.remove_note(note)


def test_channel_remove_notes_at():
    channel = Channel(index=0)
    channel.create_note(60, 0, 1)
    channel.create_note(60, 0, 4)  # 同位置重叠
    channel.create_note(61, 0, 1)
    assert channel.remove_notes_at(60, 0) == 2
    assert len(channel) == 1


def test_channel_add_note_rejects_non_note():
    channel = Channel(index=0)
    with pytest.raises(NoteError):
        channel.add_note("不是音符")


def test_channel_round_trip_dict():
    channel = Channel(index=1, name="贝斯", color="#E8663D")
    channel.create_note(40, 0, 4)
    restored = Channel.from_dict(channel.to_dict())
    assert restored.to_dict() == channel.to_dict()


def test_channel_from_dict_reports_missing_notes():
    with pytest.raises(NoteError):
        Channel.from_dict({"index": 0})


# ---------- Project ----------


def test_project_defaults():
    project = Project()
    assert project.bpm == 120.0
    assert project.time_signature_numerator == 4
    assert project.time_signature_denominator == 4
    assert project.grid_step == 4
    assert len(project) == 0


@pytest.mark.parametrize("bpm", [0, -10, "120", None])
def test_project_rejects_bad_bpm(bpm):
    with pytest.raises(ProjectError):
        Project(bpm=bpm)


@pytest.mark.parametrize("grid_step", [0, -1, 1.5, None])
def test_project_rejects_bad_grid_step(grid_step):
    with pytest.raises(ProjectError):
        Project(grid_step=grid_step)


def test_project_add_remove_channel_and_renumber():
    project = Project()
    project.add_channel("主音")
    project.add_channel("贝斯")
    project.add_channel("鼓")
    assert [c.index for c in project.channels] == [0, 1, 2]
    removed = project.remove_channel(1)
    assert removed.name == "贝斯"
    assert [c.index for c in project.channels] == [0, 1]
    assert [c.name for c in project.channels] == ["主音", "鼓"]


def test_project_remove_channel_out_of_range():
    project = Project()
    project.add_channel()
    with pytest.raises(ProjectError):
        project.remove_channel(5)


def test_project_channel_at_out_of_range():
    project = Project()
    with pytest.raises(ProjectError):
        project.channel_at(0)


def test_project_length_in_steps():
    project = Project()
    assert project.length_in_steps() == 0
    project.add_channel()
    project.create_note(0, 60, 0, 4)
    project.create_note(0, 62, 8, 2)
    assert project.length_in_steps() == 10


def test_project_allows_cross_channel_overlap():
    project = Project()
    project.add_channel()
    project.add_channel()
    project.create_note(0, 60, 0, 4)
    project.create_note(1, 60, 0, 4)
    assert len(project.channel_at(0)) == 1
    assert len(project.channel_at(1)) == 1
