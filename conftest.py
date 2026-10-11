"""pytest 配置。

本文件提供一个自管的 tmp_path 夹具，替代 pytest 内置的 tmpdir/tmp_path 插件。
原因：本项目的开发环境（Windows 沙箱）里，pytest 自己创建的 basetemp 目录
在创建之后连它自己都无法读取，会话结束时报 PermissionError: [WinError 5]。
自建目录没有这个问题，所以测试里照常写 tmp_path，不依赖内置插件。

用法：
    python -m pytest -p no:tmpdir

如果没有这个问题（普通环境），直接 `python -m pytest` 也可以，内置夹具更好用。
"""

from __future__ import annotations

import os
import shutil

import pytest

#: 自管临时目录的根，位于项目目录下，测试结束会清理。
_TMP_ROOT_NAME = ".pytest_tmp_self"


@pytest.fixture
def tmp_path(request):
    """每个测试一个独立临时目录，测试结束后递归删除。"""
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), _TMP_ROOT_NAME)
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in request.node.name)
    path = os.path.join(root, safe_name)
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
