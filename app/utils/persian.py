"""Persian text utilities."""
from __future__ import annotations

import re

_PERSIAN_PUNCT_FIXES = [
    (r"\s+،", "،"),
    (r"،\s*", "، "),
    (r"\s+\.+", "."),
    (r"\s+!+", "!"),
    (r"\s+\?+", "؟"),
    (r"\.{2,}", "..."),
]


def normalize_persian(text: str) -> str:
    if not text:
        return text
    out = text.strip()
    for pat, repl in _PERSIAN_PUNCT_FIXES:
        out = re.sub(pat, repl, out)
    out = out.replace("\u064a", "\u06cc").replace("\u0643", "\u06a9")
    out = re.sub(r"\s{2,}", " ", out)
    return out


def ensure_final_punctuation(text: str) -> str:
    if not text:
        return text
    text = text.strip()
    if not text:
        return text
    if text[-1] not in ".!؟…":
        text += "."
    return text
