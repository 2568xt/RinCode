"""实现受 Workspace 边界约束的文件读取、写入、编辑和目录列举 Tool。

所有 Tool 共享 `_resolve_path`，相对路径先基于 Workspace 解析，再用 resolved path 验证
allowed_dir，避免 ``..``、symlink 或 user home 展开绕过目录限制。Read/List 声明并发安全的
READ effect，Write/Edit 声明 WRITE；返回值统一为模型可读文本，权限与 I/O 异常不泄漏堆栈。
"""

import difflib
import re
from pathlib import Path
from typing import Any

from rincode.agent.tools.base import Tool
from rincode.agent.tools.execution import ToolCapability, ToolEffect


def _resolve_path(path: str, workspace: Path | None = None, allowed_dir: Path | None = None) -> Path:
    """解析 User path，并在真实路径上执行可选目录限制。

    输入先 expanduser；相对路径且提供 Workspace 时以 Workspace 为根，再调用 `resolve()` 消除
    ``..`` 与 symlink。设置 ``allowed_dir`` 后，最终路径必须能 `relative_to` 同样解析后的允许
    根，否则抛 `PermissionError`。返回 Path 不保证存在，具体 Tool 再判断 file/dir 类型。
    """
    p = Path(path).expanduser()
    if not p.is_absolute() and workspace:
        p = workspace / p
    resolved = p.resolve()
    if allowed_dir:
        try:
            resolved.relative_to(allowed_dir.resolve())
        except ValueError:
            raise PermissionError(f"Path {path} is outside allowed directory {allowed_dir}")
    return resolved


class _FsTool(Tool):
    """为 Filesystem Tool 共享 Workspace 配置与安全路径解析。

    构造时保存可选 Workspace 和 allowed_dir，`_resolve` 委托 `_resolve_path`，让 read、write、
    edit、list 与 search 使用同一 trust boundary。该基类不执行 I/O，也不替子类声明 effect。
    """

    def __init__(self, workspace: Path | None = None, allowed_dir: Path | None = None):
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    def _resolve(self, path: str) -> Path:
        return _resolve_path(path, self._workspace, self._allowed_dir)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


class ReadFileTool(_FsTool):
    """按行读取 UTF-8 文件，并提供可继续调用的分页结果。

    ``offset`` 使用 1-based 行号，``limit`` 默认 2000；输出每行带真实行号，便于 Edit 或人工
    定位。单次正文最多 `_MAX_CHARS=128_000`，超出会按完整行再次裁剪，并明确给出下一
    offset；单行无法放入上限时明确报错并提示分块读取，避免空页循环。空文件和越界 offset
    使用不同结果。Tool 只读且 concurrency_safe，不读取目录、
    不自动猜编码，路径仍受 `_FsTool` 允许根约束。
    """

    capability = ToolCapability(effect=ToolEffect.READ, concurrency_safe=True)
    _MAX_CHARS = 128_000
    _DEFAULT_LIMIT = 2000

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file. Returns numbered lines. Use offset and limit to paginate through large files."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to read"},
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-indexed, default 1)",
                    "minimum": 1,
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default 2000)",
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }

    async def execute(self, path: str, offset: int = 1, limit: int | None = None, **kwargs: Any) -> str:
        try:
            fp = self._resolve(path)
            if not fp.exists():
                return f"Error: File not found: {path}"
            if not fp.is_file():
                return f"Error: Not a file: {path}"

            all_lines = fp.read_text(encoding="utf-8").splitlines()
            total = len(all_lines)

            if offset < 1:
                offset = 1
            if total == 0:
                return f"(Empty file: {path})"
            if offset > total:
                return f"Error: offset {offset} is beyond end of file ({total} lines)"

            start = offset - 1
            end = min(start + (limit or self._DEFAULT_LIMIT), total)
            numbered = [f"{start + i + 1}| {line}" for i, line in enumerate(all_lines[start:end])]
            result = "\n".join(numbered)

            if len(result) > self._MAX_CHARS:
                trimmed, chars = [], 0
                for line in numbered:
                    next_chars = chars + len(line) + (1 if trimmed else 0)
                    if next_chars > self._MAX_CHARS:
                        break
                    trimmed.append(line)
                    chars = next_chars
                if not trimmed:
                    message = (
                        f"Error: line {offset} exceeds read_file's {self._MAX_CHARS}-character output limit. "
                        "Use exec to inspect this line in smaller chunks."
                    )
                    if offset < total:
                        message += f" Use offset={offset + 1} to read the remaining lines."
                    return message
                end = start + len(trimmed)
                result = "\n".join(trimmed)

            if end < total:
                result += f"\n\n(Showing lines {offset}-{end} of {total}. Use offset={end + 1} to continue.)"
            else:
                result += f"\n\n(End of file — {total} lines total)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {e}"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


