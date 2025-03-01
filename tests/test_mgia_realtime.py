"""Tests for the dependency-free MGIA real-time safety package.

Coverage policy: every line *and* branch in ``mgia_realtime`` is exercised.
The legacy prototype under ``test/`` is intentionally not collected by the
default pytest configuration.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import mgia_realtime.__main__  # noqa: F401 - imports the module entry point
from mgia_realtime.alarm import (
    AlarmConfig,
    AlarmLevel,
    AlarmStateMachine,
    AlarmTransition,
)
from mgia_realtime.cli import build_parser, load_events, main
from mgia_realtime.demo import build_demo_events
from mgia_realtime.errors import (
    ConfigurationError,
    FrameSourceError,
    InvalidEventError,
    InvalidRiskScoreError,
    OutOfOrderFrameError,
)
from mgia_realtime.events import (
    DetectedObject,
    Hazard,
    HazardKind,
    MobilityContext,
    ObjectClass,
    PerceptionEvent,
    Position,
    parse_event_stream,
)
from mgia_realtime.metrics import SessionMetrics, percentile
from mgia_realtime.pipeline import Guidance, PipelineConfig, RealtimePipeline
from mgia_realtime.risk import (
    ObjectRisk,
    RiskConfig,
    RiskScorer,
    clamp,
)
from mgia_realtime.speech import (
    EnqueueStatus,
    MessagePriority,
    SpeechConfig,
    SpeechMessage,
    SpeechScheduler,
    VerbosityProfile,
)
from mgia_realtime.tips import SafetyTipPolicy, TipConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_object(
    object_id: str = "obj-1",
    cls: ObjectClass = ObjectClass.PEDESTRIAN,
    confidence: float = 0.9,
    distance: float | None = 3.0,
    speed: float = 1.0,
    position: Position = Position.CENTER,
    bearing: float = 0.0,
) -> DetectedObject:
    return DetectedObject(
        object_id=object_id,
        cls=cls,
        confidence=confidence,
        distance_m=distance,
        relative_speed_mps=speed,
        bearing_deg=bearing,
        position=position,
    )


def make_event(
    timestamp: float = 1.0,
    frame_id: int = 1,
    objects: tuple[DetectedObject, ...] = (),
    hazards: tuple[Hazard, ...] = (),
    context: MobilityContext = MobilityContext.WALKING,
) -> PerceptionEvent:
    return PerceptionEvent(
        timestamp_s=timestamp,
        frame_id=frame_id,
        objects=objects,
        hazards=hazards,
        context=context,
    )


def make_message(
    message_id: str = "m1",
    text: str = "Stop now",
    priority: MessagePriority = MessagePriority.NORMAL,
    created_at: float = 0.0,
    dedupe_key: str = "",
    expires_at: float | None = None,
) -> SpeechMessage:
    return SpeechMessage(
        message_id=message_id,
        text=text,
        priority=priority,
        created_at_s=created_at,
        dedupe_key=dedupe_key,
        expires_at_s=expires_at,
    )


def assessment_for(
    objects: tuple[DetectedObject, ...] = (),
    hazards: tuple[Hazard, ...] = (),
    timestamp: float = 1.0,
):
    return RiskScorer().assess(make_event(timestamp=timestamp, objects=objects, hazards=hazards))


# ---------------------------------------------------------------------------
# Events and parsing
# ---------------------------------------------------------------------------


class TestEvents:
    def test_valid_event_and_object_aliases(self) -> None:
        obj = DetectedObject.from_dict(
            {
                "id": "ped",
                "class": "PEDESTRIAN",
                "confidence": 0.8,
                "distance": 4,
                "relative_speed": 1.5,
                "bearing": 3,
                "position": "left",
            }
        )
        assert obj.object_id == "ped"
        assert obj.cls is ObjectClass.PEDESTRIAN
        assert obj.distance_m == 4.0
        assert obj.position is Position.LEFT
        event = PerceptionEvent.from_dict(
            {"timestamp": 2, "frame_id": 3, "objects": [{"id": "ped"}], "context": "CROSSING"}
        )
        assert event.timestamp_s == 2.0
        assert event.context is MobilityContext.CROSSING
        assert len(event.objects) == 1

    def test_object_defaults_and_absolute_bearing(self) -> None:
        obj = DetectedObject.from_dict({"id": "x"})
        assert obj.cls is ObjectClass.UNKNOWN
        assert obj.confidence == 1.0
        assert obj.distance_m is None
        assert obj.position is Position.UNKNOWN
        assert obj.bearing_deg == 0.0
        assert make_object(bearing=-180.0).bearing_deg == -180.0

    @pytest.mark.parametrize(
        "payload",
        [
            {"id": "x", "class": "teleporter"},
            {"id": "x", "position": "up"},
        ],
    )
    def test_unknown_enum_values_fall_back(self, payload: dict[str, object]) -> None:
        obj = DetectedObject.from_dict(payload)
        assert obj.cls in (ObjectClass.UNKNOWN, ObjectClass.PEDESTRIAN)
        assert obj.position is Position.UNKNOWN

    def test_non_mapping_object_and_hazard(self) -> None:
        with pytest.raises(InvalidEventError, match="object entries"):
            DetectedObject.from_dict(["nope"])  # type: ignore[arg-type]
        with pytest.raises(InvalidEventError, match="hazard entries"):
            Hazard.from_dict(["nope"])  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"object_id": ""},
            {"object_id": 4},
            {"cls": "pedestrian"},
            {"position": "left"},
            {"confidence": "high"},
            {"confidence": True},
            {"confidence": 1.1},
            {"confidence": float("nan")},
            {"distance_m": 0},
            {"distance_m": -2},
            {"distance_m": "far"},
            {"relative_speed_mps": "fast"},
            {"bearing_deg": 181},
            {"bearing_deg": float("inf")},
        ],
    )
    def test_object_validation(self, kwargs: dict[str, object]) -> None:
        base: dict[str, object] = {
            "object_id": "o",
            "cls": ObjectClass.PEDESTRIAN,
            "confidence": 0.5,
        }
        base.update(kwargs)
        with pytest.raises(InvalidEventError):
            DetectedObject(**base)  # type: ignore[arg-type]

    def test_object_from_dict_validation(self) -> None:
        with pytest.raises(InvalidEventError):
            DetectedObject.from_dict({"id": ""})
        with pytest.raises(InvalidEventError):
            DetectedObject.from_dict({"id": 7})
        with pytest.raises(InvalidEventError):
            DetectedObject.from_dict({"id": "x", "distance": "near"})
        with pytest.raises(InvalidEventError):
            DetectedObject.from_dict({"id": "x", "cls": 3})

    def test_hazard_validation_and_defaults(self) -> None:
        hazard = Hazard(kind=HazardKind.DOOR, confidence=0.7, object_id="o", note="swing")
        assert hazard.note == "swing"
        hazard = Hazard.from_dict({"kind": "signal", "confidence": 0.5, "note": 12})
        assert hazard.kind is HazardKind.SIGNAL
        assert hazard.note == "12"
        assert Hazard.from_dict({"kind": "nonsense"}).kind is HazardKind.OTHER
        with pytest.raises(InvalidEventError):
            Hazard(kind="door")  # type: ignore[arg-type]
        with pytest.raises(InvalidEventError):
            Hazard(kind=HazardKind.DOOR, confidence=2.0)
        with pytest.raises(InvalidEventError):
            Hazard(kind=HazardKind.DOOR, object_id="")
        with pytest.raises(InvalidEventError):
            Hazard(kind=HazardKind.DOOR, object_id=3)  # type: ignore[arg-type]

    def test_event_validation(self) -> None:
        for kwargs in (
            {"timestamp_s": -1, "frame_id": 0},
            {"timestamp_s": "now", "frame_id": 0},
            {"timestamp_s": float("nan"), "frame_id": 0},
            {"timestamp_s": 0, "frame_id": -1},
            {"timestamp_s": 0, "frame_id": True},
            {"timestamp_s": 0, "frame_id": 1.5},
            {"timestamp_s": 0, "frame_id": 0, "context": "walking"},
            {"timestamp_s": 0, "frame_id": 0, "objects": ["x"]},
            {"timestamp_s": 0, "frame_id": 0, "hazards": ["x"]},
        ):
            with pytest.raises(InvalidEventError):
                PerceptionEvent(**kwargs)  # type: ignore[arg-type]

    def test_event_duplicate_and_unknown_hazard_ids(self) -> None:
        obj = make_object("same")
        with pytest.raises(InvalidEventError, match="unique"):
            make_event(objects=(obj, obj))
        with pytest.raises(InvalidEventError, match="unknown object_id"):
            make_event(
                objects=(obj,),
                hazards=(Hazard(kind=HazardKind.OTHER, object_id="missing"),),
            )

    def test_event_from_dict_validation(self) -> None:
        with pytest.raises(InvalidEventError, match="JSON object"):
            PerceptionEvent.from_dict([1])  # type: ignore[arg-type]
        with pytest.raises(InvalidEventError, match="missing timestamp"):
            PerceptionEvent.from_dict({})
        with pytest.raises(InvalidEventError, match="objects must be a list"):
            PerceptionEvent.from_dict({"timestamp_s": 0, "objects": "nope"})
        with pytest.raises(InvalidEventError, match="hazards must be a list"):
            PerceptionEvent.from_dict({"timestamp_s": 0, "hazards": "nope"})
        with pytest.raises(InvalidEventError):
            PerceptionEvent.from_dict({"timestamp_s": 0, "context": 7})
        assert PerceptionEvent.from_dict({"timestamp_s": 0, "context": "teleport"}).context is (
            MobilityContext.WALKING
        )

    def test_parse_event_stream_shapes(self) -> None:
        frames = [{"timestamp_s": 0, "frame_id": 1}]
        assert len(parse_event_stream(frames)) == 1
        assert len(parse_event_stream({"frames": frames})) == 1
        with pytest.raises(InvalidEventError, match="must contain"):
            parse_event_stream({"other": []})
        with pytest.raises(InvalidEventError, match="frames must be a list"):
            parse_event_stream(4)
        with pytest.raises(InvalidEventError, match="frames must be a list"):
            parse_event_stream("nope")


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------


class TestRisk:
    def test_clamp(self) -> None:
        assert clamp(-1) == 0.0
        assert clamp(2) == 1.0
        assert clamp(0.4) == 0.4

    def test_ttc_proximity_and_boundary(self) -> None:
        scorer = RiskScorer()
        risk = scorer.score_object(make_object(distance=2.0, speed=2.0, confidence=1.0))
        assert risk.ttc_s == pytest.approx(1.0)
        assert risk.proximity == pytest.approx(2.0 / 3.0)
        assert risk.score == pytest.approx(0.7)
        boundary = scorer.score_object(make_object(distance=3.0, speed=1.0))
        assert boundary.ttc_s == pytest.approx(3.0)
        assert boundary.proximity == 0.0
        assert boundary.score == pytest.approx(0.1 * 0.9)

    def test_static_and_receding_clearance(self) -> None:
        scorer = RiskScorer()
        static = scorer.score_object(
            make_object(distance=4.0, speed=0.0, confidence=1.0, position=Position.CENTER)
        )
        assert static.proximity == pytest.approx(0.5)
        assert static.score == pytest.approx(0.55 * 0.7)
        receding = scorer.score_object(
            make_object(distance=2.0, speed=-1.0, confidence=1.0, position=Position.CENTER)
        )
        assert receding.ttc_s is None
        assert receding.proximity == pytest.approx(0.75)
        assert receding.score == pytest.approx(0.775 * 0.45)
        far = scorer.score_object(make_object(distance=20.0, speed=0.0, confidence=1.0))
        assert far.proximity == 0.0

    def test_missing_distance_is_conservative(self) -> None:
        risk = RiskScorer().score_object(
            make_object(distance=None, speed=2.0, confidence=1.0, position=Position.LEFT)
        )
        assert risk.distance_known is False
        assert risk.ttc_s is None
        assert risk.proximity == pytest.approx(0.55)
        assert risk.score == pytest.approx(0.595 * 0.88)

    def test_position_and_confidence_factors(self) -> None:
        scorer = RiskScorer()
        left = scorer.score_object(make_object(position=Position.LEFT, distance=2.0, speed=2.0))
        right = scorer.score_object(make_object(position=Position.RIGHT, distance=2.0, speed=2.0))
        center = scorer.score_object(make_object(position=Position.CENTER, distance=2.0, speed=2.0))
        unknown = scorer.score_object(make_object(position=Position.UNKNOWN, distance=2.0, speed=2.0))
        assert left.score == right.score < center.score
        assert unknown.score < left.score
        zero = scorer.score_object(make_object(confidence=0.0))
        assert zero.score == 0.0

    def test_assessment_with_hazard_boost_and_sorting(self) -> None:
        slow = make_object("b", distance=4.0, speed=0.5, confidence=0.4)
        fast = make_object("a", distance=2.0, speed=2.0, confidence=0.9)
        hazard = Hazard(kind=HazardKind.PEDESTRIAN_PATH, confidence=1.0, object_id="a")
        assessment = assessment_for((slow, fast), (hazard,))
        assert assessment.primary is not None
        assert assessment.primary.object_id == "a"
        assert assessment.score > assessment.primary.score
        assert "contact in" in assessment.reason

    def test_tied_object_scores_sort_by_id(self) -> None:
        first = make_object("b", distance=2.0, speed=2.0)
        second = make_object("a", distance=2.0, speed=2.0)
        assessment = assessment_for((first, second))
        assert [item.object_id for item in assessment.object_risks] == ["a", "b"]

    def test_tied_hazards_sort_by_kind_and_id(self) -> None:
        obj = make_object("a")
        pedestrian = Hazard(kind=HazardKind.PEDESTRIAN_PATH, confidence=0.8, object_id="a")
        vehicle = Hazard(kind=HazardKind.VEHICLE_CONFLICT, confidence=0.8, object_id=None)
        assessment = assessment_for((obj,), (pedestrian, vehicle))
        assert assessment.hazard is pedestrian
        vehicle_tie = Hazard(kind=HazardKind.VEHICLE_CONFLICT, confidence=0.8, object_id="a")
        same_id = Hazard(kind=HazardKind.VEHICLE_CONFLICT, confidence=0.8, object_id=None)
        selected = assessment_for((obj,), (vehicle_tie, same_id)).hazard
        assert selected is same_id  # missing object_id sorts before "a"

    def test_hazard_only_and_empty_assessment(self) -> None:
        hazard = Hazard(kind=HazardKind.ROAD_EDGE, confidence=0.5)
        assessment = assessment_for((), (hazard,))
        assert assessment.primary is None
        assert assessment.score == pytest.approx(0.25)
        assert "without a tracked object" in assessment.reason
        empty = assessment_for()
        assert empty.score == 0.0
        assert empty.reason == "no objects detected"

    def test_explanations_for_distance_states(self) -> None:
        missing = assessment_for((make_object(distance=None),))
        assert "distance unavailable" in missing.reason
        static = assessment_for((make_object(distance=5.0, speed=0.0),))
        assert "not approaching" in static.reason

    def test_risk_config_validation(self) -> None:
        with pytest.raises(ConfigurationError):
            RiskConfig(horizon_s=0)
        with pytest.raises(ConfigurationError):
            RiskConfig(static_clearance_m=0)
        with pytest.raises(ConfigurationError):
            RiskConfig(unknown_distance_proximity=1.5)
        with pytest.raises(ConfigurationError):
            RiskConfig(base_weight=-0.1)
        with pytest.raises(ConfigurationError):
            RiskConfig(hazard_boost=1.1)
        with pytest.raises(ConfigurationError):
            RiskConfig(hazard_only_score=2.0)
        assert RiskConfig(horizon_s=2.0).horizon_s == 2.0

    def test_default_assessment_dataclass(self) -> None:
        from mgia_realtime.risk import RiskAssessment

        empty = RiskAssessment(timestamp_s=0.0, frame_id=0, score=0.0)
        assert empty.object_risks == ()
        assert empty.primary is None
        assert empty.hazard is None
        assert empty.reason == "no objects detected"

    def test_object_risk_is_frozen_dataclass(self) -> None:
        risk = RiskScorer().score_object(make_object())
        assert isinstance(risk, ObjectRisk)
        with pytest.raises(AttributeError):
            risk.score = 1.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Alarm state machine
# ---------------------------------------------------------------------------


class TestAlarm:
    def test_enter_threshold_boundaries(self) -> None:
        machine = AlarmStateMachine()
        assert machine.update(0.24, 0.0) is None
        watch = machine.update(0.25, 0.1)
        assert watch is not None and watch.current is AlarmLevel.WATCH
        warn = machine.update(0.55, 0.2)
        assert warn is not None and warn.current is AlarmLevel.WARN
        critical = machine.update(0.80, 0.3)
        assert critical is not None and critical.current is AlarmLevel.CRITICAL
        assert critical.escalated is True
        assert machine.state_since == 0.3
        assert machine.dwell_s(0.9) == pytest.approx(0.6)

    def test_same_level_and_initial_clear(self) -> None:
        machine = AlarmStateMachine()
        assert machine.update(0.0, 1.0) is None
        assert machine.current is AlarmLevel.CLEAR
        assert machine.update(0.2, 2.0) is None
        assert machine.update(0.2, 3.0) is None

    def test_hysteresis_band_holds_level(self) -> None:
        machine = AlarmStateMachine()
        machine.update(0.6, 0.0)
        assert machine.current is AlarmLevel.WARN
        assert machine.update(0.50, 5.0) is None
        assert machine.update(0.45, 6.0) is None
        assert machine.current is AlarmLevel.WARN

    def test_dwell_blocks_downgrade_then_steps_down(self) -> None:
        machine = AlarmStateMachine()
        machine.update(0.9, 0.0)
        assert machine.current is AlarmLevel.CRITICAL
        assert machine.update(0.0, 0.2) is None  # dwell too short
        down = machine.update(0.0, 0.6)
        assert down is not None
        assert down.current is AlarmLevel.WARN
        assert down.escalated is False
        assert machine.update(0.0, 0.7) is None  # new dwell
        assert machine.update(0.0, 1.2).current is AlarmLevel.WATCH  # type: ignore[union-attr]
        assert machine.update(0.0, 1.8).current is AlarmLevel.CLEAR  # type: ignore[union-attr]

    def test_dwell_without_updates(self) -> None:
        machine = AlarmStateMachine()
        assert machine.dwell_s(10.0) == 0.0
        machine.update(0.9, 5.0)
        assert machine.dwell_s(4.0) == 0.0

    def test_transition_dataclass(self) -> None:
        transition = AlarmTransition(
            previous=AlarmLevel.CLEAR,
            current=AlarmLevel.WATCH,
            timestamp_s=1.0,
            score=0.3,
            reason="escalation",
        )
        assert transition.escalated is True
        assert transition.reason == "escalation"

    def test_invalid_scores_and_timestamps(self) -> None:
        machine = AlarmStateMachine()
        for bad in ("0.5", None, True, float("nan"), -0.1, 1.1):
            with pytest.raises(InvalidRiskScoreError):
                machine.update(bad, 0.0)  # type: ignore[arg-type]
        with pytest.raises(OutOfOrderFrameError):
            machine.update(0.5, "0")  # type: ignore[arg-type]
        machine.update(0.5, 2.0)
        with pytest.raises(OutOfOrderFrameError):
            machine.update(0.5, 1.0)

    def test_alarm_config_validation(self) -> None:
        with pytest.raises(ConfigurationError):
            AlarmConfig(enter_watch=1.2)
        with pytest.raises(ConfigurationError):
            AlarmConfig(enter_watch=0.6, enter_warn=0.5)
        with pytest.raises(ConfigurationError):
            AlarmConfig(exit_warn=0.75, exit_critical=0.7)
        with pytest.raises(ConfigurationError):
            AlarmConfig(exit_watch=0.6, exit_warn=0.65, exit_critical=0.7)
        with pytest.raises(ConfigurationError):
            AlarmConfig(min_dwell_s=-1.0)
        assert AlarmConfig().exit_thresholds[AlarmLevel.WARN] == 0.45


# ---------------------------------------------------------------------------
# Speech scheduling
# ---------------------------------------------------------------------------


class TestSpeech:
    def test_message_validation_and_duration(self) -> None:
        message = make_message(text="one two three four five six")
        assert message.duration_s > 0.7
        short = make_message(text="Stop")
        assert short.duration_s == 0.7
        assert make_message(expires_at=0.0).expires_at_s == 0.0
        for kwargs in (
            {"message_id": ""},
            {"text": ""},
            {"priority": "HIGH"},
            {"dedupe_key": 3},
            {"created_at": -1.0},
            {"created_at": float("inf")},
            {"expires_at": -1.0},
            {"expires_at": float("nan")},
        ):
            with pytest.raises(InvalidEventError):
                make_message(**kwargs)  # type: ignore[arg-type]

    def test_profile_allow_rules(self) -> None:
        metrics = SessionMetrics()
        concise = SpeechScheduler(profile=VerbosityProfile.CONCISE, metrics=metrics)
        assert concise.allows(MessagePriority.HIGH)
        assert not concise.allows(MessagePriority.NORMAL)
        standard = SpeechScheduler(profile=VerbosityProfile.STANDARD, metrics=metrics)
        assert standard.allows(MessagePriority.NORMAL)
        assert not standard.allows(MessagePriority.LOW)
        verbose = SpeechScheduler(profile=VerbosityProfile.VERBOSE, metrics=metrics)
        assert verbose.allows(MessagePriority.LOW)

    def test_config_validation(self) -> None:
        with pytest.raises(ConfigurationError):
            SpeechConfig(cooldown_s=-1)
        with pytest.raises(ConfigurationError):
            SpeechConfig(max_messages_per_second=0)
        with pytest.raises(ConfigurationError):
            SpeechConfig(max_pending=0)
        assert SpeechConfig(max_messages_per_second=4).min_interval_s == 0.25

    def test_profile_drop_and_expiry(self) -> None:
        metrics = SessionMetrics()
        scheduler = SpeechScheduler(profile=VerbosityProfile.CONCISE, metrics=metrics)
        result = scheduler.enqueue(make_message(priority=MessagePriority.NORMAL), 0.0)
        assert result.status is EnqueueStatus.DROPPED_PROFILE
        assert metrics.messages_dropped == 1
        scheduler = SpeechScheduler(metrics=metrics)
        result = scheduler.enqueue(make_message(expires_at=1.0), 2.0)
        assert result.status is EnqueueStatus.DROPPED_EXPIRED
        assert metrics.messages_dropped == 2

    def test_queue_order_and_emission(self) -> None:
        metrics = SessionMetrics()
        scheduler = SpeechScheduler(config=SpeechConfig(max_messages_per_second=10), metrics=metrics)
        normal = make_message("n", "Normal note", priority=MessagePriority.NORMAL)
        urgent = make_message("u", "Urgent note", priority=MessagePriority.HIGH)
        assert scheduler.enqueue(normal, 0.0).accepted
        assert scheduler.enqueue(urgent, 0.0).accepted
        assert scheduler.pending_count == 2
        spoken = scheduler.tick(0.0)
        assert len(spoken) == 1
        assert spoken[0].message.message_id == "u"
        assert scheduler.active_message is not None
        assert scheduler.active_message.message_id == "u"
        assert metrics.messages_spoken == 1
        # The normal message remains queued but the channel is busy.
        assert scheduler.tick(0.1) == []
        assert scheduler.tick(1.0)[0].message.message_id == "n"
        assert scheduler.tick(2.0) == []

    def test_coalescing_replacement_and_retention(self) -> None:
        metrics = SessionMetrics()
        scheduler = SpeechScheduler(config=SpeechConfig(max_messages_per_second=10), metrics=metrics)
        scheduler.enqueue(make_message("a", "First", MessagePriority.HIGH, dedupe_key="k"), 0.0)
        lower = scheduler.enqueue(
            make_message("b", "Second", MessagePriority.NORMAL, dedupe_key="k"), 0.0
        )
        assert lower.status is EnqueueStatus.COALESCED
        assert lower.message_id == "a"
        higher = scheduler.enqueue(
            make_message("c", "Third", MessagePriority.CRITICAL, dedupe_key="k"), 0.0
        )
        assert higher.status is EnqueueStatus.COALESCED
        assert higher.message_id == "c"
        assert scheduler.tick(0.0)[0].message.message_id == "c"

    def test_equal_priority_coalesce_discards_stale_heap_entry(self) -> None:
        metrics = SessionMetrics()
        scheduler = SpeechScheduler(config=SpeechConfig(max_messages_per_second=10), metrics=metrics)
        scheduler.enqueue(make_message("a", "First", MessagePriority.HIGH, dedupe_key="k"), 0.0)
        scheduler.enqueue(make_message("b", "Second", MessagePriority.HIGH, dedupe_key="k"), 0.0)
        assert scheduler.tick(0.0)[0].message.message_id == "b"
        assert metrics.messages_coalesced == 1

    def test_active_duplicate_and_cooldown(self) -> None:
        metrics = SessionMetrics()
        scheduler = SpeechScheduler(metrics=metrics)
        scheduler.enqueue(make_message(dedupe_key="k"), 0.0)
        spoken = scheduler.tick(0.0)[0]
        duplicate = scheduler.enqueue(make_message("m2", dedupe_key="k"), 0.1)
        assert duplicate.status is EnqueueStatus.SUPPRESSED_COOLDOWN
        assert "already speaking" in duplicate.reason
        assert metrics.messages_suppressed == 1
        assert spoken.latency_s == 0.0
        scheduler.tick(0.8)
        again = scheduler.enqueue(make_message("m3", dedupe_key="k"), 0.9)
        assert again.status is EnqueueStatus.SUPPRESSED_COOLDOWN
        assert again.reason == "deduplication cooldown"
        assert metrics.messages_suppressed == 2

    def test_cooldown_expires(self) -> None:
        metrics = SessionMetrics()
        scheduler = SpeechScheduler(config=SpeechConfig(cooldown_s=0.5), metrics=metrics)
        scheduler.enqueue(make_message(dedupe_key="k"), 0.0)
        scheduler.tick(0.0)
        scheduler.tick(0.8)
        result = scheduler.enqueue(make_message("m2", dedupe_key="k"), 1.4)
        assert result.accepted

    def test_queue_overflow_drops_lowest(self) -> None:
        metrics = SessionMetrics()
        scheduler = SpeechScheduler(
            config=SpeechConfig(max_pending=1, max_messages_per_second=10),
            profile=VerbosityProfile.VERBOSE,
            metrics=metrics,
        )
        scheduler.enqueue(make_message("low", "Low note", MessagePriority.LOW), 0.0)
        dropped = scheduler.enqueue(make_message("low2", "Other low", MessagePriority.LOW), 0.0)
        assert dropped.status is EnqueueStatus.DROPPED_OVERFLOW
        added = scheduler.enqueue(make_message("high", "High note", MessagePriority.HIGH), 0.0)
        assert added.accepted
        assert metrics.messages_dropped == 2
        assert scheduler.tick(0.0)[0].message.message_id == "high"

    def test_overflow_replaces_lower_priority(self) -> None:
        metrics = SessionMetrics()
        scheduler = SpeechScheduler(
            config=SpeechConfig(max_pending=1, max_messages_per_second=10),
            profile=VerbosityProfile.VERBOSE,
            metrics=metrics,
        )
        scheduler.enqueue(make_message("low", "Low note", MessagePriority.LOW), 0.0)
        added = scheduler.enqueue(make_message("high", "High note", MessagePriority.HIGH), 0.0)
        assert added.accepted
        assert metrics.messages_dropped == 1
        assert scheduler.pending_count == 1

    def test_minimum_spacing_blocks_then_releases(self) -> None:
        metrics = SessionMetrics()
        scheduler = SpeechScheduler(
            config=SpeechConfig(max_messages_per_second=0.5), metrics=metrics
        )
        scheduler.enqueue(make_message("a", "First note"), 0.0)
        scheduler.tick(0.0)
        scheduler.enqueue(make_message("b", "Second note", priority=MessagePriority.HIGH), 0.0)
        assert scheduler.tick(0.7) == []  # active utterance still owns the channel
        assert scheduler.tick(0.8) == []  # channel idle, but speaking rate is capped
        assert scheduler.tick(2.0)[0].message.message_id == "b"

    def test_critical_bypasses_spacing(self) -> None:
        metrics = SessionMetrics()
        scheduler = SpeechScheduler(
            config=SpeechConfig(max_messages_per_second=0.5), metrics=metrics
        )
        scheduler.enqueue(make_message("a", "First note"), 0.0)
        scheduler.tick(0.0)
        scheduler.enqueue(make_message("b", "Second note", MessagePriority.CRITICAL), 0.0)
        assert scheduler.tick(0.7)[0].message.message_id == "b"

    def test_preemption_rules(self) -> None:
        metrics = SessionMetrics()
        scheduler = SpeechScheduler(
            config=SpeechConfig(max_messages_per_second=2),
            profile=VerbosityProfile.VERBOSE,
            metrics=metrics,
        )
        scheduler.enqueue(make_message("low", "Low note", MessagePriority.LOW), 0.0)
        scheduler.tick(0.0)
        scheduler.enqueue(make_message("same", "Normal note", MessagePriority.NORMAL), 0.0)
        assert scheduler.tick(0.2) == []  # spacing blocks non-critical preemption
        scheduler.enqueue(make_message("higher", "High note", MessagePriority.HIGH), 0.0)
        assert scheduler.tick(0.6)[0].message.message_id == "higher"
        assert metrics.messages_preempted == 1

    def test_lower_priority_does_not_preempt(self) -> None:
        metrics = SessionMetrics()
        scheduler = SpeechScheduler(
            config=SpeechConfig(max_messages_per_second=10),
            profile=VerbosityProfile.VERBOSE,
            metrics=metrics,
        )
        scheduler.enqueue(make_message("high", "High note", MessagePriority.HIGH), 0.0)
        scheduler.tick(0.0)
        scheduler.enqueue(make_message("low", "Low note", MessagePriority.LOW), 0.0)
        assert scheduler.tick(0.2) == []

    def test_expired_pending_message_is_dropped(self) -> None:
        metrics = SessionMetrics()
        scheduler = SpeechScheduler(config=SpeechConfig(max_messages_per_second=10), metrics=metrics)
        scheduler.enqueue(make_message("e", expires_at=2.0), 0.0)
        assert scheduler.tick(3.0) == []
        assert metrics.messages_dropped == 1
        assert scheduler.pending_count == 0

    def test_empty_scheduler(self) -> None:
        scheduler = SpeechScheduler()
        assert scheduler.tick(0.0) == []
        assert scheduler.active_message is None
        assert scheduler.pending_count == 0

    def test_scheduler_default_metrics(self) -> None:
        scheduler = SpeechScheduler()
        assert isinstance(scheduler.metrics, SessionMetrics)

    def test_enqueue_result_accepted_property(self) -> None:
        scheduler = SpeechScheduler()
        result = scheduler.enqueue(make_message(), 0.0)
        assert result.accepted is True
        blocked = scheduler.enqueue(make_message("x", priority=MessagePriority.LOW), 0.0)
        assert blocked.accepted is False


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_percentiles(self) -> None:
        assert percentile([], 0.5) is None
        assert percentile([7.0], 0.95) == 7.0
        assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
        assert percentile([1.0, 2.0, 3.0], 0.0) == 1.0
        assert percentile([1.0, 2.0, 3.0], 1.0) == 3.0
        assert percentile([1.0, 2.0, 3.0], 1 / 3) == pytest.approx(1.6666667)
        with pytest.raises(ValueError):
            percentile([1.0], 1.5)

    def test_frame_time_in_state(self) -> None:
        metrics = SessionMetrics()
        metrics.record_frame(0.0, AlarmLevel.CLEAR)
        metrics.record_frame(1.0, AlarmLevel.WARN)
        metrics.record_frame(3.0, AlarmLevel.CRITICAL)
        snapshot = metrics.snapshot()
        assert snapshot.frames_processed == 3
        assert snapshot.total_s == 3.0
        assert snapshot.time_in_state_s["CLEAR"] == 1.0
        assert snapshot.time_in_state_s["WARN"] == 2.0
        assert snapshot.time_in_state_s["CRITICAL"] == 0.0

    def test_out_of_order_frame(self) -> None:
        metrics = SessionMetrics()
        metrics.record_frame(2.0, AlarmLevel.CLEAR)
        with pytest.raises(OutOfOrderFrameError):
            metrics.record_frame(1.0, AlarmLevel.CLEAR)

    def test_counters_latency_and_export(self) -> None:
        metrics = SessionMetrics()
        metrics.record_alert(AlarmLevel.WATCH)
        metrics.record_transition()
        metrics.record_message_enqueued()
        metrics.record_spoken("two words", 0.05)
        metrics.record_spoken("three whole words", -1.0)
        metrics.record_dropped(2)
        metrics.record_coalesced()
        metrics.record_suppressed()
        metrics.record_preempted()
        snapshot = metrics.snapshot()
        assert snapshot.alerts["WATCH"] == 1
        assert snapshot.transitions == 1
        assert snapshot.messages_enqueued == 1
        assert snapshot.messages_spoken == 2
        assert snapshot.words_spoken == 5
        assert snapshot.messages_dropped == 2
        assert snapshot.messages_coalesced == 1
        assert snapshot.messages_suppressed == 1
        assert snapshot.messages_preempted == 1
        assert snapshot.latency_p50_s == pytest.approx(0.025)
        assert snapshot.latency_p95_s is not None
        assert snapshot.total_s == 0.0
        document = snapshot.to_dict()
        assert document["latency_s"]["p99"] is not None
        assert document["alerts"]["CRITICAL"] == 0
        assert document["messages"]["spoken"] == 2

    def test_empty_snapshot(self) -> None:
        snapshot = SessionMetrics().snapshot()
        assert snapshot.latency_p50_s is None
        assert snapshot.latency_p95_s is None
        assert snapshot.latency_p99_s is None
        assert snapshot.total_s == 0.0
        assert set(snapshot.alerts) == {"CLEAR", "WATCH", "WARN", "CRITICAL"}
        assert snapshot.time_in_state_s["CLEAR"] == 0.0


# ---------------------------------------------------------------------------
# Tip policy
# ---------------------------------------------------------------------------


class TestTips:
    def test_clear_returns_none(self) -> None:
        policy = SafetyTipPolicy()
        assert (
            policy.select(
                assessment_for(),
                AlarmLevel.CLEAR,
                MobilityContext.WALKING,
                VerbosityProfile.STANDARD,
                0.0,
            )
            is None
        )

    def test_profiles_and_context(self) -> None:
        assessment = assessment_for((make_object(distance=2.0, speed=2.0),))
        policy = SafetyTipPolicy()
        concise = policy.select(
            assessment, AlarmLevel.WARN, MobilityContext.WALKING, VerbosityProfile.CONCISE, 0.0
        )
        assert concise is not None
        assert "Stay behind the kerb" not in concise.text
        standard = policy.select(
            assessment, AlarmLevel.WARN, MobilityContext.TRANSIT, VerbosityProfile.STANDARD, 1.0
        )
        assert standard is not None
        assert "Hold the handrail" in standard.text
        verbose = policy.select(
            assessment,
            AlarmLevel.CRITICAL,
            MobilityContext.CROSSING,
            VerbosityProfile.VERBOSE,
            2.0,
        )
        assert verbose is not None
        assert "Stay behind the kerb" in verbose.text
        assert "Estimated contact" in verbose.text

    def test_direction_phrases(self) -> None:
        expectations = {
            Position.LEFT: "on your left",
            Position.RIGHT: "on your right",
            Position.CENTER: "directly ahead",
            Position.UNKNOWN: "ahead",
        }
        for position, phrase in expectations.items():
            assessment = assessment_for(
                (make_object(distance=2.0, speed=2.0, position=position),)
            )
            policy = SafetyTipPolicy()
            tip = policy.select(
                assessment,
                AlarmLevel.WARN,
                MobilityContext.WALKING,
                VerbosityProfile.CONCISE,
                0.0,
            )
            assert tip is not None
            assert phrase in tip.text

    def test_repetition_alternates_then_resets(self) -> None:
        policy = SafetyTipPolicy(TipConfig(repeat_window_s=5.0))
        assessment = assessment_for((make_object(),))
        first = policy.select(
            assessment, AlarmLevel.CRITICAL, MobilityContext.WALKING, VerbosityProfile.CONCISE, 0.0
        )
        second = policy.select(
            assessment, AlarmLevel.CRITICAL, MobilityContext.WALKING, VerbosityProfile.CONCISE, 1.0
        )
        third = policy.select(
            assessment, AlarmLevel.CRITICAL, MobilityContext.WALKING, VerbosityProfile.CONCISE, 1.5
        )
        reset = policy.select(
            assessment, AlarmLevel.CRITICAL, MobilityContext.WALKING, VerbosityProfile.CONCISE, 7.0
        )
        assert first is not None and first.variant == 0
        assert second is not None and second.variant == 1
        assert second.text.startswith("Still too close")
        assert third is not None and third.variant == 0
        assert reset is not None and reset.variant == 0

    def test_warn_and_watch_variants(self) -> None:
        assessment = assessment_for(
            (make_object(distance=2.0, speed=2.0, position=Position.LEFT),)
        )
        policy = SafetyTipPolicy(TipConfig(repeat_window_s=10.0))
        warn = policy.select(
            assessment, AlarmLevel.WARN, MobilityContext.WALKING, VerbosityProfile.CONCISE, 0.0
        )
        warn_alt = policy.select(
            assessment, AlarmLevel.WARN, MobilityContext.WALKING, VerbosityProfile.CONCISE, 1.0
        )
        watch = policy.select(
            assessment, AlarmLevel.WATCH, MobilityContext.WALKING, VerbosityProfile.CONCISE, 2.0
        )
        watch_alt = policy.select(
            assessment, AlarmLevel.WATCH, MobilityContext.WALKING, VerbosityProfile.CONCISE, 3.0
        )
        assert warn is not None and warn.text.startswith("Pedestrian on your left")
        assert warn_alt is not None and warn_alt.text.startswith("Still tracking")
        assert watch is not None and watch.text.startswith("Pedestrian detected")
        assert watch_alt is not None and watch_alt.text.startswith("Continuing to track")

    def test_missing_distance_and_verbose_details(self) -> None:
        missing = assessment_for((make_object(distance=None),))
        policy = SafetyTipPolicy()
        tip = policy.select(
            missing, AlarmLevel.WARN, MobilityContext.WALKING, VerbosityProfile.VERBOSE, 0.0
        )
        assert tip is not None
        assert "close by" in tip.text
        assert "Depth estimate unavailable" in tip.text

    def test_static_verbose_clearance(self) -> None:
        static = assessment_for((make_object(distance=4.0, speed=0.0),))
        tip = SafetyTipPolicy().select(
            static, AlarmLevel.WATCH, MobilityContext.WALKING, VerbosityProfile.VERBOSE, 0.0
        )
        assert tip is not None
        assert "Estimated clearance" in tip.text

    def test_hazard_only_detail_and_hazard_override(self) -> None:
        hazard = Hazard(kind=HazardKind.ROAD_EDGE, confidence=0.8)
        hazard_assessment = assessment_for((), (hazard,))
        tip = SafetyTipPolicy().select(
            hazard_assessment,
            AlarmLevel.WATCH,
            MobilityContext.CROSSING,
            VerbosityProfile.VERBOSE,
            0.0,
        )
        assert tip is not None
        assert tip.hazard is HazardKind.ROAD_EDGE
        assert "Hazard marked by perception" in tip.text
        override = assessment_for(
            (make_object("o"),),
            (Hazard(kind=HazardKind.DOOR, confidence=1.0, object_id="o"),),
        )
        overridden = SafetyTipPolicy().select(
            override, AlarmLevel.WARN, MobilityContext.TRANSIT, VerbosityProfile.CONCISE, 0.0
        )
        assert overridden is not None
        assert overridden.hazard is HazardKind.DOOR
        assert "Doorway" in overridden.text

    def test_unknown_class_maps_to_other(self) -> None:
        unknown = make_object(cls=ObjectClass.UNKNOWN)
        assessment = assessment_for((unknown,))
        tip = SafetyTipPolicy().select(
            assessment, AlarmLevel.WATCH, MobilityContext.WALKING, VerbosityProfile.CONCISE, 0.0
        )
        assert tip is not None
        assert tip.hazard is HazardKind.OTHER

    def test_priority_and_ttl_mapping(self) -> None:
        assessment = assessment_for((make_object(),))
        policy = SafetyTipPolicy()
        critical = policy.select(
            assessment, AlarmLevel.CRITICAL, MobilityContext.WALKING, VerbosityProfile.STANDARD, 1.0
        )
        warn = policy.select(
            assessment, AlarmLevel.WARN, MobilityContext.TRANSIT, VerbosityProfile.STANDARD, 2.0
        )
        watch = policy.select(
            assessment, AlarmLevel.WATCH, MobilityContext.CROSSING, VerbosityProfile.STANDARD, 3.0
        )
        assert critical is not None and critical.priority is MessagePriority.CRITICAL
        assert critical.expires_at_s == pytest.approx(3.5)
        assert warn is not None and warn.priority is MessagePriority.HIGH
        assert warn.expires_at_s == pytest.approx(6.0)
        assert watch is not None and watch.priority is MessagePriority.NORMAL
        assert watch.expires_at_s == pytest.approx(9.0)

    def test_to_message(self) -> None:
        assessment = assessment_for((make_object(),))
        tip = SafetyTipPolicy().select(
            assessment, AlarmLevel.WARN, MobilityContext.WALKING, VerbosityProfile.CONCISE, 1.0
        )
        assert tip is not None
        message = tip.to_message("tip-0001")
        assert message.message_id == "tip-0001"
        assert message.text == tip.text
        assert message.dedupe_key == tip.dedupe_key

    def test_tip_config_validation(self) -> None:
        with pytest.raises(ConfigurationError):
            TipConfig(repeat_window_s=-1)
        assert TipConfig(repeat_window_s=0).repeat_window_s == 0


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class TestPipeline:
    def test_demo_end_to_end(self) -> None:
        pipeline = RealtimePipeline()
        guidance = pipeline.run(build_demo_events())
        assert [item.level for item in guidance] == [
            AlarmLevel.WATCH,
            AlarmLevel.WARN,
            AlarmLevel.CRITICAL,
        ]
        assert guidance[0].text.startswith("Pedestrian detected")
        assert guidance[-1].text.startswith("Pedestrian in your path")
        snapshot = pipeline.snapshot()
        assert snapshot.frames_processed == 10
        assert snapshot.alerts == {"CLEAR": 0, "WATCH": 1, "WARN": 1, "CRITICAL": 1}
        assert snapshot.transitions == 6
        assert snapshot.messages_spoken == 3
        assert sum(snapshot.time_in_state_s.values()) == pytest.approx(9.0)

    def test_out_of_order_frame_rejected(self) -> None:
        pipeline = RealtimePipeline()
        pipeline.process(make_event(timestamp=2.0, frame_id=2))
        with pytest.raises(OutOfOrderFrameError):
            pipeline.process(make_event(timestamp=1.0, frame_id=1))

    def test_same_timestamp_ordering_and_preemption(self) -> None:
        pipeline = RealtimePipeline()
        watch_event = make_event(
            timestamp=1.0,
            frame_id=1,
            objects=(make_object("p", distance=4.0, speed=2.0, confidence=0.9),),
        )
        critical_event = make_event(
            timestamp=1.0,
            frame_id=2,
            objects=(make_object("p", distance=1.0, speed=5.0, confidence=0.95),),
            hazards=(Hazard(kind=HazardKind.PEDESTRIAN_PATH, confidence=0.9, object_id="p"),),
        )
        output = pipeline.run((watch_event, critical_event))
        assert [item.timestamp_s for item in output] == [1.0, 1.0]
        assert [item.priority for item in output] == [
            MessagePriority.NORMAL,
            MessagePriority.CRITICAL,
        ]
        assert pipeline.snapshot().messages_preempted == 1

    def test_concise_profile_suppresses_watch(self) -> None:
        pipeline = RealtimePipeline(PipelineConfig(profile=VerbosityProfile.CONCISE))
        guidance = pipeline.run(build_demo_events())
        assert all(item.priority >= MessagePriority.HIGH for item in guidance)
        assert pipeline.snapshot().messages_dropped > 0

    def test_stream_and_snapshot(self) -> None:
        pipeline = RealtimePipeline()
        streamed = list(pipeline.stream(build_demo_events()))
        assert len(streamed) == 3
        assert isinstance(streamed[0], Guidance)
        snapshot = pipeline.snapshot()
        assert snapshot.frames_processed == 10

    def test_metrics_injection_and_last_state(self) -> None:
        metrics = SessionMetrics()
        pipeline = RealtimePipeline(metrics=metrics)
        assert pipeline.metrics is metrics
        guidance = pipeline.process(
            make_event(objects=(make_object(distance=2.0, speed=2.0, confidence=0.9),))
        )
        assert len(guidance) == 1
        assert pipeline.last_assessment is not None
        assert pipeline.last_transition is not None
        assert pipeline.last_transition.current is AlarmLevel.WARN

    def test_guidance_export(self) -> None:
        guidance = Guidance(
            timestamp_s=1.25,
            frame_id=3,
            level=AlarmLevel.WARN,
            text="Careful",
            priority=MessagePriority.HIGH,
            message_id="tip-1",
            dedupe_key="k",
            latency_s=0.012345678,
        )
        document = guidance.to_dict()
        assert document["level"] == "WARN"
        assert document["priority"] == "HIGH"
        assert document["latency_s"] == pytest.approx(0.012346)

    def test_pipeline_config_defaults(self) -> None:
        config = PipelineConfig()
        assert config.profile is VerbosityProfile.STANDARD
        assert RealtimePipeline(config).config is config


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_help_and_missing_source(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as help_exit:
            main(["--help"])
        assert help_exit.value.code == 0
        assert "Offline MGIA" in capsys.readouterr().out
        with pytest.raises(SystemExit) as missing:
            main([])
        assert missing.value.code == 2
        assert "choose one of" in capsys.readouterr().err

    def test_mutually_exclusive_sources(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as conflict:
            main(["--demo", "--frames", "frames.json"])
        assert conflict.value.code == 2
        assert "not allowed" in capsys.readouterr().err

    def test_demo_human_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--demo"]) == 0
        output = capsys.readouterr().out
        assert "WATCH" in output
        assert "CRITICAL" in output
        assert "10 frames | 3 guidance messages" in output
        assert "p50" in output

    def test_demo_json_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--demo", "--json"]) == 0
        document = json.loads(capsys.readouterr().out)
        assert document["profile"] == "standard"
        assert document["frame_count"] == 10
        assert len(document["guidance"]) == 3
        assert document["metrics"]["messages"]["spoken"] == 3

    def test_concise_json(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["--demo", "--json", "--profile", "concise"]) == 0
        document = json.loads(capsys.readouterr().out)
        assert document["profile"] == "concise"
        assert document["metrics"]["messages"]["dropped"] > 0

    def test_frames_file_list(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = tmp_path / "frames.json"
        path.write_text(
            json.dumps([{"timestamp_s": 0, "frame_id": 0, "objects": []}]), encoding="utf-8"
        )
        assert main(["--frames", str(path)]) == 0
        output = capsys.readouterr().out
        assert "p50 n/a" in output
        assert "1 frames | 0 guidance messages" in output

    def test_frames_document_and_load_events(self, tmp_path: Path) -> None:
        path = tmp_path / "frames.json"
        payload = {
            "frames": [
                {
                    "timestamp_s": 0,
                    "frame_id": 0,
                    "objects": [
                        {
                            "id": "p",
                            "class": "pedestrian",
                            "distance": 1.0,
                            "relative_speed": 4.0,
                            "confidence": 0.95,
                        }
                    ],
                    "hazards": [{"kind": "pedestrian_path", "object_id": "p"}],
                }
            ]
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        events = load_events(path)
        assert len(events) == 1
        assert events[0].objects[0].object_id == "p"

    def test_missing_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code = main(["--frames", str(tmp_path / "missing.json")])
        assert code == 1
        assert "cannot read" in capsys.readouterr().err

    def test_invalid_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert main(["--frames", str(path)]) == 1
        assert "not valid JSON" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "payload",
        [
            {"timestamp_s": 0, "objects": "nope"},
            {"frames": [{"objects": []}]},
            4,
            {"other": []},
            [{"timestamp_s": 0, "hazards": "nope"}],
            {"frames": [{"timestamp_s": 0, "objects": ["bad"]}]},
        ],
    )
    def test_invalid_frame_documents(
        self, payload: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "invalid.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert main(["--frames", str(path)]) == 1
        assert "mgia_realtime: error:" in capsys.readouterr().err

    def test_frame_source_error_type(self, tmp_path: Path) -> None:
        with pytest.raises(FrameSourceError):
            load_events(tmp_path / "nope.json")

    def test_build_parser_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--demo"])
        assert args.profile == "standard"
        assert args.demo is True
        assert args.frames is None


# ---------------------------------------------------------------------------
# Demo and package surface
# ---------------------------------------------------------------------------


class TestDemoAndPackage:
    def test_demo_is_deterministic(self) -> None:
        first = build_demo_events()
        second = build_demo_events()
        assert first == second
        assert len(first) == 10
        assert first[0].timestamp_s == 0.0
        assert first[-1].timestamp_s == 9.0

    def test_package_exports(self) -> None:
        import mgia_realtime

        assert mgia_realtime.__version__ == "2.0.0"
        assert mgia_realtime.RealtimePipeline is RealtimePipeline
        assert "MGIA" in mgia_realtime.__doc__
        for name in mgia_realtime.__all__:
            assert hasattr(mgia_realtime, name)

    def test_unknown_numeric_event(self) -> None:
        with pytest.raises(InvalidEventError):
            make_event(timestamp=math.inf)
