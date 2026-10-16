"""撤销与重做：基于工程快照的编辑历史。

做法：每次编辑前保存一份 `Project.to_dict()` 的结果，撤销时用它重建工程。
不做反向操作，也不记录命令对象，所有编辑路径共用同一套逻辑。

代价是每次编辑复制一份工程字典。当前工程规模（几十到几百个音符）下可以接受；
工程规模大到快照明显变慢时再换成命令模式。

只保存数据，不保存界面状态：撤销恢复通道与音符，不恢复选中的通道、
播放位置与日志内容。
"""

from __future__ import annotations

#: 默认保留的编辑步数，超出后丢弃最早的记录。
DEFAULT_LIMIT = 100


class HistoryError(ValueError):
    """历史参数非法。"""


class History:
    """撤销栈与重做栈。

    两个栈里放的都是 `(标签, 工程快照)`。标签说明这次编辑是什么操作，
    界面拿它写日志，例如「撤销：编辑音符」。
    """

    def __init__(self, limit: int = DEFAULT_LIMIT) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise HistoryError(f"历史步数上限必须是正整数，实际是 {limit!r}")
        self.limit = limit
        self._undo: list[tuple[str, dict]] = []
        self._redo: list[tuple[str, dict]] = []

    # ---------- 状态查询 ----------

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str | None:
        """下一次撤销的操作名；没有可撤销项时返回 None。"""
        return self._undo[-1][0] if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1][0] if self._redo else None

    # ---------- 记录 ----------

    def record(self, label: str, snapshot: dict) -> None:
        """记录一次编辑前的工程快照。

        发生新编辑后重做链失效，这是撤销栈的常规行为：重做只能重做
        「刚被撤销的那一步」。
        """
        self._undo.append((label, snapshot))
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()

    # ---------- 撤销与重做 ----------

    def undo(self, current: dict) -> tuple[str, dict] | None:
        """弹出一步撤销，返回 (标签, 应恢复的快照)；无可撤销项时返回 None。

        current 是当前工程的快照，会被压进重做栈。
        """
        if not self._undo:
            return None
        label, snapshot = self._undo.pop()
        self._redo.append((label, current))
        return label, snapshot

    def redo(self, current: dict) -> tuple[str, dict] | None:
        """弹回一步重做，返回 (标签, 应恢复的快照)；无可重做项时返回 None。"""
        if not self._redo:
            return None
        label, snapshot = self._redo.pop()
        self._undo.append((label, current))
        return label, snapshot

    def clear(self) -> None:
        """清空两个栈。新建或打开工程时用。"""
        self._undo.clear()
        self._redo.clear()


__all__ = ["DEFAULT_LIMIT", "History", "HistoryError"]
