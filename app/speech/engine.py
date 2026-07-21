"""Speech-to-text engine built on faster-whisper (CTranslate2 Whisper).

Design goals:
- WhisperX-style capabilities: word-level timestamps, VAD filtering and
  automatic language detection, with a small dependency footprint that runs
  well on CPU-only hosts (Railway) and automatically uses CUDA when present.
- The engine is lazy: the model is downloaded/loaded on first use and can be
  swapped at runtime by admins (``/model``).
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from ..core.errors import JobCancelled, PipelineError
from .types import SegmentData, TranscriptResult, WordSpan

log = logging.getLogger("app.speech")

ProgressFn = Callable[[float], None]  # fraction 0..1


class SpeechEngine:
    def __init__(self, model_name: str, device: str = "auto",
                 compute_type: str = "auto", beam_size: int = 5,
                 vad_filter: bool = True) -> None:
        self.model_name = model_name
        self.device_pref = device
        self.compute_pref = compute_type
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self._model = None
        self._loaded_name: Optional[str] = None
        self._lock = threading.Lock()
        self.resolved_device = "cpu"
        self.resolved_compute = "int8"

    # -- device resolution --------------------------------------------------
    def _resolve(self) -> None:
        device = self.device_pref
        compute = self.compute_pref
        if device == "auto":
            device = "cpu"
            try:
                import ctranslate2  # type: ignore
                if ctranslate2.get_cuda_device_count() > 0:
                    device = "cuda"
            except Exception:
                device = "cpu"
        if compute == "auto":
            compute = "float16" if device == "cuda" else "int8"
        self.resolved_device = device
        self.resolved_compute = compute

    # -- model management ---------------------------------------------------
    @property
    def is_loaded(self) -> bool:
        return self._model is not None and self._loaded_name == self.model_name

    def set_model(self, name: str) -> None:
        """Request a different Whisper model; applied on next load."""
        with self._lock:
            self.model_name = name
            self._model = None
            self._loaded_name = None

    def _get_model(self):
        with self._lock:
            if self._model is not None and self._loaded_name == self.model_name:
                return self._model
            try:
                from faster_whisper import WhisperModel  # heavy import, lazy
            except ImportError as exc:  # pragma: no cover
                raise PipelineError(
                    "موتور تشخیص گفتار در دسترس نیست.",
                    detail="faster-whisper is not installed: %s" % exc,
                )
            self._resolve()
            t0 = time.monotonic()
            log.info("Loading whisper model", extra={
                "model": self.model_name,
                "device": self.resolved_device,
                "compute": self.resolved_compute,
            })
            try:
                self._model = WhisperModel(
                    self.model_name,
                    device=self.resolved_device,
                    compute_type=self.resolved_compute,
                )
            except Exception as exc:
                if self.resolved_device == "cuda":
                    log.warning("CUDA load failed (%s); falling back to CPU", exc)
                    self.resolved_device, self.resolved_compute = "cpu", "int8"
                    self._model = WhisperModel(self.model_name, device="cpu",
                                               compute_type="int8")
                else:
                    raise
            self._loaded_name = self.model_name
            log.info("Model loaded", extra={
                "model": self.model_name,
                "seconds": round(time.monotonic() - t0, 1),
            })
            return self._model

    async def preload(self) -> None:
        await asyncio.to_thread(self._get_model)

    # -- transcription --------------------------------------------------------
    async def transcribe(self, wav_path: Path,
                         cancel_event: Optional[asyncio.Event] = None,
                         progress: Optional[ProgressFn] = None,
                         language: Optional[str] = None) -> TranscriptResult:
        loop = asyncio.get_running_loop()

        def report(fraction: float) -> None:
            if progress is not None:
                loop.call_soon_threadsafe(progress, fraction)

        return await asyncio.to_thread(
            self._transcribe_sync, wav_path, cancel_event, report, language
        )

    def _transcribe_sync(self, wav_path: Path,
                         cancel_event: Optional[asyncio.Event],
                         report: Callable[[float], None],
                         language: Optional[str]) -> TranscriptResult:
        model = self._get_model()
        try:
            segments_iter, info = model.transcribe(
                str(wav_path),
                language=language,
                task="transcribe",
                beam_size=self.beam_size,
                vad_filter=self.vad_filter,
                vad_parameters={"min_silence_duration_ms": 500},
                word_timestamps=True,
                condition_on_previous_text=False,  # avoids repetition loops on music
            )
        except Exception as exc:
            raise PipelineError("تشخیص گفتار با خطا مواجه شد.",
                                detail="transcribe failed: %s" % exc)

        duration = float(getattr(info, "duration", 0.0) or 0.0)
        segments = []
        for seg in segments_iter:
            if cancel_event is not None and cancel_event.is_set():
                raise JobCancelled()
            text = (seg.text or "").strip()
            if not text:
                continue
            words = []
            for w in (seg.words or []):
                token = (w.word or "").strip()
                if token:
                    words.append(WordSpan(start=float(w.start), end=float(w.end),
                                          word=token))
            segments.append(SegmentData(start=float(seg.start), end=float(seg.end),
                                        text=text, words=words))
            if duration > 0:
                report(min(0.999, float(seg.end) / duration))
        report(1.0)
        return TranscriptResult(
            language=str(getattr(info, "language", "") or "unknown"),
            language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
            duration=duration,
            segments=segments,
            model_name=self.model_name,
            device=self.resolved_device,
        )
