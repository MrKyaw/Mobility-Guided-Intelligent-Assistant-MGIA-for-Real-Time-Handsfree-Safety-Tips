"""Typed, validated perception events for the streaming safety pipeline.

The real system receives frames from cameras and wearables.  This module keeps
that boundary explicit: every frame is normalised into immutable dataclasses so
downstream risk, alarm, and speech logic never has to guess about missing or
malformed fields.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .errors import InvalidEventError


class ObjectClass(str, Enum):
    """Coarse object classes emitted by the perception layer."""

    PEDESTRIAN = "pedestrian"
    VEHICLE = "vehicle"
    CYCLIST = "cyclist"
    OBSTACLE = "obstacle"
    ROAD_SIGN = "road_sign"
    KERB = "kerb"
    DOOR = "door"
    UNKNOWN = "unknown"


class Position(str, Enum):
    """Horizontal position of an object in the user's forward view."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    UNKNOWN = "unknown"


class MobilityContext(str, Enum):
    """The user's current mobility situation."""

    WALKING = "walking"
    TRANSIT = "transit"
    CROSSING = "crossing"


class HazardKind(str, Enum):
    """Semantic hazard labels that can override raw object classes."""

    PEDESTRIAN_PATH = "pedestrian_path"
    VEHICLE_CONFLICT = "vehicle_conflict"
    CYCLIST_PATH = "cyclist_path"
    OBSTACLE = "obstacle"
    ROAD_EDGE = "road_edge"
    DOOR = "door"
    SIGNAL = "signal"
    OTHER = "other"


def _as_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidEventError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidEventError(f"{field_name} must be a finite number")
    return number


def _as_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidEventError(f"{field_name} must be an integer")
    if value < 0:
        raise InvalidEventError(f"{field_name} must be non-negative")
    return value


def _fraction(value: Any, field_name: str) -> float:
    number = _as_float(value, field_name)
    if not 0.0 <= number <= 1.0:
        raise InvalidEventError(f"{field_name} must be between 0 and 1")
    return number


def _parse_enum(value: Any, enum_type: type[Enum], fallback: Enum, field_name: str) -> Any:
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise InvalidEventError(f"{field_name} must be a string or {enum_type.__name__}")
    try:
        return enum_type(value.strip().lower())
    except ValueError:
        return fallback


@dataclass(frozen=True, slots=True)
class DetectedObject:
    """One perceived object with the fields needed for risk estimation.

    ``distance_m`` is optional because depth estimation fails more often than
    classification.  A missing distance is handled conservatively rather than
    silently discarded.  ``relative_speed_mps`` is positive when the object is
    approaching the user.
    """

    object_id: str
    cls: ObjectClass
    confidence: float
    distance_m: float | None = None
    relative_speed_mps: float = 0.0
    bearing_deg: float = 0.0
    position: Position = Position.UNKNOWN

    def __post_init__(self) -> None:
        if not isinstance(self.object_id, str) or not self.object_id.strip():
            raise InvalidEventError("object_id must be a non-empty string")
        if not isinstance(self.cls, ObjectClass):
            raise InvalidEventError("cls must be an ObjectClass")
        if not isinstance(self.position, Position):
            raise InvalidEventError("position must be a Position")
        object.__setattr__(self, "confidence", _fraction(self.confidence, "confidence"))
        if self.distance_m is not None:
            distance = _as_float(self.distance_m, "distance_m")
            if distance <= 0.0:
                raise InvalidEventError("distance_m must be greater than zero")
            object.__setattr__(self, "distance_m", distance)
        object.__setattr__(
            self,
            "relative_speed_mps",
            _as_float(self.relative_speed_mps, "relative_speed_mps"),
        )
        bearing = _as_float(self.bearing_deg, "bearing_deg")
        if not -180.0 <= bearing <= 180.0:
            raise InvalidEventError("bearing_deg must be between -180 and 180")
        object.__setattr__(self, "bearing_deg", bearing)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DetectedObject:
        """Build an object from a JSON-compatible mapping."""
        if not isinstance(payload, Mapping):
            raise InvalidEventError("object entries must be JSON objects")
        object_id = payload.get("object_id", payload.get("id"))
        if not isinstance(object_id, str) or not object_id.strip():
            raise InvalidEventError("object_id must be a non-empty string")
        distance_value = payload.get("distance_m", payload.get("distance"))
        distance = None if distance_value is None else _as_float(distance_value, "distance_m")
        return cls(
            object_id=object_id,
            cls=_parse_enum(payload.get("cls", payload.get("class", ObjectClass.UNKNOWN)), ObjectClass, ObjectClass.UNKNOWN, "cls"),
            confidence=payload.get("confidence", 1.0),
            distance_m=distance,
            relative_speed_mps=payload.get("relative_speed_mps", payload.get("relative_speed", 0.0)),
            bearing_deg=payload.get("bearing_deg", payload.get("bearing", 0.0)),
            position=_parse_enum(payload.get("position", Position.UNKNOWN), Position, Position.UNKNOWN, "position"),
        )


