"""撤销历史测试：记录、撤销、重做、上限、清空，以及与工程快照的配合。"""

import pytest

from sequencer.core.history import History, HistoryError
from sequencer.model.project import Project


def test_new_history_has_nothing_to_undo_or_redo():
    history = History()
    assert history.can_undo is False
    assert history.can_redo is False
    assert history.undo_label is None
    assert history.undo({"n": 1}) is None
    assert history.redo({"n": 1}) is None


def test_record_then_undo_returns_previous_snapshot():
    history = History()
    history.record("编辑音符", {"n": 1})
    assert history.can_undo is True
    assert history.undo_label == "编辑音符"

    assert history.undo({"n": 2}) == ("编辑音符", {"n": 1})
    assert history.can_undo is False
    assert history.can_redo is True


def test_redo_returns_the_state_that_was_current():
    history = History()
    history.record("编辑音符", {"n": 1})
    history.undo({"n": 2})
    assert history.redo({"n": 1}) == ("编辑音符", {"n": 2})
    assert history.can_undo is True
    assert history.can_redo is False


def test_undo_and_redo_walk_multiple_steps_in_order():
    history = History()
    history.record("添加通道", {"n": 0})
    history.record("编辑音符", {"n": 1})
    assert history.undo({"n": 2}) == ("编辑音符", {"n": 1})
    assert history.undo({"n": 1}) == ("添加通道", {"n": 0})
    assert history.redo({"n": 0}) == ("添加通道", {"n": 1})
    assert history.redo({"n": 1}) == ("编辑音符", {"n": 2})


def test_new_record_drops_redo_chain():
    history = History()
    history.record("编辑音符", {"n": 1})
    history.undo({"n": 2})
    history.record("添加通道", {"n": 2})
    assert history.can_redo is False
    assert history.redo({"n": 3}) is None


def test_limit_drops_oldest_entries():
    history = History(limit=2)
    history.record("一", {"n": 0})
    history.record("二", {"n": 1})
    history.record("三", {"n": 2})
    assert history.undo({"n": 3}) == ("三", {"n": 2})
    assert history.undo({"n": 2}) == ("二", {"n": 1})
    assert history.can_undo is False


def test_clear_empties_both_stacks():
    history = History()
    history.record("编辑音符", {"n": 1})
    history.undo({"n": 2})
    history.clear()
    assert history.can_undo is False
    assert history.can_redo is False


def test_limit_must_be_positive_int():
    with pytest.raises(HistoryError):
        History(limit=0)
    with pytest.raises(HistoryError):
        History(limit=True)


def test_snapshot_rebuilds_the_project():
    """撤销恢复工程的方式：把快照交给 Project.from_dict。"""
    project = Project(bpm=120.0, grid_step=4)
    project.add_channel("主音")
    project.create_note(0, 60, 0, 4)

    history = History()
    history.record("编辑音符", project.to_dict())
    project.create_note(0, 62, 4, 4)

    label, snapshot = history.undo(project.to_dict())
    restored = Project.from_dict(snapshot)
    assert label == "编辑音符"
    assert len(restored.channels) == 1
    assert restored.channels[0].name == "主音"
    assert [note.pitch for note in restored.channels[0]] == [60]
