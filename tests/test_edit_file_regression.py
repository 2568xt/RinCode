from pathlib import Path

import pytest

from rincode.agent.tools.filesystem import EditFileTool


@pytest.mark.asyncio
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("final_newline", [False, True])
async def test_whitespace_match_edits_located_line_only(tmp_path: Path, newline: str, final_newline: bool):
    path = tmp_path / "example.py"
    original = "#    value = 1\nif True:\n    value = 1" + ("\n" if final_newline else "")
    path.write_bytes(original.replace("\n", newline).encode())

    result = await EditFileTool(workspace=tmp_path).execute(
        path="example.py", old_text="value = 1 ", new_text="    value = 2"
    )

    expected = "#    value = 1\nif True:\n    value = 2" + ("\n" if final_newline else "")
    assert result.startswith("Successfully edited")
    assert path.read_bytes() == expected.replace("\n", newline).encode()


@pytest.mark.asyncio
async def test_replace_all_handles_distinct_whitespace_without_editing_comment(tmp_path: Path):
    path = tmp_path / "example.py"
    path.write_text("#    value = 1  \nif True:\n    value = 1  \n    value = 1\n")

    result = await EditFileTool(workspace=tmp_path).execute(
        path="example.py", old_text="\tvalue = 1", new_text="    value = 2", replace_all=True
    )

    assert result.startswith("Successfully edited")
    assert path.read_text() == "#    value = 1  \nif True:\n    value = 2\n    value = 2\n"


@pytest.mark.asyncio
async def test_ambiguous_whitespace_match_does_not_write(tmp_path: Path):
    path = tmp_path / "example.py"
    original = "    value = 1  \n    value = 1\n"
    path.write_text(original)

    result = await EditFileTool(workspace=tmp_path).execute(
        path="example.py", old_text="\tvalue = 1", new_text="    value = 2"
    )

    assert "appears 2 times" in result
    assert path.read_text() == original


@pytest.mark.asyncio
async def test_exact_match_takes_precedence_over_whitespace_match(tmp_path: Path):
    path = tmp_path / "example.py"
    path.write_text("    value = 1\n\tvalue = 1\n")

    result = await EditFileTool(workspace=tmp_path).execute(
        path="example.py", old_text="\tvalue = 1", new_text="\tvalue = 2", replace_all=True
    )

    assert result.startswith("Successfully edited")
    assert path.read_text() == "    value = 1\n\tvalue = 2\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_all", [False, True])
async def test_overlapping_multiline_whitespace_matches(tmp_path: Path, replace_all: bool):
    path = tmp_path / "example.py"
    original = "    value\n    value\n    value\n"
    path.write_text(original)

    result = await EditFileTool(workspace=tmp_path).execute(
        path="example.py", old_text="\tvalue\n\tvalue", new_text="    replacement", replace_all=replace_all
    )

    if replace_all:
        assert result.startswith("Successfully edited")
        assert path.read_text() == "    replacement\n    value\n"
    else:
        assert "appears 2 times" in result
        assert path.read_text() == original
