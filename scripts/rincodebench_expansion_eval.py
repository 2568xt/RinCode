"""Run the frozen 14-task RinCodeBench expansion campaign.

The campaign reuses the native Ship gate and runtime runners while replacing
only the formal Context and Tool/MCP task sets.  Preflight reads redacted local
configuration metadata and compiles both plans without resolving or calling a
Provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

from benchmarks.rincodebench.budget import BudgetGuardedProvider
from benchmarks.rincodebench.campaign import (
    CampaignError,
    CampaignMode,
    CampaignOutcome,
    DeterministicGateResult,
    ResolvedProvider,
    default_campaign_services,
    estimate_worst_case_cost,
    load_campaign_suite,
    run_campaign,
)
from benchmarks.rincodebench.canonical import to_primitive
from benchmarks.rincodebench.packs.context import (
    ContextPack,
    ContextTrack,
    RuntimeContextTrialRunner,
    load_context_tasks,
)
from benchmarks.rincodebench.packs.context.models import ContextTask
from benchmarks.rincodebench.packs.context.tasks import _parse_task as _parse_context_task
from benchmarks.rincodebench.packs.tool_mcp import (
    MCPRuntimeTrialRunner,
    ToolMCPPack,
    ToolMCPTrack,
)
from benchmarks.rincodebench.packs.tool_mcp.models import ToolMCPTask
from benchmarks.rincodebench.packs.tool_mcp.tasks import _parse_task as _parse_tool_mcp_task
from benchmarks.rincodebench.registry import PackRegistry
from rincode.providers.base import LLMProvider
from scripts.expanded_native_eval import (
    _apply_evaluation_provider_overrides,
    _evaluation_request_overrides,
    read_local_provider_summary,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE_PATH = _REPOSITORY_ROOT / "benchmarks" / "rincodebench" / "suites" / "resume_expansion_20260909.yaml"
DEFAULT_OUTPUT_ROOT = Path(".rincode") / "evidence" / "rincodebench-resume-expansion-20260909"
_CONTEXT_EXPANSION_TASKS = (
    _REPOSITORY_ROOT / "benchmarks" / "rincodebench" / "tasks" / "context" / "expansion_20260909.json"
)
_TOOL_MCP_EXPANSION_TASKS = (
    _REPOSITORY_ROOT / "benchmarks" / "rincodebench" / "tasks" / "tool_mcp" / "expansion_20260909.json"
)
_CONTEXT_EXPANSION_PACK_ID = "context-expansion-20260909"
_TOOL_MCP_EXPANSION_PACK_ID = "tool-mcp-expansion-20260909"
_EXPANSION_TASK_COUNT = 7
_EXPECTED_PACKS = {
    CampaignMode.CALIBRATION: (
        "context-calibration",
        "tool-mcp-calibration",
    ),
    CampaignMode.FORMAL: (
        _CONTEXT_EXPANSION_PACK_ID,
        _TOOL_MCP_EXPANSION_PACK_ID,
    ),
}
_TASK_SCHEMAS = {
    "context": "rincode.rincodebench.context-tasks.v1",
    "tool_mcp": "rincode.rincodebench.tool-mcp-tasks.v1",
}


def _load_expansion_tasks(
    path: Path,
    *,
    schema: str,
    parser: Callable[[ContextTrack | ToolMCPTrack, object], ContextTask | ToolMCPTask],
    track: ContextTrack | ToolMCPTrack,
    historical_task_ids: set[str],
) -> tuple[ContextTask | ToolMCPTask, ...]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignError(f"cannot load expansion task file {path}: {exc}") from exc
    if not isinstance(raw, Mapping) or raw.get("schema") != schema:
        raise CampaignError(f"unsupported expansion task schema in {path}")
    entries = raw.get("tasks")
    if not isinstance(entries, list) or len(entries) != _EXPANSION_TASK_COUNT:
        raise CampaignError(
            f"{path.name} requires exactly {_EXPANSION_TASK_COUNT} expansion tasks",
        )
    try:
        tasks = tuple(parser(track, entry) for entry in entries)
    except (TypeError, ValueError, KeyError) as exc:
        raise CampaignError(f"invalid expansion task in {path}: {exc}") from exc
    task_ids = {task.task_id for task in tasks}
    if len(task_ids) != len(tasks):
        raise CampaignError(f"duplicate expansion task ID in {path}")
    overlap = task_ids & historical_task_ids
    if overlap:
        raise CampaignError(
            "expansion task IDs overlap the frozen historical tasks: " + ", ".join(sorted(overlap)),
        )
    return tasks


def load_expansion_context_tasks(
    path: Path = _CONTEXT_EXPANSION_TASKS,
) -> tuple[ContextTask, ...]:
    historical = {
        task.task_id for track in (ContextTrack.CALIBRATION, ContextTrack.FORMAL) for task in load_context_tasks(track)
    }
    tasks = _load_expansion_tasks(
        path,
        schema=_TASK_SCHEMAS["context"],
        parser=_parse_context_task,
        track=ContextTrack.FORMAL,
        historical_task_ids=historical,
    )
    return tuple(task for task in tasks if isinstance(task, ContextTask))


def load_expansion_tool_mcp_tasks(
    path: Path = _TOOL_MCP_EXPANSION_TASKS,
) -> tuple[ToolMCPTask, ...]:
    from benchmarks.rincodebench.packs.tool_mcp import load_tool_mcp_tasks

    historical = {
        task.task_id for track in (ToolMCPTrack.CALIBRATION, ToolMCPTrack.FORMAL) for task in load_tool_mcp_tasks(track)
    }
    tasks = _load_expansion_tasks(
        path,
        schema=_TASK_SCHEMAS["tool_mcp"],
        parser=_parse_tool_mcp_task,
        track=ToolMCPTrack.FORMAL,
        historical_task_ids=historical,
    )
    return tuple(task for task in tasks if isinstance(task, ToolMCPTask))


def _runtime_pack_objects(mode: CampaignMode) -> tuple[object, ...]:
    if CampaignMode(mode) is CampaignMode.CALIBRATION:
        return (
            ContextPack(ContextTrack.CALIBRATION),
            ToolMCPPack(ToolMCPTrack.CALIBRATION),
        )
    return (
        ContextPack(
            ContextTrack.FORMAL,
            tasks=load_expansion_context_tasks(),
            pack_id=_CONTEXT_EXPANSION_PACK_ID,
        ),
        ToolMCPPack(
            ToolMCPTrack.FORMAL,
            tasks=load_expansion_tool_mcp_tasks(),
            pack_id=_TOOL_MCP_EXPANSION_PACK_ID,
        ),
    )


def build_expansion_registry(
    mode: CampaignMode,
    resolved: ResolvedProvider,
) -> PackRegistry:
    """Build Context and Tool/MCP native runners on the shared budget ledger."""

    if resolved.config is None or resolved.rincode_config is None:
        raise CampaignError("resolved Provider is missing Runtime configuration")
    provider = resolved.provider
    if not isinstance(provider, LLMProvider):
        raise CampaignError("resolved Provider is not an LLMProvider")
    if (
        resolved.budget_ledger is None
        or not isinstance(provider, BudgetGuardedProvider)
        or provider.ledger is not resolved.budget_ledger
    ):
        raise CampaignError("expansion runners must share the campaign Provider ledger")

    calibration = CampaignMode(mode) is CampaignMode.CALIBRATION
    context_track = ContextTrack.CALIBRATION if calibration else ContextTrack.FORMAL
    tool_track = ToolMCPTrack.CALIBRATION if calibration else ToolMCPTrack.FORMAL
    context_kwargs: dict[str, object] = {
        "runner": RuntimeContextTrialRunner(
            config=resolved.config,
            rincode_config=resolved.rincode_config,
            provider=provider,
        ),
    }
    tool_kwargs: dict[str, object] = {
        "runner": MCPRuntimeTrialRunner(
            provider=provider,
            model=(resolved.configured_model or resolved.provider_name + "/" + resolved.model),
        ),
    }
    if calibration:
        context_pack = ContextPack(context_track, **context_kwargs)
        tool_pack = ToolMCPPack(tool_track, **tool_kwargs)
    else:
        context_pack = ContextPack(
            context_track,
            tasks=load_expansion_context_tasks(),
            pack_id=_CONTEXT_EXPANSION_PACK_ID,
            **context_kwargs,
        )
        tool_pack = ToolMCPPack(
            tool_track,
            tasks=load_expansion_tool_mcp_tasks(),
            pack_id=_TOOL_MCP_EXPANSION_PACK_ID,
            **tool_kwargs,
        )
    registry = PackRegistry()
    registry.register(context_pack)
    registry.register(tool_pack)
    return registry


def _validate_pack_matrix(suite) -> dict[str, object]:
    """Validate the frozen 32-calibration/84-formal denominators."""

    details: dict[str, object] = {}
    plans: dict[CampaignMode, object] = {}
    for mode in (CampaignMode.CALIBRATION, CampaignMode.FORMAL):
        track = suite.calibration if mode is CampaignMode.CALIBRATION else suite.formal
        expected_pack_ids = _EXPECTED_PACKS[mode]
        if track.pack_ids != expected_pack_ids:
            raise CampaignError(
                f"{mode.value} must use the expansion matrix: {track.pack_ids!r} != {expected_pack_ids!r}",
            )
        packs = _runtime_pack_objects(mode)
        definitions = tuple(pack.definition() for pack in packs)
        actual_pack_ids = tuple(definition.pack_id for definition in definitions)
        if actual_pack_ids != track.pack_ids:
            raise CampaignError(
                f"{mode.value} registry pack IDs drifted: {actual_pack_ids!r} != {track.pack_ids!r}",
            )
        from benchmarks.rincodebench.plan import compile_plan
        from benchmarks.rincodebench.schema import ExperimentSpec

        spec = ExperimentSpec(
            suite=track.suite,
            repetitions=track.repetitions,
            pack_ids=track.pack_ids,
            output_root=Path(".rincode") / "preflight" / mode.value,
            identity={"kind": "expansion-pack-preflight"},
        )
        try:
            plan = compile_plan(spec, packs)
        except (KeyError, ValueError) as exc:
            raise CampaignError(f"cannot compile {mode.value} pack matrix: {exc}") from exc
        if len(plan.trials) != track.expected_trials:
            raise CampaignError(
                f"{mode.value} Trial denominator drift: {len(plan.trials)} != {track.expected_trials}",
            )
        if len(plan.retrieval_cases) != track.expected_retrieval_cases:
            raise CampaignError(
                f"{mode.value} Retrieval denominator drift: {len(plan.retrieval_cases)} != {track.expected_retrieval_cases}",
            )
        by_pack = {
            definition.pack_id: {
                "tasks": len(definition.tasks),
                "variants": len(definition.variants),
                "pairs": len(definition.pairs),
                "trials": sum(1 for trial in plan.trials if trial.key.pack_id == definition.pack_id),
                "comparison_blocks": sum(
                    1 for block in plan.comparison_blocks if block.key.pack_id == definition.pack_id
                ),
                "retrieval_cases": sum(
                    len(retrieval_suite.queries) * len(retrieval_suite.configurations)
                    for retrieval_suite in definition.retrieval_suites
                ),
                "task_set_digest": definition.identity.get("task_set_digest"),
            }
            for definition in definitions
        }
        expected_trials_by_pack = dict(track.expected_trials_by_pack)
        expected_blocks_by_pack = dict(track.expected_comparison_blocks_by_pack)
        expected_retrieval_by_pack = dict(track.expected_retrieval_cases_by_pack)
        for pack_id, counts in by_pack.items():
            expected = {
                "trials": expected_trials_by_pack[pack_id],
                "comparison_blocks": expected_blocks_by_pack[pack_id],
                "retrieval_cases": expected_retrieval_by_pack[pack_id],
            }
            actual = {field: counts[field] for field in expected}
            if actual != expected:
                raise CampaignError(
                    f"{mode.value} {pack_id} denominator drift: {actual!r} != {expected!r}",
                )
        details[mode.value] = {
            "pack_ids": list(actual_pack_ids),
            "expected_trials": track.expected_trials,
            "expected_retrieval_cases": track.expected_retrieval_cases,
            "packs": by_pack,
        }
        plans[mode] = plan

    calibration_tasks = {trial.key.task_id for trial in plans[CampaignMode.CALIBRATION].trials}
    formal_tasks = {trial.key.task_id for trial in plans[CampaignMode.FORMAL].trials}
    if calibration_tasks & formal_tasks:
        raise CampaignError("calibration and formal task IDs must be disjoint")
    return details


def build_preflight_report(suite_path: Path = DEFAULT_SUITE_PATH) -> dict[str, object]:
    """Return a credential-safe, no-network readiness report."""

    suite = load_campaign_suite(Path(suite_path))
    matrix = _validate_pack_matrix(suite)
    estimate = estimate_worst_case_cost(
        suite,
        modes=(CampaignMode.CALIBRATION, CampaignMode.FORMAL),
    )
    services = default_campaign_services()
    config_summary = read_local_provider_summary()
    request_overrides = _evaluation_request_overrides(suite, config_summary)
    provider_matches = (
        config_summary["provider"] == suite.provider.name
        and isinstance(config_summary["model"], str)
        and config_summary["model"].split("/", 1)[-1] == suite.provider.model
    )
    blockers: list[str] = []
    if not provider_matches:
        blockers.append("configured Provider/model does not match the suite")
    if not config_summary["api_key_configured"]:
        blockers.append("configured Provider API key is missing")
    if not config_summary["api_base_configured"]:
        blockers.append("configured Provider API base is missing")
    if estimate.estimated_cny > suite.budget.hard_cap_cny:
        blockers.append(
            f"worst-case estimate exceeds hard cap: {estimate.estimated_cny:.2f} CNY > {suite.budget.hard_cap_cny:.2f} CNY",
        )
    worktree_clean = services.worktree_is_clean()
    if not worktree_clean:
        blockers.append("worktree is dirty; paid campaign requires a clean snapshot")
    return {
        "schema": "rincode.rincodebench.expansion-preflight.v1",
        "suite": suite.suite,
        "provider": {
            "suite_name": suite.provider.name,
            "suite_model": suite.provider.model,
            **config_summary,
        },
        "request_mode": {
            "thinking": "disabled" if request_overrides else "provider_default",
            "extra_body": request_overrides,
        },
        "pricing": {
            "source": suite.budget.pricing_source,
            "input_cache_miss_usd_per_million": suite.budget.input_cache_miss_usd_per_million,
            "output_usd_per_million": suite.budget.output_usd_per_million,
            "conservative_usd_to_cny_multiplier": suite.budget.conservative_usd_to_cny_multiplier,
            "hard_cap_cny": suite.budget.hard_cap_cny,
        },
        "estimate": to_primitive(estimate),
        "estimate_scope": "ship-calibration-plus-formal-expansion",
        "matrix": matrix,
        "git": {
            "rincode_commit": services.resolve_rincode_commit(),
            "worktree_clean": worktree_clean,
        },
        "paid_run_ready": not blockers,
        "blockers": blockers,
    }


async def run_expansion_campaign(
    *,
    output_root: Path,
    suite_path: Path = DEFAULT_SUITE_PATH,
) -> CampaignOutcome:
    suite = load_campaign_suite(Path(suite_path))
    defaults = default_campaign_services()
    rincode_commit = defaults.resolve_rincode_commit()

    def resolve_provider() -> ResolvedProvider:
        return _apply_evaluation_provider_overrides(
            suite,
            defaults.resolve_provider(),
        )

    async def deterministic(_output_root: Path) -> DeterministicGateResult:
        matrix = _validate_pack_matrix(suite)
        return DeterministicGateResult(
            passed=True,
            details={
                "stage": "expansion-pack-preflight",
                "rincode_commit": rincode_commit,
                "matrix": matrix,
            },
        )

    services = replace(
        defaults,
        resolve_provider=resolve_provider,
        run_deterministic=deterministic,
        build_registry=build_expansion_registry,
    )
    return await run_campaign(
        CampaignMode.SHIP,
        output_root=Path(output_root),
        suite=suite,
        services=services,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preflight or run the native RinCodeBench task expansion campaign.",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--preflight",
        action="store_true",
        help="validate config, matrix, budget, and Git state without Provider calls (default)",
    )
    action.add_argument(
        "--run",
        action="store_true",
        help="run the paid calibration+formal expansion campaign",
    )
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.run:
            report = build_preflight_report(args.suite)
            print(json.dumps(to_primitive(report), indent=2, sort_keys=True))
            return 0
        outcome = asyncio.run(
            run_expansion_campaign(
                output_root=args.output_root,
                suite_path=args.suite,
            )
        )
    except CampaignError as exc:
        print(f"RinCodeBench expansion campaign aborted: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(to_primitive(outcome), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
