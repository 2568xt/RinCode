from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rincode.cli.commands import app
from rincode.maintenance.acceptance import _kill_process_group, _run_command

runner = CliRunner()


def _fixture(tmp_path: Path, *, command: list[str] | None = None, timeout: float = 2.0):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("def add(left, right):\n    return left - right\n", encoding="utf-8")
    tests = workspace / "tests"
    tests.mkdir()
    (tests / "test_bug.py").write_text(
        "from app import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    candidate = tmp_path / "candidate"
    shutil.copytree(workspace, candidate)
    manifest = tmp_path / "task.json"
    manifest.write_text(
        json.dumps(
            {
                "workspace": str(workspace),
                "command": command or [sys.executable, "-m", "pytest", "-q", "tests/test_bug.py"],
                "allowed_paths": ["app.py"],
                "protected_paths": ["tests/"],
                "timeout_seconds": timeout,
            }
        ),
        encoding="utf-8",
    )
    return workspace, candidate, manifest


def _run(manifest: Path, candidate: Path, report: Path):
    return runner.invoke(
        app,
        [
            "maintain",
            "accept",
            "--manifest",
            str(manifest),
            "--candidate",
            str(candidate),
            "--report",
            str(report),
        ],
    )


def _report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_maintenance_acceptance_records_baseline_failure_then_candidate_pass(tmp_path: Path):
    workspace, candidate, manifest = _fixture(tmp_path)
    (candidate / "app.py").write_text("def add(left, right):\n    return left + right\n", encoding="utf-8")
    original = (workspace / "app.py").read_bytes()
    report_path = tmp_path / "report.json"

    result = _run(manifest, candidate, report_path)

    assert result.exit_code == 0, result.output
    report = _report(report_path)
    assert report["accepted"] is True
    assert report["reason"] == "baseline_failed_candidate_passed"
    assert report["baseline"]["status"] == "failed"
    assert report["baseline"]["exit_code"] != 0
    assert report["candidate_result"]["status"] == "passed"
    assert report["candidate_result"]["exit_code"] == 0
    assert report["workspace_unchanged"] is True
    assert (workspace / "app.py").read_bytes() == original
    assert report["command_sha256"]
    assert report["task_digest"]


def test_maintenance_rejects_candidate_test_tampering_before_execution(tmp_path: Path):
    _workspace, candidate, manifest = _fixture(tmp_path)
    (candidate / "tests/test_bug.py").write_text("def test_pseudo_fix():\n    pass\n", encoding="utf-8")
    report_path = tmp_path / "report.json"

    result = _run(manifest, candidate, report_path)

    assert result.exit_code == 1
    report = _report(report_path)
    assert report["accepted"] is False
    assert report["reason"] == "candidate_changed_protected_path"
    assert report["protected_changes"] == ["tests/test_bug.py"]
    assert report["baseline"]["status"] == "not_run"


def test_maintenance_rejects_candidate_acceptance_command_tampering(tmp_path: Path):
    workspace, candidate, manifest = _fixture(tmp_path)
    verifier = workspace / "checks/verify.py"
    verifier.parent.mkdir()
    verifier.write_text(
        "from app import add\n\nif add(2, 3) != 5:\n    raise SystemExit(1)\n",
        encoding="utf-8",
    )
    (candidate / "checks").mkdir()
    shutil.copy2(verifier, candidate / "checks/verify.py")
    manifest.write_text(
        json.dumps(
            {
                "workspace": str(workspace),
                "command": [sys.executable, "checks/verify.py"],
                "allowed_paths": ["app.py"],
                "protected_paths": ["tests/", "checks/"],
            }
        ),
        encoding="utf-8",
    )
    (candidate / "checks/verify.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    report_path = tmp_path / "report.json"

    result = _run(manifest, candidate, report_path)

    assert result.exit_code == 1
    report = _report(report_path)
    assert report["reason"] == "candidate_changed_protected_path"
    assert report["protected_changes"] == ["checks/verify.py"]
    assert report["baseline"]["status"] == "not_run"


def test_maintenance_rejects_changes_outside_source_allowlist(tmp_path: Path):
    _workspace, candidate, manifest = _fixture(tmp_path)
    (candidate / "README.md").write_text("candidate claim\n", encoding="utf-8")
    report_path = tmp_path / "report.json"

    result = _run(manifest, candidate, report_path)

    assert result.exit_code == 1
    report = _report(report_path)
    assert report["reason"] == "candidate_changed_disallowed_path"
    assert report["disallowed_changes"] == ["README.md"]


def test_maintenance_timeout_is_not_success_and_process_group_is_reaped(tmp_path: Path):
    command = [sys.executable, "tests/timeout_check.py"]
    workspace, candidate, manifest = _fixture(tmp_path, command=command, timeout=0.1)
    timeout_script = workspace / "tests/timeout_check.py"
    timeout_script.write_text(
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parents[1]))\n"
        "from app import add\n\n"
        "if add(2, 3) != 5:\n"
        "    raise SystemExit(1)\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    shutil.copy2(timeout_script, candidate / "tests/timeout_check.py")
    (candidate / "app.py").write_text("def add(left, right):\n    return left + right\n", encoding="utf-8")
    report_path = tmp_path / "report.json"

    result = _run(manifest, candidate, report_path)

    assert result.exit_code == 1
    report = _report(report_path)
    assert report["accepted"] is False
    assert report["reason"] == "candidate_timeout"
    assert report["baseline"]["status"] == "failed"
    assert report["candidate_result"]["status"] == "timeout"
    assert report["candidate_result"]["timed_out"] is True
    assert report["candidate_result"]["leader_reaped"] is True
    assert report["candidate_result"]["process_group_terminated"] is True
    assert report["workspace_unchanged"] is True


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-specific")
def test_kill_process_group_handles_an_exited_leader_with_a_live_child():
    child_code = "import time; time.sleep(10)"
    parent_code = (
        f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child_code!r}]); time.sleep(0.05)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", parent_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    process.wait(timeout=1.0)
    assert process.poll() is not None

    _kill_process_group(process)
    _stdout, _stderr = process.communicate(timeout=1.0)


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-specific")
def test_run_command_terminates_children_after_a_normal_leader_exit(tmp_path: Path):
    marker = tmp_path / "child-survived"
    child_code = f"import time; time.sleep(0.4); open({str(marker)!r}, 'w').write('alive')"
    parent_code = (
        f"import subprocess,sys,time; subprocess.Popen([sys.executable, '-c', {child_code!r}]); time.sleep(0.05)"
    )

    result = _run_command(tmp_path, (sys.executable, "-c", parent_code), 1.0)

    assert result["status"] == "passed"
    assert result["leader_reaped"] is True
    assert result["process_group_terminated"] is True
    time.sleep(0.6)
    assert not marker.exists()


