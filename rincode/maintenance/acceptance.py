"""Deterministic acceptance for a maintainer repair candidate.

The acceptance boundary is deliberately small: a maintainer-owned manifest fixes
the command and its protected files, while a candidate directory contains the
proposed source change. Baseline and candidate commands run in disposable local
copies. This gives the maintainer an auditable before/after result without
turning the local worktree into a sandbox or claiming that an Agent succeeded.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "rincode.maintenance.acceptance.v1"
_VOLATILE_PARTS = frozenset({".git", ".rincode", ".pytest_cache", "__pycache__", ".venv", "node_modules"})
_MAX_OUTPUT_CHARS = 8_192


class MaintenanceConfigError(ValueError):
    """The maintainer-owned task manifest is invalid."""


@dataclass(frozen=True)
class MaintenanceTask:
    """Fixed acceptance policy for one repair task.

    ``allowed_paths`` uses exact file rules by default. A trailing slash marks a
    directory subtree. ``protected_paths`` names files or directories consumed
    by the acceptance command; any candidate change there is rejected before a
    command runs.
    """

    workspace: Path
    command: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        workspace = Path(self.workspace).expanduser().resolve()
        if not workspace.is_dir():
            raise MaintenanceConfigError(f"workspace is not a directory: {workspace}")
        if not self.command or any(not isinstance(part, str) or not part for part in self.command):
            raise MaintenanceConfigError("command must be a non-empty list of non-empty strings")
        if not self.allowed_paths:
            raise MaintenanceConfigError("allowed_paths must contain at least one source path")
        if not self.protected_paths:
            raise MaintenanceConfigError("protected_paths must contain the acceptance files")
        if self.timeout_seconds <= 0 or not math.isfinite(self.timeout_seconds):
            raise MaintenanceConfigError("timeout_seconds must be greater than zero")
        allowed = tuple(_normalise_rule(rule) for rule in self.allowed_paths)
        protected = tuple(_normalise_rule(rule) for rule in self.protected_paths)
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "allowed_paths", allowed)
        object.__setattr__(self, "protected_paths", protected)
        object.__setattr__(self, "command", tuple(self.command))

    def digest(self) -> str:
        payload = {
            "workspace": str(self.workspace),
            "command": list(self.command),
            "allowed_paths": list(self.allowed_paths),
            "protected_paths": list(self.protected_paths),
            "timeout_seconds": self.timeout_seconds,
        }
        return _sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


def load_task_manifest(path: Path) -> MaintenanceTask:
    """Load a manifest whose workspace path is relative to the manifest file."""

    manifest_path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MaintenanceConfigError(f"could not read manifest {manifest_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise MaintenanceConfigError("manifest must be a JSON object")
    required = {"workspace", "command", "allowed_paths", "protected_paths"}
    missing = sorted(required - set(raw))
    if missing:
        raise MaintenanceConfigError(f"manifest is missing fields: {', '.join(missing)}")
    if not isinstance(raw["workspace"], str):
        raise MaintenanceConfigError("manifest workspace must be a string")
    workspace = Path(raw["workspace"])
    if not workspace.is_absolute():
        workspace = manifest_path.parent / workspace
    for field in ("command", "allowed_paths", "protected_paths"):
        if not isinstance(raw[field], list):
            raise MaintenanceConfigError(f"manifest {field} must be a list")
    timeout = raw.get("timeout_seconds", 30.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise MaintenanceConfigError("manifest timeout_seconds must be a positive number")
    return MaintenanceTask(
        workspace=workspace,
        command=tuple(raw["command"]),
        allowed_paths=tuple(raw["allowed_paths"]),
        protected_paths=tuple(raw["protected_paths"]),
        timeout_seconds=float(timeout),
    )


def accept_maintenance_task(task: MaintenanceTask, candidate: Path) -> dict[str, Any]:
    """Run the fixed command against baseline and candidate copies.

    A candidate is accepted only when it changes source within the allowlist,
    leaves protected acceptance files untouched, makes a previously failing
    baseline command pass, and leaves the original workspace byte-for-byte
    unchanged under the snapshot policy.
    """

    workspace = task.workspace
    candidate_path = Path(candidate).expanduser()
    candidate_root = candidate_path.resolve()
    base: dict[str, Any] = {
        "schema": SCHEMA,
        "accepted": False,
        "reason": "not_evaluated",
        "workspace": str(workspace),
        "candidate": str(candidate_root),
        "task_digest": task.digest(),
        "command": list(task.command),
        "command_sha256": _sha256_bytes(json.dumps(list(task.command), separators=(",", ":")).encode("utf-8")),
        "allowed_paths": list(task.allowed_paths),
        "protected_paths": list(task.protected_paths),
        "timeout_seconds": task.timeout_seconds,
        "changed_paths": [],
        "baseline": _not_run_result(),
        "candidate_result": _not_run_result(),
        "workspace_unchanged": None,
    }

    try:
        before = _snapshot(workspace)
    except OSError as exc:
        base["reason"] = "workspace_snapshot_failed"
        base["workspace_error"] = str(exc)
        return _with_workspace_check(base, workspace, None)

    base["workspace_before_sha256"] = _snapshot_digest(before)
    if candidate_path.is_symlink():
        base["reason"] = "invalid_candidate_tree"
        base["candidate_error"] = "symlinks are not allowed in the acceptance tree: ."
        return _with_workspace_check(base, workspace, before)
    if not candidate_root.is_dir() or candidate_root == workspace or _is_within(candidate_root, workspace):
        base["reason"] = "invalid_candidate_root"
        return _with_workspace_check(base, workspace, before)

    try:
        candidate_snapshot = _snapshot(candidate_root)
    except OSError as exc:
        base["reason"] = "invalid_candidate_tree"
        base["candidate_error"] = str(exc)
        return _with_workspace_check(base, workspace, before)

    changed = _changed_paths(before, candidate_snapshot)
    base["changed_paths"] = changed
    base["candidate_sha256"] = _snapshot_digest(candidate_snapshot)

    protected = [path for path in changed if _matches_any(path, task.protected_paths)]
    disallowed = [path for path in changed if not _matches_any(path, task.allowed_paths)]
    if protected:
        base["reason"] = "candidate_changed_protected_path"
        base["protected_changes"] = protected
        return _with_workspace_check(base, workspace, before)
    if disallowed:
        base["reason"] = "candidate_changed_disallowed_path"
        base["disallowed_changes"] = disallowed
        return _with_workspace_check(base, workspace, before)
    if not changed:
        base["reason"] = "candidate_has_no_content_change"
        return _with_workspace_check(base, workspace, before)

    try:
        with tempfile.TemporaryDirectory(prefix="rincode-maintenance-") as temp_dir:
            temp_root = Path(temp_dir)
            baseline_root = temp_root / "baseline"
            candidate_copy = temp_root / "candidate"
            _copy_tree(workspace, baseline_root)
            if _snapshot(baseline_root) != before or _snapshot(workspace) != before:
                base["reason"] = "workspace_changed_during_copy"
                return _with_workspace_check(base, workspace, before)
            _copy_tree(candidate_root, candidate_copy)
            if _snapshot(candidate_copy) != candidate_snapshot or _snapshot(candidate_root) != candidate_snapshot:
                base["reason"] = "candidate_changed_during_copy"
                return _with_workspace_check(base, workspace, before)
            baseline = _run_command(baseline_root, task.command, task.timeout_seconds)
            base["baseline"] = baseline
            if baseline["status"] != "failed":
                base["reason"] = "baseline_did_not_fail"
                return _with_workspace_check(base, workspace, before)
            candidate_result = _run_command(candidate_copy, task.command, task.timeout_seconds)
            base["candidate_result"] = candidate_result
            if candidate_result["status"] == "timeout":
                base["reason"] = "candidate_timeout"
            elif candidate_result["status"] != "passed":
                base["reason"] = "candidate_did_not_pass"
            else:
                base["accepted"] = True
                base["reason"] = "baseline_failed_candidate_passed"
    except OSError as exc:
        base["reason"] = "execution_setup_failed"
        base["execution_error"] = str(exc)

    final = _with_workspace_check(base, workspace, before)
    if final["workspace_unchanged"] is False:
        final["accepted"] = False
        final["reason"] = "workspace_changed_during_acceptance"
    return final


def write_acceptance_report(path: Path, report: dict[str, Any]) -> Path:
    """Persist a stable JSON report and return its resolved path."""

    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def resolve_report_path(
    requested: Path | None,
    *,
    manifest: Path,
    workspace: Path,
    candidate: Path,
) -> Path:
    """Resolve a report path while protecting all acceptance inputs."""

    candidate_root = Path(candidate).expanduser().resolve()
    if requested is None:
        output = (Path.cwd() / "maintenance-acceptance-report.json").resolve()
        if _is_within(output, workspace) or _is_within(output, candidate_root):
            output = (workspace.parent / f"{workspace.name}.maintenance-acceptance-report.json").resolve()
    else:
        output = Path(requested).expanduser().resolve()
    manifest_path = Path(manifest).expanduser().resolve()
    if output == manifest_path or _is_within(output, workspace) or _is_within(output, candidate_root):
        raise MaintenanceConfigError("report must be outside the manifest, workspace, and candidate trees")
    if output.exists():
        raise MaintenanceConfigError("report path already exists; choose a new path")
    return output


def _normalise_rule(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise MaintenanceConfigError("path rules must be non-empty strings")
    directory = value.endswith("/")
    raw = value.replace("\\", "/").rstrip("/")
    path = PurePosixPath(raw)
    if path.is_absolute() or not raw or any(part in {"", ".", ".."} for part in path.parts):
        raise MaintenanceConfigError(f"path rule must be workspace-relative: {value!r}")
    normalised = path.as_posix()
    return f"{normalised}/" if directory else normalised


def _matches_any(path: str, rules: tuple[str, ...]) -> bool:
    return any(path == rule.rstrip("/") or (rule.endswith("/") and path.startswith(rule)) for rule in rules)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _snapshot(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        raise OSError(f"not a directory: {root}")
    files: dict[str, bytes] = {}
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        rel_current = current_path.relative_to(root).as_posix()
        kept_directories = []
        for name in directories:
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise OSError(f"symlinks are not allowed in the acceptance tree: {rel}")
            if not _ignored(rel_current, name):
                kept_directories.append(name)
        directories[:] = kept_directories
        for name in names:
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            if _ignored(rel_current, name):
                continue
            if path.is_symlink():
                raise OSError(f"symlinks are not allowed in the acceptance tree: {rel}")
            if not path.is_file():
                raise OSError(f"unsupported non-file entry in acceptance tree: {rel}")
            files[rel] = path.read_bytes()
    return files


def _ignored(parent: str, name: str) -> bool:
    return name in _VOLATILE_PARTS or any(part in _VOLATILE_PARTS for part in PurePosixPath(parent).parts)


def _snapshot_digest(snapshot: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(snapshot):
        encoded = path.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(snapshot[path]).to_bytes(8, "big"))
        digest.update(snapshot[path])
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _changed_paths(before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def _copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        symlinks=False,
        ignore=shutil.ignore_patterns(*_VOLATILE_PARTS),
    )


def _run_command(root: Path, command: tuple[str, ...], timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    kwargs: dict[str, Any] = {
        "cwd": root,
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        kwargs["stdout"] = stdout_file
        kwargs["stderr"] = stderr_file
        try:
            process = subprocess.Popen(list(command), **kwargs)
        except OSError as exc:
            return _command_result("error", None, False, False, False, started, "", str(exc))

        try:
            process.communicate(timeout=timeout_seconds)
            group_terminated = _kill_process_group(process)
            return _command_result(
                "passed" if process.returncode == 0 else "failed",
                process.returncode,
                False,
                process.poll() is not None,
                group_terminated,
                started,
                _read_tail(stdout_file),
                _read_tail(stderr_file),
            )
        except subprocess.TimeoutExpired:
            group_terminated = _kill_process_group(process)
            try:
                process.communicate(timeout=max(timeout_seconds, 1.0))
            except subprocess.TimeoutExpired:
                group_terminated = _kill_process_group(process) or group_terminated
                process.wait()
            return _command_result(
                "timeout",
                process.returncode,
                True,
                process.poll() is not None,
                group_terminated,
                started,
                _read_tail(stdout_file),
                _read_tail(stderr_file),
            )


def _kill_process_group(process: subprocess.Popen[str]) -> bool:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return True
        except ProcessLookupError:
            return False
    elif process.poll() is None:
        process.kill()
        return True
    return False


def _read_tail(stream: Any) -> str:
    stream.flush()
    stream.seek(0, os.SEEK_END)
    stream.seek(max(0, stream.tell() - _MAX_OUTPUT_CHARS))
    return stream.read(_MAX_OUTPUT_CHARS).decode("utf-8", errors="replace")


def _command_result(
    status: str,
    exit_code: int | None,
    timed_out: bool,
    leader_reaped: bool,
    process_group_terminated: bool,
    started: float,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "leader_reaped": leader_reaped,
        "process_group_terminated": process_group_terminated,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "stdout": stdout[-_MAX_OUTPUT_CHARS:],
        "stderr": stderr[-_MAX_OUTPUT_CHARS:],
    }


def _not_run_result() -> dict[str, Any]:
    return {
        "status": "not_run",
        "exit_code": None,
        "timed_out": False,
        "leader_reaped": False,
        "process_group_terminated": False,
        "duration_ms": 0.0,
        "stdout": "",
        "stderr": "",
    }


def _with_workspace_check(
    report: dict[str, Any],
    workspace: Path,
    before: dict[str, bytes] | None,
) -> dict[str, Any]:
    if before is None:
        report["workspace_unchanged"] = False
        return report
    try:
        after = _snapshot(workspace)
    except OSError as exc:
        report["workspace_unchanged"] = False
        report["workspace_error_after"] = str(exc)
        return report
    report["workspace_after_sha256"] = _snapshot_digest(after)
    report["workspace_unchanged"] = after == before
    return report
