"""Exception hierarchy for the MGIA real-time safety core.

All package-specific failures derive from :class:`MgiaError` so callers can
distinguish expected input/config problems from programming errors.
"""

from __future__ import annotations


class MgiaError(Exception):
    """Base class for every expected MGIA failure."""


class ConfigurationError(MgiaError, ValueError):
    """Raised when a configuration object contains invalid values."""


class InvalidEventError(MgiaError, ValueError):
    """Raised when a perception event or frame payload is malformed."""


class OutOfOrderFrameError(MgiaError):
    """Raised when frames arrive earlier than a previously processed frame."""


class InvalidRiskScoreError(MgiaError, ValueError):
    """Raised when a risk score is not a finite value in ``[0, 1]``."""


class FrameSourceError(MgiaError):
    """Raised when a frame file cannot be read or decoded."""
