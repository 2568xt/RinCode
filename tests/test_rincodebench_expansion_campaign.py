from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from benchmarks.rincodebench.campaign import CampaignMode, estimate_worst_case_cost, load_campaign_suite
from benchmarks.rincodebench.packs.context import ContextPack, ContextTrack, context_task_set_digest, load_context_tasks
from benchmarks.rincodebench.packs.context.reducer import reduce_context_artifacts
from benchmarks.rincodebench.packs.tool_mcp import ToolMCPPack, ToolMCPTrack, load_tool_mcp_tasks
from benchmarks.rincodebench.packs.tool_mcp.reducer import reduce_tool_mcp_claim_from_artifacts
from scripts import rincodebench_expansion_eval

SUITE_PATH = (
    Path(__file__).resolve().parents[1] / "benchmarks" / "rincodebench" / "suites" / "resume_expansion_20260909.yaml"
)


def test_default_pack_identity_and_output_remain_frozen() -> None:
    context = ContextPack(ContextTrack.FORMAL).definition()
    context_with_explicit_default_tasks = ContextPack(
        ContextTrack.FORMAL,
        tasks=load_context_tasks(ContextTrack.FORMAL),
    ).definition()
    tool = ToolMCPPack(ToolMCPTrack.FORMAL).definition()
    tool_with_explicit_default_tasks = ToolMCPPack(
        ToolMCPTrack.FORMAL,
        tasks=load_tool_mcp_tasks(ToolMCPTrack.FORMAL),
    ).definition()

    assert context.pack_id == "context"
    assert len(context.tasks) == 8
    assert context.identity == context_with_explicit_default_tasks.identity
    assert context.identity["task_set_digest"] == context_task_set_digest(ContextTrack.FORMAL)
    assert context.identity["result_scope"] == "exploratory_eight_task_pack"
    assert tool.pack_id == "tool-mcp"
    assert len(tool.tasks) == 8
    assert tool.identity == tool_with_explicit_default_tasks.identity
    assert tool.identity["result_scope"] == "exploratory_eight_task_pack"


def test_expansion_matrix_is_frozen_at_32_calibration_and_84_formal_trials() -> None:
    suite = load_campaign_suite(SUITE_PATH)
    details = rincodebench_expansion_eval._validate_pack_matrix(suite)

    assert details["calibration"]["expected_trials"] == 32
    assert details["formal"]["expected_trials"] == 84
    assert details["calibration"]["pack_ids"] == [
        "context-calibration",
        "tool-mcp-calibration",
    ]
    assert details["formal"]["pack_ids"] == [
        "context-expansion-20260909",
        "tool-mcp-expansion-20260909",
    ]
    assert details["formal"]["packs"]["context-expansion-20260909"]["tasks"] == 7
    assert details["formal"]["packs"]["tool-mcp-expansion-20260909"]["tasks"] == 7


def test_expansion_task_digest_changes_when_context_expected_fixture_changes(tmp_path: Path) -> None:
    tasks = rincodebench_expansion_eval.load_expansion_context_tasks()
    original = (
        ContextPack(
            ContextTrack.FORMAL,
            tasks=tasks,
            pack_id="context-expansion-20260909",
        )
        .definition()
        .identity["task_set_digest"]
    )

    expected = json.loads(tasks[0].expected_path.read_text(encoding="utf-8"))
    key = next(iter(expected))
    expected[key] = f"changed-{expected[key]}"
    changed_expected_path = tmp_path / "changed-expected.json"
    changed_expected_path.write_text(json.dumps(expected), encoding="utf-8")
    changed_task = replace(tasks[0], expected_path=changed_expected_path)
    changed = (
        ContextPack(
            ContextTrack.FORMAL,
            tasks=(changed_task, *tasks[1:]),
            pack_id="context-expansion-20260909",
        )
        .definition()
        .identity["task_set_digest"]
    )

    assert changed != original


def test_expansion_task_digest_changes_when_tool_expected_receipt_changes() -> None:
    tasks = rincodebench_expansion_eval.load_expansion_tool_mcp_tasks()
    original = (
        ToolMCPPack(
            ToolMCPTrack.FORMAL,
            tasks=tasks,
            pack_id="tool-mcp-expansion-20260909",
        )
        .definition()
        .identity["task_set_digest"]
    )

    target = tasks[0].targets[0]
    changed_target = replace(target, arguments={**dict(target.arguments), "value": 99999})
    changed_task = replace(tasks[0], targets=(changed_target, *tasks[0].targets[1:]))
    changed = (
        ToolMCPPack(
            ToolMCPTrack.FORMAL,
            tasks=(changed_task, *tasks[1:]),
            pack_id="tool-mcp-expansion-20260909",
        )
        .definition()
        .identity["task_set_digest"]
    )

    assert changed != original


