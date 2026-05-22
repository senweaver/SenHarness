"""Lightweight Prometheus-format metrics — no external dep.

Emits a plain-text ``/metrics/prometheus`` scrape payload using the
Prometheus exposition format. Keeps to the standard four metric types
(counter / gauge / histogram / summary) but implemented as dead-simple
thread-safe in-memory structures.

Rationale: the `prometheus_client` library is a fine choice, but bringing
it in just for a handful of metrics adds startup weight and multiprocess
gotchas. Our needs (agent runs, token cost, latency buckets, error counts)
fit comfortably in ~150 lines and stay operator-auditable.

Counters exposed:

    senharness_agent_runs_total{provider,model,status}
    senharness_agent_run_duration_seconds{provider,model}         (histogram)
    senharness_agent_tokens_total{provider,model,direction}
    senharness_agent_cost_usd_total{provider,model}
    senharness_tool_calls_total{tool,status}
    senharness_eval_verdict_total{verdict}
    senharness_http_requests_total{method,path,status}
    senharness_http_request_duration_seconds{method,path}         (histogram)
    senharness_web_vital_value{metric,path}                       (histogram · ms / score)

All metrics are best-effort — failure to record never propagates to the
caller (the agent loop / HTTP handler must not break on metrics errors).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

_LOCK = threading.Lock()


# ─── Histogram buckets (seconds / tokens) ────────────────────
_LATENCY_BUCKETS = (
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
)


# Web Vitals span CLS (0-1) and LCP (~0-10000ms) — we normalize each
# metric to its native unit (CLS stays a score, the rest are ms) and
# use a generous logarithmic bucket ladder that covers both.
_WEB_VITAL_BUCKETS = (
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    50.0,
    100.0,
    250.0,
    500.0,
    1000.0,
    2500.0,
    5000.0,
    10000.0,
)


# ─── In-memory stores ────────────────────────────────────────
@dataclass(slots=True)
class _Counter:
    values: dict[tuple[tuple[str, str], ...], float] = field(default_factory=dict)

    def inc(self, labels: dict[str, str], amount: float = 1.0) -> None:
        key = tuple(sorted(labels.items()))
        self.values[key] = self.values.get(key, 0.0) + amount


@dataclass(slots=True)
class _Histogram:
    buckets: tuple[float, ...]
    # { label_tuple -> (bucket_counts[list], sum, count) }
    values: dict[tuple[tuple[str, str], ...], tuple[list[int], float, int]] = field(
        default_factory=dict
    )

    def observe(self, labels: dict[str, str], value: float) -> None:
        key = tuple(sorted(labels.items()))
        counts, total, n = self.values.get(key, ([0] * len(self.buckets), 0.0, 0))
        counts = list(counts)
        for i, boundary in enumerate(self.buckets):
            if value <= boundary:
                counts[i] += 1
                break
        else:  # overflow
            counts.append(counts[-1] + 1 if counts else 1)
            counts = counts[: len(self.buckets)]
            counts[-1] += 1
        self.values[key] = (counts, total + value, n + 1)


# ─── Module-level singletons ─────────────────────────────────
_runs = _Counter()
_tokens = _Counter()
_cost = _Counter()
_tool_calls = _Counter()
_eval_verdicts = _Counter()
_http_requests = _Counter()
_run_latency = _Histogram(_LATENCY_BUCKETS)
_http_latency = _Histogram(_LATENCY_BUCKETS)
_web_vitals = _Histogram(_WEB_VITAL_BUCKETS)


# ─── Public recording API ────────────────────────────────────
def record_run(
    *,
    provider: str,
    model: str,
    status: str,
    duration_s: float,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    try:
        with _LOCK:
            _runs.inc({"provider": provider, "model": model, "status": status})
            _run_latency.observe({"provider": provider, "model": model}, duration_s)
            _tokens.inc(
                {"provider": provider, "model": model, "direction": "input"},
                float(input_tokens),
            )
            _tokens.inc(
                {"provider": provider, "model": model, "direction": "output"},
                float(output_tokens),
            )
            _cost.inc({"provider": provider, "model": model}, float(cost_usd))
    except Exception:  # pragma: no cover
        pass


def record_tool_call(tool: str, *, status: str = "ok") -> None:
    try:
        with _LOCK:
            _tool_calls.inc({"tool": tool, "status": status})
    except Exception:  # pragma: no cover
        pass


def record_eval(verdict: str) -> None:
    try:
        with _LOCK:
            _eval_verdicts.inc({"verdict": verdict})
    except Exception:  # pragma: no cover
        pass


def record_http(method: str, path: str, status: int, duration_s: float) -> None:
    try:
        with _LOCK:
            _http_requests.inc({"method": method, "path": path, "status": str(status)})
            _http_latency.observe({"method": method, "path": path}, duration_s)
    except Exception:  # pragma: no cover
        pass


# Allow-list of Web Vital metric names we accept from the browser; anything
# else is silently dropped so the endpoint can't be used to write arbitrary
# labels into our Prometheus series set.
_WEB_VITAL_NAMES = frozenset({"CLS", "FCP", "INP", "LCP", "TTFB"})


def record_web_vital(name: str, value: float, path: str = "/") -> None:
    """Record a browser-reported Web Vital sample.

    ``name`` must be one of the standard Web Vitals names (CLS / FCP /
    INP / LCP / TTFB). ``value`` is in the metric's native unit (CLS is
    a score, the rest are milliseconds). ``path`` is the URL path to
    scope the sample by — truncated defensively so a hostile client
    can't balloon our label set.
    """
    try:
        upper = name.upper() if isinstance(name, str) else ""
        if upper not in _WEB_VITAL_NAMES:
            return
        safe_path = (path or "/")[:128]
        with _LOCK:
            _web_vitals.observe({"metric": upper, "path": safe_path}, float(value))
    except Exception:  # pragma: no cover
        pass


# ─── Exposition renderer ─────────────────────────────────────
def _fmt_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    parts = [f'{k}="{_escape(v)}"' for k, v in labels]
    return "{" + ",".join(parts) + "}"


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _render_counter(name: str, help_text: str, counter: _Counter) -> list[str]:
    out = [f"# HELP {name} {help_text}", f"# TYPE {name} counter"]
    for key, v in counter.values.items():
        out.append(f"{name}{_fmt_labels(key)} {v}")
    return out


def _render_histogram(name: str, help_text: str, hist: _Histogram) -> list[str]:
    out = [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
    for key, (counts, total, n) in hist.values.items():
        label_str = _fmt_labels(key)
        cumulative = 0
        for i, boundary in enumerate(hist.buckets):
            cumulative += counts[i]
            bucket_labels = dict(key) | {"le": str(boundary)}
            out.append(
                f"{name}_bucket"
                + _fmt_labels(tuple(sorted(bucket_labels.items())))
                + f" {cumulative}"
            )
        inf_labels = dict(key) | {"le": "+Inf"}
        out.append(f"{name}_bucket" + _fmt_labels(tuple(sorted(inf_labels.items()))) + f" {n}")
        out.append(f"{name}_sum{label_str} {total}")
        out.append(f"{name}_count{label_str} {n}")
    return out


def render_exposition() -> str:
    with _LOCK:
        lines: list[str] = []
        lines += _render_counter("senharness_agent_runs_total", "Total agent runs", _runs)
        lines += _render_histogram(
            "senharness_agent_run_duration_seconds",
            "Agent run duration in seconds",
            _run_latency,
        )
        lines += _render_counter(
            "senharness_agent_tokens_total",
            "LLM tokens processed (input + output, per direction)",
            _tokens,
        )
        lines += _render_counter(
            "senharness_agent_cost_usd_total",
            "Cumulative LLM cost in USD",
            _cost,
        )
        lines += _render_counter(
            "senharness_tool_calls_total",
            "Builtin + MCP tool invocations",
            _tool_calls,
        )
        lines += _render_counter(
            "senharness_eval_verdict_total",
            "Independent-evaluator verdicts",
            _eval_verdicts,
        )
        lines += _render_counter(
            "senharness_http_requests_total",
            "HTTP requests processed",
            _http_requests,
        )
        lines += _render_histogram(
            "senharness_http_request_duration_seconds",
            "HTTP request duration in seconds",
            _http_latency,
        )
        lines += _render_histogram(
            "senharness_web_vital_value",
            "Browser-reported Web Vitals (CLS score or ms).",
            _web_vitals,
        )
    return "\n".join(lines) + "\n"
