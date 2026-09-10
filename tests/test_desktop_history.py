"""History catalog stays read-only and shares session validation rules."""

import hashlib
import json

import pytest

from rincode.cli.desktop_history import read_project_history
from rincode.product import get_project_state_dir


def write_session(project, chat, *, title="", text="hello", updated="2026-09-11T00:00:00", channel="tui"):
    path = get_project_state_dir(project) / "sessions" / channel / f"{chat}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"_type": "metadata", "key": f"{channel}:{chat}", "created_at": updated,
         "updated_at": updated, "metadata": {"title": title}},
        {"role": "user", "content": text},
        {"role": "assistant", "content": "private transcript body"},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in records))
    return path


def test_missing_history_creates_no_directories(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("RINCODE_HOME", str(home))
    assert read_project_history([{"id": "a", "path": str(tmp_path / "missing")}]) == {
        "a": {"sessions": []},
    }
    assert not home.exists()


def test_project_isolation_metadata_titles_and_no_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("RINCODE_HOME", str(tmp_path / "home"))
    a, b = tmp_path / "a", tmp_path / "b"
    old = write_session(a, "old", title="Saved title")
    new = write_session(a, "new", text="first   user " + "x" * 80, updated="2026-09-11T01:00:00")
    cli = write_session(a, "cli-only", channel="cli")
    other = write_session(b, "other")
    files = [old, new, cli, other]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in files]
    projects = [{"id": "a", "path": str(a)}, {"id": "b", "path": str(b)}]
    result = read_project_history(projects)
    assert [s["id"] for s in result["a"]["sessions"]] == ["tui:new", "tui:old"]
    assert result["a"]["sessions"][0]["title"] == ("first user " + "x" * 80)[:48] + "…"
    assert result["a"]["sessions"][1]["title"] == "Saved title"
    assert result["a"]["sessions"][0]["message_count"] == 2
    assert [s["id"] for s in result["b"]["sessions"]] == ["tui:other"]
    assert "private transcript body" not in json.dumps(result)
    assert read_project_history(projects) == result
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in files] == before


def test_corrupt_session_reported_without_hiding_valid_history(tmp_path, monkeypatch):
    monkeypatch.setenv("RINCODE_HOME", str(tmp_path / "home"))
    valid = write_session(tmp_path / "a", "valid")
    (valid.parent / "bad.jsonl").write_text("not-json\n")
    result = read_project_history([{"id": "a", "path": str(tmp_path / "a")}])["a"]
    assert result["error"]
    assert [s["id"] for s in result["sessions"]] == ["tui:valid"]


@pytest.mark.parametrize("query,expected", [
    ("研究笔记", ["tui:chinese"]),
    ("量子计算", ["tui:chinese"]),
    ("needle", ["tui:english"]),
    ("STRASSE", ["tui:english"]),
    ("never-present", []),
])
def test_search_all_message_bodies_and_titles_without_writes(tmp_path, monkeypatch, query, expected):
    monkeypatch.setenv("RINCODE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    chinese = write_session(project, "chinese", title="研究笔记", text="开场白")
    english = write_session(project, "english", title="Notes", text="introduction")
    with chinese.open("a") as stream:
        stream.write(json.dumps({"role": "assistant", "content": "无关前文" * 100 + "量子计算结果" + "无关后文" * 100}) + "\n")
    with english.open("a") as stream:
        stream.write(json.dumps({"role": "tool", "content": "result includes NeEdLe and Straße"}) + "\n")
    files = [chinese, english]
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
    result = read_project_history([{"id": "a", "path": str(project)}], query)["a"]
    assert [s["id"] for s in result["sessions"]] == expected
    for session in result["sessions"]:
        assert query.casefold() in session["preview"].casefold()
        assert len(session["preview"]) <= 180
        assert "无关前文" * 100 not in session["preview"]
    assert [hashlib.sha256(p.read_bytes()).hexdigest() for p in files] == before


def test_search_preserves_partial_results_for_corrupt_files(tmp_path, monkeypatch):
    monkeypatch.setenv("RINCODE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    valid = write_session(project, "valid", title="find me")
    (valid.parent / "bad.jsonl").write_text("broken\n")
    result = read_project_history([{"id": "a", "path": str(project)}], "find")["a"]
    assert result["error"]
    assert [s["id"] for s in result["sessions"]] == ["tui:valid"]
