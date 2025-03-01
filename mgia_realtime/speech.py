"""Priority-aware speech scheduling with deduplication and preemption.

An assistive device cannot narrate every detection.  This scheduler models the
parts that matter for hands-free safety:

* urgent guidance preempts a lower-priority utterance;
* repeated guidance is merged or suppressed by a cooldown;
* the speaking rate is capped so the user is not flooded;
* verbosity profiles suppress non-essential messages for concise users.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from types import MappingProxyType

from .errors import ConfigurationError, InvalidEventError
from .metrics import SessionMetrics


class VerbosityProfile(str, Enum):
    """How much guidance a user wants to hear."""

    CONCISE = "concise"
    STANDARD = "standard"
    VERBOSE = "verbose"


class MessagePriority(IntEnum):
    """Ordered speech priority; higher values interrupt lower values."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


_PROFILE_MINIMUM: MappingProxyType[VerbosityProfile, MessagePriority] = MappingProxyType(
    {
        VerbosityProfile.CONCISE: MessagePriority.HIGH,
        VerbosityProfile.STANDARD: MessagePriority.NORMAL,
        VerbosityProfile.VERBOSE: MessagePriority.LOW,
    }
)


class EnqueueStatus(str, Enum):
    """Outcome of attempting to queue a message."""

    ACCEPTED = "accepted"
    COALESCED = "coalesced"
    SUPPRESSED_COOLDOWN = "suppressed_cooldown"
    DROPPED_PROFILE = "dropped_profile"
    DROPPED_EXPIRED = "dropped_expired"
    DROPPED_OVERFLOW = "dropped_overflow"


@dataclass(frozen=True, slots=True)
class SpeechMessage:
    """A candidate utterance with a stable identity and dedupe key."""

    message_id: str
    text: str
    priority: MessagePriority
    created_at_s: float
    dedupe_key: str = ""
    expires_at_s: float | None = None
    duration_s: float = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.message_id, str) or not self.message_id.strip():
            raise InvalidEventError("message_id must be a non-empty string")
        if not isinstance(self.text, str) or not self.text.strip():
            raise InvalidEventError("text must be a non-empty string")
        if not isinstance(self.priority, MessagePriority):
            raise InvalidEventError("priority must be a MessagePriority")
        if not isinstance(self.dedupe_key, str):
            raise InvalidEventError("dedupe_key must be a string")
        created = float(self.created_at_s)
        if not math.isfinite(created) or created < 0.0:
            raise InvalidEventError("created_at_s must be finite and non-negative")
        if self.expires_at_s is not None:
            expires = float(self.expires_at_s)
            if not math.isfinite(expires) or expires < created:
                raise InvalidEventError("expires_at_s must be finite and not before creation")
            object.__setattr__(self, "expires_at_s", expires)
        # A small deterministic estimate used for occupancy, not a TTS claim.
        object.__setattr__(self, "duration_s", max(0.7, len(self.text.split()) / 2.6))


@dataclass(frozen=True, slots=True)
class SpokenMessage:
    """A message emitted by the scheduler at a concrete session time."""

    message: SpeechMessage
    started_at_s: float
    latency_s: float


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    """Structured result so callers can report why a message was not spoken."""

    status: EnqueueStatus
    bucket: str
    message_id: str | None = None
    reason: str = ""

    @property
    def accepted(self) -> bool:
        """True when the message entered the pending queue."""
        return self.status is EnqueueStatus.ACCEPTED


@dataclass(frozen=True, slots=True)
class SpeechConfig:
    """Scheduler limits."""

    cooldown_s: float = 4.0
    max_messages_per_second: float = 2.0
    max_pending: int = 16

    def __post_init__(self) -> None:
        if self.cooldown_s < 0.0:
            raise ConfigurationError("cooldown_s must be non-negative")
        if self.max_messages_per_second <= 0.0:
            raise ConfigurationError("max_messages_per_second must be greater than zero")
        if self.max_pending < 1:
            raise ConfigurationError("max_pending must be at least one")

    @property
    def min_interval_s(self) -> float:
        """Minimum time between two non-critical utterance starts."""
        return 1.0 / self.max_messages_per_second


