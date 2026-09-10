#!/usr/bin/env python3
"""Measure the ToolRegistry ``execute_many`` concurrency contract.

The read arm reuses the existing RinCodebench ToolRegistry experiment and adds
median/P95 and environment metadata.  The write arm is an in-memory guard
check: it deliberately asks for parallel execution, while the registry must
keep a WRITE/non-concurrency-safe tool serial.  Both arms use
``asyncio.sleep`` only as deterministic synthetic I/O; they do not measure a
real file, database, HTTP, or MCP service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmarks.rincodebench.packs.runtime.tool_execution_experiments import (  # noqa: E402
    ToolExecutionExperimentConfig,
    run_tool_execution_experiment,
)
from rincode.agent.tools import (  # noqa: E402
    Tool,
    ToolCapability,
    ToolEffect,
    ToolInvocation,
    ToolRegistry,
)

BENCHMARK_SCHEMA = "rincode.tool-registry-concurrency.v1"
DEFAULT_OUTPUT = _REPO_ROOT / "docs" / "evidence" / "rincode-tool-concurrency.json"


@dataclass(frozen=True)
class BenchmarkConfig:
    """Inputs frozen into the machine-readable artifact."""

    repetitions: int = 9
    warmups: int = 1
    tool_calls: int = 8
    delay_ms: float = 20.0
    max_parallel: int = 8

    def __post_init__(self) -> None:
        if self.repetitions < 3:
            raise ValueError("repetitions must be at least three for median/P95")
        if self.warmups < 0:
            raise ValueError("warmups must be non-negative")
        if self.tool_calls != 8:
            raise ValueError("tool_calls must be exactly eight for the frozen workload")
        if self.delay_ms <= 0:
            raise ValueError("delay_ms must be positive")
        if self.max_parallel < 1:
            raise ValueError("max_parallel must be positive")


class _SyntheticWriteTool(Tool):
    """An in-memory side-effect fixture used only to check serial dispatch."""

    capability = ToolCapability(effect=ToolEffect.WRITE, concurrency_safe=False)

    def __init__(self, delay_ms: float) -> None:
        self._delay_s = delay_ms / 1_000
        self._active = 0
        self.peak = 0
        self.committed: list[str] = []

    @property
    def name(self) -> str:
        return "synthetic_write"

    @property
    def description(self) -> str:
        return "append a label to an in-memory fixture"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
        }

    async def execute(self, label: str, **kwargs: Any) -> str:
        self._active += 1
        self.peak = max(self.peak, self._active)
        try:
            # Yield while the fixture is "busy" so an accidental parallel
            # dispatch would be observable in peak concurrency.
            await asyncio.sleep(self._delay_s)
            self.committed.append(label)
            return label
        finally:
            self._active -= 1


def _percentile(samples: Sequence[float], probability: float) -> float:
    """Return an inclusive, linearly interpolated percentile."""

    if not samples:
        raise ValueError("cannot calculate a percentile from no samples")
    ordered = sorted(samples)
    rank = (len(ordered) - 1) * probability
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _stats(samples: Sequence[float]) -> dict[str, float]:
    return {
        "median_ms": median(samples),
        "p95_ms": _percentile(samples, 0.95),
        "min_ms": min(samples),
        "max_ms": max(samples),
    }


def _environment() -> dict[str, Any]:
    return {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "event_loop_policy": type(asyncio.get_event_loop_policy()).__name__,
        "timer": "time.perf_counter_ns",
        "background_activity": os.environ.get(
            "RINCODE_TOOL_BENCH_BACKGROUND",
            "unspecified; benchmark itself performs no network I/O",
        ),
    }


async def _run_side_effect_guard(config: BenchmarkConfig) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for repetition in range(config.repetitions):
        tool = _SyntheticWriteTool(config.delay_ms)
        registry = ToolRegistry(max_parallel=config.max_parallel)
        registry.register(tool)
        labels = [f"write-{index}" for index in range(config.tool_calls)]
        invocations = [ToolInvocation(tool.name, {"label": label}) for label in labels]

        started = time.perf_counter_ns()
        executions = await registry.execute_many(invocations, parallel_safe=True)
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        outputs = [str(execution.result) for execution in executions]
        runs.append(
            {
                "repetition": repetition,
                "elapsed_ms": elapsed_ms,
                "peak_concurrency": tool.peak,
                "outputs": outputs,
                "commit_order": list(tool.committed),
                "correct": (
                    outputs == labels
                    and tool.committed == labels
                    and tool.peak == 1
                    and all(not execution.result.failed for execution in executions)
                ),
            }
        )

    samples = [run["elapsed_ms"] for run in runs]
    return {
        "requested_parallel_safe": True,
        "capability": {"effect": ToolEffect.WRITE.value, "concurrency_safe": False},
        "io_mode": "synthetic_in_memory_write_with_asyncio_sleep",
        "runs": runs,
        "sample_count": len(runs),
        "stats": _stats(samples),
        "max_peak_concurrency": max(run["peak_concurrency"] for run in runs),
        "passed": all(run["correct"] for run in runs),
    }


async def run_benchmark(config: BenchmarkConfig | None = None) -> dict[str, Any]:
    """Run the frozen read comparison and the side-effect serial guard."""

    config = config or BenchmarkConfig()
    experiment_config = ToolExecutionExperimentConfig(
        repetitions=config.repetitions,
        tool_calls=config.tool_calls,
        delay_ms=config.delay_ms,
        max_parallel=config.max_parallel,
    )
    warmup_config = ToolExecutionExperimentConfig(
        repetitions=1,
        tool_calls=config.tool_calls,
        delay_ms=config.delay_ms,
        max_parallel=config.max_parallel,
    )
    for _ in range(config.warmups):
        await run_tool_execution_experiment(warmup_config)

    read_result = await run_tool_execution_experiment(experiment_config)
    repetitions = read_result["repetitions"]
    serial_samples = [item["serial"]["elapsed_ms"] for item in repetitions]
    parallel_samples = [item["capability_parallel"]["elapsed_ms"] for item in repetitions]
    serial_stats = _stats(serial_samples)
    parallel_stats = _stats(parallel_samples)
    serial_peak = max(item["serial"]["peak_concurrency"] for item in repetitions)
    parallel_peak = max(item["capability_parallel"]["peak_concurrency"] for item in repetitions)
    expected_parallel_peak = min(config.tool_calls, config.max_parallel)
    read_correct = bool(read_result["summary"]["correctness_passed"])
    side_effect = await _run_side_effect_guard(config)

    median_reduction = (serial_stats["median_ms"] - parallel_stats["median_ms"]) / serial_stats["median_ms"] * 100
    p95_reduction = (serial_stats["p95_ms"] - parallel_stats["p95_ms"]) / serial_stats["p95_ms"] * 100
    contract_passed = (
        read_correct
        and serial_peak == 1
        and parallel_peak == expected_parallel_peak
        and side_effect["passed"]
        and side_effect["max_peak_concurrency"] == 1
    )

    return {
        "schema": BENCHMARK_SCHEMA,
        "benchmark": "ToolRegistry.execute_many read-vs-side-effect contract",
        "evidence_scope": "synthetic_asyncio_sleep_tool_registry_microbenchmark",
        "positive_claim_eligible": False,
        "statistics": {
            "median": "statistics.median",
            "p95": "inclusive_linear_interpolation_over_repetition_samples",
        },
        "reused_existing_experiment": {
            "module": "benchmarks.rincodebench.packs.runtime.tool_execution_experiments",
            "schema": read_result["schema"],
        },
        "io_mode": {
            "kind": "synthetic_asyncio_sleep",
            "delay_ms": config.delay_ms,
            "external_io": False,
            "statement": "asyncio.sleep is only a deterministic scheduling fixture; no file/API/DB/MCP latency is measured.",
        },
        "environment": _environment(),
        "config": asdict(config),
        "arms": {
            "serial": {
                **serial_stats,
                "sample_count": len(serial_samples),
                "samples_ms": serial_samples,
                "peak_concurrency": serial_peak,
            },
            "capability_parallel": {
                **parallel_stats,
                "sample_count": len(parallel_samples),
                "samples_ms": parallel_samples,
                "peak_concurrency": parallel_peak,
            },
        },
        "summary": {
            "contract_passed": contract_passed,
            "latency_order_observed": serial_stats["median_ms"] > parallel_stats["median_ms"],
            "median_latency_reduction_percent": median_reduction,
            "p95_latency_reduction_percent": p95_reduction,
            "serial_peak_concurrency": serial_peak,
            "capability_parallel_peak_concurrency": parallel_peak,
            "expected_capability_parallel_peak": expected_parallel_peak,
        },
        "side_effect_guard": side_effect,
        "limitations": [
            "The read fixture waits with asyncio.sleep and does not represent real file, API, DB, or MCP latency.",
            "The write fixture mutates only an in-memory list; it checks dispatch serialization, not durable atomicity.",
            "Results are local scheduler evidence and are not production throughput or SLO measurements.",
        ],
        "passed": contract_passed,
    }


def write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark ToolRegistry.execute_many concurrency semantics")
    parser.add_argument("--repetitions", type=int, default=BenchmarkConfig.repetitions)
    parser.add_argument("--warmups", type=int, default=BenchmarkConfig.warmups)
    parser.add_argument("--delay-ms", type=float, default=BenchmarkConfig.delay_ms)
    parser.add_argument("--max-parallel", type=int, default=BenchmarkConfig.max_parallel)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    result = asyncio.run(
        run_benchmark(
            BenchmarkConfig(
                repetitions=args.repetitions,
                warmups=args.warmups,
                delay_ms=args.delay_ms,
                max_parallel=args.max_parallel,
            )
        )
    )
    write_result(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
