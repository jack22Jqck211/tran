"""Persian text normalization and subtitle line wrapping helpers."""
from __future__ import annotations

import re
from typing import List

RLM = "‏"  # RIGHT-TO-LEFT MARK

_PERSIAN_RE = re.compile(r"[؀-ۿ]")
_ARABIC_TO_PERSIAN = {
    "ي": "ی",  # ي -> ی
    "ك": "ک",  # ك -> ک
    "ة": "ه",  # ة -> ه
    "ۀ": "ه",
}
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([،؛؟!.…:,])")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_WRAPPING_QUOTES = re.compile(r'^\s*["\'«]?(.*?)["\'»]?\s*$', re.DOTALL)


def has_persian(text: str) -> bool:
    return bool(_PERSIAN_RE.search(text or ""))


def persian_ratio(text: str) -> float:
    if not text:
        return 0.0
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    persian = [c for c in letters if _PERSIAN_RE.match(c)]
    return len(persian) / len(letters)


def strip_wrapping_quotes(text: str) -> str:
    """Remove a single pair of quotes wrapping the whole line (LLM artifact)."""
    stripped = (text or "").strip()
    if len(stripped) >= 2:
        pairs = [('"', '"'), ("'", "'"), ("«", "»"), ("“", "”")]
        for open_q, close_q in pairs:
            if stripped.startswith(open_q) and stripped.endswith(close_q):
                inner = stripped[len(open_q):-len(close_q)].strip()
                # Only unwrap when quotes wrap the entire line, not dialogue quotes.
                if open_q not in inner and close_q not in inner:
                    return inner
    return stripped


def normalize_fa(text: str) -> str:
    """Normalize Persian text: unify characters, punctuation and spacing."""
    if not text:
        return ""
    out = text.strip()
    for src, dst in _ARABIC_TO_PERSIAN.items():
        out = out.replace(src, dst)
    if has_persian(out):
        # Persian sentences should use Persian punctuation.
        out = out.replace("?", "؟").replace("!", "!")
        # Convert Latin commas/semicolons between Persian words.
        out = re.sub(r"(?<=[؀-ۿ])\s*,\s*", "، ", out)
        out = re.sub(r"(?<=[؀-ۿ])\s*;\s*", "؛ ", out)
    out = _SPACE_BEFORE_PUNCT.sub(r"\1", out)
    # Ensure a space after Persian comma when followed by a letter.
    out = re.sub(r"،(?=\S)", "، ", out)
    out = _MULTI_SPACE.sub(" ", out)
    return out.strip()


def wrap_lines(text: str, max_len: int = 42, max_lines: int = 2) -> List[str]:
    """Split subtitle text into balanced display lines.

    Keeps a single line when it fits; otherwise splits on word boundaries,
    preferring balanced line lengths. Falls back to more lines (up to
    ``max_lines + 1``) for extremely long content rather than truncating.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    words = text.split()
    if len(words) == 1:
        return [text]  # single very long token: leave as-is

    # Try to distribute into n lines, n = 2..max_lines+1
    for n_lines in range(2, max_lines + 2):
        target = max(1, (len(text) + n_lines - 1) // n_lines)
        limit = max(target, max_len)
        lines: List[str] = []
        current = ""
        for word in words:
            candidate = (current + " " + word).strip()
            if current and len(candidate) > limit and len(lines) < n_lines - 1:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        if len(lines) <= n_lines and all(len(l) <= max_len * 1.35 for l in lines):
            return lines
    return lines  # last attempt, possibly slightly over


def apply_rtl_marks(lines: List[str]) -> List[str]:
    """Prefix RTL mark so punctuation renders on the correct side in players."""
    return [RLM + line if line and not line.startswith(RLM) else line for line in lines]