class SpeechScheduler:
    """Heap-based priority queue with cooldowns and urgent preemption."""

    def __init__(
        self,
        config: SpeechConfig | None = None,
        profile: VerbosityProfile = VerbosityProfile.STANDARD,
        metrics: SessionMetrics | None = None,
    ) -> None:
        self.config = config or SpeechConfig()
        self.profile = profile
        self.metrics = metrics or SessionMetrics()
        self._pending: dict[str, SpeechMessage] = {}
        self._heap: list[tuple[int, int, str, str]] = []
        self._sequence = 0
        self._cooldown_until: dict[str, float] = {}
        self._last_emit_at: float | None = None
        self._active: SpeechMessage | None = None
        self._active_bucket: str | None = None
        self._active_until = 0.0

    @property
    def pending_count(self) -> int:
        """Number of messages currently waiting to speak."""
        return len(self._pending)

    @property
    def active_message(self) -> SpeechMessage | None:
        """The utterance currently occupying the speech channel, if any."""
        return self._active

    def allows(self, priority: MessagePriority) -> bool:
        """Whether the active verbosity profile admits ``priority``."""
        return priority >= _PROFILE_MINIMUM[self.profile]

    def _push(self, bucket: str, message: SpeechMessage) -> None:
        self._sequence += 1
        heapq.heappush(
            self._heap,
            (-int(message.priority), self._sequence, bucket, message.message_id),
        )

    def _drop(self, bucket: str) -> None:
        self._pending.pop(bucket, None)
        self.metrics.record_dropped()

    def enqueue(self, message: SpeechMessage, now_s: float) -> EnqueueResult:
        """Attempt to queue ``message`` at session time ``now_s``.

        Cooldowns begin when speech finishes, so a long warning does not
        immediately repeat after it ends.
        """
        bucket = message.dedupe_key or f"id:{message.message_id}"
        if not self.allows(message.priority):
            self.metrics.record_dropped()
            return EnqueueResult(
                EnqueueStatus.DROPPED_PROFILE,
                bucket,
                message.message_id,
                "priority below profile minimum",
            )
        if message.expires_at_s is not None and message.expires_at_s <= now_s:
            self.metrics.record_dropped()
            return EnqueueResult(
                EnqueueStatus.DROPPED_EXPIRED,
                bucket,
                message.message_id,
                "message expired before enqueue",
            )
        if self._active is not None and self._active_bucket == bucket:
            self.metrics.record_suppressed()
            return EnqueueResult(
                EnqueueStatus.SUPPRESSED_COOLDOWN,
                bucket,
                message.message_id,
                "equivalent message is already speaking",
            )
        if self._cooldown_until.get(bucket, -math.inf) > now_s:
            self.metrics.record_suppressed()
            return EnqueueResult(
                EnqueueStatus.SUPPRESSED_COOLDOWN,
                bucket,
                message.message_id,
                "deduplication cooldown",
            )
        if bucket in self._pending:
            existing = self._pending[bucket]
            self.metrics.record_coalesced()
            if message.priority >= existing.priority:
                self._pending[bucket] = message
                self._push(bucket, message)
                return EnqueueResult(
                    EnqueueStatus.COALESCED,
                    bucket,
                    message.message_id,
                    "replaced queued message",
                )
            return EnqueueResult(
                EnqueueStatus.COALESCED,
                bucket,
                existing.message_id,
                "kept higher-priority queued message",
            )
        if len(self._pending) >= self.config.max_pending:
            lowest_bucket, lowest = min(
                self._pending.items(),
                key=lambda item: (item[1].priority, item[1].created_at_s, item[0]),
            )
            if message.priority <= lowest.priority:
                self.metrics.record_dropped()
                return EnqueueResult(
                    EnqueueStatus.DROPPED_OVERFLOW,
                    bucket,
                    message.message_id,
                    "queue full",
                )
            self._drop(lowest_bucket)
        self._pending[bucket] = message
        self._push(bucket, message)
        self.metrics.record_message_enqueued()
        return EnqueueResult(EnqueueStatus.ACCEPTED, bucket, message.message_id)

    def _peek(self, now_s: float) -> SpeechMessage | None:
        """Discard stale/expired heap entries and return the next message."""
        while self._heap:
            _, _, bucket, message_id = self._heap[0]
            message = self._pending.get(bucket)
            if message is None or message.message_id != message_id:
                heapq.heappop(self._heap)
                continue
            if message.expires_at_s is not None and message.expires_at_s <= now_s:
                heapq.heappop(self._heap)
                self._drop(bucket)
                continue
            return message
        return None

    def tick(self, now_s: float) -> list[SpokenMessage]:
        """Advance the clock and optionally return one newly spoken message.

        The pipeline calls this once per frame.  At most one utterance starts
        per tick, which keeps ordering deterministic and makes latency
        accounting unambiguous.
        """
        if self._active is not None and now_s >= self._active_until:
            self._active = None
            self._active_bucket = None
        candidate = self._peek(now_s)
        if candidate is None:
            return []

        spacing_ok = (
            self._last_emit_at is None
            or now_s - self._last_emit_at >= self.config.min_interval_s
        )
        if self._active is not None:
            if candidate.priority <= self._active.priority:
                return []
            if not spacing_ok and candidate.priority < MessagePriority.CRITICAL:
                return []
            self.metrics.record_preempted()
            self._active = None
            self._active_bucket = None
        elif not spacing_ok and candidate.priority < MessagePriority.CRITICAL:
            return []

        bucket = candidate.dedupe_key or f"id:{candidate.message_id}"
        _, _, _, _ = heapq.heappop(self._heap)
        del self._pending[bucket]
        self._active = candidate
        self._active_bucket = bucket
        self._active_until = now_s + candidate.duration_s
        self._last_emit_at = now_s
        self._cooldown_until[bucket] = self._active_until + self.config.cooldown_s
        latency = max(0.0, now_s - candidate.created_at_s)
        self.metrics.record_spoken(candidate.text, latency)
        return [SpokenMessage(message=candidate, started_at_s=now_s, latency_s=latency)]