class WriteFileTool(_FsTool):
    """以 UTF-8 把完整 content 写入目标文件，必要时创建 Parent 目录。

    这是覆盖式 WRITE Tool，不做 append、merge 或旧内容校验；调用方应在修改前先读取并理解
    文件。路径通过 `_resolve` 检查 allowed_dir，写入成功返回字符长度与 resolved path，权限或
    I/O 失败返回 Error。该方法可能替换既有文件，不能视为可逆操作。
    """

    capability = ToolCapability(effect=ToolEffect.WRITE)

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "Write content to a file at the given path. Creates parent directories if needed."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to write to"},
                "content": {"type": "string", "description": "The content to write"},
            },
            "required": ["path", "content"],
        }

    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        try:
            fp = self._resolve(path)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {e}"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


def _find_match(content: str, old_text: str) -> list[tuple[int, int]]:
    """在 ``content`` 中定位 old_text，先精确匹配，再做逐行 trim 的滑动匹配。

    调用方应先把 CRLF 规范成 LF。精确命中时返回不重叠的字符范围；否则按相同行数滑动，
    比较每行 strip 后内容，使轻微缩进或尾随空白差异仍可编辑。保留每个候选的原文范围，
    避免替换时重新搜索同名子串而改到其他行。没有匹配时返回空列表；多匹配由调用方处理。
    """
    if old_text in content:
        return [match.span() for match in re.finditer(re.escape(old_text), content)]

    old_lines = old_text.splitlines()
    if not old_lines:
        return []
    stripped_old = [line.strip() for line in old_lines]
    content_lines = content.splitlines()
    line_offsets = [0]
    for line in content.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))

    candidates = []
    for i in range(len(content_lines) - len(stripped_old) + 1):
        window = content_lines[i : i + len(stripped_old)]
        if [line.strip() for line in window] == stripped_old:
            last = i + len(stripped_old) - 1
            candidates.append((line_offsets[i], line_offsets[last] + len(content_lines[last])))

    return candidates


