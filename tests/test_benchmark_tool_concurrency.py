import json

from scripts.benchmark_tool_concurrency import BenchmarkConfig, run_benchmark, write_result


async def test_benchmark_reports_eight_read_calls_and_serializes_side_effects(tmp_path) -> None:
    result = await run_benchmark(
        BenchmarkConfig(
            repetitions=3,
            warmups=0,
            delay_ms=1,
            max_parallel=8,
        )
    )
    output = tmp_path / "rincode-tool-concurrency.json"
    write_result(output, result)

    artifact = json.loads(output.read_text())
    assert artifact["passed"] is True
    assert artifact["config"]["tool_calls"] == 8
    assert artifact["arms"]["serial"]["peak_concurrency"] == 1
    assert artifact["arms"]["capability_parallel"]["peak_concurrency"] == 8
    assert artifact["arms"]["serial"]["p95_ms"] >= artifact["arms"]["serial"]["median_ms"]
    assert artifact["arms"]["capability_parallel"]["p95_ms"] >= artifact["arms"]["capability_parallel"]["median_ms"]
    assert artifact["side_effect_guard"]["max_peak_concurrency"] == 1
    assert artifact["side_effect_guard"]["passed"] is True
    assert artifact["positive_claim_eligible"] is False


def test_config_rejects_non_frozen_call_count() -> None:
    try:
        BenchmarkConfig(tool_calls=4)
    except ValueError as exc:
        assert "exactly eight" in str(exc)
    else:
        raise AssertionError("the frozen workload must contain eight tool calls")
