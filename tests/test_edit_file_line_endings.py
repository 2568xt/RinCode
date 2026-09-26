"""Unedited bytes survive edits to files with mixed newline styles."""

import pytest

from rincode.agent.tools.filesystem import EditFileTool


@pytest.mark.parametrize("trailing", [b"", b"\n", b"\r\n"])
async def test_edit_preserves_unmatched_mixed_endings(tmp_path, trailing):
    before = b"# header\r\ndef add(a, b):\n    return a - b" + trailing
    path = tmp_path / "example.py"
    path.write_bytes(before)

    result = await EditFileTool(workspace=tmp_path).execute(
        path=path.name, old_text="return a - b", new_text="return a + b"
    )

    assert result.startswith("Successfully edited")
    assert path.read_bytes() == before.replace(b"return a - b", b"return a + b")


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
async def test_replacement_newlines_follow_target_line(tmp_path, newline):
    other = "\n" if newline == "\r\n" else "\r\n"
    before = "# 中文标题" + other + "value = 1" + newline + "# unchanged" + other
    path = tmp_path / "example.py"
    path.write_bytes(before.encode())

    result = await EditFileTool(workspace=tmp_path).execute(
        path=path.name, old_text="value = 1", new_text="value = 2\nother = 3"
    )

    assert result.startswith("Successfully edited")
    assert path.read_bytes() == before.replace("value = 1", "value = 2" + newline + "other = 3").encode()


async def test_replace_all_uses_each_target_line_ending(tmp_path):
    path = tmp_path / "example.py"
    path.write_bytes(b"value = 1\r\n# gap\nvalue = 1\n")

    result = await EditFileTool(workspace=tmp_path).execute(
        path=path.name, old_text="value = 1", new_text="value = 2\nother = 3", replace_all=True
    )

    assert result.startswith("Successfully edited")
    assert path.read_bytes() == b"value = 2\r\nother = 3\r\n# gap\nvalue = 2\nother = 3\n"


async def test_multiline_whitespace_fallback_preserves_surrounding_bytes(tmp_path):
    path = tmp_path / "example.py"
    path.write_bytes("# 中文\nif True:\r\n    a = 1\r\n    b = 2\n# end".encode())

    result = await EditFileTool(workspace=tmp_path).execute(
        path=path.name, old_text="a = 1\nb = 2", new_text="    a = 3\n    b = 4"
    )

    assert result.startswith("Successfully edited")
    assert path.read_bytes() == "# 中文\nif True:\r\n    a = 3\r\n    b = 4\n# end".encode()