class EditFileTool(_FsTool):
    """通过 old_text → new_text 替换编辑文件，并提供受限 whitespace fallback。

    Tool 先保存原文件是否使用 CRLF，再在 LF 视图上调用 `_find_match`。零命中时返回最相似窗口
    unified diff；多命中且未设置 ``replace_all`` 时拒绝含糊修改，要求更多 Context。成功后按
    原 line ending 写回 UTF-8 bytes。它不会用模糊相似度自动替换，fallback 只容忍逐行空白。

    这是 WRITE effect。``replace_all=True`` 会替换每个匹配 fragment，调用方必须确认范围；
    路径限制和错误处理与其他 `_FsTool` 一致。
    """

    capability = ToolCapability(effect=ToolEffect.WRITE)

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing old_text with new_text. "
            "Supports minor whitespace/line-ending differences. "
            "Set replace_all=true to replace every occurrence."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to edit"},
                "old_text": {"type": "string", "description": "The text to find and replace"},
                "new_text": {"type": "string", "description": "The text to replace with"},
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences (default false)",
                },
            },
            "required": ["path", "old_text", "new_text"],
        }

    async def execute(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
        **kwargs: Any,
    ) -> str:
        try:
            fp = self._resolve(path)
            if not fp.exists():
                return f"Error: File not found: {path}"

            raw = fp.read_bytes()
            uses_crlf = b"\r\n" in raw
            content = raw.decode("utf-8").replace("\r\n", "\n")
            matches = _find_match(content, old_text.replace("\r\n", "\n"))

            if not matches:
                return self._not_found_msg(old_text, content, path)
            count = len(matches)
            if count > 1 and not replace_all:
                return (
                    f"Warning: old_text appears {count} times. "
                    "Provide more context to make it unique, or set replace_all=true."
                )

            norm_new = new_text.replace("\r\n", "\n")
            parts, cursor = [], 0
            for start, end in matches if replace_all else matches[:1]:
                # 与 str.replace 一致，从左向右替换，跳过重叠的多行候选。
                if start < cursor:
                    continue
                parts.extend((content[cursor:start], norm_new))
                cursor = end
            parts.append(content[cursor:])
            new_content = "".join(parts)
            if uses_crlf:
                new_content = new_content.replace("\n", "\r\n")

            fp.write_bytes(new_content.encode("utf-8"))
            return f"Successfully edited {fp}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {e}"

    @staticmethod
    def _not_found_msg(old_text: str, content: str, path: str) -> str:
        lines = content.splitlines(keepends=True)
        old_lines = old_text.splitlines(keepends=True)
        window = len(old_lines)

        best_ratio, best_start = 0.0, 0
        for i in range(max(1, len(lines) - window + 1)):
            ratio = difflib.SequenceMatcher(None, old_lines, lines[i : i + window]).ratio()
            if ratio > best_ratio:
                best_ratio, best_start = ratio, i

        if best_ratio > 0.5:
            diff = "\n".join(
                difflib.unified_diff(
                    old_lines,
                    lines[best_start : best_start + window],
                    fromfile="old_text (provided)",
                    tofile=f"{path} (actual, line {best_start + 1})",
                    lineterm="",
                )
            )
            return f"Error: old_text not found in {path}.\nBest match ({best_ratio:.0%} similar) at line {best_start + 1}:\n{diff}"
        return f"Error: old_text not found in {path}. No similar text found. Verify the file content."


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------


class ListDirTool(_FsTool):
    """列举目录内容，并可选择递归探索与结果上限。

    非递归结果用文件/目录图标区分；``recursive=True`` 时返回相对路径，并跳过 .git、
    node_modules、缓存、构建和 coverage 等 `_IGNORE_DIRS`。``max_entries`` 默认 200，只限制实际
    展示数量，Tool 仍统计总条目并在截断时报告完整 total。Read effect 可并发；目标不存在、
    不是目录或越过 allowed_dir 时返回明确 Error。
    """

    capability = ToolCapability(effect=ToolEffect.READ, concurrency_safe=True)
    _DEFAULT_MAX = 200
    _IGNORE_DIRS = {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".coverage",
        "htmlcov",
    }

    @property
    def name(self) -> str:
        return "list_dir"

    @property
    def description(self) -> str:
        return (
            "List the contents of a directory. "
            "Set recursive=true to explore nested structure. "
            "Common noise directories (.git, node_modules, __pycache__, etc.) are auto-ignored."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The directory path to list"},
                "recursive": {
                    "type": "boolean",
                    "description": "Recursively list all files (default false)",
                },
                "max_entries": {
                    "type": "integer",
                    "description": "Maximum entries to return (default 200)",
                    "minimum": 1,
                },
            },
            "required": ["path"],
        }

    async def execute(
        self,
        path: str,
        recursive: bool = False,
        max_entries: int | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            dp = self._resolve(path)
            if not dp.exists():
                return f"Error: Directory not found: {path}"
            if not dp.is_dir():
                return f"Error: Not a directory: {path}"

            cap = max_entries or self._DEFAULT_MAX
            items: list[str] = []
            total = 0

            if recursive:
                for item in sorted(dp.rglob("*")):
                    if any(p in self._IGNORE_DIRS for p in item.parts):
                        continue
                    total += 1
                    if len(items) < cap:
                        rel = item.relative_to(dp)
                        items.append(f"{rel}/" if item.is_dir() else str(rel))
            else:
                for item in sorted(dp.iterdir()):
                    if item.name in self._IGNORE_DIRS:
                        continue
                    total += 1
                    if len(items) < cap:
                        pfx = "📁 " if item.is_dir() else "📄 "
                        items.append(f"{pfx}{item.name}")

            if total == 0:
                return f"Directory {path} is empty"

            result = "\n".join(items)
            if total > cap:
                result += f"\n\n(truncated, showing first {cap} of {total} entries)"
            return result
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {e}"