def test_maintenance_rejects_a_symlinked_candidate_directory(tmp_path: Path):
    _workspace, candidate, manifest = _fixture(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (candidate / "linked").symlink_to(outside, target_is_directory=True)
    report_path = tmp_path / "report.json"

    result = _run(manifest, candidate, report_path)

    assert result.exit_code == 1
    report = _report(report_path)
    assert report["reason"] == "invalid_candidate_tree"
    assert "symlinks are not allowed" in report["candidate_error"]
    assert report["baseline"]["status"] == "not_run"


@pytest.mark.parametrize("report_name", ["app.py", "task.json"])
def test_maintenance_report_cannot_overwrite_acceptance_inputs(tmp_path: Path, report_name: str):
    workspace, candidate, manifest = _fixture(tmp_path)
    (candidate / "app.py").write_text("def add(left, right):\n    return left + right\n", encoding="utf-8")
    target = workspace / report_name if report_name == "app.py" else manifest
    original = target.read_bytes()

    result = _run(manifest, candidate, target)

    assert result.exit_code == 2
    assert target.read_bytes() == original


def test_maintenance_report_cannot_overwrite_an_existing_external_file(tmp_path: Path):
    _workspace, candidate, manifest = _fixture(tmp_path)
    (candidate / "app.py").write_text("def add(left, right):\n    return left + right\n", encoding="utf-8")
    target = tmp_path / "existing-report.json"
    target.write_text("keep me\n", encoding="utf-8")

    result = _run(manifest, candidate, target)

    assert result.exit_code == 2
    assert target.read_text(encoding="utf-8") == "keep me\n"


def test_maintenance_help_exposes_the_acceptance_entrypoint():
    result = runner.invoke(app, ["maintain", "--help"])

    assert result.exit_code == 0
    assert "accept" in result.stdout
    assert "disposable local copies" in result.stdout
