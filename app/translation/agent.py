"""Autonomous translation agent.

Responsibilities:
- chunk subtitles and translate chunks in parallel workers
- keep translations context-aware (neighboring dialogue passed as context)
- select providers/models by priority with health tracking and automatic
  fallback (circuit breaker per provider)
- retry with backoff on transient failures, respect Retry-After
- recover missing lines individually so a bad chunk never sinks the job
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

import httpx

from ..config import ProviderConfig
from ..core.errors import TranslationError
from ..subtitles.builder import Cue
from ..utils.textproc import normalize_fa, strip_wrapping_quotes
from .openai_compat import (ModelUnavailableError, OpenAICompatClient,
                            ProviderAuthError, RetryableAPIError)
from .parsing import coverage, parse_translations

log = logging.getLogger("app.translate")

SYSTEM_PROMPT = (
    "You are an expert film and TV subtitle translator. Translate each "
    "subtitle line into natural, fluent, conversational Persian (Farsi) the "
    "way a professional Iranian subtitle team would. Rules:\n"
    "- Prioritize meaning and natural tone over literal translation.\n"
    "- Keep character/proper names transliterated consistently across lines.\n"
    "- Keep each translation short and subtitle-friendly; never merge, split, "
    "reorder or drop lines.\n"
    "- Preserve the emotional register (slang stays casual, formal stays formal).\n"
    "- Never add explanations, notes or romanization.\n"
    'Respond with ONLY a JSON array: [{"i": <line number>, "t": "<Persian>"}] '
    "— one object per input line, same numbering."
)

RETRY_PROMPT_SUFFIX = (
    "\nIMPORTANT: your previous answer was not valid JSON. Return ONLY the "
    "JSON array, with every requested line number present."
)


@dataclass
class ProviderState:
    config: ProviderConfig
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    total_requests: int = 0
    total_failures: int = 0

    @property
    def available(self) -> bool:
        return time.monotonic() >= self.cooldown_until

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.total_requests += 1

    def record_failure(self, cooldown: float = 0.0) -> None:
        self.consecutive_failures += 1
        self.total_requests += 1
        self.total_failures += 1
        if cooldown:
            self.cooldown_until = time.monotonic() + cooldown
        elif self.consecutive_failures >= 4:
            # Circuit breaker: rest a failing provider for a minute.
            self.cooldown_until = time.monotonic() + 60.0


@dataclass
class _Chunk:
    ids: List[int]
    items: List[dict]
    context_before: List[str] = field(default_factory=list)
    context_after: List[str] = field(default_factory=list)


ProgressFn = Callable[[float], None]


class TranslationAgent:
    def __init__(self, providers: Sequence[ProviderConfig],
                 http: httpx.AsyncClient, workers: int = 4,
                 chunk_size: int = 25, target_language: str = "fa") -> None:
        self.states = [ProviderState(p) for p in providers]
        self.client = OpenAICompatClient(http)
        self.workers = max(1, workers)
        self.chunk_size = max(5, chunk_size)
        self.target_language = target_language

    @property
    def configured(self) -> bool:
        return bool(self.states)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def translate_cues(self, cues: Sequence[Cue], source_lang: str,
                             progress: Optional[ProgressFn] = None) -> int:
        """Fill ``cue.translation`` in place. Returns count of failed lines."""
        if not self.configured:
            raise TranslationError("no translation providers configured")
        cues = list(cues)
        if not cues:
            return 0

        chunks = self._make_chunks(cues)
        results: Dict[int, str] = {}
        done_chunks = 0
        semaphore = asyncio.Semaphore(self.workers)

        async def run_chunk(chunk: _Chunk) -> None:
            nonlocal done_chunks
            async with semaphore:
                try:
                    part = await self._translate_chunk(chunk, source_lang)
                    results.update(part)
                except TranslationError as exc:
                    log.warning("chunk failed permanently", extra={
                        "ids": "%d-%d" % (chunk.ids[0], chunk.ids[-1]),
                        "error": str(exc),
                    })
                done_chunks += 1
                if progress:
                    progress(done_chunks / len(chunks) * 0.9)

        await asyncio.gather(*(run_chunk(c) for c in chunks))

        # Recover lines missing from chunk results, one by one.
        missing = [c for c in cues if not results.get(c.index, "").strip()]
        for i, cue in enumerate(missing):
            try:
                results[cue.index] = await self._translate_single(cue.text, source_lang)
            except TranslationError:
                pass
            if progress and missing:
                progress(0.9 + (i + 1) / len(missing) * 0.1)

        failed = 0
        for cue in cues:
            text = results.get(cue.index, "").strip()
            if text:
                cue.translation = normalize_fa(strip_wrapping_quotes(text))
            else:
                cue.translation = cue.text  # graceful degradation: keep original
                failed += 1
        if progress:
            progress(1.0)
        return failed

    async def translate_single(self, text: str, source_lang: str = "") -> str:
        return await self._translate_single(text, source_lang)

    def health_snapshot(self) -> List[dict]:
        return [{
            "provider": s.config.name,
            "models": s.config.models,
            "requests": s.total_requests,
            "failures": s.total_failures,
            "cooling": not s.available,
        } for s in self.states]

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _make_chunks(self, cues: Sequence[Cue]) -> List[_Chunk]:
        chunks: List[_Chunk] = []
        for offset in range(0, len(cues), self.chunk_size):
            window = cues[offset:offset + self.chunk_size]
            before = [c.text for c in cues[max(0, offset - 3):offset]]
            after_start = offset + self.chunk_size
            after = [c.text for c in cues[after_start:after_start + 3]]
            chunks.append(_Chunk(
                ids=[c.index for c in window],
                items=[{"i": c.index, "t": c.text} for c in window],
                context_before=before,
                context_after=after,
            ))
        return chunks

    def _chunk_prompt(self, chunk: _Chunk, source_lang: str) -> str:
        parts: List[str] = []
        lang = source_lang or "the source language"
        parts.append("Source language: %s. Target: Persian (Farsi)." % lang)
        if chunk.context_before:
            parts.append("Context — lines right BEFORE this batch (do NOT translate):\n"
                         + "\n".join(chunk.context_before))
        if chunk.context_after:
            parts.append("Context — lines right AFTER this batch (do NOT translate):\n"
                         + "\n".join(chunk.context_after))
        parts.append("Translate these subtitle lines:\n"
                     + json.dumps(chunk.items, ensure_ascii=False))
        return "\n\n".join(parts)

    async def _translate_chunk(self, chunk: _Chunk,
                               source_lang: str) -> Dict[int, str]:
        best: Dict[int, str] = {}
        prompt = self._chunk_prompt(chunk, source_lang)
        # Generous budget: reasoning models burn hundreds of hidden tokens
        # per line before emitting the JSON answer.
        max_tokens = 1200 + 350 * len(chunk.ids)
        for attempt in range(2):
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT
                    + (RETRY_PROMPT_SUFFIX if attempt else "")},
                {"role": "user", "content": prompt},
            ]
            raw = await self._complete(messages, max_tokens)
            parsed = parse_translations(raw, chunk.ids)
            if len(parsed) > len(best):
                best = parsed
            if coverage(best, chunk.ids) >= 0.95:
                return best
        return best

    async def _translate_single(self, text: str, source_lang: str) -> str:
        messages = [
            {"role": "system", "content":
                "Translate this single subtitle line into natural, fluent "
                "Persian (Farsi). Reply with ONLY the Persian translation, "
                "no quotes, no explanation."},
            {"role": "user", "content": text},
        ]
        raw = await self._complete(messages, max_tokens=1600)
        cleaned = normalize_fa(strip_wrapping_quotes(raw.strip()))
        if not cleaned or len(cleaned) > max(200, len(text) * 6):
            raise TranslationError("single-line translation looked invalid")
        return cleaned

    async def _complete(self, messages: List[dict], max_tokens: int) -> str:
        """Try providers by priority, models in order, with retries/backoff."""
        last_error: Optional[Exception] = None
        for state in self.states:
            if not state.available:
                continue
            provider = state.config
            for model in provider.models:
                for attempt in range(2):
                    try:
                        result = await self.client.chat(
                            provider, model, messages,
                            max_tokens=min(max_tokens, provider.max_tokens),
                        )
                        state.record_success()
                        return result
                    except RetryableAPIError as exc:
                        last_error = exc
                        state.record_failure()
                        delay = exc.retry_after if exc.retry_after \
                            else (1.2 * (attempt + 1) + random.random())
                        await asyncio.sleep(min(delay, 15.0))
                    except ModelUnavailableError as exc:
                        last_error = exc
                        log.info("model unavailable, rotating", extra={
                            "provider": provider.name, "model": model})
                        break  # next model
                    except ProviderAuthError as exc:
                        last_error = exc
                        state.record_failure(cooldown=300.0)
                        log.error("provider auth failed", extra={
                            "provider": provider.name})
                        break  # next provider
                if isinstance(last_error, ProviderAuthError):
                    break
        raise TranslationError("all providers failed: %s" % last_error)
