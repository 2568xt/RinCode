"""Directory noise filters apply below the requested listing root."""

from pathlib import Path

import pytest

from rincode.agent.tools.filesystem import ListDirTool


@pytest.mark.parametrize("ignored_name", ["build", "dist", "node_modules", ".venv"])
@pytest.mark.parametrize("root_is_ignored_name", [False, True])
async def test_recursive_listing_ignores_only_descendants(
    tmp_path: Path, ignored_name: str, root_is_ignored_name: bool
):
    root = tmp_path / ignored_name
    if not root_is_ignored_name:
        root /= "my-project"
    root.mkdir(parents=True)
    (root / "app.py").write_text("value = 1\n")
    (root / "src").mkdir()
    (root / "src" / "helper.py").write_text("value = 2\n")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "noise.py").write_text("ignored\n")
    tool = ListDirTool(workspace=root)

    result = await tool.execute(path=".", recursive=True)

    assert result == "app.py\nsrc/\nsrc/helper.py"
    assert "app.py" in await tool.execute(path=".")
    limited = await tool.execute(path=".", recursive=True, max_entries=1)
    assert limited.startswith("app.py\n")
    assert "of 3" in limited


async def test_explicit_subdirectory_is_listing_root(tmp_path: Path):
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "generated.py").write_text("value = 1\n")
    tool = ListDirTool(workspace=tmp_path)

    assert await tool.execute(path="build", recursive=True) == "generated.py"
    assert "generated.py" not in await tool.execute(path=".", recursive=True)
