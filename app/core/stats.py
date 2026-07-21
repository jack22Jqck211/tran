"""In-memory operational statistics (admin /stats)."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Stats:
    started_at: float = field(default_factory=time.time)
    total: int = 0
    ok: int = 0
    failed: int = 0
    cancelled: int = 0
    media_seconds: float = 0.0
    processing_seconds: float = 0.0

    def record(self, status: str, media_seconds: float = 0.0,
               processing_seconds: float = 0.0) -> None:
        self.total += 1
        if status == "done":
            self.ok += 1
        elif status == "cancelled":
            self.cancelled += 1
        else:
            self.failed += 1
        self.media_seconds += max(0.0, media_seconds)
        self.processing_seconds += max(0.0, processing_seconds)
