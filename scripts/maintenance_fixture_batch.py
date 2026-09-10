"""Run the fixed local maintainer acceptance fixture batch.

This batch is a deterministic contract check for the acceptance entry-point. It
uses no Provider, model, network, or benchmark task data. Each case is reported,
including rejected candidates, so a passing aggregate cannot hide a failure.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from rincode.maintenance.acceptance import MaintenanceTask, accept_maintenance_task, write_acceptance_report

CaseBuilder = Callable[[Path], tuple[MaintenanceTask, Path, dict[str, Any]]]


def _base_fixture(root: Path, *, fixed: bool = False) -> tuple[Path, Path]:
    workspace = root / "workspace"
    workspace.mkdir()
    operator = "+" if fixed else "-"
    (workspace / "app.py").write_text(f"def add(left, right):\n    return left {operator} right\n", encoding="utf-8")
    tests = workspace / "tests"
    tests.mkdir()
    (tests / "test_bug.py").write_text(
        "from app import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    candidate = root / "candidate"
    shutil.copytree(workspace, candidate)
    return workspace, candidate


def _pytest_task(workspace: Path, *, timeout: float = 2.0, fixed: bool = False) -> MaintenanceTask:
    return MaintenanceTask(
        workspace=workspace,
        command=(sys.executable, "-m", "pytest", "-q", "tests/test_bug.py"),
        allowed_paths=("app.py",),
        protected_paths=("tests/",),
        timeout_seconds=timeout,
    )


def _repair_pass(root: Path) -> tuple[MaintenanceTask, Path, dict[str, Any]]:
    workspace, candidate = _base_fixture(root)
    (candidate / "app.py").write_text("def add(left, right):\n    return left + right\n", encoding="utf-8")
    return _pytest_task(workspace), candidate, {"accepted": True, "reason": "baseline_failed_candidate_passed"}


def _test_tamper(root: Path) -> tuple[MaintenanceTask, Path, dict[str, Any]]:
    workspace, candidate = _base_fixture(root)
    (candidate / "tests/test_bug.py").write_text("def test_pseudo_fix():\n    pass\n", encoding="utf-8")
    return _pytest_task(workspace), candidate, {"accepted": False, "reason": "candidate_changed_protected_path"}


def _command_tamper(root: Path) -> tuple[MaintenanceTask, Path, dict[str, Any]]:
    workspace, candidate = _base_fixture(root)
    verifier = workspace / "checks/verify.py"
    verifier.parent.mkdir()
    verifier.write_text("from app import add\n\nif add(2, 3) != 5:\n    raise SystemExit(1)\n", encoding="utf-8")
    (candidate / "checks").mkdir()
    shutil.copy2(verifier, candidate / "checks/verify.py")
    (candidate / "checks/verify.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    task = MaintenanceTask(
        workspace=workspace,
        command=(sys.executable, "checks/verify.py"),
        allowed_paths=("app.py",),
        protected_paths=("tests/", "checks/"),
    )
    return task, candidate, {"accepted": False, "reason": "candidate_changed_protected_path"}


def _disallowed_change(root: Path) -> tuple[MaintenanceTask, Path, dict[str, Any]]:
    workspace, candidate = _base_fixture(root)
    (candidate / "README.md").write_text("claim\n", encoding="utf-8")
    return _pytest_task(workspace), candidate, {"accepted": False, "reason": "candidate_changed_disallowed_path"}


def _candidate_timeout(root: Path) -> tuple[MaintenanceTask, Path, dict[str, Any]]:
    workspace, candidate = _base_fixture(root)
    script = workspace / "tests/timeout_check.py"
    script.write_text(
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
    shutil.copy2(script, candidate / "tests/timeout_check.py")
    (candidate / "app.py").write_text("def add(left, right):\n    return left + right\n", encoding="utf-8")
    task = MaintenanceTask(
        workspace=workspace,
        command=(sys.executable, "tests/timeout_check.py"),
        allowed_paths=("app.py",),
        protected_paths=("tests/",),
        timeout_seconds=0.1,
    )
    return task, candidate, {"accepted": False, "reason": "candidate_timeout"}


def _baseline_passes(root: Path) -> tuple[MaintenanceTask, Path, dict[str, Any]]:
    workspace, candidate = _base_fixture(root, fixed=True)
    (candidate / "app.py").write_text(
        "def add(left, right):\n    return left + right\n# harmless candidate change\n",
        encoding="utf-8",
    )
    return _pytest_task(workspace, fixed=True), candidate, {"accepted": False, "reason": "baseline_did_not_fail"}


def _symlink_tree(root: Path) -> tuple[MaintenanceTask, Path, dict[str, Any]]:
    workspace, candidate = _base_fixture(root)
    outside = root / "outside"
    outside.mkdir()
    (candidate / "linked").symlink_to(outside, target_is_directory=True)
    return _pytest_task(workspace), candidate, {"accepted": False, "reason": "invalid_candidate_tree"}


def _public_result(report: dict[str, Any]) -> dict[str, Any]:
    baseline = report["baseline"]
    candidate = report["candidate_result"]
    return {
        "accepted": report["accepted"],
        "reason": report["reason"],
        "changed_paths": report["changed_paths"],
        "baseline": {
            "status": baseline["status"],
            "exit_code": baseline["exit_code"],
        },
        "candidate": {
            "status": candidate["status"],
            "exit_code": candidate["exit_code"],
            "timed_out": candidate["timed_out"],
            "leader_reaped": candidate["leader_reaped"],
            "process_group_terminated": candidate["process_group_terminated"],
        },
    }


def run_batch() -> dict[str, Any]:
    builders: tuple[tuple[str, CaseBuilder], ...] = (
        ("repair_pass", _repair_pass),
        ("test_tamper", _test_tamper),
        ("command_tamper", _command_tamper),
        ("disallowed_change", _disallowed_change),
        ("candidate_timeout", _candidate_timeout),
        ("baseline_passes", _baseline_passes),
        ("symlink_tree", _symlink_tree),
    )
    cases: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="rincode-maintenance-fixtures-") as temp_dir:
        root = Path(temp_dir)
        for case_id, builder in builders:
            case_root = root / case_id
            case_root.mkdir()
            task, candidate, expected = builder(case_root)
            actual = _public_result(accept_maintenance_task(task, candidate))
            cases.append(
                {
                    "id": case_id,
                    "expected": expected,
                    "actual": actual,
                    "case_passed": actual["accepted"] == expected["accepted"]
                    and actual["reason"] == expected["reason"],
                }
            )
    return {
        "schema": "rincode.maintenance.fixture-batch.v1",
        "execution": "deterministic local fixture; no Provider, model, network, or LLM judge",
        "cases": cases,
        "all_cases_passed": all(case["case_passed"] for case in cases),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="JSON output path")
    args = parser.parse_args(argv)
    report = run_batch()
    write_acceptance_report(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["all_cases_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