def _expansion_context_artifacts() -> tuple[list[dict], list[dict]]:
    trials: list[dict] = []
    pairs: list[dict] = []
    for task in rincodebench_expansion_eval.load_expansion_context_tasks():
        for repetition in range(3):
            for variant_id, main_tokens, total_tokens, auxiliary_tokens in (
                ("context-fifo", 900, 1_000, 0),
                ("context-curator", 650, 800, 150),
            ):
                trials.append(
                    {
                        "key": {
                            "pack_id": "context-expansion-20260909",
                            "task_id": task.task_id,
                            "variant_id": variant_id,
                            "repetition": repetition,
                        },
                        "selected_block_attempt": 1,
                        "status": "passed",
                        "metrics": {
                            "main_agent_input_tokens": main_tokens,
                            "trial_total_input_tokens": total_tokens,
                            "context_auxiliary_input_tokens": auxiliary_tokens,
                            "usage_complete": True,
                            "early_constraint_retained": True,
                            "active_constraint_applied": True,
                            "latest_decision_applied": True,
                            "artifact_exact": True,
                        },
                    }
                )
            pairs.append(
                {
                    "key": {
                        "pack_id": "context-expansion-20260909",
                        "treatment_axis": "history_manager",
                        "task_id": task.task_id,
                        "repetition": repetition,
                        "control_variant_id": "context-fifo",
                        "treatment_variant_id": "context-curator",
                    },
                    "selected_block_attempt": 1,
                    "valid": True,
                    "actual_variant_diff": {
                        "history_manager": {
                            "control": "fifo_tail",
                            "treatment": "curator",
                        }
                    },
                }
            )
    return trials, pairs


def _expansion_tool_mcp_artifacts() -> tuple[list[dict], list[dict]]:
    trials: list[dict] = []
    pairs: list[dict] = []
    catalog_digest = ToolMCPPack().definition().identity["mcp_catalog_digest"]
    for task in rincodebench_expansion_eval.load_expansion_tool_mcp_tasks():
        for repetition in range(3):
            for variant_id, schema_tokens, visible_names in (
                (
                    "tool-mcp-all-tools",
                    1_000,
                    ["mcp_rincodebench_catalog_probe_00"],
                ),
                (
                    "tool-mcp-progressive",
                    300,
                    ["tool_search", "tool_call"],
                ),
            ):
                trials.append(
                    {
                        "key": {
                            "pack_id": "tool-mcp-expansion-20260909",
                            "task_id": task.task_id,
                            "variant_id": variant_id,
                            "repetition": repetition,
                        },
                        "selected_block_attempt": 1,
                        "status": "passed",
                        "metrics": {
                            "trial_total_estimated_visible_tool_schema_tokens": schema_tokens,
                            "invalid_target_call_rate": 0.0,
                            "exact_target_repeat_rate": 0.0,
                            "usage_complete": True,
                            "initial_visible_tool_names": visible_names,
                            "mcp_transport": "stdio",
                            "mcp_connected": True,
                            "mcp_catalog_count": 64,
                            "mcp_catalog_digest": catalog_digest,
                            "mcp_catalog_source_digest": catalog_digest,
                            "provider_model": "provider/exact-model",
                            "actual_model_names": ["exact-model"],
                            "model_call_records": [
                                {
                                    "requested_model": "provider/exact-model",
                                    "model": "exact-model",
                                    "finish_reason": "stop",
                                }
                            ],
                        },
                    }
                )
            pairs.append(
                {
                    "key": {
                        "pack_id": "tool-mcp-expansion-20260909",
                        "treatment_axis": "tool_disclosure",
                        "task_id": task.task_id,
                        "repetition": repetition,
                        "control_variant_id": "tool-mcp-all-tools",
                        "treatment_variant_id": "tool-mcp-progressive",
                    },
                    "selected_block_attempt": 1,
                    "valid": True,
                    "actual_variant_diff": {
                        "tool_disclosure": [
                            "all_tools",
                            "progressive_disclosure",
                        ]
                    },
                }
            )
    return trials, pairs


def test_complete_expansion_context_pairs_are_valid() -> None:
    trials, pairs = _expansion_context_artifacts()

    result = reduce_context_artifacts(trial_records=trials, pair_results=pairs)

    assert result["context.expected_pair_count"] == 21
    assert result["context.pair_measurement_count"] == 21
    assert result["context.valid_pair_measurement_count"] == 21
    assert result["context.measurement_valid"] is True
    assert result["context.capability_measurement_valid"] is True


def test_missing_expansion_context_pair_is_invalid_even_with_all_trials() -> None:
    trials, pairs = _expansion_context_artifacts()
    pairs.pop()

    result = reduce_context_artifacts(trial_records=trials, pair_results=pairs)

    assert result["context.expected_pair_count"] == 21
    assert result["context.pair_measurement_count"] == 20
    assert result["context.valid_pair_measurement_count"] == 20
    assert result["context.measurement_valid"] is False
    assert result["context.capability_measurement_valid"] is False


