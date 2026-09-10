"""Fixed local fixtures for a small, independent maintenance-repair pilot.

The four tasks are intentionally synthetic and locally injected.  They are
boundary-oriented examples for exercising RinCode's repair-and-accept workflow;
they are not production incidents, benchmark claims, or evidence of model
quality by themselves.  ``source`` and ``reference`` are written as
``source.py`` by the runner.  ``hidden_test`` stays outside the candidate
workspace and is only used by the independent verifier.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fixture:
    """One source version, public check, reference fix, and hidden verifier."""

    task_id: str
    prompt: str
    source: str
    reference: str
    public_test: str
    hidden_test: str


def fixtures() -> tuple[Fixture, ...]:
    """Return the fixed four-task fixture batch in stable order."""

    return (
        Fixture(
            task_id="pagination-boundary",
            prompt=(
                "修复 source.py 中的分页边界缺陷。paginate 使用从 1 开始的 page，page 和 page_size "
                "都必须是正整数；超出末页返回空列表。请只修改 source.py，先运行 "
                "python test_public.py 再确认通过，不要修改测试文件。"
            ),
            source='''from __future__ import annotations


def paginate(items: list[str], page: int, page_size: int) -> list[str]:
    """Return one 1-based page from items."""
    if page < 0 or page_size <= 0:
        raise ValueError("page and page_size must be positive")
    start = (page - 1) * page_size
    return items[start : start + page_size]
''',
            reference='''from __future__ import annotations


def paginate(items: list[str], page: int, page_size: int) -> list[str]:
    """Return one 1-based page from items."""
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be positive")
    start = (page - 1) * page_size
    return items[start : start + page_size]
''',
            public_test='''from source import paginate


def test_first_page():
    assert paginate(["a", "b", "c"], 1, 2) == ["a", "b"]


def test_second_page():
    assert paginate(["a", "b", "c"], 2, 2) == ["c"]


if __name__ == "__main__":
    test_first_page()
    test_second_page()
''',
            hidden_test='''import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from source import paginate


def test_hidden_pagination_boundaries():
    assert paginate(["a", "b", "c", "d"], 1, 2) == ["a", "b"]
    assert paginate(["a", "b", "c", "d"], 3, 2) == []
    for bad in ((0, 2), (-1, 2), (1, 0), (1, -1)):
        try:
            paginate(["a"], *bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid pagination arguments were accepted: {bad}")
''',
        ),
        Fixture(
            task_id="stable-deduplication",
            prompt=(
                "修复 source.py 中的记录去重缺陷：只按非空 id 去重，保留每个 id 的第一条记录和原始顺序；"
                "id 缺失或为空的记录必须各自保留。请只修改 source.py，运行 python test_public.py，"
                "不要修改测试文件。"
            ),
            source='''from __future__ import annotations


def deduplicate(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep the first record for every id."""
    seen: set[object] = set()
    result: list[dict[str, object]] = []
    for record in records:
        identifier = record.get("id")
        if identifier in seen:
            continue
        seen.add(identifier)
        result.append(record)
    return result
''',
            reference='''from __future__ import annotations


