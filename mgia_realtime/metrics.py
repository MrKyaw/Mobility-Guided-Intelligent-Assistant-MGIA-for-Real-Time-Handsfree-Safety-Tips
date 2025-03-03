"""Session metrics for safety, latency, and speech-quality reporting.

Metrics are intentionally in-memory and allocation-light: the pipeline must not
block on dashboards.  A :class:`MetricsSnapshot` is an immutable export suitable
for JSON, logs, or a reviewer-facing demo summary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .alarm import AlarmLevel
from .errors import OutOfOrderFrameError


def percentile(samples: list[float], fraction: float) -> float | None:
    """Linear-interpolated percentile, or ``None`` for an empty sample set.

    ``fraction`` is in ``[0, 1]`` (for example ``0.95`` for p95).  Linear
    interpolation avoids the coarse jumps of nearest-rank on small demo runs
    while remaining deterministic.
    """
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0 and 1")
    if not samples:
        return None
    ordered = sorted(samples)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * fraction
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """Immutable point-in-time export of a session."""

    frames_processed: int
    alerts: dict[str, int]
    transitions: int
    messages_enqueued: int
    messages_spoken: int
    messages_dropped: int
    messages_coalesced: int
    messages_suppressed: int
    messages_preempted: int
    words_spoken: int
    latency_p50_s: float | None
    latency_p95_s: float | None
    latency_p99_s: float | None
    time_in_state_s: dict[str, float]
    total_s: float

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable dictionary with stable key names."""
        return {
            "frames_processed": self.frames_processed,
            "alerts": dict(self.alerts),
            "transitions": self.transitions,
            "messages": {
                "enqueued": self.messages_enqueued,
                "spoken": self.messages_spoken,
                "dropped": self.messages_dropped,
                "coalesced": self.messages_coalesced,
                "suppressed": self.messages_suppressed,
                "preempted": self.messages_preempted,
                "words_spoken": self.words_spoken,
            },
            "latency_s": {
                "p50": self.latency_p50_s,
                "p95": self.latency_p95_s,
                "p99": self.latency_p99_s,
            },
            "time_in_state_s": dict(self.time_in_state_s),
            "total_s": self.total_s,
        }


class SessionMetrics:
    """Accumulates counters and latency samples for one assistive session."""

    def __init__(self) -> None:
        self.frames_processed = 0
        self.transitions = 0
        self.messages_enqueued = 0
        self.messages_spoken = 0
        self.messages_dropped = 0
        self.messages_coalesced = 0
        self.messages_suppressed = 0
        self.messages_preempted = 0
        self.words_spoken = 0
        self._alerts: dict[AlarmLevel, int] = {level: 0 for level in AlarmLevel}
        self._latencies: list[float] = []
        self._time_in_state: dict[AlarmLevel, float] = {level: 0.0 for level in AlarmLevel}
        self._current_level = AlarmLevel.CLEAR
        self._last_timestamp: float | None = None

    def record_frame(self, timestamp_s: float, level: AlarmLevel) -> None:
        """Account for elapsed time and count one processed frame."""
        if self._last_timestamp is not None:
            elapsed = timestamp_s - self._last_timestamp
            if elapsed < 0.0:
                raise OutOfOrderFrameError("metrics timestamps must be non-decreasing")
            self._time_in_state[self._current_level] += elapsed
        self._current_level = level
        self._last_timestamp = timestamp_s
        self.frames_processed += 1

    def record_alert(self, level: AlarmLevel) -> None:
        """Count one alert announcement at ``level``."""
        self._alerts[level] += 1

    def record_transition(self) -> None:
        """Count one alarm state change in either direction."""
        self.transitions += 1

    def record_message_enqueued(self) -> None:
        """Count a message accepted by the speech scheduler."""
        self.messages_enqueued += 1

    def record_spoken(self, text: str, latency_s: float) -> None:
        """Count a spoken message and retain its end-to-end latency."""
        self.messages_spoken += 1
        self.words_spoken += len(text.split())
        self._latencies.append(max(0.0, latency_s))

    def record_dropped(self, count: int = 1) -> None:
        """Count messages discarded for expiry or queue pressure."""
        self.messages_dropped += count

    def record_coalesced(self) -> None:
        """Count messages merged with an equivalent pending message."""
        self.messages_coalesced += 1

    def record_suppressed(self) -> None:
        """Count messages suppressed by a deduplication cooldown."""
        self.messages_suppressed += 1

    def record_preempted(self) -> None:
        """Count lower-priority speech interrupted by urgent guidance."""
        self.messages_preempted += 1

    @property
    def latency_percentiles(self) -> dict[str, float | None]:
        """Return p50/p95/p99 end-to-end guidance latency in seconds."""
        return {
            "p50": percentile(self._latencies, 0.50),
            "p95": percentile(self._latencies, 0.95),
            "p99": percentile(self._latencies, 0.99),
        }

    def snapshot(self) -> MetricsSnapshot:
        """Freeze the current counters and latency distribution."""
        latencies = self.latency_percentiles
        total = self._last_timestamp if self._last_timestamp is not None else 0.0
        return MetricsSnapshot(
            frames_processed=self.frames_processed,
            alerts={level.name: count for level, count in self._alerts.items()},
            transitions=self.transitions,
            messages_enqueued=self.messages_enqueued,
            messages_spoken=self.messages_spoken,
            messages_dropped=self.messages_dropped,
            messages_coalesced=self.messages_coalesced,
            messages_suppressed=self.messages_suppressed,
            messages_preempted=self.messages_preempted,
            words_spoken=self.words_spoken,
            latency_p50_s=latencies["p50"],
            latency_p95_s=latencies["p95"],
            latency_p99_s=latencies["p99"],
            time_in_state_s={
                level.name: round(duration, 6)
                for level, duration in self._time_in_state.items()
            },
            total_s=total,
        )