def test_extra_expansion_context_repetition_is_invalid() -> None:
    trials, pairs = _expansion_context_artifacts()
    task_id = pairs[0]["key"]["task_id"]
    for variant_id in ("context-fifo", "context-curator"):
        record = next(
            record
            for record in trials
            if record["key"]["task_id"] == task_id
            and record["key"]["variant_id"] == variant_id
            and record["key"]["repetition"] == 0
        )
        trials.append(
            {
                **record,
                "key": {**record["key"], "repetition": 3},
            }
        )
    pairs.append(
        {
            **pairs[0],
            "key": {**pairs[0]["key"], "repetition": 3},
        }
    )

    result = reduce_context_artifacts(trial_records=trials, pair_results=pairs)

    assert result["context.measurement_valid"] is False
    assert "context_expansion_trial_keys_do_not_match_plan" in result["context.findings"]


def test_complete_expansion_tool_mcp_pairs_are_valid() -> None:
    trials, pairs = _expansion_tool_mcp_artifacts()

    result = reduce_tool_mcp_claim_from_artifacts(trial_records=trials, pair_results=pairs)

    assert result["tool_mcp.pair_measurement_count"] == 21
    assert result["tool_mcp.valid_pair_measurement_count"] == 21
    assert result["tool_mcp.measurement_valid"] is True


def test_missing_expansion_tool_mcp_pair_is_invalid_even_with_all_trials() -> None:
    trials, pairs = _expansion_tool_mcp_artifacts()
    pairs.pop()

    result = reduce_tool_mcp_claim_from_artifacts(trial_records=trials, pair_results=pairs)

    assert result["tool_mcp.pair_measurement_count"] == 20
    assert result["tool_mcp.valid_pair_measurement_count"] == 20
    assert result["tool_mcp.measurement_valid"] is False


def test_expansion_budget_estimate_stays_below_independent_hard_cap() -> None:
    suite = load_campaign_suite(SUITE_PATH)
    estimate = estimate_worst_case_cost(
        suite,
        modes=(CampaignMode.CALIBRATION, CampaignMode.FORMAL),
    )

    assert suite.budget.hard_cap_cny == 100
    assert estimate.trial_count == 116
    assert estimate.estimated_cny <= suite.budget.hard_cap_cny


def test_missing_expansion_pair_keeps_fixed_denominator_and_fails_context_reduction() -> None:
    pair_key = {
        "pack_id": "context-expansion-20260909",
        "task_id": "ctx-expand-synthetic-export",
        "repetition": 0,
        "treatment_axis": "history_manager",
        "control_variant_id": "context-fifo",
        "treatment_variant_id": "context-curator",
    }
    result = reduce_context_artifacts(
        trial_records=(),
        pair_results=({"key": pair_key, "valid": True, "selected_block_attempt": 0},),
    )

    assert result["context.expected_pair_count"] == 21
    assert result["context.measurement_valid"] is False
    assert "context_pair_denominator_mismatch:0:21" in result["context.findings"]


def test_missing_expansion_pair_keeps_fixed_tool_denominator_and_fails_reduction() -> None:
    pair_key = {
        "pack_id": "tool-mcp-expansion-20260909",
        "task_id": "tool-expansion-01",
        "repetition": 0,
        "treatment_axis": "tool_disclosure",
        "control_variant_id": "tool-mcp-all-tools",
        "treatment_variant_id": "tool-mcp-progressive",
    }
    result = reduce_tool_mcp_claim_from_artifacts(
        trial_records=(),
        pair_results=({"key": pair_key, "valid": True, "selected_block_attempt": 0},),
    )

    assert result["tool_mcp.measurement_valid"] is False
    assert "tool_mcp_pair_denominator_mismatch:0:21" in result["tool_mcp.findings"]


def test_preflight_uses_redacted_config_only_and_does_not_resolve_provider(monkeypatch) -> None:
    class NoProviderServices:
        def worktree_is_clean(self) -> bool:
            return True

        def resolve_rincode_commit(self) -> str:
            return "a" * 40

        def resolve_provider(self):
            raise AssertionError("preflight must not resolve a Provider")

    monkeypatch.setattr(
        rincodebench_expansion_eval,
        "default_campaign_services",
        lambda: NoProviderServices(),
    )
    monkeypatch.setattr(
        rincodebench_expansion_eval,
        "read_local_provider_summary",
        lambda: {
            "config_path": "~/.rincode/config.json",
            "provider": "deepseek",
            "model": "deepseek/deepseek-v4-flash",
            "api_key_configured": True,
            "api_base_configured": True,
            "api_base_host": "api.deepseek.com",
        },
    )

    report = rincodebench_expansion_eval.build_preflight_report(SUITE_PATH)

    assert report["paid_run_ready"] is True
    assert report["matrix"]["formal"]["expected_trials"] == 84
    assert '"api_key":' not in json.dumps(report, sort_keys=True)
