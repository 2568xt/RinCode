from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

from benchmarks.rincodebench.campaign import load_campaign_suite
from scripts import expanded_native_eval

SUITE_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "rincodebench" / "suites" / "resume_eval_20260909.yaml"


def test_expanded_matrix_matches_native_pack_denominators() -> None:
    suite = load_campaign_suite(SUITE_PATH)

    details = expanded_native_eval._validate_pack_matrix(suite)

    assert details["calibration"]["expected_trials"] == 56
    assert details["calibration"]["expected_retrieval_cases"] == 34
    assert details["formal"]["expected_trials"] == 168
    assert details["formal"]["expected_retrieval_cases"] == 260
    assert details["formal"]["pack_ids"] == [
        "context",
        "memory-skill-v1",
        "tool-mcp",
    ]


def test_preflight_does_not_resolve_or_call_a_provider(monkeypatch) -> None:
    class NoProviderServices:
        def worktree_is_clean(self) -> bool:
            return True

        def resolve_rincode_commit(self) -> str:
            return "a" * 40

        def resolve_provider(self):
            raise AssertionError("preflight must not resolve a provider")

    monkeypatch.setattr(
        expanded_native_eval,
        "default_campaign_services",
        lambda: NoProviderServices(),
    )
    monkeypatch.setattr(
        expanded_native_eval,
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

    report = expanded_native_eval.build_preflight_report(SUITE_PATH)

    assert report["git"]["worktree_clean"] is True
    assert report["estimate_scope"] == "ship-calibration-plus-formal"
    assert report["request_mode"] == {
        "thinking": "disabled",
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    assert '"api_key":' not in json.dumps(report, sort_keys=True)


def test_deepseek_override_is_in_memory_and_survives_memory_config_reload() -> None:
    from benchmarks.rincodebench.campaign import ResolvedProvider, _redacted_runtime_config
    from benchmarks.rincodebench.canonical import canonical_digest
    from rincode.cli._helpers import make_provider
    from rincode.config.rincode import RinCodeConfig
    from rincode.config.schema import Config

    suite = load_campaign_suite(SUITE_PATH)
    config = Config()
    config.agents.defaults.provider = "deepseek"
    config.agents.defaults.model = "deepseek/deepseek-v4-flash"
    config.providers.deepseek.api_key = "test-key"
    rincode_config = RinCodeConfig(base=config)
    resolved = ResolvedProvider(
        provider_name="deepseek",
        model="deepseek-v4-flash",
        provider=make_provider(config),
        runtime_config_digest="original-digest",
        config=config,
        rincode_config=rincode_config,
        configured_model=config.agents.defaults.model,
    )

    overridden = expanded_native_eval._apply_evaluation_provider_overrides(
        suite,
        resolved,
    )

    assert overridden.config is not config
    assert overridden.rincode_config is not rincode_config
    assert config.providers.deepseek.extra_body == {}
    assert overridden.config.providers.deepseek.extra_body == {
        "thinking": {"type": "disabled"},
    }
    assert overridden.rincode_config.base is overridden.config
    assert overridden.provider.extra_body == {
        "thinking": {"type": "disabled"},
    }
    redacted_runtime_config = _redacted_runtime_config(
        overridden.config,
        overridden.rincode_config,
    )
    assert overridden.runtime_config_digest == canonical_digest(redacted_runtime_config)
    assert "test-key" not in json.dumps(redacted_runtime_config, sort_keys=True)
    assert overridden.runtime_config_digest != resolved.runtime_config_digest

    payload = {
        "config": overridden.config.model_dump(mode="json"),
        "rincode_config": overridden.rincode_config.model_dump(mode="json"),
    }
    reloaded_config = Config.model_validate(payload["config"])
    reloaded_rincode_config = RinCodeConfig.model_validate(payload["rincode_config"])
    assert reloaded_config.providers.deepseek.extra_body == {
        "thinking": {"type": "disabled"},
    }
    assert reloaded_rincode_config.base.providers.deepseek.extra_body == {
        "thinking": {"type": "disabled"},
    }


def test_deepseek_override_does_not_apply_to_other_provider_models() -> None:
    from benchmarks.rincodebench.campaign import ResolvedProvider
    from rincode.cli._helpers import make_provider
    from rincode.config.rincode import RinCodeConfig
    from rincode.config.schema import Config

    suite = load_campaign_suite(SUITE_PATH)
    config = Config()
    config.agents.defaults.provider = "openai"
    config.agents.defaults.model = "openai/gpt-4o"
    config.providers.openai.api_key = "test-key"
    rincode_config = RinCodeConfig(base=config)
    resolved = ResolvedProvider(
        provider_name="openai",
        model="gpt-4o",
        provider=make_provider(config),
        runtime_config_digest="original-digest",
        config=config,
        rincode_config=rincode_config,
        configured_model=config.agents.defaults.model,
    )

    assert expanded_native_eval._apply_evaluation_provider_overrides(
        suite,
        resolved,
    ) is resolved


def test_run_entrypoint_delegates_to_public_ship_gate(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_run_campaign(mode, *, output_root, suite, services):
        captured.update(
            {
                "mode": mode,
                "output_root": output_root,
                "suite": suite,
                "services": services,
            }
        )
        return object()

    defaults = expanded_native_eval.default_campaign_services()
    monkeypatch.setattr(
        expanded_native_eval,
        "default_campaign_services",
        lambda: replace(defaults, resolve_rincode_commit=lambda: "a" * 40),
    )
    monkeypatch.setattr(expanded_native_eval, "run_campaign", fake_run_campaign)

    asyncio.run(
        expanded_native_eval.run_expanded_campaign(
            output_root=tmp_path,
            suite_path=SUITE_PATH,
        )
    )

    assert captured["mode"] == expanded_native_eval.CampaignMode.SHIP
    assert captured["suite"].suite == "resume-eval-20260909"
    assert captured["services"].resolve_provider is not defaults.resolve_provider
    assert captured["services"].build_registry is expanded_native_eval.build_expanded_registry
