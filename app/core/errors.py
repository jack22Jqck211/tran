"""Domain errors. User-facing messages are Persian; internals stay English."""
from __future__ import annotations


class JobCancelled(Exception):
    """Raised when the user cancels an in-flight job."""


class UserFacingError(Exception):
    """Base for errors that carry a safe Persian message for the user."""

    def __init__(self, user_message: str, detail: str = "") -> None:
        super().__init__(detail or user_message)
        self.user_message = user_message
        self.detail = detail


class ValidationError(UserFacingError):
    """Incoming file failed validation (type, size, integrity)."""


class PipelineError(UserFacingError):
    """A processing stage failed (ffmpeg, ASR, IO...)."""


class TranslationError(Exception):
    """All translation providers/models failed for a request."""
