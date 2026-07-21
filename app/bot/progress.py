"""Throttled live status updates (single message edited through all stages)."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

log = logging.getLogger("app.progress")

try:  # Telethon is present in production; tests run without it.
    from telethon import errors as tg_errors
    _FLOOD = tg_errors.FloodWaitError
    _NOT_MODIFIED = tg_errors.MessageNotModifiedError
except Exception:  # pragma: no cover
    class _FLOOD(Exception):
        seconds = 5

    class _NOT_MODIFIED(Exception):
        pass


class StatusReporter:
    """Edits one Telegram message with live progress, respecting rate limits."""

    def __init__(self, status_msg: Any, min_interval: float = 2.5,
                 buttons: Any = None) -> None:
        self._msg = status_msg
        self._min_interval = min_interval
        self._buttons = buttons
        self._last_text = ""
        self._last_edit = 0.0
        self._muted_until = 0.0

    def set_buttons(self, buttons: Any) -> None:
        self._buttons = buttons

    async def update(self, text: str, force: bool = False,
                     keep_buttons: bool = True) -> None:
        now = time.monotonic()
        if not force:
            if now < self._muted_until:
                return
            if text == self._last_text:
                return
            if now - self._last_edit < self._min_interval:
                return
        if self._msg is None:
            return
        try:
            await self._msg.edit(text, buttons=self._buttons if keep_buttons else None)
            self._last_text = text
            self._last_edit = now
        except _NOT_MODIFIED:
            self._last_text = text
            self._last_edit = now
        except _FLOOD as exc:
            seconds = float(getattr(exc, "seconds", 10) or 10)
            self._muted_until = now + seconds + 1.0
            log.warning("flood wait on edit; muting updates",
                        extra={"seconds": seconds})
        except Exception as exc:
            # Message may have been deleted by the user; never crash the job.
            log.debug("status edit failed: %s", exc)

    async def finalize(self, text: str) -> None:
        await self.update(text, force=True, keep_buttons=False)
