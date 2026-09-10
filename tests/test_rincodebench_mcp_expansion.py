from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from benchmarks.rincodebench.fixtures.mcp import catalog_definitions, receipt_payload
from benchmarks.rincodebench.packs.tool_mcp import (
    SealedMCPReceiptVerifier,
    ToolMCPTrack,
    load_tool_mcp_tasks,
)
from benchmarks.rincodebench.packs.tool_mcp.models import ToolMCPTask
from benchmarks.rincodebench.packs.tool_mcp.tasks import _parse_task
from benchmarks.rincodebench.records import VerificationState

EXPANSION_PATH = (
    Path(__file__).resolve().parents[1] / "benchmarks" / "rincodebench" / "tasks" / "tool_mcp" / "expansion_20260909.json"
)


def _load_expansion() -> tuple[ToolMCPTask, ...]:
    raw = json.loads(EXPANSION_PATH.read_text(encoding="utf-8"))
    assert raw["schema"] == "rincode.rincodebench.tool-mcp-tasks.v1"
    entries = raw["tasks"]
    return tuple(_parse_task(ToolMCPTrack.FORMAL, entry) for entry in entries)


def _write_receipts(path: Path, receipts: tuple[dict, ...]) -> None:
    path.write_text(
        "".join(json.dumps(receipt, sort_keys=True) + "\n" for receipt in receipts),
        encoding="utf-8",
    )


def test_expansion_has_seven_distinct_tasks_and_fourteen_new_targets() -> None:
    tasks = _load_expansion()
    existing_ids = {
        task.task_id for track in (ToolMCPTrack.FORMAL, ToolMCPTrack.CALIBRATION) for task in load_tool_mcp_tasks(track)
    }

    assert len(tasks) == 7
    assert len({task.task_id for task in tasks}) == 7
    assert {task.task_id for task in tasks}.isdisjoint(existing_ids)
    assert all(1 <= len(task.targets) <= 3 for task in tasks)
    assert sum(len(task.targets) for task in tasks) == 14
    assert 36 + sum(len(task.targets) for task in tasks) == 50


def test_expansion_targets_match_real_catalog_schema_and_explicit_prompts() -> None:
    tasks = _load_expansion()
    definitions = {definition.name: definition for definition in catalog_definitions()}
    expected_operations = {"inspect", "transform", "validate"}

    for task in tasks:
        for target in task.targets:
            assert target.tool_name in definitions
            schema = definitions[target.tool_name].parameters
            assert target.tool_name.removeprefix("catalog_probe_") in task.prompt
            assert schema["additionalProperties"] is False
            assert target.arguments.keys() == set(schema["required"])
            assert isinstance(target.arguments["resource"], str)
            assert target.arguments["resource"]
            assert len(target.arguments["resource"]) >= schema["properties"]["resource"]["minLength"]
            assert target.arguments["resource"] in task.prompt
            assert target.arguments["operation"] in schema["properties"]["operation"]["enum"]
            assert target.arguments["operation"] in expected_operations
            assert target.arguments["operation"] in task.prompt
            assert isinstance(target.arguments["value"], int)
            assert not isinstance(target.arguments["value"], bool)
            assert schema["properties"]["value"]["minimum"] <= target.arguments["value"]
            assert target.arguments["value"] <= schema["properties"]["value"]["maximum"]
            assert str(target.arguments["value"]) in task.prompt

    same_tool_task = tasks[1]
    assert [target.tool_name for target in same_tool_task.targets] == [
        "catalog_probe_02",
        "catalog_probe_02",
    ]
    assert same_tool_task.targets[0].arguments != same_tool_task.targets[1].arguments
    assert {target.arguments["value"] for target in tasks[3].targets} == {0, 10_000}
    assert tasks[4].targets[0].arguments["resource"] == tasks[4].targets[1].arguments["resource"]
    assert tasks[4].targets[0].arguments["operation"] != tasks[4].targets[1].arguments["operation"]


def test_expansion_verifier_accepts_expected_multiset_in_any_order(tmp_path: Path) -> None:
    for task in _load_expansion():
        receipt_path = tmp_path / f"{task.task_id}.jsonl"
        expected = tuple(target.expected_receipt for target in task.targets)
        _write_receipts(receipt_path, tuple(reversed(expected)))

        result, observed = SealedMCPReceiptVerifier.capture(task).verify(receipt_path)

        assert result.state is VerificationState.PASSED
        assert result.metrics == {
            "expected_receipt_count": len(task.targets),
            "observed_receipt_count": len(task.targets),
        }
        assert Counter(json.dumps(item, sort_keys=True) for item in observed) == Counter(
            json.dumps(item, sort_keys=True) for item in expected
        )


def test_expansion_verifier_rejects_missing_extra_duplicate_and_wrong_parameter(
    tmp_path: Path,
) -> None:
    task = _load_expansion()[1]
    expected = tuple(target.expected_receipt for target in task.targets)
    verifier = SealedMCPReceiptVerifier.capture(task)

    cases = {
        "missing": expected[:1],
        "extra": expected + (_extra_receipt(task),),
        "duplicate": expected + (expected[0],),
        "wrong-parameter": (
            expected[0],
            receipt_payload(
                task.targets[1].tool_name,
                {
                    **dict(task.targets[1].arguments),
                    "value": task.targets[1].arguments["value"] + 1,
                },
            ),
        ),
    }
    expected_findings = {
        "missing": ("expected_mcp_receipt_missing",),
        "extra": ("unexpected_mcp_receipt",),
        "duplicate": ("unexpected_mcp_receipt",),
        "wrong-parameter": ("expected_mcp_receipt_missing",),
    }

    for name, receipts in cases.items():
        receipt_path = tmp_path / f"{name}.jsonl"
        _write_receipts(receipt_path, receipts)

        result, _ = verifier.verify(receipt_path)

        assert result.state is VerificationState.FAILED
        assert result.findings == expected_findings[name]


def _extra_receipt(task: ToolMCPTask) -> dict:
    """Return a valid catalog receipt that is outside this task's target multiset."""

    target = task.targets[0]
    arguments = {
        **dict(target.arguments),
        "resource": f"{target.arguments['resource']}-extra",
    }
    return receipt_payload(target.tool_name, arguments)
