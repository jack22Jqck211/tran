"""Plain data types shared between the speech engine and subtitle builder.

Kept dependency-free so unit tests can run without faster-whisper installed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class WordSpan:
    start: float
    end: float
    word: str


@dataclass
class SegmentData:
    start: float
    end: float
    text: str
    words: List[WordSpan] = field(default_factory=list)


@dataclass
class TranscriptResult:
    language: str
    language_probability: float
    duration: float
    segments: List[SegmentData]
    model_name: str = ""
    device: str = ""

    @property
    def is_empty(self) -> bool:
        return not any(seg.text.strip() for seg in self.segments)
