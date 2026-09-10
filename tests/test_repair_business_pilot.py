from __future__ import annotations

import asyncio
import hashlib
import shutil
from pathlib import Path

import pytest

import scripts.repair_business_pilot as pilot
from benchmarks.repair_business.fixtures import fixtures
from rincode.maintenance.acceptance import _run_command
from rincode.providers.base import LLMProvider
from scripts.repair_business_pilot import (
    BudgetLedger,
    NormalizedFixture,
    PilotBudgetExceededError,
    PilotError,
    PilotLimits,
    _command_summary,
    _persist_candidate_artifacts,
    _score_model_source,
    prepare_task,
    run_fixture_preflight,
    run_pilot,
)


def test_worker_fixture_schema_is_four_local_tasks() -> None:
    values = fixtures()

    assert len(values) == 4
    assert [value.task_id for value in values] == [
        "pagination-boundary",
        "stable-deduplication",
        "safe-relative-path",
        "async-cleanup",
    ]
    assert all(value.source and value.reference and value.public_test and value.hidden_test for value in values)


def test_fixture_preflight_checks_baseline_reference_and_public_tampering(tmp_path: Path) -> None:
    normalized = tuple(
        NormalizedFixture(
            task_id=value.task_id,
            prompt=value.prompt,
            source=value.source,
            reference=value.reference,
            public_test=value.public_test,
            hidden_test=value.hidden_test,
        )
        for value in fixtures()
    )

    report = run_fixture_preflight(normalized, tmp_path)

    assert report["all_checks_passed"] is True
    assert len(report["tasks"]) == 4
    for task in report["tasks"]:
        assert task["task_source"] == "local_fixture"
        assert task["checks"] == {
            "baseline_failed": True,
            "reference_passed": True,
            "public_test_only_rejected": True,
        }
        assert task["public_test_only"]["accepted"] is False
        assert task["public_test_only"]["reason"] == "candidate_changed_protected_path"


def test_hidden_verifier_rejects_source_that_exits_zero_without_junit(tmp_path: Path) -> None:
    fixture = NormalizedFixture(
        task_id="exit-zero-source",
        prompt="修复 source.py",
        source="import os\n\ndef answer():\n    os._exit(0)\n",
        reference="def answer():\n    return 1\n",
        public_test="from source import answer\n\nif __name__ == '__main__':\n    assert answer() == 1\n",
        hidden_test=(
            "from source import answer\n\n\n"
            "def test_hidden_answer():\n"
            "    assert answer() == 1\n"
        ),
    )
    prepared = prepare_task(fixture, tmp_path)

    baseline = _run_command(
        prepared.scoring_workspace,
        prepared.acceptance_task.command,
        prepared.acceptance_task.timeout_seconds,
    )
    candidate = prepared.root / "reference-scoring"
    shutil.copytree(prepared.scoring_workspace, candidate)
    (candidate / "source.py").write_text(fixture.reference, encoding="utf-8")
    from rincode.maintenance.acceptance import accept_maintenance_task

    accepted = accept_maintenance_task(prepared.acceptance_task, candidate)

    assert _command_summary(baseline)["status"] == "failed"
    assert accepted["accepted"] is True
    assert accepted["candidate_result"]["status"] == "passed"


def test_model_public_test_mutation_is_rejected(tmp_path: Path) -> None:
    fixture = next(
        NormalizedFixture(
            task_id=value.task_id,
            prompt=value.prompt,
            source=value.source,
            reference=value.reference,
            public_test=value.public_test,
            hidden_test=value.hidden_test,
        )
        for value in fixtures()
        if value.task_id == "pagination-boundary"
    )
    prepared = prepare_task(fixture, tmp_path)
    (prepared.model_workspace / "source.py").write_text(fixture.reference, encoding="utf-8")
    (prepared.model_workspace / "test_public.py").write_text("# test removed\n", encoding="utf-8")

    report = _score_model_source(prepared)

    assert report["accepted"] is False
    assert report["reason"] == "candidate_changed_protected_path"
    assert "test_public.py" in report["protected_changes"]


