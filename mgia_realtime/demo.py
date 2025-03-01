"""Deterministic synthetic scenario for demos and smoke tests.

The scenario walks a user along a pavement, introduces an approaching pedestrian
with an annotated crossing hazard, escalates to a critical alert, then lets the
scene clear.  It contains no camera input and no randomness, so the same
guidance is produced on every machine and CI run.
"""

from __future__ import annotations

from .events import (
    DetectedObject,
    Hazard,
    HazardKind,
    MobilityContext,
    ObjectClass,
    PerceptionEvent,
    Position,
)


def _pedestrian(
    object_id: str,
    distance_m: float,
    speed_mps: float,
    confidence: float,
    position: Position,
) -> DetectedObject:
    return DetectedObject(
        object_id=object_id,
        cls=ObjectClass.PEDESTRIAN,
        confidence=confidence,
        distance_m=distance_m,
        relative_speed_mps=speed_mps,
        bearing_deg=-18.0 if position is Position.LEFT else 0.0,
        position=position,
    )


def build_demo_events() -> tuple[PerceptionEvent, ...]:
    """Return a ten-frame, ten-second synthetic walking scenario."""
    return (
        PerceptionEvent(timestamp_s=0.0, frame_id=0, context=MobilityContext.WALKING),
        PerceptionEvent(
            timestamp_s=1.0,
            frame_id=1,
            objects=(
                _pedestrian("ped-1", 7.0, 0.8, 0.90, Position.LEFT),
                DetectedObject(
                    object_id="car-1",
                    cls=ObjectClass.VEHICLE,
                    confidence=0.80,
                    distance_m=14.0,
                    relative_speed_mps=0.0,
                    bearing_deg=42.0,
                    position=Position.RIGHT,
                ),
            ),
            context=MobilityContext.WALKING,
        ),
        PerceptionEvent(
            timestamp_s=2.0,
            frame_id=2,
            objects=(_pedestrian("ped-1", 4.0, 2.0, 0.92, Position.LEFT),),
            context=MobilityContext.WALKING,
        ),
        PerceptionEvent(
            timestamp_s=3.0,
            frame_id=3,
            objects=(_pedestrian("ped-1", 3.0, 2.5, 0.92, Position.LEFT),),
            hazards=(
                Hazard(
                    kind=HazardKind.PEDESTRIAN_PATH,
                    confidence=0.9,
                    object_id="ped-1",
                    note="annotated crossing conflict",
                ),
            ),
            context=MobilityContext.CROSSING,
        ),
        PerceptionEvent(
            timestamp_s=4.0,
            frame_id=4,
            objects=(_pedestrian("ped-1", 2.0, 3.0, 0.95, Position.CENTER),),
            hazards=(
                Hazard(kind=HazardKind.PEDESTRIAN_PATH, confidence=0.9, object_id="ped-1"),
            ),
            context=MobilityContext.CROSSING,
        ),
        PerceptionEvent(
            timestamp_s=5.0,
            frame_id=5,
            objects=(_pedestrian("ped-1", 1.2, 4.0, 0.95, Position.CENTER),),
            hazards=(
                Hazard(kind=HazardKind.PEDESTRIAN_PATH, confidence=0.95, object_id="ped-1"),
            ),
            context=MobilityContext.CROSSING,
        ),
        PerceptionEvent(
            timestamp_s=6.0,
            frame_id=6,
            objects=(
                DetectedObject(
                    object_id="ped-1",
                    cls=ObjectClass.PEDESTRIAN,
                    confidence=0.90,
                    distance_m=4.0,
                    relative_speed_mps=0.0,
                    bearing_deg=-20.0,
                    position=Position.LEFT,
                ),
            ),
            context=MobilityContext.TRANSIT,
        ),
        PerceptionEvent(timestamp_s=7.0, frame_id=7, context=MobilityContext.TRANSIT),
        PerceptionEvent(timestamp_s=8.0, frame_id=8, context=MobilityContext.TRANSIT),
        PerceptionEvent(timestamp_s=9.0, frame_id=9, context=MobilityContext.WALKING),
    )
