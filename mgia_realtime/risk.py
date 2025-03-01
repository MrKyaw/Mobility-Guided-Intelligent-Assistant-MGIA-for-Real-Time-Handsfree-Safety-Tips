"""Deterministic, explainable risk scoring.

The scorer uses a time-to-contact (TTC) heuristic rather than a learned model:
it is fast, auditable, runs on-device, and degrades predictably when depth is
missing.  The output is a 0-1 risk score plus enough context for the speech
layer to explain *why* an alert fired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

from .errors import ConfigurationError
from .events import (
    DetectedObject,
    Hazard,
    HazardKind,
    ObjectClass,
    PerceptionEvent,
    Position,
)

CLASS_WEIGHTS: MappingProxyType[ObjectClass, float] = MappingProxyType(
    {
        ObjectClass.PEDESTRIAN: 1.0,
        ObjectClass.CYCLIST: 0.95,
        ObjectClass.OBSTACLE: 0.9,
        ObjectClass.VEHICLE: 0.85,
        ObjectClass.DOOR: 0.8,
        ObjectClass.KERB: 0.6,
        ObjectClass.ROAD_SIGN: 0.4,
        ObjectClass.UNKNOWN: 0.5,
    }
)

POSITION_FACTORS: MappingProxyType[Position, float] = MappingProxyType(
    {
        Position.CENTER: 1.0,
        Position.LEFT: 0.88,
        Position.RIGHT: 0.88,
        Position.UNKNOWN: 0.72,
    }
)

# Hazard labels are more specific than object classes, so they receive a small
# bounded boost instead of replacing the geometric score.
HAZARD_BOOSTS: MappingProxyType[HazardKind, float] = MappingProxyType(
    {
        HazardKind.PEDESTRIAN_PATH: 1.0,
        HazardKind.VEHICLE_CONFLICT: 0.95,
        HazardKind.CYCLIST_PATH: 0.95,
        HazardKind.OBSTACLE: 0.9,
        HazardKind.ROAD_EDGE: 0.8,
        HazardKind.DOOR: 0.8,
        HazardKind.SIGNAL: 0.6,
        HazardKind.OTHER: 0.5,
    }
)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Clamp ``value`` into the inclusive ``[low, high]`` range."""
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """Tunable constants for the TTC risk heuristic."""

    horizon_s: float = 3.0
    static_clearance_m: float = 8.0
    unknown_distance_proximity: float = 0.55
    base_weight: float = 0.1
    hazard_boost: float = 0.1
    hazard_only_score: float = 0.5

    def __post_init__(self) -> None:
        if self.horizon_s <= 0.0:
            raise ConfigurationError("horizon_s must be greater than zero")
        if self.static_clearance_m <= 0.0:
            raise ConfigurationError("static_clearance_m must be greater than zero")
        if not 0.0 <= self.unknown_distance_proximity <= 1.0:
            raise ConfigurationError("unknown_distance_proximity must be between 0 and 1")
        if not 0.0 <= self.base_weight <= 1.0:
            raise ConfigurationError("base_weight must be between 0 and 1")
        if not 0.0 <= self.hazard_boost <= 1.0:
            raise ConfigurationError("hazard_boost must be between 0 and 1")
        if not 0.0 <= self.hazard_only_score <= 1.0:
            raise ConfigurationError("hazard_only_score must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ObjectRisk:
    """Per-object risk contribution and its explanatory signals."""

    object_id: str
    cls: ObjectClass
    score: float
    confidence: float
    distance_m: float | None
    ttc_s: float | None
    proximity: float
    position: Position
    relative_speed_mps: float
    distance_known: bool


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Frame-level risk decision consumed by the alarm state machine."""

    timestamp_s: float
    frame_id: int
    score: float
    object_risks: tuple[ObjectRisk, ...] = field(default_factory=tuple)
    primary: ObjectRisk | None = None
    hazard: Hazard | None = None
    reason: str = "no objects detected"


class RiskScorer:
    """Convert a :class:`PerceptionEvent` into a :class:`RiskAssessment`."""

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.config = config or RiskConfig()

    def score_object(self, obj: DetectedObject) -> ObjectRisk:
        """Score one detection.

        Approach speed drives a TTC proximity curve when depth is available.
        Without approach speed the scorer falls back to absolute clearance.
        Without depth it uses a configurable conservative proximity instead of
        treating the object as harmless.
        """
        ttc: float | None = None
        distance = obj.distance_m
        if distance is None:
            distance_known = False
            proximity = self.config.unknown_distance_proximity
        else:
            distance_known = True
            if obj.relative_speed_mps > 0.0:
                ttc = distance / obj.relative_speed_mps
                proximity = clamp((self.config.horizon_s - ttc) / self.config.horizon_s)
            else:
                proximity = clamp(
                    (self.config.static_clearance_m - distance)
                    / self.config.static_clearance_m
                )

        if obj.relative_speed_mps > 0.0:
            approach_factor = 1.0
        elif obj.relative_speed_mps == 0.0:
            approach_factor = 0.7
        else:
            approach_factor = 0.45

        weighted = (
            obj.confidence
            * CLASS_WEIGHTS[obj.cls]
            * (self.config.base_weight + (1.0 - self.config.base_weight) * proximity)
            * POSITION_FACTORS[obj.position]
            * approach_factor
        )
        return ObjectRisk(
            object_id=obj.object_id,
            cls=obj.cls,
            score=clamp(weighted),
            confidence=obj.confidence,
            distance_m=obj.distance_m,
            ttc_s=ttc,
            proximity=proximity,
            position=obj.position,
            relative_speed_mps=obj.relative_speed_mps,
            distance_known=distance_known,
        )

    def assess(self, event: PerceptionEvent) -> RiskAssessment:
        """Score every object and combine with annotated hazards.

        Object risks are sorted by descending score and then ``object_id`` so
        two frames with identical detections always produce identical output.
        """
        risks = sorted(
            (self.score_object(obj) for obj in event.objects),
            key=lambda item: (-item.score, item.object_id),
        )
        hazard = None
        if event.hazards:
            hazard = sorted(
                event.hazards,
                key=lambda item: (-item.confidence, item.kind.value, item.object_id or ""),
            )[0]

        if risks:
            score = risks[0].score
            if hazard is not None:
                boost = self.config.hazard_boost * hazard.confidence * HAZARD_BOOSTS[hazard.kind]
                score = clamp(score + boost)
        elif hazard is not None:
            score = clamp(self.config.hazard_only_score * hazard.confidence)
        else:
            score = 0.0

        primary = risks[0] if risks else None
        reason = self._explain(primary, hazard)
        return RiskAssessment(
            timestamp_s=event.timestamp_s,
            frame_id=event.frame_id,
            score=score,
            object_risks=tuple(risks),
            primary=primary,
            hazard=hazard,
            reason=reason,
        )

    @staticmethod
    def _explain(primary: ObjectRisk | None, hazard: Hazard | None) -> str:
        if primary is None:
            if hazard is not None:
                return f"annotated {hazard.kind.value} hazard without a tracked object"
            return "no objects detected"
        label = primary.cls.value.replace("_", " ")
        if not primary.distance_known:
            return f"{label} distance unavailable; conservative score {primary.score:.2f}"
        if primary.ttc_s is not None:
            return f"{label} at {primary.distance_m:.1f} m, contact in {primary.ttc_s:.1f} s"
        return f"{label} at {primary.distance_m:.1f} m, not approaching"
