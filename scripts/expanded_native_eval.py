"""Run the expanded native RinCodeBench campaign.

The entry point deliberately keeps campaign orchestration in
``benchmarks.rincodebench.campaign``.  It only supplies the three-pack registry
that excludes the historical EverOS semantic-effect pack.  ``--preflight`` is
credential-safe: it reads the local model configuration for a redacted
summary, validates the frozen pack matrix, estimates cost, and never creates
or calls an LLM provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

from benchmarks.rincodebench.budget import BudgetGuardedProvider
from benchmarks.rincodebench.campaign import (
    CampaignError,
    CampaignMode,
    CampaignOutcome,
    CampaignSuite,
    DeterministicGateResult,
    ResolvedProvider,
    _redacted_runtime_config,
    default_campaign_services,
    estimate_worst_case_cost,
    load_campaign_suite,
    run_campaign,
)
from benchmarks.rincodebench.canonical import canonical_digest, to_primitive
from benchmarks.rincodebench.packs.context import (
    ContextPack,
    ContextTrack,
)
from benchmarks.rincodebench.packs.memory_skill import (
    DeterministicCrossProcessRunner,
    RuntimeCrossProcessRunner,
    create_calibration_pack,
    create_formal_pack,
)
from benchmarks.rincodebench.packs.tool_mcp import (
    ToolMCPPack,
    ToolMCPTrack,
)
from benchmarks.rincodebench.plan import compile_plan
from benchmarks.rincodebench.registry import PackRegistry
from benchmarks.rincodebench.schema import ExperimentSpec
from benchmarks.rincodebench.scorecard_campaign import build_scorecard_registry
from rincode.config.loader import get_config_path, load_config
from rincode.config.rincode import RinCodeConfig
from rincode.config.schema import Config
from rincode.providers.base import LLMProvider

DEFAULT_SUITE_PATH = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "rincodebench"
    / "suites"
    / "resume_eval_20260909.yaml"
)
DEFAULT_OUTPUT_ROOT = Path(".rincode") / "evidence" / "rincodebench-resume-eval-20260909"

_EXPECTED_PACKS = {
    CampaignMode.CALIBRATION: (
        "context-calibration",
        "memory-skill-calibration-v1",
        "tool-mcp-calibration",
    ),
    CampaignMode.FORMAL: (
        "context",
        "memory-skill-v1",
        "tool-mcp",
    ),
}
_DEEPSEEK_V4_FLASH = "deepseek-v4-flash"
_DEEPSEEK_THINKING_DISABLED = {"thinking": {"type": "disabled"}}


def _runtime_pack_objects(mode: CampaignMode) -> tuple[object, ...]:
    """Build pack definitions without constructing a provider.

    The memory runner is deterministic here and is used only to expose the
    frozen local retrieval matrix.  Context and Tool/MCP definitions do not
    require a runner for plan compilation.
    """

    calibration = CampaignMode(mode) is CampaignMode.CALIBRATION
    context_track = ContextTrack.CALIBRATION if calibration else ContextTrack.FORMAL
    tool_track = ToolMCPTrack.CALIBRATION if calibration else ToolMCPTrack.FORMAL
    memory_runner = DeterministicCrossProcessRunner()
    return (
        ContextPack(context_track),
        create_calibration_pack(memory_runner)
        if calibration
        else create_formal_pack(memory_runner),
        ToolMCPPack(tool_track),
    )


def build_expanded_registry(
    mode: CampaignMode,
    resolved: ResolvedProvider,
) -> PackRegistry:
    """Build the paid three-pack registry on the campaign's shared ledger."""

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
        raise CampaignError("expanded runners must share the campaign Provider ledger")

    calibration = CampaignMode(mode) is CampaignMode.CALIBRATION
    memory_runner = RuntimeCrossProcessRunner(
        config=resolved.config,
        rincode_config=resolved.rincode_config,
        provider=provider,
    )
    memory_pack = (
        create_calibration_pack(memory_runner)
        if calibration
        else create_formal_pack(memory_runner)
    )
    if getattr(memory_pack, "retrieval_cost_mode", None) != "local_zero_provider":
        raise CampaignError("memory retrieval must remain local and zero-Provider")

    registry = build_scorecard_registry(mode, resolved)
    registry.register(memory_pack)
    return registry


