"""工程文件的 JSON 保存与读取。

文件格式（format_version 1）：

    {
      "format_version": 1,
      "bpm": 120.0,
      "time_signature_numerator": 4,
      "time_signature_denominator": 4,
      "grid_step": 4,
      "channels": [
        {"index": 0, "name": "通道 0", "color": "#4C8BF5",
         "notes": [{"pitch": 60, "start": 0, "duration": 4}]}
      ]
    }

暂不实现旧版本兼容：读到非 1 的 format_version 直接报错，不做自动迁移。
"""

from __future__ import annotations

import json
import os

from ..model.project import Project

FORMAT_VERSION = 1


class ProjectIOError(Exception):
    """工程文件读写失败。"""


def project_to_dict(project: Project) -> dict:
    """工程 -> 字典（含 format_version）。"""
    data = {"format_version": FORMAT_VERSION}
    data.update(project.to_dict())
    return data


def project_from_dict(data: dict) -> Project:
    """字典 -> 工程。"""
    if not isinstance(data, dict):
        raise ProjectIOError(f"工程文件顶层必须是 JSON 对象，实际是 {type(data).__name__}")
    if "format_version" not in data:
        raise ProjectIOError("工程文件缺少 format_version 字段")
    version = data["format_version"]
    if version != FORMAT_VERSION:
        raise ProjectIOError(
            f"不支持的工程文件版本：{version}，本程序只支持 {FORMAT_VERSION}"
            "（暂不做旧版本兼容）"
        )
    return Project.from_dict(data)


def project_to_json(project: Project, indent: int = 2) -> str:
    """工程 -> JSON 文本。中文不转义，方便直接看文件内容。"""
    return json.dumps(project_to_dict(project), ensure_ascii=False, indent=indent)


def save_project(project: Project, path: str) -> None:
    """保存工程到 path（写 UTF-8 JSON）。失败抛 ProjectIOError。"""
    text = project_to_json(project)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fp:
            fp.write(text)
            fp.write("\n")
    except OSError as exc:
        raise ProjectIOError(f"保存工程失败：{path}（{exc}）") from exc


def load_project(path: str) -> Project:
    """从 path 读取工程。文件不存在 / JSON 非法 / 版本不符都会抛 ProjectIOError。"""
    if not os.path.isfile(path):
        raise ProjectIOError(f"工程文件不存在：{path}")
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except OSError as exc:
        raise ProjectIOError(f"读取工程失败：{path}（{exc}）") from exc
    except json.JSONDecodeError as exc:
        raise ProjectIOError(f"工程文件不是合法 JSON：{path}（{exc}）") from exc
    try:
        return project_from_dict(data)
    except ProjectIOError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise ProjectIOError(f"工程文件内容非法：{path}（{exc}）") from exc
