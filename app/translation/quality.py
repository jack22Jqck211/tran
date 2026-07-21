"""AI quality-controller pass over translated subtitles.

Detects broken lines (leaked code fences, refusals, untranslated text,
degenerate repetition) and repairs them with targeted single-line
re-translation, then applies final Persian normalization.
"""
from __future__ import annotations

import logging
import re
from typing import List, Sequence

from ..core.errors import TranslationError
from ..subtitles.builder import Cue
from ..utils.textproc import has_persian, normalize_fa, strip_wrapping_quotes
from .agent import TranslationAgent

log = logging.getLogger("app.translate.qc")

_REFUSAL_MARKERS = (
    "i cannot", "i can't", "as an ai", "sorry, i", "i'm unable",
    "cannot translate", "متأسفم، نمی‌توانم", "نمی‌توانم ترجمه",
)
_LEAK_MARKERS = ("```", '{"i"', "[{", "}]")
_PREFIX_RE = re.compile(r"^\s*(ترجمه|Translation)\s*[:：]\s*", re.IGNORECASE)


def _is_broken(cue: Cue) -> bool:
    tr = (cue.translation or "").strip()
    if not tr:
        return True
    low = tr.lower()
    if any(marker in low for marker in _REFUSAL_MARKERS):
        return True
    if any(marker in tr for marker in _LEAK_MARKERS):
        return True
    # Source clearly non-Persian but "translation" has no Persian at all
    # (and isn't just numbers/symbols/names).
    if not has_persian(tr) and not has_persian(cue.text):
        letters = [c for c in tr if c.isalpha()]
        if len(letters) >= 6 and tr.strip() == cue.text.strip():
            return True
    # Degenerate repetition ("نه نه نه نه نه ..." beyond reason)
    words = tr.split()
    if len(words) >= 8 and len(set(words)) <= 2:
        return True
    return False


async def quality_pass(cues: Sequence[Cue], agent: TranslationAgent,
                       source_lang: str, max_fixes: int = 60) -> int:
    """Repair broken translations in place. Returns number of repaired lines."""
    fixed = 0
    broken: List[Cue] = [c for c in cues if _is_broken(c)]
    for cue in broken[:max_fixes]:
        try:
            cue.translation = await agent.translate_single(cue.text, source_lang)
            fixed += 1
        except TranslationError:
            cue.translation = cue.translation or cue.text
    if broken:
        log.info("quality pass complete", extra={
            "broken": len(broken), "fixed": fixed})

    # Final polish on every line.
    for cue in cues:
        if cue.translation:
            text = _PREFIX_RE.sub("", cue.translation)
            cue.translation = normalize_fa(strip_wrapping_quotes(text))
    return fixed