def _matches_deepseek_v4_flash(
    suite: CampaignSuite,
    *,
    provider_name: str,
    model: str,
) -> bool:
    """Return whether this run targets the explicit DeepSeek V4 Flash override."""

    return (
        provider_name == "deepseek"
        and suite.provider.name == "deepseek"
        and suite.provider.model == _DEEPSEEK_V4_FLASH
        and model.split("/", 1)[-1] == _DEEPSEEK_V4_FLASH
    )


def _apply_evaluation_provider_overrides(
    suite: CampaignSuite,
    resolved: ResolvedProvider,
) -> ResolvedProvider:
    """Clone runtime config and disable DeepSeek V4 thinking for this campaign only."""

    if not _matches_deepseek_v4_flash(
        suite,
        provider_name=resolved.provider_name,
        model=resolved.model,
    ):
        return resolved
    if not isinstance(resolved.config, Config) or not isinstance(resolved.rincode_config, RinCodeConfig):
        raise CampaignError("DeepSeek evaluation override requires RinCode runtime configuration")

    config = resolved.config.model_copy(deep=True)
    provider_config = config.get_provider(config.agents.defaults.model)
    if provider_config is None:
        raise CampaignError("DeepSeek evaluation override cannot find the configured provider")
    provider_config.extra_body = {
        **dict(provider_config.extra_body or {}),
        **_DEEPSEEK_THINKING_DISABLED,
    }

    rincode_config = resolved.rincode_config.model_copy(deep=True)
    rincode_config.base = config

    # Import lazily so ``--preflight`` remains credential-safe and provider-free.
    from rincode.cli._helpers import make_provider

    provider = make_provider(config)
    return replace(
        resolved,
        provider=provider,
        runtime_config_digest=canonical_digest(
            _redacted_runtime_config(config, rincode_config),
        ),
        config=config,
        rincode_config=rincode_config,
    )


def _evaluation_request_overrides(
    suite: CampaignSuite,
    provider_summary: dict[str, object],
) -> dict[str, object]:
    """Describe the run-scoped request override without resolving a provider."""

    if _matches_deepseek_v4_flash(
        suite,
        provider_name=str(provider_summary.get("provider", "")),
        model=str(provider_summary.get("model", "")),
    ):
        return dict(_DEEPSEEK_THINKING_DISABLED)
    return {}