def test_model_workspace_keeps_hidden_files_outside_agent_view(tmp_path: Path) -> None:
    value = fixtures()[0]
    fixture = NormalizedFixture(
        task_id=value.task_id,
        prompt=value.prompt,
        source=value.source,
        reference=value.reference,
        public_test=value.public_test,
        hidden_test=value.hidden_test,
    )
    prepared = prepare_task(fixture, tmp_path)

    assert not (prepared.model_workspace / "test_hidden.py").exists()
    assert not (prepared.model_workspace / "verify_hidden.py").exists()


def test_budget_ledger_rejects_seventh_turn_call_and_preserves_bounds() -> None:
    limits = PilotLimits(max_turns=1, max_provider_calls_per_turn=2, max_provider_calls_total=2)
    ledger = BudgetLedger(limits)
    ledger.begin_turn("task", 1)
    ledger.reserve_provider_call(model="model")
    ledger.reserve_provider_call(model="model")

    with pytest.raises(PilotBudgetExceededError):
        ledger.reserve_provider_call(model="model")

    finished = ledger.finish_turn()
    assert finished["provider_calls"] == 2
    assert ledger.snapshot()["budget_ok"] is True
    assert len(ledger.calls) == 2


class _NoCallProvider(LLMProvider):
    def get_default_model(self) -> str:
        return "test-model"

    async def chat(self, *args, **kwargs):
        raise AssertionError("artifact persistence test must not call a provider")


def test_candidate_artifacts_survive_temporary_tree_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value = fixtures()[0]
    fixture = NormalizedFixture(
        task_id=value.task_id,
        prompt=value.prompt,
        source=value.source,
        reference=value.reference,
        public_test=value.public_test,
        hidden_test=value.hidden_test,
    )
    artifact_root = tmp_path / "artifacts"
    prior_artifact = artifact_root / "prior-run" / "sentinel.txt"
    prior_artifact.parent.mkdir(parents=True)
    prior_artifact.write_text("keep me", encoding="utf-8")

    async def fake_run_one_task(prepared, provider, ledger, limits):
        (prepared.model_workspace / "source.py").write_text(fixture.reference, encoding="utf-8")
        return {
            "task_id": fixture.task_id,
            "turns": [{"task_id": fixture.task_id, "provider_calls": 0}],
            "first_attempt_accepted": True,
            "final_accepted": True,
        }

    monkeypatch.setattr(pilot, "_run_one_task", fake_run_one_task)
    report = asyncio.run(
        run_pilot(
            (fixture,),
            provider=_NoCallProvider(),
            limits=PilotLimits(max_turns=1),
            artifact_output=artifact_root,
        )
    )

    assert report["result_complete"] is True
    artifacts = report["tasks"][0]["candidate_artifacts"]
    source_path = Path(artifacts["source_path"])
    patch_path = Path(artifacts["patch_path"])
    assert source_path.is_file()
    assert patch_path.is_file()
    source_bytes = source_path.read_bytes()
    patch_bytes = patch_path.read_bytes()
    assert artifacts["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert artifacts["baseline_source_sha256"] == hashlib.sha256(fixture.source.encode()).hexdigest()
    assert artifacts["patch_sha256"] == hashlib.sha256(patch_bytes).hexdigest()
    assert source_bytes == fixture.reference.encode()
    assert b"--- baseline/source.py" in patch_bytes
    assert b"+++ candidate/source.py" in patch_bytes
    assert report["candidate_artifact_root"].startswith(str(artifact_root.resolve()))
    assert Path(report["candidate_artifact_root"]) != artifact_root.resolve()
    assert prior_artifact.read_text(encoding="utf-8") == "keep me"


def test_existing_task_artifact_is_not_deleted(tmp_path: Path) -> None:
    value = fixtures()[0]
    fixture = NormalizedFixture(
        task_id=value.task_id,
        prompt=value.prompt,
        source=value.source,
        reference=value.reference,
        public_test=value.public_test,
        hidden_test=value.hidden_test,
    )
    prepared = prepare_task(fixture, tmp_path / "run")
    (prepared.model_workspace / "source.py").write_text(fixture.reference, encoding="utf-8")
    task_artifact_root = tmp_path / "artifacts" / fixture.task_id
    sentinel = task_artifact_root / "sentinel.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("keep me", encoding="utf-8")

    with pytest.raises(PilotError, match="already exists"):
        _persist_candidate_artifacts(prepared, tmp_path / "artifacts")

    assert sentinel.read_text(encoding="utf-8") == "keep me"
