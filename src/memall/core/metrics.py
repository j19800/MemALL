"""In-memory process metrics — counters, gauges, histograms."""

import threading
import time
from collections import defaultdict


class MetricsCollector:
    """Thread-safe, lock-free-ish metrics collector for single-process use.

    Public counters are thread-safe via Lock. Histogram buckets are
    simple lists — acceptable for per-process debugging, not for
    high-cardinality production pipelines.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._latencies: dict[str, list[float]] = defaultdict(list)  # tool_name → [durations]
        self._start_time = time.time()

    # ── counters ────────────────────────────────────────────

    def incr(self, name: str, delta: int = 1):
        with self._lock:
            self._counters[name] += delta

    def counter(self, name: str) -> int:
        return self._counters.get(name, 0)

    def counters(self) -> dict[str, int]:
        return dict(self._counters)

    # ── latency histograms ────────────────────────────────

    def record_latency(self, bucket: str, duration_ms: float):
        with self._lock:
            self._latencies[bucket].append(duration_ms)

    def latencies(self, bucket: str) -> list[float]:
        return list(self._latencies.get(bucket, []))

    def latency_p50(self, bucket: str) -> float | None:
        vals = sorted(self._latencies.get(bucket, []))
        if not vals:
            return None
        return vals[len(vals) // 2]

    def latency_p99(self, bucket: str) -> float | None:
        vals = sorted(self._latencies.get(bucket, []))
        if not vals:
            return None
        return vals[int(len(vals) * 0.99) - 1]

    # ── snapshot ──────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return a flat dict suitable for JSON serialisation."""
        uptime_s = int(time.time() - self._start_time)
        out: dict = {"uptime_s": uptime_s, "counters": dict(self._counters)}
        lat_snapshot: dict[str, dict] = {}
        for bucket, vals in self._latencies.items():
            sorted_vals = sorted(vals)
            lat_snapshot[bucket] = {
                "count": len(sorted_vals),
                "p50": sorted_vals[len(sorted_vals) // 2] if sorted_vals else 0,
                "p99": sorted_vals[int(len(sorted_vals) * 0.99) - 1] if sorted_vals else 0,
            }
        out["latencies"] = lat_snapshot
        return out


# Module-level singleton
_METRICS: MetricsCollector | None = None
_lock = threading.Lock()


def get_metrics() -> MetricsCollector:
    global _METRICS
    if _METRICS is None:
        with _lock:
            if _METRICS is None:
                _METRICS = MetricsCollector()
    return _METRICS
