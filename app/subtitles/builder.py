"""Turn raw ASR segments into clean, readable subtitle cues.

Rules implemented:
- split segments that are too long (chars or duration), using word timings
- merge fragments that are too short to read
- keep a comfortable reading speed by extending display time into gaps
- guarantee monotonic, non-overlapping timing
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from ..speech.types import SegmentData, WordSpan

SENTENCE_END = tuple(".!?؟!…។।")
SOFT_BREAK = tuple(",،;؛:")


@dataclass
class Cue:
    index: int
    start: float
    end: float
    text: str
    translation: Optional[str] = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class BuilderOptions:
    max_chars: int = 84            # ~2 lines of 42 chars
    max_duration: float = 6.0
    min_duration: float = 1.0
    merge_max_gap: float = 0.4     # merge fragments closer than this
    merge_below_duration: float = 1.2
    merge_below_chars: int = 24
    reading_cps: float = 17.0      # comfortable chars/second
    gap_padding: float = 0.05


@dataclass
class _Piece:
    start: float
    end: float
    text: str
    words: List[WordSpan] = field(default_factory=list)


def _split_by_words(piece: _Piece, opts: BuilderOptions) -> List[_Piece]:
    """Split an oversized piece on word boundaries, preferring punctuation."""
    words = piece.words
    if not words:
        return _split_by_chars(piece, opts)

    pieces: List[_Piece] = []
    current: List[WordSpan] = []
    current_len = 0
    last_break = -1  # index in `current` after which a punctuation break exists

    def flush(upto: Optional[int] = None) -> None:
        nonlocal current, current_len, last_break
        take = current if upto is None else current[:upto]
        rest = [] if upto is None else current[upto:]
        if take:
            text = " ".join(w.word for w in take).strip()
            pieces.append(_Piece(start=take[0].start, end=take[-1].end,
                                 text=text, words=list(take)))
        current = rest
        current_len = sum(len(w.word) + 1 for w in current)
        last_break = -1
        for i, w in enumerate(current):
            if w.word.endswith(SENTENCE_END) or w.word.endswith(SOFT_BREAK):
                last_break = i

    for word in words:
        current.append(word)
        current_len += len(word.word) + 1
        if word.word.endswith(SENTENCE_END) or word.word.endswith(SOFT_BREAK):
            last_break = len(current) - 1
        too_long = current_len > opts.max_chars
        too_slow = (current[-1].end - current[0].start) > opts.max_duration
        if (too_long or too_slow) and len(current) > 1:
            # Prefer breaking after recent punctuation; otherwise before this word.
            if last_break >= 0 and last_break >= len(current) - 8 and last_break < len(current) - 1:
                flush(last_break + 1)
            else:
                flush(len(current) - 1)
    flush(None)
    return [p for p in pieces if p.text]


def _split_by_chars(piece: _Piece, opts: BuilderOptions) -> List[_Piece]:
    """Fallback split without word timings: proportional time allocation."""
    text = piece.text.strip()
    if len(text) <= opts.max_chars and (piece.end - piece.start) <= opts.max_duration:
        return [piece]
    words = text.split()
    if len(words) < 2:
        return [piece]
    mid = len(words) // 2
    left = " ".join(words[:mid])
    right = " ".join(words[mid:])
    total_chars = max(1, len(left) + len(right))
    split_time = piece.start + (piece.end - piece.start) * (len(left) / total_chars)
    out: List[_Piece] = []
    for sub in (_Piece(piece.start, split_time, left),
                _Piece(split_time, piece.end, right)):
        out.extend(_split_by_chars(sub, opts))
    return out


def build_cues(segments: Sequence[SegmentData],
               opts: Optional[BuilderOptions] = None) -> List[Cue]:
    opts = opts or BuilderOptions()

    # 1) normalize and split oversized segments
    pieces: List[_Piece] = []
    for seg in segments:
        text = " ".join((seg.text or "").split())
        if not text:
            continue
        piece = _Piece(start=seg.start, end=max(seg.end, seg.start + 0.2),
                       text=text, words=list(seg.words or []))
        if len(text) > opts.max_chars or piece.end - piece.start > opts.max_duration:
            pieces.extend(_split_by_words(piece, opts))
        else:
            pieces.append(piece)

    pieces = [p for p in pieces if p.text.strip()]
    pieces.sort(key=lambda p: (p.start, p.end))

    # 2) merge fragments that are too short to read comfortably
    merged: List[_Piece] = []
    for piece in pieces:
        if merged:
            prev = merged[-1]
            gap = piece.start - prev.end
            combined_len = len(prev.text) + 1 + len(piece.text)
            prev_short = (prev.end - prev.start) < opts.merge_below_duration \
                or len(prev.text) < opts.merge_below_chars
            if prev_short and gap <= opts.merge_max_gap \
                    and combined_len <= opts.max_chars \
                    and not prev.text.endswith(SENTENCE_END):
                prev.text = (prev.text + " " + piece.text).strip()
                prev.end = max(prev.end, piece.end)
                prev.words.extend(piece.words)
                continue
        merged.append(piece)

    # 3) timing polish: reading speed, minimum duration, no overlaps
    cues: List[Cue] = []
    for i, piece in enumerate(merged):
        start = max(0.0, piece.start)
        end = max(start + 0.3, piece.end)
        desired = max(opts.min_duration, len(piece.text) / opts.reading_cps)
        next_start = merged[i + 1].start if i + 1 < len(merged) else None
        limit = (next_start - opts.gap_padding) if next_start is not None else end + 1.5
        if end - start < desired:
            end = min(start + desired, max(limit, start + 0.3))
        if next_start is not None and end > next_start - opts.gap_padding:
            end = max(start + 0.3, next_start - opts.gap_padding)
        cues.append(Cue(index=0, start=start, end=end, text=piece.text))

    # ensure strictly monotonic, non-overlapping cues
    for i in range(1, len(cues)):
        if cues[i].start < cues[i - 1].end:
            cues[i].start = cues[i - 1].end + 0.01
            if cues[i].end < cues[i].start + 0.3:
                cues[i].end = cues[i].start + 0.3

    for n, cue in enumerate(cues, start=1):
        cue.index = n
    return cues
