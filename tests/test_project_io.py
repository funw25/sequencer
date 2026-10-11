"""工程文件读写测试：JSON round-trip、版本校验、错误提示。"""

import json
import os

import pytest

from sequencer.io.project_io import (
    FORMAT_VERSION,
    ProjectIOError,
    load_project,
    project_from_dict,
    project_to_dict,
    project_to_json,
    save_project,
)
from sequencer.model.project import Project


def make_project() -> Project:
    """带两个通道、重叠音符和自定义时间设置的工程。"""
    project = Project(
        bpm=137.5,
        time_signature_numerator=3,
        time_signature_denominator=8,
        grid_step=8,
    )
    project.add_channel("主音")
    project.add_channel("贝斯")
    project.create_note(0, 60, 0, 8)
    project.create_note(0, 60, 4, 8)  # 重叠
    project.create_note(0, 63, 12, 1)
    project.create_note(1, 0, 0, 4)  # 音高 0 也要能存下来
    return project


def test_round_trip_via_dict():
    project = make_project()
    restored = project_from_dict(project_to_dict(project))
    assert restored.to_dict() == project.to_dict()


def test_round_trip_via_json_text():
    project = make_project()
    restored = project_from_dict(json.loads(project_to_json(project)))
    assert restored.to_dict() == project.to_dict()


def test_round_trip_via_file(tmp_path):
    project = make_project()
    path = os.path.join(tmp_path, "工程.json")
    save_project(project, path)
    restored = load_project(path)
    assert restored.to_dict() == project.to_dict()
    # 字段逐个核对，避免 to_dict 同时错也互相抵消
    assert restored.bpm == pytest.approx(137.5)
    assert (restored.time_signature_numerator, restored.time_signature_denominator) == (3, 8)
    assert restored.grid_step == 8
    assert len(restored.channels) == 2
    assert len(restored.channels[0]) == 3
    assert restored.channels[0].notes_at(60, 4)[0].duration == 8


def test_saved_file_declares_format_version(tmp_path):
    path = os.path.join(tmp_path, "ver.json")
    save_project(make_project(), path)
    with open(path, encoding="utf-8") as fp:
        data = json.load(fp)
    assert data["format_version"] == FORMAT_VERSION == 1


def test_empty_project_round_trip(tmp_path):
    path = os.path.join(tmp_path, "empty.json")
    save_project(Project(), path)
    restored = load_project(path)
    assert restored.to_dict() == Project().to_dict()
    assert len(restored) == 0


def test_load_missing_file(tmp_path):
    with pytest.raises(ProjectIOError):
        load_project(os.path.join(tmp_path, "不存在.json"))


def test_load_broken_json(tmp_path):
    path = os.path.join(tmp_path, "broken.json")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("{ 这不是 JSON")
    with pytest.raises(ProjectIOError):
        load_project(path)


def test_load_rejects_missing_format_version():
    data = project_to_dict(make_project())
    del data["format_version"]
    with pytest.raises(ProjectIOError):
        project_from_dict(data)


def test_load_rejects_unsupported_format_version():
    data = project_to_dict(make_project())
    data["format_version"] = 2
    with pytest.raises(ProjectIOError):
        project_from_dict(data)


def test_load_rejects_non_object_top_level():
    with pytest.raises(ProjectIOError):
        project_from_dict([1, 2, 3])


def test_load_rejects_invalid_note(tmp_path):
    data = project_to_dict(make_project())
    data["channels"][0]["notes"][0]["duration"] = 0  # 非法持续时间
    path = os.path.join(tmp_path, "bad.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False)
    with pytest.raises(ProjectIOError):
        load_project(path)


def test_load_rejects_missing_required_field(tmp_path):
    data = project_to_dict(make_project())
    del data["channels"]
    path = os.path.join(tmp_path, "no_channels.json")
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False)
    with pytest.raises(ProjectIOError):
        load_project(path)


def test_chinese_channel_name_survives_file_round_trip(tmp_path):
    path = os.path.join(tmp_path, "cn.json")
    project = Project()
    project.add_channel("主音色")
    save_project(project, path)
    assert load_project(path).channels[0].name == "主音色"
