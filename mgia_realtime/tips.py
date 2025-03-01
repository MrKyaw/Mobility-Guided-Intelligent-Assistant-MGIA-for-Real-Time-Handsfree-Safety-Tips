"""Hands-free safety tip policy.

The policy maps a risk assessment and a mobility context to short, speakable
guidance.  Templates are hazard-specific, alternate phrasing when a hazard
persists, and adapt detail to the listener's verbosity profile.  Nothing here
calls a model or the network, so phrasing is deterministic and testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from .alarm import AlarmLevel
from .errors import ConfigurationError
from .events import HazardKind, MobilityContext, ObjectClass, Position
from .risk import RiskAssessment
from .speech import MessagePriority, SpeechMessage, VerbosityProfile

_CLASS_TO_HAZARD: MappingProxyType[ObjectClass, HazardKind] = MappingProxyType(
    {
        ObjectClass.PEDESTRIAN: HazardKind.PEDESTRIAN_PATH,
        ObjectClass.CYCLIST: HazardKind.CYCLIST_PATH,
        ObjectClass.VEHICLE: HazardKind.VEHICLE_CONFLICT,
        ObjectClass.OBSTACLE: HazardKind.OBSTACLE,
        ObjectClass.KERB: HazardKind.ROAD_EDGE,
        ObjectClass.DOOR: HazardKind.DOOR,
        ObjectClass.ROAD_SIGN: HazardKind.SIGNAL,
        ObjectClass.UNKNOWN: HazardKind.OTHER,
    }
)

_NOUNS: MappingProxyType[HazardKind, str] = MappingProxyType(
    {
        HazardKind.PEDESTRIAN_PATH: "pedestrian",
        HazardKind.VEHICLE_CONFLICT: "vehicle",
        HazardKind.CYCLIST_PATH: "cyclist",
        HazardKind.OBSTACLE: "obstacle",
        HazardKind.ROAD_EDGE: "road edge",
        HazardKind.DOOR: "doorway",
        HazardKind.SIGNAL: "signal",
        HazardKind.OTHER: "hazard",
    }
)

_CRITICAL: MappingProxyType[HazardKind, str] = MappingProxyType(
    {
        HazardKind.PEDESTRIAN_PATH: "Pedestrian in your path. Stop now.",
        HazardKind.VEHICLE_CONFLICT: "Vehicle crossing ahead. Stop and wait.",
        HazardKind.CYCLIST_PATH: "Cyclist ahead. Give way and stop.",
        HazardKind.OBSTACLE: "Obstacle in your path. Stop now.",
        HazardKind.ROAD_EDGE: "You are at the road edge. Stop and check.",
        HazardKind.DOOR: "Doorway ahead. Stop and check.",
        HazardKind.SIGNAL: "Signal requires a stop.",
        HazardKind.OTHER: "Hazard directly ahead. Stop now.",
    }
)

_CONTEXT_CUES: MappingProxyType[MobilityContext, str] = MappingProxyType(
    {
        MobilityContext.WALKING: "Keep to the clearer side of the path.",
        MobilityContext.TRANSIT: "Hold the handrail and keep your stance stable.",
        MobilityContext.CROSSING: "Stay behind the kerb until the path is clear.",
    }
)

_PRIORITY: MappingProxyType[AlarmLevel, MessagePriority] = MappingProxyType(
    {
        AlarmLevel.CLEAR: MessagePriority.LOW,
        AlarmLevel.WATCH: MessagePriority.NORMAL,
        AlarmLevel.WARN: MessagePriority.HIGH,
        AlarmLevel.CRITICAL: MessagePriority.CRITICAL,
    }
)

_TTL_S: MappingProxyType[AlarmLevel, float] = MappingProxyType(
    {
        AlarmLevel.CLEAR: 1.0,
        AlarmLevel.WATCH: 6.0,
        AlarmLevel.WARN: 4.0,
        AlarmLevel.CRITICAL: 2.5,
    }
)


@dataclass(frozen=True, slots=True)
class TipConfig:
    """Policy tuning for repetition control."""

    repeat_window_s: float = 8.0

    def __post_init__(self) -> None:
        if self.repeat_window_s < 0.0:
            raise ConfigurationError("repeat_window_s must be non-negative")


@dataclass(frozen=True, slots=True)
class Tip:
    """A selected tip and the metadata needed to schedule it."""

    text: str
    priority: MessagePriority
    hazard: HazardKind
    level: AlarmLevel
    context: MobilityContext
    dedupe_key: str
    variant: int
    created_at_s: float
    expires_at_s: float

    def to_message(self, message_id: str) -> SpeechMessage:
        """Convert the tip into a scheduler message."""
        return SpeechMessage(
            message_id=message_id,
            text=self.text,
            priority=self.priority,
            created_at_s=self.created_at_s,
            dedupe_key=self.dedupe_key,
            expires_at_s=self.expires_at_s,
        )


def _direction(position: Position) -> str:
    if position is Position.LEFT:
        return "on your left"
    if position is Position.RIGHT:
        return "on your right"
    if position is Position.CENTER:
        return "directly ahead"
    return "ahead"


class SafetyTipPolicy:
    """Deterministic tip selector with a small repetition memory."""

    def __init__(self, config: TipConfig | None = None) -> None:
        self.config = config or TipConfig()
        self._history: dict[str, tuple[float, int]] = {}
        self._sequence = 0

    def _hazard(self, assessment: RiskAssessment) -> HazardKind:
        if assessment.hazard is not None:
            return assessment.hazard.kind
        if assessment.primary is not None:
            return _CLASS_TO_HAZARD[assessment.primary.cls]
        return HazardKind.OTHER

    def _core_text(self, hazard: HazardKind, level: AlarmLevel, variant: int, assessment: RiskAssessment) -> str:
        noun = _NOUNS[hazard]
        direction = _direction(
            assessment.primary.position if assessment.primary is not None else Position.UNKNOWN
        )
        distance_known = assessment.primary is not None and assessment.primary.distance_known
        distance = (
            f"{assessment.primary.distance_m:.0f} metres away"
            if distance_known and assessment.primary is not None and assessment.primary.distance_m is not None
            else "close by"
        )
        if level is AlarmLevel.CRITICAL:
            if variant == 0:
                return _CRITICAL[hazard]
            return f"Still too close. Keep stopped until the {noun} clears."
        if level is AlarmLevel.WARN:
            if variant == 0:
                return f"{noun.capitalize()} {direction}, {distance}. Slow down."
            return f"Still tracking the {noun} {direction}. Keep your pace slow."
        if variant == 0:
            return f"{noun.capitalize()} detected {direction}. Keep scanning."
        return f"Continuing to track the {noun} {direction}."

    @staticmethod
    def _detail(assessment: RiskAssessment) -> str:
        primary = assessment.primary
        if primary is None:
            return "Hazard marked by perception."
        if primary.ttc_s is not None:
            return f"Estimated contact in {primary.ttc_s:.1f} seconds."
        if primary.distance_known:
            return f"Estimated clearance {primary.distance_m:.1f} metres."
        return "Depth estimate unavailable; guidance is conservative."

    def select(
        self,
        assessment: RiskAssessment,
        level: AlarmLevel,
        context: MobilityContext,
        profile: VerbosityProfile,
        now_s: float,
    ) -> Tip | None:
        """Choose a tip, or ``None`` while the path is clear."""
        if level is AlarmLevel.CLEAR:
            return None
        hazard = self._hazard(assessment)
        dedupe_key = f"{hazard.value}:{level.name.lower()}:{context.value}"
        previous = self._history.get(dedupe_key)
        if previous is not None and now_s - previous[0] < self.config.repeat_window_s:
            variant = (previous[1] + 1) % 2
        else:
            variant = 0
        self._history[dedupe_key] = (now_s, variant)

        core = self._core_text(hazard, level, variant, assessment)
        cue = _CONTEXT_CUES[context]
        if profile is VerbosityProfile.CONCISE:
            text = core
        elif profile is VerbosityProfile.STANDARD:
            text = f"{core} {cue}"
        else:
            text = f"{core} {cue} {self._detail(assessment)}"
        self._sequence += 1
        return Tip(
            text=text,
            priority=_PRIORITY[level],
            hazard=hazard,
            level=level,
            context=context,
            dedupe_key=dedupe_key,
            variant=variant,
            created_at_s=now_s,
            expires_at_s=now_s + _TTL_S[level],
        )
