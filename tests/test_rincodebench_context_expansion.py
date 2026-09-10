from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.rincodebench.packs.context import (
    ContextTrack,
    SealedContextTaskVerifier,
    load_context_tasks,
)
from benchmarks.rincodebench.packs.context import tasks as context_tasks

_CONTEXT_ROOT = Path(__file__).resolve().parents[1] / "benchmarks" / "rincodebench" / "tasks" / "context"
_EXPANSION_PATH = _CONTEXT_ROOT / "expansion_20260909.json"
_STALE_DECISIONS = {
    "ctx-expand-synthetic-export": {"mode": "full"},
    "ctx-expand-access-correction": {"status": "denied"},
    "ctx-expand-rollout-boundary": {"canary_percent": 25},
    "ctx-expand-retry-floor": {"retry_limit": 3},
    "ctx-expand-flag-rollback": {"rollout_state": "active"},
    "ctx-expand-allowlist-order": {"allowlist": ["alpha"]},
    "ctx-expand-retention-cutoff": {"retention_days": 30},
}


def _load_expansion_tasks():
    raw = json.loads(_EXPANSION_PATH.read_text(encoding="utf-8"))
    assert raw["schema"] == "rincode.rincodebench.context-tasks.v1"
    return tuple(
        context_tasks._parse_task(ContextTrack.FORMAL, entry)  # noqa: SLF001 - exercise the existing task parser
        for entry in raw["tasks"]
    )


EXPANSION_TASKS = _load_expansion_tasks()


def _value_atoms(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(atom for item in value for atom in _value_atoms(item))
    if isinstance(value, str):
        return (value,)
    return (str(value),)


def _answer_messages(task) -> tuple[str, ...]:
    history = task.materialize_history()
    return tuple(
        str(message.get("content", ""))
        for message in history
        if message.get("role") == "user"
        and str(message.get("content", "")).startswith(
            ("[required-early-constraint]", "[decision-v1]", "[decision-v2-supersedes-v1]"),
        )
    )


def test_expansion_loader_accepts_seven_independent_synthetic_tasks() -> None:
    formal_ids = {task.task_id for task in load_context_tasks(ContextTrack.FORMAL)}
    calibration_ids = {task.task_id for task in load_context_tasks(ContextTrack.CALIBRATION)}
    expansion_ids = {task.task_id for task in EXPANSION_TASKS}

    assert len(EXPANSION_TASKS) == 7
    assert len(expansion_ids) == 7
    assert expansion_ids.isdisjoint(formal_ids | calibration_ids)
    assert all(task.track is ContextTrack.FORMAL for task in EXPANSION_TASKS)
    assert all("DEMO-" in " ".join(_answer_messages(task)) for task in EXPANSION_TASKS)
    assert all(task.materialize_history() for task in EXPANSION_TASKS)


def test_expansion_prompts_keep_complete_answers_in_context_and_out_of_final_prompt() -> None:
    for task in EXPANSION_TASKS:
        expected = json.loads(task.expected_path.read_text(encoding="utf-8"))
        answer_text = "\n".join(_answer_messages(task)).casefold()
        final_prompt = task.final_prompt.casefold()

        assert task.early_constraint.casefold() in answer_text
        assert task.superseded_before.casefold() in answer_text
        assert task.superseded_after.casefold() in answer_text
        assert task.early_constraint.casefold() not in final_prompt
        assert task.superseded_before.casefold() not in final_prompt
        assert task.superseded_after.casefold() not in final_prompt
        for value in expected.values():
            for atom in _value_atoms(value):
                if len(atom) > 1:
                    assert atom.casefold() not in final_prompt
                    assert atom.casefold() in answer_text


@pytest.mark.parametrize(
    "task",
    EXPANSION_TASKS,
    ids=lambda task: task.task_id,
)
@pytest.mark.asyncio
async def test_expansion_golden_artifact_passes_sealed_verifier(task, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    artifact = workspace / task.artifact_path
    artifact.parent.mkdir(parents=True)
    artifact.write_text(task.expected_path.read_text(encoding="utf-8"), encoding="utf-8")

    execution = await SealedContextTaskVerifier.capture(task).verify(workspace)

    assert execution.infrastructure_error is None
    assert execution.result.state.value == "passed"


@pytest.mark.parametrize(
    ("task", "mutation"),
    [
        pytest.param(task, "missing_constraint", id=f"{task.task_id}-missing-constraint")
        for task in EXPANSION_TASKS
    ]
    + [
        pytest.param(task, "stale_decision", id=f"{task.task_id}-stale-decision")
        for task in EXPANSION_TASKS
    ],
)
@pytest.mark.asyncio
async def test_expansion_rejects_omitted_constraint_and_superseded_decision(
    task,
    mutation: str,
    tmp_path: Path,
) -> None:
    artifact_payload = json.loads(task.expected_path.read_text(encoding="utf-8"))
    if mutation == "missing_constraint":
        del artifact_payload[task.constraint_keys[0]]
    else:
        artifact_payload.update(_STALE_DECISIONS[task.task_id])

    artifact = tmp_path / task.artifact_path
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps(artifact_payload), encoding="utf-8")

    execution = await SealedContextTaskVerifier.capture(task).verify(tmp_path)

    assert execution.infrastructure_error is None
    assert execution.result.state.value == "failed"


@pytest.mark.parametrize(
    "task",
    EXPANSION_TASKS,
    ids=lambda task: task.task_id,
)
@pytest.mark.asyncio
async def test_expansion_rejects_forbidden_path_creation(task, tmp_path: Path) -> None:
    artifact = tmp_path / task.artifact_path
    artifact.parent.mkdir(parents=True)
    artifact.write_text(task.expected_path.read_text(encoding="utf-8"), encoding="utf-8")
    forbidden = tmp_path / task.forbidden_paths[0]
    forbidden.parent.mkdir(parents=True)
    forbidden.write_text("synthetic forbidden fixture", encoding="utf-8")

    execution = await SealedContextTaskVerifier.capture(task).verify(tmp_path)

    assert execution.infrastructure_error is None
    assert execution.result.state.value == "failed"
    assert execution.result.findings == (f"forbidden_path:{task.forbidden_paths[0]}",)