@dataclass(frozen=True, slots=True)
class Hazard:
    """A semantic hazard annotation attached to a frame.

    Hazards can reinforce a detection (for example, a kerb annotated as a road
    edge) or stand alone when the perception layer cannot produce an object.
    """

    kind: HazardKind
    confidence: float = 1.0
    object_id: str | None = None
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, HazardKind):
            raise InvalidEventError("hazard kind must be a HazardKind")
        object.__setattr__(self, "confidence", _fraction(self.confidence, "hazard confidence"))
        if self.object_id is not None and (
            not isinstance(self.object_id, str) or not self.object_id.strip()
        ):
            raise InvalidEventError("hazard object_id must be a non-empty string when provided")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Hazard:
        """Build a hazard from a JSON-compatible mapping."""
        if not isinstance(payload, Mapping):
            raise InvalidEventError("hazard entries must be JSON objects")
        return cls(
            kind=_parse_enum(payload.get("kind", HazardKind.OTHER), HazardKind, HazardKind.OTHER, "kind"),
            confidence=payload.get("confidence", 1.0),
            object_id=payload.get("object_id"),
            note=str(payload.get("note", "")),
        )


@dataclass(frozen=True, slots=True)
class PerceptionEvent:
    """An immutable frame emitted by the perception layer.

    Timestamps are monotonic seconds from a session clock.  The pipeline, not
    this class, enforces ordering; this keeps frame construction cheap and lets
    tests build events out of order on purpose.
    """

    timestamp_s: float
    frame_id: int
    objects: tuple[DetectedObject, ...] = field(default_factory=tuple)
    hazards: tuple[Hazard, ...] = field(default_factory=tuple)
    context: MobilityContext = MobilityContext.WALKING

    def __post_init__(self) -> None:
        timestamp = _as_float(self.timestamp_s, "timestamp_s")
        if timestamp < 0.0:
            raise InvalidEventError("timestamp_s must be non-negative")
        object.__setattr__(self, "timestamp_s", timestamp)
        object.__setattr__(self, "frame_id", _as_int(self.frame_id, "frame_id"))
        if not isinstance(self.context, MobilityContext):
            raise InvalidEventError("context must be a MobilityContext")
        objects = tuple(self.objects)
        hazards = tuple(self.hazards)
        if not all(isinstance(item, DetectedObject) for item in objects):
            raise InvalidEventError("objects must contain DetectedObject instances")
        if not all(isinstance(item, Hazard) for item in hazards):
            raise InvalidEventError("hazards must contain Hazard instances")
        object.__setattr__(self, "objects", objects)
        object.__setattr__(self, "hazards", hazards)
        object_ids = [item.object_id for item in objects]
        if len(object_ids) != len(set(object_ids)):
            raise InvalidEventError("object_id values must be unique within a frame")
        known_ids = set(object_ids)
        for hazard in hazards:
            if hazard.object_id is not None and hazard.object_id not in known_ids:
                raise InvalidEventError(
                    f"hazard references unknown object_id {hazard.object_id!r}"
                )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PerceptionEvent:
        """Parse one frame from a JSON-compatible mapping.

        Both verbose (``timestamp_s``) and concise (``timestamp``) keys are
        accepted so recorded frame files and hand-written fixtures can coexist.
        """
        if not isinstance(payload, Mapping):
            raise InvalidEventError("frame must be a JSON object")
        timestamp = payload.get("timestamp_s", payload.get("timestamp"))
        if timestamp is None:
            raise InvalidEventError("frame is missing timestamp_s")
        raw_objects = payload.get("objects", [])
        raw_hazards = payload.get("hazards", [])
        if not isinstance(raw_objects, Sequence) or isinstance(raw_objects, (str, bytes)):
            raise InvalidEventError("objects must be a list")
        if not isinstance(raw_hazards, Sequence) or isinstance(raw_hazards, (str, bytes)):
            raise InvalidEventError("hazards must be a list")
        return cls(
            timestamp_s=timestamp,
            frame_id=payload.get("frame_id", 0),
            objects=tuple(DetectedObject.from_dict(item) for item in raw_objects),
            hazards=tuple(Hazard.from_dict(item) for item in raw_hazards),
            context=_parse_enum(
                payload.get("context", MobilityContext.WALKING),
                MobilityContext,
                MobilityContext.WALKING,
                "context",
            ),
        )


def parse_event_stream(payload: Any) -> list[PerceptionEvent]:
    """Parse a list of frames, or an object with a ``frames`` list.

    The helper deliberately returns a concrete list: frame files are expected
    to be short recording excerpts, and a list keeps CLI error reporting and
    ordering checks straightforward.
    """
    if isinstance(payload, Mapping):
        if "frames" not in payload:
            raise InvalidEventError("frame document must contain a 'frames' list")
        raw_frames = payload["frames"]
    else:
        raw_frames = payload
    if not isinstance(raw_frames, Sequence) or isinstance(raw_frames, (str, bytes)):
        raise InvalidEventError("frames must be a list")
    return [PerceptionEvent.from_dict(item) for item in raw_frames]
