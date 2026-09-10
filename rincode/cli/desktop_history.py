"""Read existing desktop project history without constructing a runtime."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from rincode.product import get_project_state_dir
from rincode.session.manager import SessionManager


def _message_texts(path: Path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict) or message.get("_type") == "metadata":
                continue
            content = message.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    block.get("text", "") for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                )
            if isinstance(content, str):
                yield message.get("role"), content


def _title(path: Path, metadata: dict) -> str:
    title = metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title
    for role, content in _message_texts(path):
        if role == "user" and content.strip():
            text = " ".join(content.split())
            return text[:48] + ("…" if len(text) > 48 else "")
    return "新对话"


def _snippet(text: str, query: str) -> str | None:
    position = text.casefold().find(query)
    if position < 0:
        return None
    # Casefold can expand characters (e.g. ß -> ss); map back to source offset.
    offset = 0
    for index, character in enumerate(text):
        offset += len(character.casefold())
        if offset > position:
            break
    start = max(0, index - 35)
    end = min(len(text), start + 160)
    return ("…" if start else "") + " ".join(text[start:end].split()) + ("…" if end < len(text) else "")


def _match_preview(path: Path, title: str, query: str) -> str | None:
    preview = _snippet(title, query)
    if preview is not None:
        return preview
    for _, content in _message_texts(path):
        preview = _snippet(content, query)
        if preview is not None:
            return preview
    return None


def read_project_history(projects: list[dict], query: str = "") -> dict:
    query = query.strip().casefold()
    result = {}
    for project in projects:
        rows = []
        result[project["id"]] = entry = {"sessions": rows}
        try:
            directory = get_project_state_dir(Path(project["path"])) / "sessions" / "tui"
            if not directory.exists():
                continue
            dated_rows = []
            for path in directory.glob("*.jsonl"):
                metadata, count = SessionManager._scan_file(path)
                if metadata is None:
                    entry["error"] = "部分会话文件无法读取"
                    continue
                try:
                    title = _title(path, metadata.get("metadata") or {})
                    preview = _match_preview(path, title, query) if query else ""
                except (OSError, UnicodeError):
                    entry["error"] = "部分会话文件无法读取"
                    continue
                if preview is None:
                    continue
                try:
                    started_at = datetime.fromisoformat(metadata.get("created_at") or "").timestamp()
                except ValueError:
                    started_at = 0
                dated_rows.append((metadata.get("updated_at") or "", {
                    "id": metadata["key"],
                    "title": title,
                    "message_count": count,
                    "preview": preview,
                    "source": "tui",
                    "started_at": started_at,
                }))
            rows.extend(row for _, row in sorted(dated_rows, key=lambda pair: pair[0], reverse=True))
        except (OSError, ValueError):
            entry["error"] = "无法读取项目会话目录"
    return result


def main() -> None:
    request = json.load(sys.stdin)
    projects = request if isinstance(request, list) else request["projects"]
    query = "" if isinstance(request, list) else request.get("query", "")
    json.dump(read_project_history(projects, query), sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
