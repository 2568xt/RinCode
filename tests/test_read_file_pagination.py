"""Regression coverage for bounded, actionable read_file pagination."""

import pytest

from rincode.agent.tools.filesystem import ReadFileTool


@pytest.mark.parametrize("trailing_line", [False, True])
async def test_oversized_line_reports_limit_instead_of_empty_page(tmp_path, trailing_line):
    path = tmp_path / "generated.py"
    path.write_text("x" * 128_000 + ("\nvalue = 1\n" if trailing_line else ""))
    tool = ReadFileTool(workspace=tmp_path)

    result = await tool.execute(path=path.name)

    assert result.startswith("Error:")
    assert "line 1" in result
    assert "128000" in result
    assert "exec" in result
    assert "Showing lines" not in result
    assert "offset=1" not in result
    if trailing_line:
        assert "offset=2" in result
        assert "2| value = 1" in await tool.execute(path=path.name, offset=2)
    else:
        assert "offset=" not in result


async def test_page_before_oversized_line_can_continue(tmp_path):
    (tmp_path / "source.py").write_text("first\n" + "x" * 128_000 + "\nlast\n")
    tool = ReadFileTool(workspace=tmp_path)

    first = await tool.execute(path="source.py")
    blocked = await tool.execute(path="source.py", offset=2)
    last = await tool.execute(path="source.py", offset=3)

    assert "1| first" in first
    assert "Use offset=2" in first
    assert blocked.startswith("Error:")
    assert "line 2" in blocked
    assert "offset=3" in blocked
    assert "3| last" in last
    assert "End of file" in last


async def test_numbered_line_exactly_at_character_limit_is_readable(tmp_path):
    content = "x" * (ReadFileTool._MAX_CHARS - len("1| "))
    (tmp_path / "source.py").write_text(content + "\nnext\n")

    result = await ReadFileTool(workspace=tmp_path).execute(path="source.py")

    assert result.startswith("1| " + content)
    assert "Showing lines 1-1 of 2" in result
    assert "offset=2" in result


async def test_regular_line_pagination(tmp_path):
    (tmp_path / "source.py").write_text("first\nsecond\nthird\n")
    tool = ReadFileTool(workspace=tmp_path)

    assert await tool.execute(path="source.py", limit=2) == (
        "1| first\n2| second\n\n(Showing lines 1-2 of 3. Use offset=3 to continue.)"
    )
    assert await tool.execute(path="source.py", offset=3) == "3| third\n\n(End of file — 3 lines total)"
