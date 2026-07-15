"""
telemetry.py — usage + cost + timing capture for the extraction pipeline.

Why this exists
---------------
The stakeholder ask was to back the savings story with measurable KPIs. You
cannot report "$X compute per contract" or "N analyst-hours saved" without
first capturing what each run actually costs. This module is that capture
layer — model-agnostic, so it works the same for Claude (sandbox) and Falcon
(production).

It records, per model call: input/output tokens, estimated USD cost, latency,
and whether the call was served from cache (a cached clause costs $0). A run
aggregates those into a RunStats the UI and exports can display, and that a
KPI dashboard can later persist per contract.

Nothing here talks to a network or an SDK — providers report usage *into* this
module. That keeps it unit-testable and endpoint-independent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Model pricing (USD per 1M tokens). These are configurable, not hardcoded
# into logic — update the table when pricing changes or a new model is added.
# Unknown models fall back to (0, 0) so cost simply reads $0.00 rather than
# crashing — telemetry must never break an extraction run.
# ---------------------------------------------------------------------------
PRICING_PER_MTOK: Dict[str, Dict[str, float]] = {
    # model_id: {"input": $/1M input tokens, "output": $/1M output tokens}
    "claude-sonnet-4-6":       {"input": 3.00, "output": 15.00},
    "claude-opus-4-8":         {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5":        {"input": 0.80, "output": 4.00},
    # Falcon (production) — set to the real negotiated rate when known.
    "jll-falcon":              {"input": 0.00, "output": 0.00},
}


def cost_for(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD cost for a single call. Unknown model → 0.0 (never raises)."""
    rate = PRICING_PER_MTOK.get(model, {"input": 0.0, "output": 0.0})
    return (input_tokens / 1_000_000) * rate["input"] + \
           (output_tokens / 1_000_000) * rate["output"]


@dataclass
class CallRecord:
    """One model call (one clause extraction attempt)."""
    section_id: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    cached: bool = False
    error: Optional[str] = None

    @property
    def cost_usd(self) -> float:
        return 0.0 if self.cached else cost_for(self.model, self.input_tokens, self.output_tokens)


@dataclass
class RunStats:
    """Aggregated telemetry for one document run. Thread-safe: extract_all runs
    clauses in parallel, so record() is guarded by a lock."""
    document_name: str = ""
    model: str = ""
    calls: List[CallRecord] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def record(self, rec: CallRecord) -> None:
        with self._lock:
            self.calls.append(rec)

    def finish(self) -> "RunStats":
        self.finished_at = time.time()
        return self

    # --- aggregates the UI / KPI layer reads -------------------------------
    @property
    def total_calls(self) -> int:
        return len(self.calls)

    @property
    def cached_calls(self) -> int:
        return sum(1 for c in self.calls if c.cached)

    @property
    def error_calls(self) -> int:
        return sum(1 for c in self.calls if c.error)

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.calls)

    @property
    def total_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def cache_hit_rate(self) -> float:
        return self.cached_calls / self.total_calls if self.total_calls else 0.0

    @property
    def wall_clock_s(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.time()
        return end - self.started_at

    def summary(self) -> Dict:
        """Flat dict for display, export, or persistence to a KPI store."""
        return {
            "document_name": self.document_name,
            "model": self.model,
            "total_calls": self.total_calls,
            "cached_calls": self.cached_calls,
            "cache_hit_rate": round(self.cache_hit_rate, 3),
            "error_calls": self.error_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "wall_clock_s": round(self.wall_clock_s, 2),
        }


def extract_usage(response) -> tuple:
    """Best-effort (input_tokens, output_tokens) from an Anthropic-style response.
    Returns (0, 0) if usage isn't present — telemetry is advisory, never fatal."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return 0, 0
    get = (lambda k: getattr(usage, k, None)) if not isinstance(usage, dict) else usage.get
    return int(get("input_tokens") or 0), int(get("output_tokens") or 0)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print("=== telemetry.py self-test ===\n")

    # cost math
    c = cost_for("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert abs(c - 18.0) < 1e-9, c
    assert cost_for("unknown-model", 1_000_000, 1_000_000) == 0.0
    print("cost_for: OK")

    # run aggregation
    run = RunStats(document_name="test.pdf", model="claude-sonnet-4-6")
    run.record(CallRecord("8.1", "claude-sonnet-4-6", 1000, 500))
    run.record(CallRecord("8.2", "claude-sonnet-4-6", 2000, 800))
    run.record(CallRecord("8.3", "claude-sonnet-4-6", cached=True))  # cache hit → $0
    run.record(CallRecord("8.4", "claude-sonnet-4-6", error="timeout"))
    run.finish()

    assert run.total_calls == 4
    assert run.cached_calls == 1
    assert run.error_calls == 1
    assert run.input_tokens == 3000
    assert run.output_tokens == 1300
    assert abs(run.cache_hit_rate - 0.25) < 1e-9
    expected = cost_for("claude-sonnet-4-6", 3000, 1300)
    assert abs(run.total_cost_usd - expected) < 1e-9, (run.total_cost_usd, expected)
    print("RunStats aggregation: OK")

    # usage extraction from both object and dict shapes
    class U:  # noqa
        input_tokens = 123
        output_tokens = 45
    class R:  # noqa
        usage = U()
    assert extract_usage(R()) == (123, 45)
    assert extract_usage({"usage": {"input_tokens": 7, "output_tokens": 8}}) == (7, 8)
    assert extract_usage({"no": "usage"}) == (0, 0)
    print("extract_usage: OK")

    import json
    print("\nSample run summary:")
    print(json.dumps(run.summary(), indent=2))
    print("\nAll telemetry self-tests passed.")
