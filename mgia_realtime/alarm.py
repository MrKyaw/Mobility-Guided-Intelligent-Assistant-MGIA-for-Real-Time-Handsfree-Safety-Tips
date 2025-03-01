"""Alarm state machine with hysteresis and minimum dwell time.

Alert flapping is a safety and accessibility problem: repeated tones and
interrupted speech make guidance harder to follow.  Rising risk changes state
immediately, while falling risk must cross a lower exit threshold and remain in
the current state for a minimum dwell time.  Downgrades move one level at a
time, giving the user a chance to hear the change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum
from types import MappingProxyType

from .errors import ConfigurationError, InvalidRiskScoreError, OutOfOrderFrameError


class AlarmLevel(IntEnum):
    """Ordered alarm levels; higher values are more urgent."""

    CLEAR = 0
    WATCH = 1
    WARN = 2
    CRITICAL = 3


@dataclass(frozen=True, slots=True)
class AlarmConfig:
    """Enter/exit thresholds and dwell settings.

    Every exit threshold is below its matching enter threshold, creating a
    hysteresis band.  One set of thresholds serves all levels.
    """

    enter_watch: float = 0.25
    enter_warn: float = 0.55
    enter_critical: float = 0.80
    exit_watch: float = 0.18
    exit_warn: float = 0.45
    exit_critical: float = 0.70
    min_dwell_s: float = 0.5

    def __post_init__(self) -> None:
        for name, value in (
            ("enter_watch", self.enter_watch),
            ("enter_warn", self.enter_warn),
            ("enter_critical", self.enter_critical),
            ("exit_watch", self.exit_watch),
            ("exit_warn", self.exit_warn),
            ("exit_critical", self.exit_critical),
        ):
            if not 0.0 <= value <= 1.0:
                raise ConfigurationError(f"{name} must be between 0 and 1")
        if not self.enter_watch < self.enter_warn < self.enter_critical:
            raise ConfigurationError("enter thresholds must be strictly increasing")
        if not self.exit_watch < self.exit_warn < self.exit_critical:
            raise ConfigurationError("exit thresholds must be strictly increasing")
        if not (
            self.exit_watch < self.enter_watch
            and self.exit_warn < self.enter_warn
            and self.exit_critical < self.enter_critical
        ):
            raise ConfigurationError("each exit threshold must be below its enter threshold")
        if self.min_dwell_s < 0.0:
            raise ConfigurationError("min_dwell_s must be non-negative")

    @property
    def exit_thresholds(self) -> MappingProxyType[AlarmLevel, float]:
        """Exit thresholds keyed by the level being left."""
        return MappingProxyType(
            {
                AlarmLevel.CLEAR: 0.0,
                AlarmLevel.WATCH: self.exit_watch,
                AlarmLevel.WARN: self.exit_warn,
                AlarmLevel.CRITICAL: self.exit_critical,
            }
        )


@dataclass(frozen=True, slots=True)
class AlarmTransition:
    """An immutable record of a state change."""

    previous: AlarmLevel
    current: AlarmLevel
    timestamp_s: float
    score: float
    reason: str

    @property
    def escalated(self) -> bool:
        """True when the transition moved to a more urgent level."""
        return self.current > self.previous


class AlarmStateMachine:
    """Stateful hysteresis filter for frame-level risk scores."""

    def __init__(self, config: AlarmConfig | None = None) -> None:
        self.config = config or AlarmConfig()
        self.current = AlarmLevel.CLEAR
        self._state_since: float | None = None
        self._last_update: float | None = None

    @property
    def state_since(self) -> float | None:
        """Timestamp at which the current level was entered."""
        return self._state_since

    def dwell_s(self, timestamp_s: float) -> float:
        """Seconds spent in the current level at ``timestamp_s``."""
        if self._state_since is None:
            return 0.0
        return max(0.0, timestamp_s - self._state_since)

    def _target_level(self, score: float) -> AlarmLevel:
        if score >= self.config.enter_critical:
            return AlarmLevel.CRITICAL
        if score >= self.config.enter_warn:
            return AlarmLevel.WARN
        if score >= self.config.enter_watch:
            return AlarmLevel.WATCH
        return AlarmLevel.CLEAR

    def update(self, score: float, timestamp_s: float) -> AlarmTransition | None:
        """Feed one score and return a transition when the level changes.

        Raises:
            InvalidRiskScoreError: If ``score`` is non-finite or outside 0-1.
            OutOfOrderFrameError: If time moves backwards for this machine.
        """
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            raise InvalidRiskScoreError("score must be a number")
        numeric_score = float(score)
        if not math.isfinite(numeric_score) or not 0.0 <= numeric_score <= 1.0:
            raise InvalidRiskScoreError("score must be finite and between 0 and 1")
        if not isinstance(timestamp_s, (int, float)) or isinstance(timestamp_s, bool):
            raise OutOfOrderFrameError("timestamp must be a number")
        timestamp = float(timestamp_s)
        if self._last_update is not None and timestamp < self._last_update:
            raise OutOfOrderFrameError("alarm timestamps must be non-decreasing")

        if self._state_since is None:
            self._state_since = timestamp
        self._last_update = timestamp
        target = self._target_level(numeric_score)

        if target > self.current:
            return self._transition(target, timestamp, numeric_score, "escalation")
        if target < self.current:
            if numeric_score >= self.config.exit_thresholds[self.current]:
                return None
            if timestamp - self._state_since < self.config.min_dwell_s:
                return None
            downgraded = AlarmLevel(int(self.current) - 1)
            return self._transition(downgraded, timestamp, numeric_score, "de-escalation")
        return None

    def _transition(
        self,
        new_level: AlarmLevel,
        timestamp_s: float,
        score: float,
        reason: str,
    ) -> AlarmTransition:
        previous = self.current
        self.current = new_level
        self._state_since = timestamp_s
        return AlarmTransition(
            previous=previous,
            current=new_level,
            timestamp_s=timestamp_s,
            score=score,
            reason=reason,
        )