def _validate_pack_matrix(suite: CampaignSuite) -> dict[str, object]:
    """Validate frozen denominators against the actual native pack definitions."""

    details: dict[str, object] = {}
    plans: dict[CampaignMode, object] = {}
    for mode in (CampaignMode.CALIBRATION, CampaignMode.FORMAL):
        track = suite.calibration if mode is CampaignMode.CALIBRATION else suite.formal
        expected_pack_ids = _EXPECTED_PACKS[mode]
        if track.pack_ids != expected_pack_ids:
            raise CampaignError(
                f"{mode.value} must use the expanded three-pack matrix: "
                f"{track.pack_ids!r} != {expected_pack_ids!r}"
            )
        packs = _runtime_pack_objects(mode)
        definitions = tuple(pack.definition() for pack in packs)
        actual_pack_ids = tuple(definition.pack_id for definition in definitions)
        if actual_pack_ids != track.pack_ids:
            raise CampaignError(
                f"{mode.value} registry pack IDs drifted: "
                f"{actual_pack_ids!r} != {track.pack_ids!r}"
            )
        spec = ExperimentSpec(
            suite=track.suite,
            repetitions=track.repetitions,
            pack_ids=track.pack_ids,
            output_root=Path(".rincode") / "preflight" / mode.value,
            identity={"kind": "expanded-native-pack-preflight"},
        )
        try:
            plan = compile_plan(spec, packs)
        except (KeyError, ValueError) as exc:
            raise CampaignError(f"cannot compile {mode.value} pack matrix: {exc}") from exc
        if len(plan.trials) != track.expected_trials:
            raise CampaignError(
                f"{mode.value} Trial denominator drift: "
                f"{len(plan.trials)} != {track.expected_trials}"
            )
        if len(plan.retrieval_cases) != track.expected_retrieval_cases:
            raise CampaignError(
                f"{mode.value} Retrieval denominator drift: "
                f"{len(plan.retrieval_cases)} != {track.expected_retrieval_cases}"
            )
        by_pack = {
            definition.pack_id: {
                "tasks": len(definition.tasks),
                "variants": len(definition.variants),
                "pairs": len(definition.pairs),
                "trials": sum(
                    1 for trial in plan.trials if trial.key.pack_id == definition.pack_id
                ),
                "comparison_blocks": sum(
                    1
                    for block in plan.comparison_blocks
                    if block.key.pack_id == definition.pack_id
                ),
                "retrieval_cases": sum(
                    len(retrieval_suite.queries)
                    * len(retrieval_suite.configurations)
                    for retrieval_suite in definition.retrieval_suites
                ),
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
            actual = {
                field: counts[field]
                for field in ("trials", "comparison_blocks", "retrieval_cases")
            }
            if actual != expected:
                raise CampaignError(
                    f"{mode.value} {pack_id} denominator drift: "
                    f"{actual!r} != {expected!r}"
                )
        details[mode.value] = {
            "pack_ids": list(actual_pack_ids),
            "expected_trials": track.expected_trials,
            "expected_retrieval_cases": track.expected_retrieval_cases,
            "packs": by_pack,
        }
        plans[mode] = plan

    calibration_tasks = {
        trial.key.task_id for trial in plans[CampaignMode.CALIBRATION].trials
    }
    formal_tasks = {trial.key.task_id for trial in plans[CampaignMode.FORMAL].trials}
    if calibration_tasks & formal_tasks:
        raise CampaignError("calibration and formal task IDs must be disjoint")
    return details


def _base_url_host(base_url: object) -> str | None:
    if not isinstance(base_url, str) or not base_url:
        return None
    return urlparse(base_url).hostname


def read_local_provider_summary() -> dict[str, object]:
    """Read only redacted provider metadata from the local RinCode config."""

    config = load_config()
    configured_model = str(config.agents.defaults.model)
    provider_name = config.get_provider_name(configured_model)
    provider = config.get_provider(configured_model)
    api_base = config.get_api_base(configured_model)
    return {
        "config_path": str(get_config_path()),
        "provider": provider_name,
        "model": configured_model,
        "api_key_configured": bool(provider and provider.api_key),
        "api_base_configured": bool(api_base),
        "api_base_host": _base_url_host(api_base),
    }


def build_preflight_report(suite_path: Path = DEFAULT_SUITE_PATH) -> dict[str, object]:
    """Return a no-network readiness report for the expanded campaign."""

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
            f"worst-case estimate exceeds hard cap: "
            f"{estimate.estimated_cny:.2f} CNY > {suite.budget.hard_cap_cny:.2f} CNY"
        )
    worktree_clean = services.worktree_is_clean()
    if not worktree_clean:
        blockers.append("worktree is dirty; paid campaign requires a clean snapshot")
    return {
        "schema": "rincode.rincodebench.expanded-native-preflight.v1",
        "suite": suite.suite,
        "provider": {
            "suite_name": suite.provider.name,
            "suite_model": suite.provider.model,
            **config_summary,
        },
        "request_mode": {
            "thinking": (
                "disabled" if request_overrides else "provider_default"
            ),
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
        "estimate_scope": "ship-calibration-plus-formal",
        "matrix": matrix,
        "git": {
            "rincode_commit": services.resolve_rincode_commit(),
            "worktree_clean": worktree_clean,
        },
        "paid_run_ready": not blockers,
        "blockers": blockers,
    }


async def run_expanded_campaign(
    *,
    output_root: Path,
    suite_path: Path = DEFAULT_SUITE_PATH,
) -> CampaignOutcome:
    """Run calibration then formal packs through the public Ship gate."""

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
                "stage": "expanded-native-pack-preflight",
                "rincode_commit": rincode_commit,
                "matrix": matrix,
            },
        )

    # Keep the native Ship gate, probe, budget guard, harness, and report
    # rebuild.  Only the registry and credential-free pack gate are specific
    # to this expanded suite.
    services = replace(
        defaults,
        resolve_provider=resolve_provider,
        run_deterministic=deterministic,
        build_registry=build_expanded_registry,
    )
    return await run_campaign(
        CampaignMode.SHIP,
        output_root=Path(output_root),
        suite=suite,
        services=services,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preflight or run the expanded native RinCodeBench campaign.",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--preflight",
        action="store_true",
        help="validate config, matrix, budget, and Git state without provider calls (default)",
    )
    action.add_argument(
        "--run",
        action="store_true",
        help="run the paid calibration+formal expanded campaign",
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
            run_expanded_campaign(
                output_root=args.output_root,
                suite_path=args.suite,
            )
        )
    except CampaignError as exc:
        print(f"RinCodeBench expanded campaign aborted: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(to_primitive(outcome), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
