from pathlib import Path

import pytest

from rincode.agent.tools import ToolRegistry
from rincode.agent.tools.file_search import GrepTool
from rincode.agent.tools.tool_search import ToolCallTool, ToolSearchController


@pytest.mark.parametrize("nested", [False, True])
async def test_grep_context_lines_work_through_registry(tmp_path: Path, nested: bool):
    (tmp_path / "sample.py").write_text("before\nneedle\nafter\n")
    registry = ToolRegistry()
    registry.register(GrepTool(workspace=tmp_path, allowed_dir=tmp_path))
    args = {"pattern": "needle", "path": "sample.py", "context": 1}
    if nested:
        registry.register(ToolCallTool(ToolSearchController(registry, always_visible={"grep"})))
        result = await registry.execute("tool_call", {"name": "grep", "arguments": args})
    else:
        result = await registry.execute("grep", args)
    assert not result.failed, result
    assert all(word in str(result) for word in ["before", "needle", "after"])