def deduplicate(records: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep the first record for every non-empty id, preserving order."""
    seen: set[object] = set()
    result: list[dict[str, object]] = []
    for record in records:
        identifier = record.get("id")
        if identifier:
            if identifier in seen:
                continue
            seen.add(identifier)
        result.append(record)
    return result
''',
            public_test='''from source import deduplicate


def test_keeps_first_duplicate():
    first = {"id": "a", "value": 1}
    second = {"id": "a", "value": 2}
    other = {"id": "b", "value": 3}
    assert deduplicate([first, second, other]) == [first, other]


if __name__ == "__main__":
    test_keeps_first_duplicate()
''',
            hidden_test='''import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from source import deduplicate


records = [
    {"id": "a", "value": 1},
    {"id": "", "value": "empty-1"},
    {"value": "missing-1"},
    {"id": "a", "value": 2},
    {"value": "missing-2"},
    {"id": "b", "value": 3},
    {"id": "b", "value": 4},
]
expected = [records[0], records[1], records[2], records[4], records[5]]


def test_hidden_stable_deduplication():
    assert deduplicate(records) == expected
''',
        ),
        Fixture(
            task_id="safe-relative-path",
            prompt=(
                "修复 source.py 中的路径边界缺陷：resolve_under 只接受相对路径，解析后的路径必须仍位于 "
                "root 下；绝对路径和通过 .. 逃逸的路径都应抛出 ValueError。请只修改 source.py，"
                "运行 python test_public.py，不要修改测试文件。"
            ),
            source='''from __future__ import annotations

from pathlib import Path


def resolve_under(root: str | Path, requested: str) -> Path:
    """Resolve requested below root."""
    return (Path(root) / requested).resolve()
''',
            reference='''from __future__ import annotations

from pathlib import Path


def resolve_under(root: str | Path, requested: str) -> Path:
    """Resolve a relative requested path while keeping it below root."""
    root_path = Path(root).resolve()
    relative = Path(requested)
    if relative.is_absolute():
        raise ValueError("requested path must be relative")
    resolved = (root_path / relative).resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError as exc:
        raise ValueError("requested path escapes root") from exc
    return resolved
''',
            public_test='''from pathlib import Path

from source import resolve_under


def test_normal_path():
    root = Path("workspace")
    assert resolve_under(root, "reports/today.json") == (root / "reports/today.json").resolve()


if __name__ == "__main__":
    test_normal_path()
''',
            hidden_test='''import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from source import resolve_under


root = Path.cwd() / "sandbox-root"
root.mkdir(exist_ok=True)


def test_hidden_safe_path():
    assert resolve_under(root, "reports/today.json") == (root / "reports/today.json").resolve()
    for bad in ("../outside.txt", "../../outside.txt", str(Path.cwd() / "outside.txt")):
        try:
            resolve_under(root, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"path escape was accepted: {bad}")
''',
        ),
        Fixture(
            task_id="async-cleanup",
            prompt=(
                "修复 source.py 中的异步清理缺陷：close 必须取消并等待所有已登记任务结束，返回时 pending "
                "应为 0；重复调用 close 必须安全。请只修改 source.py，运行 python test_public.py，"
                "不要修改测试文件。"
            ),
            source='''from __future__ import annotations

import asyncio


class TaskGroup:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[object]] = set()

    def add(self, task: asyncio.Task[object]) -> None:
        self._tasks.add(task)

    @property
    def pending(self) -> int:
        return len(self._tasks)

    async def close(self) -> None:
        pending = tuple(self._tasks)
        for task in pending:
            task.cancel()
        self._tasks.clear()
''',
            reference='''from __future__ import annotations

import asyncio


class TaskGroup:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[object]] = set()

    def add(self, task: asyncio.Task[object]) -> None:
        self._tasks.add(task)

    @property
    def pending(self) -> int:
        return len(self._tasks)

    async def close(self) -> None:
        pending = tuple(self._tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._tasks.difference_update(pending)
''',
            public_test='''import asyncio

from source import TaskGroup


async def _worker():
    await asyncio.Event().wait()


async def _test_close_cancels_task():
    group = TaskGroup()
    task = asyncio.create_task(_worker())
    group.add(task)
    await asyncio.sleep(0)
    await group.close()
    assert task.cancelled()
    assert group.pending == 0


def test_close_cancels_task():
    asyncio.run(_test_close_cancels_task())


if __name__ == "__main__":
    test_close_cancels_task()
''',
            hidden_test='''import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from source import TaskGroup


async def _worker(stopped: asyncio.Event):
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await asyncio.sleep(0)
        stopped.set()
        raise


async def _main():
    group = TaskGroup()
    stopped = asyncio.Event()
    task = asyncio.create_task(_worker(stopped))
    group.add(task)
    await asyncio.sleep(0)
    await group.close()
    assert task.done()
    assert task.cancelled()
    assert stopped.is_set()
    assert group.pending == 0
    await group.close()
    assert group.pending == 0


def test_hidden_async_cleanup():
    asyncio.run(_main())
''',
        ),
    )


__all__ = ["Fixture", "fixtures"]
