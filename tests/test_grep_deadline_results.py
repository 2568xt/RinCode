"""An expired Python grep scan must never look like a complete result."""

from unittest.mock import Mock

import pytest

from rincode.agent.tools import file_search
from rincode.agent.tools.file_search import GrepTool


@pytest.mark.asyncio
@pytest.mark.parametrize("output_mode", ["content", "files_with_matches", "count"])
@pytest.mark.parametrize("clock_values", [[0, 21], [0, 0, 21]], ids=["before-scan", "after-root"])
async def test_grep_deadline_reports_incomplete_search(tmp_path, monkeypatch, output_mode, clock_values):
    monkeypatch.setattr(file_search.shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr(file_search, "time", Mock(monotonic=Mock(side_effect=clock_values)))
    (tmp_path / "a.txt").write_text("needle\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("needle\nneedle\n", encoding="utf-8")

    result = await GrepTool(workspace=tmp_path).execute(pattern="needle", output_mode=output_mode)

    assert result.startswith("Error")
    assert "timed out" in result
    assert "incomplete" in result
    assert "narrower" in result
    assert "No matches found" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("output_mode", ["content", "files_with_matches", "count"])
async def test_grep_completed_scan_reports_no_matches(tmp_path, monkeypatch, output_mode):
    monkeypatch.setattr(file_search.shutil, "which", lambda *_a, **_k: None)
    (tmp_path / "a.txt").write_text("haystack\n", encoding="utf-8")

    result = await GrepTool(workspace=tmp_path).execute(pattern="needle", output_mode=output_mode)

    assert result == "No matches found."
