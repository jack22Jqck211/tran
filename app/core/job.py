"""Job model shared between the Telegram layer, queue and pipeline."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class JobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class Job:
    user_id: int
    chat_id: int
    media_msg: Any                      # telethon Message with media
    status_msg: Any                     # telethon Message we keep editing
    file_name: str
    file_size: int
    mime_type: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = JobStatus.QUEUED
    stage: str = "در صف"
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    created_at: float = field(default_factory=time.monotonic)
    started_at: Optional[float] = None
    timings: Dict[str, float] = field(default_factory=dict)

    @property
    def elapsed(self) -> float:
        base = self.started_at or self.created_at
        return time.monotonic() - base

    def mark_stage(self, name: str, seconds: float) -> None:
        self.timings[name] = self.timings.get(name, 0.0) + seconds
