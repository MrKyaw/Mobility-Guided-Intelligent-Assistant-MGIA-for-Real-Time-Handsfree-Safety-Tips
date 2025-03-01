"""End-to-end real-time pipeline orchestrator.

``RealtimePipeline`` deliberately has no threads, network calls, or model
dependencies.  A frame enters, risk and alarm state are updated, a tip may be
queued, and at most one timestamped guidance message is emitted.  That
determinism is what makes latency and ordering testable.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from .alarm import AlarmConfig, AlarmLevel, AlarmStateMachine, AlarmTransition
from .errors import OutOfOrderFrameError
from .events import PerceptionEvent
from .metrics import MetricsSnapshot, SessionMetrics
from .risk import RiskAssessment, RiskConfig, RiskScorer
from .speech import (
    MessagePriority,
    SpeechConfig,
    SpeechScheduler,
    VerbosityProfile,
)
from .tips import SafetyTipPolicy, TipConfig


@dataclass(frozen=True, slots=True)
class Guidance:
    """One ordered, timestamped utterance emitted by the pipeline."""

    timestamp_s: float
    frame_id: int
    level: AlarmLevel
    text: str
    priority: MessagePriority
    message_id: str
    dedupe_key: str
    latency_s: float

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable representation."""
        return {
            "timestamp_s": self.timestamp_s,
            "frame_id": self.frame_id,
            "level": self.level.name,
            "text": self.text,
            "priority": self.priority.name,
            "message_id": self.message_id,
            "dedupe_key": self.dedupe_key,
            "latency_s": round(self.latency_s, 6),
        }


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Configuration bundle; each stage remains independently tunable."""

    risk: RiskConfig = field(default_factory=RiskConfig)
    alarm: AlarmConfig = field(default_factory=AlarmConfig)
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    tips: TipConfig = field(default_factory=TipConfig)
    profile: VerbosityProfile = VerbosityProfile.STANDARD


class RealtimePipeline:
    """Consume perception frames and emit ordered guidance."""

    def __init__(
        self,
        config: PipelineConfig | None = None,
        metrics: SessionMetrics | None = None,
    ) -> None:
        self.config = config or PipelineConfig()
        self.metrics = metrics or SessionMetrics()
        self.scorer = RiskScorer(self.config.risk)
        self.alarm = AlarmStateMachine(self.config.alarm)
        self.tips = SafetyTipPolicy(self.config.tips)
        self.scheduler = SpeechScheduler(
            self.config.speech,
            profile=self.config.profile,
            metrics=self.metrics,
        )
        self.last_assessment: RiskAssessment | None = None
        self.last_transition: AlarmTransition | None = None
        self._last_timestamp: float | None = None
        self._message_sequence = 0

    def process(self, event: PerceptionEvent) -> list[Guidance]:
        """Process one frame.

        Returns zero or one guidance message.  Frames must arrive in
        non-decreasing timestamp order; a late frame raises
        :class:`OutOfOrderFrameError` instead of silently reordering safety
        output.
        """
        if self._last_timestamp is not None and event.timestamp_s < self._last_timestamp:
            raise OutOfOrderFrameError(
                f"frame {event.frame_id} at {event.timestamp_s:.3f}s arrived after "
                f"{self._last_timestamp:.3f}s"
            )
        self._last_timestamp = event.timestamp_s

        assessment = self.scorer.assess(event)
        self.last_assessment = assessment
        transition = self.alarm.update(assessment.score, event.timestamp_s)
        self.last_transition = transition
        if transition is not None:
            self.metrics.record_transition()
            if transition.escalated:
                self.metrics.record_alert(transition.current)
        self.metrics.record_frame(event.timestamp_s, self.alarm.current)

        tip = self.tips.select(
            assessment,
            self.alarm.current,
            event.context,
            self.config.profile,
            event.timestamp_s,
        )
        if tip is not None:
            self._message_sequence += 1
            message = tip.to_message(f"tip-{self._message_sequence:04d}")
            self.scheduler.enqueue(message, event.timestamp_s)

        spoken = self.scheduler.tick(event.timestamp_s)
        return [
            Guidance(
                timestamp_s=item.started_at_s,
                frame_id=event.frame_id,
                level=self.alarm.current,
                text=item.message.text,
                priority=item.message.priority,
                message_id=item.message.message_id,
                dedupe_key=item.message.dedupe_key,
                latency_s=item.latency_s,
            )
            for item in spoken
        ]

    def run(self, events: Iterable[PerceptionEvent]) -> list[Guidance]:
        """Process a finite stream and return every emitted guidance message."""
        output: list[Guidance] = []
        for event in events:
            output.extend(self.process(event))
        return output

    def stream(self, events: Iterable[PerceptionEvent]) -> Iterator[Guidance]:
        """Lazily yield guidance; useful for simulated real-time playback."""
        for event in events:
            yield from self.process(event)

    def snapshot(self) -> MetricsSnapshot:
        """Export current session metrics."""
        return self.metrics.snapshot()
