"""Robust parsing of LLM translation responses.

Models are instructed to return a strict JSON array, but real-world outputs
drift: code fences, object maps, numbered lists... This module recovers the
``{index: translation}`` mapping from all common shapes.
"""
from __future__ import annotations

import json
import re
from typing import Dict, Iterable, Sequence

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\s*|\s*```$", re.MULTILINE)
_NUMBERED_RE = re.compile(r"^\s*(\d{1,6})\s*[\.\):\-–—]\s*(.+?)\s*$")


def _strip_fences(raw: str) -> str:
    return _FENCE_RE.sub("", raw or "").strip()


def _coerce_items(data: object) -> Dict[int, str]:
    out: Dict[int, str] = {}
    if isinstance(data, dict):
        # {"1": "...", "2": "..."} or {"translations": [...]}
        inner = data.get("translations") if "translations" in data else None
        if isinstance(inner, (list, dict)):
            return _coerce_items(inner)
        for key, value in data.items():
            try:
                idx = int(str(key).strip())
            except (TypeError, ValueError):
                continue
            if isinstance(value, str) and value.strip():
                out[idx] = value.strip()
            elif isinstance(value, dict):
                text = value.get("t") or value.get("text") or value.get("translation")
                if isinstance(text, str) and text.strip():
                    out[idx] = text.strip()
        return out
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            idx_raw = item.get("i", item.get("index", item.get("id")))
            text = item.get("t") or item.get("text") or item.get("translation") \
                or item.get("fa")
            try:
                idx = int(str(idx_raw).strip())
            except (TypeError, ValueError):
                continue
            if isinstance(text, str) and text.strip():
                out[idx] = text.strip()
    return out


def _find_json_payload(raw: str) -> object:
    """Extract the first JSON array/object from arbitrary model output."""
    candidates = []
    array_start, array_end = raw.find("["), raw.rfind("]")
    if array_start != -1 and array_end > array_start:
        candidates.append(raw[array_start:array_end + 1])
    obj_start, obj_end = raw.find("{"), raw.rfind("}")
    if obj_start != -1 and obj_end > obj_start:
        candidates.append(raw[obj_start:obj_end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # Some models emit trailing commas — try a light repair.
            repaired = re.sub(r",\s*([\]\}])", r"\1", candidate)
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                continue
    return None


def parse_translations(raw: str, expected_ids: Sequence[int]) -> Dict[int, str]:
    """Return whatever subset of ``expected_ids`` could be recovered."""
    if not raw:
        return {}
    cleaned = _strip_fences(raw)

    payload = _find_json_payload(cleaned)
    result = _coerce_items(payload) if payload is not None else {}

    if not result:
        # Fallback: numbered lines ("12. متن ترجمه")
        for line in cleaned.splitlines():
            match = _NUMBERED_RE.match(line)
            if match:
                idx = int(match.group(1))
                text = match.group(2).strip()
                if text:
                    result[idx] = text

    expected = set(expected_ids)
    return {idx: text for idx, text in result.items() if idx in expected}


def coverage(result: Dict[int, str], expected_ids: Iterable[int]) -> float:
    expected = list(expected_ids)
    if not expected:
        return 1.0
    hit = sum(1 for idx in expected if result.get(idx, "").strip())
    return hit / len(expected)
