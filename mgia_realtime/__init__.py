"""MGIA real-time assistive safety core.

A dependency-free, deterministic pipeline for typed perception events, TTC risk
scoring, hysteretic alarms, accessible speech scheduling, and session metrics.
See ``python -m mgia_realtime --demo`` for an offline scenario.
"""

from __future__ import annotations

from .alarm import AlarmConfig, AlarmLevel, AlarmStateMachine, AlarmTransition
from .errors import (
    ConfigurationError,
    FrameSourceError,
    InvalidEventError,
    InvalidRiskScoreError,
    MgiaError,
    OutOfOrderFrameError,
)
from .events import (
    DetectedObject,
    Hazard,
    HazardKind,
    MobilityContext,
    ObjectClass,
    PerceptionEvent,
    Position,
    parse_event_stream,
)
from .metrics import MetricsSnapshot, SessionMetrics
from .pipeline import Guidance, PipelineConfig, RealtimePipeline
from .risk import ObjectRisk, RiskAssessment, RiskConfig, RiskScorer
from .speech import (
    EnqueueResult,
    EnqueueStatus,
    MessagePriority,
    SpeechConfig,
    SpeechMessage,
    SpeechScheduler,
    SpokenMessage,
    VerbosityProfile,
)
from .tips import SafetyTipPolicy, Tip, TipConfig

__all__ = [
    "AlarmConfig",
    "AlarmLevel",
    "AlarmStateMachine",
    "AlarmTransition",
    "ConfigurationError",
    "DetectedObject",
    "EnqueueResult",
    "EnqueueStatus",
    "FrameSourceError",
    "Guidance",
    "Hazard",
    "HazardKind",
    "InvalidEventError",
    "InvalidRiskScoreError",
    "MetricsSnapshot",
    "MessagePriority",
    "MgiaError",
    "MobilityContext",
    "ObjectClass",
    "ObjectRisk",
    "OutOfOrderFrameError",
    "PerceptionEvent",
    "PipelineConfig",
    "Position",
    "RealtimePipeline",
    "RiskAssessment",
    "RiskConfig",
    "RiskScorer",
    "SafetyTipPolicy",
    "SessionMetrics",
    "SpeechConfig",
    "SpeechMessage",
    "SpeechScheduler",
    "SpokenMessage",
    "Tip",
    "TipConfig",
    "VerbosityProfile",
    "parse_event_stream",
]

__version__ = "2.0.0"
