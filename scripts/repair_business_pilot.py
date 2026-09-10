#!/usr/bin/env python3
"""Run the bounded RinCode business maintenance repair pilot.

The pilot deliberately keeps the model and evaluator on opposite sides of a
small trust boundary.  A model sees a temporary workspace containing one
source file and its public test.  The hidden test stays in a separate scoring
workspace; after the turn, the model workspace changes are overlaid into that
copy so protected-file edits are visible to ``accept_maintenance_task``.

The default command runs the local four-fixture pilot using the configured
RinCode provider.  ``--preflight-only`` runs all deterministic checks without an
LLM call.  ``--task-id`` is available for an explicitly selected diagnostic
run; the formal pilot command runs all four fixtures in one ledger.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import difflib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_EVIDENCE_ROOT = PROJECT_ROOT / "docs" / "evidence"
DEFAULT_PREFLIGHT_PATH = DEFAULT_EVIDENCE_ROOT / "rincode-business-preflight.json"
DEFAULT_RESULTS_PATH = DEFAULT_EVIDENCE_ROOT / "rincode-business-results.json"
DEFAULT_RAW_PATH = DEFAULT_EVIDENCE_ROOT / "rincode-business-raw-outcomes.jsonl"
DEFAULT_ARTIFACT_ROOT = DEFAULT_EVIDENCE_ROOT / "rincode-business-patches"

SCHEMA = "rincode.business.repair-pilot.v1"
PREFLIGHT_SCHEMA = "rincode.business.repair-pilot-preflight.v1"
FIXTURE_MODULE = "benchmarks.repair_business.fixtures"

MAX_TURNS = 8
MAX_PROVIDER_CALLS_PER_TURN = 6
MAX_PROVIDER_CALLS_TOTAL = 48
TURN_TIMEOUT_SECONDS = 120.0
PUBLIC_CHECK_TIMEOUT_SECONDS = 15.0
ACCEPTANCE_TIMEOUT_SECONDS = 15.0
# Five normal loop iterations plus the optional tools-disabled synthesis call
# gives the runtime a hard six-call ceiling without relying on the provider to
# stop at the right time.
AGENT_MAX_ITERATIONS = 5

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rincode.providers.base import LLMProvider  # noqa: E402


class PilotError(RuntimeError):
    """Base error for pilot setup and accounting failures."""


class PilotBudgetExceededError(PilotError):
    """Raised before a provider request would exceed a pilot budget."""


@dataclass(frozen=True)
class PilotLimits:
    max_turns: int = MAX_TURNS
    max_provider_calls_per_turn: int = MAX_PROVIDER_CALLS_PER_TURN
    max_provider_calls_total: int = MAX_PROVIDER_CALLS_TOTAL
    turn_timeout_seconds: float = TURN_TIMEOUT_SECONDS

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_turns": self.max_turns,
            "max_provider_calls_per_turn": self.max_provider_calls_per_turn,
            "max_provider_calls_total": self.max_provider_calls_total,
            "turn_timeout_seconds": self.turn_timeout_seconds,
        }


@dataclass
class BudgetLedger:
    """Shared hard accounting for turn and provider-call budgets."""

    limits: PilotLimits = field(default_factory=PilotLimits)
    turn_count: int = 0
    total_provider_calls: int = 0
    current_turn_calls: int = 0
    current_turn: dict[str, Any] | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def begin_turn(self, task_id: str, attempt: int) -> None:
        if self.current_turn is not None:
            raise PilotError("a previous pilot turn is still open")
        if self.turn_count >= self.limits.max_turns:
            raise PilotBudgetExceededError("maximum pilot turns exhausted")
        self.turn_count += 1
        self.current_turn_calls = 0
        self.current_turn = {
            "turn": self.turn_count,
            "task_id": task_id,
            "attempt": attempt,
            "provider_calls": 0,
            "started_at": _now(),
        }

    def reserve_provider_call(self, *, model: str | None) -> None:
        if self.current_turn is None:
            raise PilotError("provider call outside an active pilot turn")
        if self.current_turn_calls >= self.limits.max_provider_calls_per_turn:
            raise PilotBudgetExceededError("per-turn provider-call budget exhausted")
        if self.total_provider_calls >= self.limits.max_provider_calls_total:
            raise PilotBudgetExceededError("pilot provider-call budget exhausted")
        self.current_turn_calls += 1
        self.total_provider_calls += 1
        self.current_turn["provider_calls"] = self.current_turn_calls
        self.calls.append(
            {
                "ordinal": self.total_provider_calls,
                "turn": self.turn_count,
                "task_id": self.current_turn["task_id"],
                "attempt": self.current_turn["attempt"],
                "model": model,
                "provider_dispatched": True,
                "started_at": _now(),
            }
        )

    def record_blocked_call(self, *, model: str | None, error: BaseException) -> None:
        """Record a denied request without counting it as provider dispatched."""

        turn = self.current_turn or {}
        self.calls.append(
            {
                "ordinal": len(self.calls) + 1,
                "turn": turn.get("turn"),
                "task_id": turn.get("task_id"),
                "attempt": turn.get("attempt"),
                "model": model,
                "provider_dispatched": False,
                "succeeded": False,
                "error": _safe_exception(error),
                "started_at": _now(),
            }
        )

    def finish_turn(self) -> dict[str, Any]:
        if self.current_turn is None:
            raise PilotError("no active pilot turn")
        result = dict(self.current_turn)
        result["finished_at"] = _now()
        result["provider_calls"] = self.current_turn_calls
        self.current_turn = None
        self.current_turn_calls = 0
        return result

    def snapshot(self) -> dict[str, Any]:
        return {
            "turns": self.turn_count,
            "provider_calls": self.total_provider_calls,
            "max_turns": self.limits.max_turns,
            "max_provider_calls_per_turn": self.limits.max_provider_calls_per_turn,
            "max_provider_calls_total": self.limits.max_provider_calls_total,
            "budget_ok": self.turn_count <= self.limits.max_turns
            and self.total_provider_calls <= self.limits.max_provider_calls_total,
        }


class BudgetedProvider(LLMProvider):
    """Count every underlying single provider attempt used by AgentLoop.

    AgentLoop calls ``chat_with_retry``.  Inheriting the runtime's
    ``LLMProvider.chat_with_retry`` while implementing ``chat`` makes each
    retry a separately accounted request.  No provider response content is
    persisted, which keeps credentials and unrelated prompt data out of the
    evidence files.
    """

    _CHAT_RETRY_DELAYS = (0, 0, 0)

    def __init__(self, inner: Any, ledger: BudgetLedger):
        if not isinstance(inner, LLMProvider):
            raise TypeError("pilot provider must be an LLMProvider")
        super().__init__(api_key=getattr(inner, "api_key", None), api_base=getattr(inner, "api_base", None))
        self._inner = inner
        self._ledger = ledger
        self.generation = getattr(inner, "generation", self.generation)

    def get_default_model(self) -> str:
        return self._inner.get_default_model()

    def classify_error(self, exc: BaseException | None = None, content: str | None = None):
        classify = getattr(self._inner, "classify_error", None)
        if callable(classify):
            try:
                return classify(exc, content)
            except TypeError:
                return classify(exc=exc, content=content)
        from rincode.providers.base import LLMProvider

        return LLMProvider.classify_error(exc, content)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ):
        try:
            self._ledger.reserve_provider_call(model=model or self.get_default_model())
        except PilotBudgetExceededError as exc:
            self._ledger.record_blocked_call(model=model or self.get_default_model(), error=exc)
            raise
        try:
            response = await self._inner.chat(
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                reasoning_effort=reasoning_effort,
                tool_choice=tool_choice,
            )
        except asyncio.CancelledError:
            self._record_call_failure(_safe_exception(asyncio.CancelledError()))
            raise
        except Exception as exc:
            self._record_call_failure(_safe_exception(exc))
            raise
        self._record_response(response)
        return response

    def _record_call_failure(self, error: str) -> None:
        if self._ledger.calls:
            self._ledger.calls[-1].update(
                {
                    "succeeded": False,
                    "error": error,
                    "finished_at": _now(),
                }
            )

    def _record_response(self, response: Any) -> None:
        usage = _usage_record(getattr(response, "usage", None))
        succeeded = getattr(response, "finish_reason", None) != "error"
        classification = getattr(response, "error_classification", None)
        if self._ledger.calls:
            self._ledger.calls[-1].update(
                {
                    "succeeded": succeeded,
                    "finish_reason": getattr(response, "finish_reason", None),
                    "actual_model": getattr(response, "model", None),
                    "usage": usage,
                    "usage_complete": _usage_complete(usage),
                    "error_category": getattr(classification, "category", None),
                    "finished_at": _now(),
                }
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@dataclass(frozen=True)
class NormalizedFixture:
    """Adapter for the worker-owned ``Fixture`` schema."""

    task_id: str
    prompt: str
    source: str
    reference: str
    public_test: str
    hidden_test: str
    source_filename: str = "source.py"


@dataclass
class PreparedTask:
    fixture: NormalizedFixture
    root: Path
    model_workspace: Path
    scoring_workspace: Path
    candidate_workspace: Path
    acceptance_task: Any
    model_initial_snapshot: dict[str, bytes]


def load_fixtures() -> tuple[NormalizedFixture, ...]:
    """Load the worker-owned fixed ``Fixture`` schema."""

    from benchmarks.repair_business.fixtures import fixtures

    raw_items = fixtures()
    normalized: list[NormalizedFixture] = []
    for raw in raw_items:
        values = {name: getattr(raw, name, None) for name in ("task_id", "prompt", "source", "reference", "public_test", "hidden_test")}
        missing = [key for key in values if not isinstance(values[key], str)]
        if missing:
            raise PilotError(f"fixture {getattr(raw, 'task_id', '<unknown>')} missing fields: {', '.join(missing)}")
        normalized.append(
            NormalizedFixture(
                task_id=values["task_id"],
                prompt=values["prompt"],
                source=values["source"],
                reference=values["reference"],
                public_test=values["public_test"],
                hidden_test=values["hidden_test"],
                source_filename="source.py",
            )
        )
    if len(normalized) != 4:
        raise PilotError(f"business pilot requires exactly four fixtures, got {len(normalized)}")
    if len({item.task_id for item in normalized}) != len(normalized):
        raise PilotError("repair fixture task_id values must be unique")
    return tuple(normalized)


def prepare_task(fixture: NormalizedFixture, root: Path) -> PreparedTask:
    """Create separated model and scoring trees for one fixture."""

    from rincode.maintenance.acceptance import MaintenanceTask

    task_root = root / fixture.task_id
    model_workspace = task_root / "model-workspace"
    scoring_workspace = task_root / "scoring-workspace"
    candidate_workspace = task_root / "candidate-scoring"
    model_workspace.mkdir(parents=True)
    scoring_workspace.mkdir(parents=True)
    _write_fixture_source(model_workspace, fixture.source_filename, fixture.source)
    (model_workspace / "test_public.py").write_text(fixture.public_test, encoding="utf-8")

    _write_fixture_source(scoring_workspace, fixture.source_filename, fixture.source)
    (scoring_workspace / "test_public.py").write_text(fixture.public_test, encoding="utf-8")
    # This file is deliberately never copied into model_workspace.
    (scoring_workspace / "test_hidden.py").write_text(fixture.hidden_test, encoding="utf-8")
    (scoring_workspace / "verify_hidden.py").write_text(
        _hidden_verifier_source(expected_tests=_expected_hidden_tests(fixture.hidden_test)),
        encoding="utf-8",
    )

    acceptance_task = MaintenanceTask(
        workspace=scoring_workspace,
        command=(sys.executable, "verify_hidden.py"),
        allowed_paths=(fixture.source_filename,),
        protected_paths=("test_hidden.py", "verify_hidden.py", "test_public.py"),
        timeout_seconds=ACCEPTANCE_TIMEOUT_SECONDS,
    )
    return PreparedTask(
        fixture=fixture,
        root=task_root,
        model_workspace=model_workspace,
        scoring_workspace=scoring_workspace,
        candidate_workspace=candidate_workspace,
        acceptance_task=acceptance_task,
        model_initial_snapshot=_tree_snapshot(model_workspace),
    )


def _write_fixture_source(root: Path, filename: str, source: str) -> None:
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _expected_hidden_tests(source: str) -> int:
    """Return the fixture-declared pytest test count from its source AST."""

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise PilotError(f"hidden fixture is not valid Python: {exc}") from exc
    count = sum(
        1
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    )
    if count <= 0:
        raise PilotError("hidden fixture must declare at least one pytest test function")
    return count


def _hidden_verifier_source(*, expected_tests: int) -> str:
    """Create a protected scorer that validates JUnit execution counts."""

    return f'''from __future__ import annotations

import sys
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

EXPECTED_TESTS = {expected_tests}
REPORT = Path(".rincode") / "hidden-junit.xml"


def main() -> int:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.unlink(missing_ok=True)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "test_hidden.py", f"--junitxml={{REPORT}}"],
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 1
    try:
        root = ET.parse(REPORT).getroot()
        suite = root.find("testsuite") if root.tag == "testsuites" else root
        if suite is None:
            return 1
        tests = int(suite.attrib.get("tests", "-1"))
        failures = int(suite.attrib.get("failures", "-1"))
        errors = int(suite.attrib.get("errors", "-1"))
        skipped = int(suite.attrib.get("skipped", "-1"))
        passed = tests - failures - errors - skipped
    except (OSError, ET.ParseError, ValueError):
        return 1
    # A process that exits 0 without collecting/running tests must fail here.
    return 0 if completed.returncode == 0 and tests == EXPECTED_TESTS and failures == 0 and errors == 0 and skipped == 0 and passed == EXPECTED_TESTS else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def run_fixture_preflight(fixtures: Sequence[NormalizedFixture], root: Path) -> dict[str, Any]:
    """Check baseline failure, reference success, and public-test tampering rejection."""

    from rincode.maintenance.acceptance import _run_command, accept_maintenance_task

    reports: list[dict[str, Any]] = []
    for fixture in fixtures:
        prepared = prepare_task(fixture, root)
        baseline = _run_command(
            prepared.scoring_workspace,
            prepared.acceptance_task.command,
            prepared.acceptance_task.timeout_seconds,
        )

        reference_candidate = prepared.root / "reference-scoring"
        shutil.copytree(prepared.scoring_workspace, reference_candidate)
        _write_fixture_source(reference_candidate, fixture.source_filename, fixture.reference)
        reference_report = accept_maintenance_task(prepared.acceptance_task, reference_candidate)

        test_only_candidate = prepared.root / "public-test-only-scoring"
        shutil.copytree(prepared.scoring_workspace, test_only_candidate)
        (test_only_candidate / "test_public.py").write_text(
            "# public-test-only mutation; acceptance must reject this tree\n",
            encoding="utf-8",
        )
        test_only_report = accept_maintenance_task(prepared.acceptance_task, test_only_candidate)
        reports.append(
            {
                "task_id": fixture.task_id,
                "task_source": "local_fixture",
                "source_filename": fixture.source_filename,
                "baseline": _command_summary(baseline),
                "reference": _acceptance_summary(reference_report),
                "public_test_only": _acceptance_summary(test_only_report),
                "checks": {
                    "baseline_failed": baseline["status"] == "failed",
                    "reference_passed": reference_report["accepted"] is True,
                    "public_test_only_rejected": test_only_report["accepted"] is False
                    and test_only_report["reason"] == "candidate_changed_protected_path",
                },
            }
        )
    return {
        "schema": "rincode.business.repair-pilot-fixture-preflight.v1",
        "task_source": "local_fixture",
        "fixture_module": FIXTURE_MODULE,
        "tasks": reports,
        "all_checks_passed": all(all(item["checks"].values()) for item in reports),
    }


def build_preflight_report(
    *,
    fixtures: Sequence[NormalizedFixture],
    config_path: Path | None = None,
    limits: PilotLimits | None = None,
) -> tuple[dict[str, Any], Any | None]:
    """Build local and provider readiness evidence before any model turn."""

    from rincode.cli._helpers import make_provider
    from rincode.config.loader import get_config_path, load_config

    limits = limits or PilotLimits()
    provider: Any | None = None
    provider_error: str | None = None
    config: Any | None = None
    try:
        config = load_config(config_path)
        provider = make_provider(config)
    except Exception as exc:
        provider_error = _safe_exception(exc, _config_secrets(config))

    if config is not None:
        model = config.agents.defaults.model
        provider_name = config.get_provider_name(model)
        provider_cfg = config.get_provider(model)
        provider_info = {
            "model": model,
            "provider": provider_name,
            "provider_class": type(provider).__name__ if provider is not None else None,
            "api_key_configured": bool(getattr(provider_cfg, "api_key", None)),
            "api_base_configured": bool(config.get_api_base(model)),
            "config_path": str(config_path or get_config_path()),
            "config_digest": _path_digest(config_path or get_config_path()),
            "error": provider_error,
        }
    else:
        provider_info = {
            "model": None,
            "provider": None,
            "provider_class": None,
            "api_key_configured": False,
            "api_base_configured": False,
            "config_path": str(config_path or get_config_path()),
            "config_digest": _path_digest(config_path or get_config_path()),
            "error": provider_error,
        }

    with tempfile.TemporaryDirectory(prefix="rincode-business-preflight-") as temp_dir:
        fixture_checks = run_fixture_preflight(fixtures, Path(temp_dir))

    report = {
        "schema": PREFLIGHT_SCHEMA,
        "created_at": _now(),
        "task_source": "local_fixture",
        "fixture_module": FIXTURE_MODULE,
        "fixture_count": len(fixtures),
        "limits": limits.as_dict(),
        "provider": provider_info,
        "provider_ready": provider is not None,
        "fixture_checks": fixture_checks,
        "passed": provider is not None and fixture_checks["all_checks_passed"],
        "hidden_tests_exposed_to_model": False,
        "comparison_arms": 1,
    }
    return report, provider


async def run_pilot(
    fixtures: Sequence[NormalizedFixture],
    *,
    provider: Any,
    limits: PilotLimits | None = None,
    task_ids: Iterable[str] | None = None,
    artifact_root: Path | None = None,
    ledger: BudgetLedger | None = None,
    progress_output: Path | None = None,
    progress_raw_output: Path | None = None,
    artifact_output: Path | None = None,
) -> dict[str, Any]:
    """Run model turns and independent hidden acceptance sequentially."""

    limits = limits or PilotLimits()
    selected_ids = set(task_ids) if task_ids is not None else {fixture.task_id for fixture in fixtures}
    selected = [fixture for fixture in fixtures if fixture.task_id in selected_ids]
    if not selected:
        raise PilotError("no matching repair fixture selected")
    if len(selected) > limits.max_turns:
        raise PilotError("selected fixtures exceed the pilot turn budget")

    ledger = ledger or getattr(provider, "_ledger", None) or BudgetLedger(limits)
    if ledger.limits != limits:
        raise PilotError("provider and pilot use different budget limits")
    if not isinstance(provider, BudgetedProvider):
        provider = BudgetedProvider(provider, ledger)
    turn_results: list[dict[str, Any]] = []
    task_results: list[dict[str, Any]] = []
    temp_context: tempfile.TemporaryDirectory[str] | None = None
    if artifact_root is None:
        temp_context = tempfile.TemporaryDirectory(prefix="rincode-business-pilot-")
        run_root = Path(temp_context.name)
    else:
        run_root = artifact_root.resolve()
        run_root.mkdir(parents=True, exist_ok=True)

    try:
        artifact_parent = Path(artifact_output or DEFAULT_ARTIFACT_ROOT).expanduser().resolve()
        if temp_context is not None and _is_within(artifact_parent, run_root):
            raise PilotError("candidate artifacts must be outside the temporary model tree")
        candidate_artifact_root = _create_artifact_run_root(artifact_parent)
        for fixture in selected:
            prepared = prepare_task(fixture, run_root)
            task_result = await _run_one_task(prepared, provider, ledger, limits)
            task_result["candidate_artifacts"] = _persist_candidate_artifacts(prepared, candidate_artifact_root)
            task_results.append(task_result)
            turn_results.extend(task_result["turns"])
            if progress_output is not None:
                partial = _pilot_report(
                    selected_count=len(selected),
                    task_results=task_results,
                    turn_results=turn_results,
                    ledger=ledger,
                    complete=False,
                    candidate_artifact_root=candidate_artifact_root,
                )
                write_json(progress_output, partial)
                if progress_raw_output is not None:
                    write_raw_outcomes(progress_raw_output, partial)
    finally:
        if temp_context is not None:
            temp_context.cleanup()

    return _pilot_report(
        selected_count=len(selected),
        task_results=task_results,
        turn_results=turn_results,
        ledger=ledger,
        complete=True,
        candidate_artifact_root=candidate_artifact_root,
    )


def _pilot_report(
    *,
    selected_count: int,
    task_results: list[dict[str, Any]],
    turn_results: list[dict[str, Any]],
    ledger: BudgetLedger,
    complete: bool,
    candidate_artifact_root: Path | None = None,
) -> dict[str, Any]:
    first_successes = sum(1 for item in task_results if item.get("first_attempt_accepted") is True)
    final_successes = sum(1 for item in task_results if item.get("final_accepted") is True)
    return {
        "schema": SCHEMA,
        "created_at": _now(),
        "task_source": "local_fixture",
        "fixture_module": FIXTURE_MODULE,
        "comparison_arms": 1,
        "tasks_total": selected_count,
        "tasks_completed": len(task_results),
        "first_attempt_success_count": first_successes,
        "final_success_count": final_successes,
        "turns": turn_results,
        "tasks": task_results,
        "budget": ledger.snapshot(),
        "provider_calls": ledger.calls,
        "candidate_artifact_root": (
            str(candidate_artifact_root.expanduser().resolve()) if candidate_artifact_root is not None else None
        ),
        "result_complete": complete and _result_complete(task_results, turn_results, ledger),
    }


async def _run_one_task(
    prepared: PreparedTask,
    provider: Any,
    ledger: BudgetLedger,
    limits: PilotLimits,
) -> dict[str, Any]:
    fixture = prepared.fixture
    state_root = prepared.root / "runtime-state"
    state_root.mkdir(parents=True, exist_ok=True)
    turn_records: list[dict[str, Any]] = []
    public_checks: list[dict[str, Any]] = []
    model_changed_paths: list[list[str]] = []
    turn_status = "not_started"
    agent: Any | None = None
    first_acceptance: dict[str, Any] | None = None
    try:
        agent = _build_agent(provider, prepared.model_workspace, state_root)
        for attempt in (1, 2):
            if attempt == 2 and not public_checks:
                break
            if attempt == 2 and public_checks[-1]["status"] == "passed":
                break
            try:
                ledger.begin_turn(fixture.task_id, attempt)
            except PilotBudgetExceededError as exc:
                turn_records.append({"task_id": fixture.task_id, "attempt": attempt, "status": "budget_exceeded", "error": _safe_exception(exc)})
                break
            before = _tree_snapshot(prepared.model_workspace)
            collector = _TurnCollector()
            turn_text = fixture.prompt if attempt == 1 else _visible_feedback(fixture, public_checks[-1])
            req = _turn_request(fixture.task_id, turn_text)
            started = time.monotonic()
            try:
                outcome = await asyncio.wait_for(
                    agent.run_turn(req, collector.emit, lambda: [], stream=False, text_sink={}),
                    timeout=limits.turn_timeout_seconds,
                )
                turn_status = "completed"
                outcome_summary = _outcome_summary(outcome)
                error = None
            except asyncio.TimeoutError as exc:
                turn_status = "timeout"
                outcome_summary = None
                error = _safe_exception(exc) or "turn timeout"
            except Exception as exc:
                turn_status = "api_error"
                outcome_summary = None
                error = _safe_exception(exc, _provider_secrets(provider))
            finally:
                ledger_turn = ledger.finish_turn()
            after = _tree_snapshot(prepared.model_workspace)
            changed = _changed_paths(before, after)
            model_changed_paths.append(changed)
            turn_record = {
                "task_id": fixture.task_id,
                "attempt": attempt,
                "status": turn_status,
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "provider_calls": ledger_turn["provider_calls"],
                "changed_paths": changed,
                "tool_events": collector.tool_events,
                "tool_event_count": len(collector.tool_events),
                "outcome": outcome_summary,
                "error": error,
            }
            turn_records.append(turn_record)
            public = _run_public_check(prepared.model_workspace)
            public_checks.append(public)
            turn_record["public_check"] = _public_summary(public)
            if attempt == 1:
                # Score the source after the first model turn so the report can
                # distinguish first-pass from eventual acceptance.  The hidden
                # verdict is never sent back in the visible feedback.
                first_acceptance = _score_model_source(prepared)
            if public["status"] == "passed":
                break
            if attempt == 1:
                continue
        candidate_report = _score_model_source(prepared)
    except Exception as exc:
        candidate_report = {
            "accepted": False,
            "reason": "runner_error",
            "error": _safe_exception(exc),
        }
    finally:
        if agent is not None:
            try:
                await agent.close()
            except Exception as exc:
                turn_records.append(
                    {
                        "task_id": fixture.task_id,
                        "attempt": None,
                        "status": "runtime_close_error",
                        "error": _safe_exception(exc, _provider_secrets(provider)),
                    }
                )
    final_accepted = candidate_report.get("accepted") is True
    first_attempt_accepted = bool(first_acceptance and first_acceptance.get("accepted") is True)
    return {
        "task_id": fixture.task_id,
        "task_source": "local_fixture",
        "source_filename": fixture.source_filename,
        "turns": turn_records,
        "public_checks": [_public_summary(item) for item in public_checks],
        "model_changed_paths": model_changed_paths,
        "acceptance": _acceptance_summary(candidate_report),
        "first_attempt_accepted": first_attempt_accepted,
        "final_accepted": final_accepted,
        "first_attempt_score": _acceptance_summary(first_acceptance) if first_acceptance else None,
    }


def _build_agent(provider: Any, workspace: Path, state: Path):
    from rincode.agent.loop import AgentLoop
    from rincode.sandbox import SandboxConfig
    from rincode.session.manager import SessionManager

    # The caller supplies a BudgetedProvider already.  A direct executor keeps
    # the model's writable tree local and bounded by AgentLoop's workspace guard.
    return AgentLoop(
        provider=provider,
        workspace=workspace,
        state=state,
        model=provider.get_default_model(),
        max_iterations=AGENT_MAX_ITERATIONS,
        context_window_tokens=32768,
        restrict_to_workspace=True,
        sandbox_config=SandboxConfig(backend="none"),
        session_manager=SessionManager(state),
        disabled_tools=["web_search", "web_fetch", "message", "spawn", "ask_user", "cron", "read_skill"],
        interactive=False,
    )


def _turn_request(task_id: str, text: str):
    from rincode.spine.message import ChatType, Source
    from rincode.spine.turn import Origin, TurnRequest

    return TurnRequest(
        origin=Origin.USER,
        source=Source(channel="repair_business", chat_id=task_id, sender_id="pilot", chat_type=ChatType.DM),
        conversation=f"repair_business:{task_id}",
        text=text,
    )


class _TurnCollector:
    def __init__(self) -> None:
        self.tool_events: list[dict[str, Any]] = []

    async def emit(self, event: Any) -> None:
        if type(event).__name__ != "ToolEvent":
            return
        phase = getattr(getattr(event, "phase", None), "value", getattr(event, "phase", None))
        self.tool_events.append(
            {
                "phase": phase,
                "name": getattr(event, "name", None),
                "tool_call_id_present": bool(getattr(event, "tool_call_id", None)),
            }
        )


def _run_public_check(workspace: Path) -> dict[str, Any]:
    from rincode.maintenance.acceptance import _run_command

    result = _run_command(workspace, (sys.executable, "test_public.py"), PUBLIC_CHECK_TIMEOUT_SECONDS)
    return {
        "status": result["status"],
        "exit_code": result["exit_code"],
        "timed_out": result["timed_out"],
        "duration_ms": result["duration_ms"],
        "output_digest": _text_digest(result.get("stdout", "") + result.get("stderr", "")),
        "output": (result.get("stdout", "") + "\n" + result.get("stderr", ""))[-1600:],
    }


def _score_model_source(prepared: PreparedTask) -> dict[str, Any]:
    from rincode.maintenance.acceptance import accept_maintenance_task

    if prepared.candidate_workspace.exists():
        shutil.rmtree(prepared.candidate_workspace)
    shutil.copytree(prepared.scoring_workspace, prepared.candidate_workspace)
    symlinks = _find_symlinks(prepared.model_workspace)
    if symlinks:
        return {
            "accepted": False,
            "reason": "candidate_changed_disallowed_path",
            "changed_paths": symlinks,
            "protected_changes": [],
            "disallowed_changes": symlinks,
        }
    current = _tree_snapshot(prepared.model_workspace)
    changed = _changed_paths(prepared.model_initial_snapshot, current)
    for relative in changed:
        source = prepared.model_workspace / relative
        destination = prepared.candidate_workspace / relative
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        elif destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
    report = accept_maintenance_task(prepared.acceptance_task, prepared.candidate_workspace)
    return report


def _persist_candidate_artifacts(prepared: PreparedTask, artifact_root: Path) -> dict[str, Any]:
    """Persist the model source and its unified diff before the run tree is removed."""

    fixture = prepared.fixture
    relative_source = Path(fixture.source_filename)
    if relative_source.is_absolute() or ".." in relative_source.parts:
        raise PilotError(f"fixture source path must stay workspace-relative: {fixture.source_filename!r}")
    source_path = prepared.model_workspace / relative_source
    task_artifact_root = Path(artifact_root).expanduser().resolve() / fixture.task_id
    if task_artifact_root.exists() or task_artifact_root.is_symlink():
        raise PilotError(f"candidate artifact path already exists: {task_artifact_root}")
    task_artifact_root.mkdir(parents=True, exist_ok=True)

    baseline_bytes = prepared.model_initial_snapshot.get(relative_source.as_posix())
    if baseline_bytes is None:
        raise PilotError(f"baseline source is missing from model snapshot: {fixture.source_filename}")
    baseline_sha256 = _bytes_digest(baseline_bytes)
    patch_path = task_artifact_root / f"{relative_source.name}.patch"

    if source_path.is_symlink():
        return {
            "status": "not_saved",
            "reason": "candidate_source_symlink",
            "source_path": None,
            "patch_path": None,
            "baseline_source_sha256": baseline_sha256,
            "source_sha256": None,
            "patch_sha256": None,
        }

    candidate_bytes: bytes | None = source_path.read_bytes() if source_path.is_file() else None
    if candidate_bytes is not None:
        source_artifact = task_artifact_root / relative_source
        source_artifact.parent.mkdir(parents=True, exist_ok=True)
        source_artifact.write_bytes(candidate_bytes)
        source_path_value: str | None = str(source_artifact.resolve())
        source_sha256 = _bytes_digest(candidate_bytes)
    else:
        source_path_value = None
        source_sha256 = None

    patch_bytes = b"".join(
        difflib.diff_bytes(
            difflib.unified_diff,
            baseline_bytes.splitlines(keepends=True),
            (candidate_bytes or b"").splitlines(keepends=True),
            fromfile=f"baseline/{fixture.source_filename}".encode("utf-8"),
            tofile=f"candidate/{fixture.source_filename}".encode("utf-8"),
            lineterm=b"\n",
        )
    )
    patch_path.write_bytes(patch_bytes)
    return {
        "status": "saved",
        "reason": "candidate_source_persisted",
        "source_path": source_path_value,
        "patch_path": str(patch_path.resolve()),
        "baseline_source_sha256": baseline_sha256,
        "source_sha256": source_sha256,
        "patch_sha256": _bytes_digest(patch_bytes),
    }


def _create_artifact_run_root(parent: Path) -> Path:
    """Allocate a durable, collision-free directory for one pilot run."""

    parent.mkdir(parents=True, exist_ok=True)
    run_root = parent / f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}-{uuid.uuid4().hex[:12]}"
    run_root.mkdir()
    return run_root


def _visible_feedback(fixture: NormalizedFixture, public: dict[str, Any]) -> str:
    # Public output is fixture-owned and may be shown to the model.  Hidden
    # acceptance status/output is intentionally absent here.
    return (
        f"上一轮已完成源文件修改，但开发检查未通过。请检查当前 {fixture.source_filename}，"
        "只修复源文件后再次运行 python test_public.py。\n"
        f"开发检查状态：{public['status']}，退出码：{public.get('exit_code')}，"
        f"输出摘要哈希：{public.get('output_digest')}。\n"
        f"开发检查输出：\n{public.get('output', '')[-1200:]}\n"
        "不要修改 test_public.py。"
    )


def _result_complete(tasks: list[dict[str, Any]], turns: list[dict[str, Any]], ledger: BudgetLedger) -> bool:
    expected = {item["task_id"] for item in tasks}
    seen = {item["task_id"] for item in turns if item.get("task_id")}
    return len(tasks) > 0 and expected == seen and ledger.current_turn is None


def write_json(path: Path, value: dict[str, Any]) -> Path:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def write_raw_outcomes(path: Path, report: dict[str, Any]) -> Path:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for task in report.get("tasks", []):
            handle.write(json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def execute_cli(args: argparse.Namespace) -> int:
    fixtures = load_fixtures()
    limits = PilotLimits()
    selected_ids = [args.task_id] if args.task_id else None
    preflight, provider = build_preflight_report(fixtures=fixtures, config_path=args.config, limits=limits)
    preflight_path = write_json(args.preflight_output, preflight)
    print(json.dumps({"preflight": str(preflight_path), "passed": preflight["passed"]}, ensure_ascii=False))
    if not preflight["passed"]:
        return 2
    if args.preflight_only:
        return 0
    if provider is None:
        return 2

    ledger = BudgetLedger(limits)
    tracked_provider = BudgetedProvider(provider, ledger)
    # The one-task and four-task commands both write complete pilot reports;
    # there is no synthetic patch fallback when the API cannot be used.
    try:
        report = asyncio.run(
            run_pilot(
                fixtures,
                provider=tracked_provider,
                limits=limits,
                task_ids=selected_ids,
                ledger=ledger,
                progress_output=args.output,
                progress_raw_output=args.raw_output,
                artifact_output=args.artifact_output,
            )
        )
    except Exception as exc:
        report = {
            "schema": SCHEMA,
            "created_at": _now(),
            "task_source": "local_fixture",
            "comparison_arms": 1,
            "tasks_total": len(selected_ids or fixtures),
            "first_attempt_success_count": 0,
            "final_success_count": 0,
            "tasks": [],
            "turns": [],
            "budget": ledger.snapshot(),
            "provider_calls": ledger.calls,
            "candidate_artifact_root": str(args.artifact_output.expanduser().resolve()),
            "result_complete": False,
            "runner_error": _safe_exception(exc, _provider_secrets(provider)),
        }
    result_path = write_json(args.output, report)
    raw_path = write_raw_outcomes(args.raw_output, report)
    print(
        json.dumps(
            {
                "results": str(result_path),
                "raw_outcomes": str(raw_path),
                "tasks_total": report.get("tasks_total", 0),
                "first_attempt_success_count": report.get("first_attempt_success_count", 0),
                "final_success_count": report.get("final_success_count", 0),
                "provider_calls": report.get("budget", {}).get("provider_calls", 0),
                "result_complete": report.get("result_complete", False),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.get("result_complete") is True else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None, help="optional RinCode config path")
    parser.add_argument("--task-id", choices=[item.task_id for item in load_fixtures()], help="run one fixture")
    parser.add_argument("--preflight-only", action="store_true", help="run deterministic checks and provider construction only")
    parser.add_argument("--preflight-output", type=Path, default=DEFAULT_PREFLIGHT_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_RESULTS_PATH)
    parser.add_argument("--raw-output", type=Path, default=DEFAULT_RAW_PATH)
    parser.add_argument("--artifact-output", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return execute_cli(build_parser().parse_args(argv))
    except Exception as exc:
        print(json.dumps({"error": _safe_exception(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


def _usage_record(raw: Any) -> dict[str, int | None]:
    usage = raw if isinstance(raw, dict) else {}
    return {
        "prompt_tokens": _optional_int(usage.get("prompt_tokens")),
        "completion_tokens": _optional_int(usage.get("completion_tokens")),
        "total_tokens": _optional_int(usage.get("total_tokens")),
    }


def _usage_complete(usage: dict[str, int | None]) -> bool:
    return all(usage.get(key) is not None for key in ("prompt_tokens", "completion_tokens", "total_tokens"))


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _outcome_summary(outcome: Any) -> dict[str, Any]:
    usage = getattr(outcome, "usage", None)
    return {
        "explicit_reply": getattr(outcome, "explicit_reply", None),
        "tool_calls": getattr(outcome, "tool_calls", None),
        "tool_failures": getattr(outcome, "tool_failures", None),
        "usage": {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        },
    }


def _command_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "exit_code": result.get("exit_code"),
        "timed_out": result.get("timed_out"),
        "duration_ms": result.get("duration_ms"),
    }


def _acceptance_summary(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    baseline = result.get("baseline", {})
    candidate = result.get("candidate_result", result.get("candidate", {}))
    return {
        "accepted": result.get("accepted") is True,
        "reason": result.get("reason"),
        "changed_paths": result.get("changed_paths", []),
        "protected_changes": result.get("protected_changes", []),
        "disallowed_changes": result.get("disallowed_changes", []),
        "baseline": {"status": baseline.get("status"), "exit_code": baseline.get("exit_code")},
        "candidate": {
            "status": candidate.get("status"),
            "exit_code": candidate.get("exit_code"),
            "timed_out": candidate.get("timed_out"),
        },
        "workspace_unchanged": result.get("workspace_unchanged"),
    }


def _public_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "exit_code": result.get("exit_code"),
        "timed_out": result.get("timed_out"),
        "duration_ms": result.get("duration_ms"),
        "output_digest": result.get("output_digest"),
    }


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = [name for name in directories if name not in {".git", ".rincode", "__pycache__", ".pytest_cache"}]
        for name in names:
            path = current_path / name
            if path.is_file() and not path.is_symlink():
                files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def _find_symlinks(root: Path) -> list[str]:
    links: list[str] = []
    for current, directories, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in (*directories, *names):
            path = current_path / name
            if path.is_symlink():
                links.append(path.relative_to(root).as_posix())
    return sorted(links)


def _changed_paths(before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _bytes_digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _path_digest(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.expanduser().resolve().read_bytes()).hexdigest()
    except OSError:
        return None


def _config_secrets(config: Any | None) -> list[str]:
    if config is None:
        return []
    values: list[str] = []
    try:
        providers = getattr(config, "providers", None)
        fields = getattr(providers, "model_fields", {})
        for field_name in fields:
            provider = getattr(providers, field_name, None)
            key = getattr(provider, "api_key", None)
            base = getattr(provider, "api_base", None)
            if isinstance(key, str) and key:
                values.append(key)
            if isinstance(base, str) and base:
                values.append(base)
    except TypeError:
        pass
    return values


def _provider_secrets(provider: Any) -> list[str]:
    return [value for value in (getattr(provider, "api_key", None), getattr(provider, "api_base", None)) if isinstance(value, str) and value]


def _safe_exception(exc: BaseException, secrets: Iterable[str] = ()) -> str:
    message = f"{type(exc).__name__}: {exc}".strip()
    for secret in secrets:
        message = message.replace(secret, "[REDACTED]")
    return message[:2000]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "AGENT_MAX_ITERATIONS",
    "BudgetLedger",
    "BudgetedProvider",
    "NormalizedFixture",
    "PilotBudgetExceededError",
    "PilotLimits",
    "PreparedTask",
    "build_preflight_report",
    "load_fixtures",
    "prepare_task",
    "run_fixture_preflight",
    "run_pilot",
    "write_json",
]


if __name__ == "__main__":
    raise SystemExit(main())
