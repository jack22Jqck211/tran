"""End-to-end processing pipeline for one job.

download → validate → probe → extract audio → transcribe → build cues →
translate (parallel agent) → quality pass → write subtitle files → send →
cleanup. Temporary files are always deleted, on every exit path.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import List, Optional

from ..bot import messages as M
from ..bot.progress import StatusReporter
from ..config import Settings
from ..core.errors import (JobCancelled, PipelineError, TranslationError,
                           UserFacingError)
from ..core.job import Job, JobStatus
from ..core.stats import Stats
from ..media import ffmpeg
from ..media.validator import extension_of, sanitize_stem, validate_incoming
from ..speech.engine import SpeechEngine
from ..subtitles.builder import BuilderOptions, build_cues
from ..subtitles.writers import RenderOptions, write_outputs
from ..translation.agent import TranslationAgent
from ..translation.quality import quality_pass

log = logging.getLogger("app.pipeline")


class Pipeline:
    def __init__(self, client, settings: Settings, engine: SpeechEngine,
                 agent: Optional[TranslationAgent], stats: Stats) -> None:
        self.client = client
        self.settings = settings
        self.engine = engine
        self.agent = agent
        self.stats = stats

    # ------------------------------------------------------------------
    async def run(self, job: Job) -> None:
        """Entry point used by queue workers. Never raises."""
        workdir = self.settings.temp_dir / job.id
        reporter = StatusReporter(job.status_msg, buttons=self._cancel_buttons(job))
        started = time.monotonic()
        media_duration = 0.0
        try:
            timeout = self.settings.job_timeout_minutes * 60
            media_duration = await asyncio.wait_for(
                self._process(job, workdir, reporter), timeout=timeout)
            job.status = JobStatus.DONE
        except JobCancelled:
            job.status = JobStatus.CANCELLED
            await reporter.finalize(M.CANCELLED)
        except asyncio.TimeoutError:
            job.status = JobStatus.TIMEOUT
            await reporter.finalize(M.TIMEOUT)
        except UserFacingError as exc:
            job.status = JobStatus.FAILED
            log.error("job failed", extra={"job_id": job.id,
                                           "detail": exc.detail or str(exc)})
            await reporter.finalize("❌ " + exc.user_message)
        except Exception:
            job.status = JobStatus.FAILED
            log.exception("unexpected pipeline error", extra={"job_id": job.id})
            await reporter.finalize(M.ERR_GENERIC.format(code=job.id))
        finally:
            elapsed = time.monotonic() - started
            if self.settings.delete_after_send or job.status != JobStatus.DONE:
                shutil.rmtree(workdir, ignore_errors=True)
            self.stats.record(job.status, media_seconds=media_duration,
                              processing_seconds=elapsed)
            log.info("job finished", extra={
                "job_id": job.id,
                "status": job.status,
                "media_seconds": round(media_duration, 1),
                "elapsed_seconds": round(elapsed, 1),
                "timings": {k: round(v, 1) for k, v in job.timings.items()},
            })

    # ------------------------------------------------------------------
    def _cancel_buttons(self, job: Job):
        try:
            from telethon import Button
            return [[Button.inline(M.CANCEL_BUTTON, data="cancel:%s" % job.id)]]
        except Exception:  # pragma: no cover
            return None

    @staticmethod
    def _check_cancel(job: Job) -> None:
        if job.cancel_event.is_set():
            raise JobCancelled()

    # ------------------------------------------------------------------
    async def _process(self, job: Job, workdir: Path,
                       reporter: StatusReporter) -> float:
        settings = self.settings
        workdir.mkdir(parents=True, exist_ok=True)

        # -- 1) download -----------------------------------------------------
        self._check_cancel(job)
        job.stage = "دریافت فایل"
        await reporter.update(M.RECEIVING.format(bar=M.progress_bar(0)), force=True)
        t0 = time.monotonic()
        ext = extension_of(job.file_name) or "bin"
        src = workdir / ("input.%s" % ext)

        async def dl_progress(current: int, total: int) -> None:
            self._check_cancel(job)
            if total:
                await reporter.update(
                    M.RECEIVING.format(bar=M.progress_bar(current / total)))

        try:
            result = await job.media_msg.download_media(
                file=str(src), progress_callback=dl_progress)
        except JobCancelled:
            raise
        except Exception as exc:
            raise PipelineError(M.ERR_DOWNLOAD, detail="download failed: %s" % exc)
        if not result or not src.exists() or src.stat().st_size == 0:
            raise PipelineError(M.ERR_DOWNLOAD, detail="empty download result")
        job.mark_stage("download", time.monotonic() - t0)

        # -- 2) validate & probe ----------------------------------------------
        self._check_cancel(job)
        job.stage = "بررسی فایل"
        await reporter.update(M.VALIDATING, force=True)
        validate_incoming(job.file_name, job.mime_type, src.stat().st_size,
                          settings.max_upload_size)
        info = await ffmpeg.probe(src)
        if not info.has_audio:
            raise PipelineError(M.ERR_NO_AUDIO, detail="no audio stream")
        media_duration = info.duration

        # -- 3) extract audio --------------------------------------------------
        self._check_cancel(job)
        job.stage = "استخراج صدا"
        await reporter.update(M.EXTRACTING, force=True)
        t0 = time.monotonic()
        wav = workdir / "audio.wav"
        await ffmpeg.extract_audio(src, wav, normalize=settings.audio_normalize)
        # Free the original media early — it can be gigabytes.
        try:
            src.unlink()
        except OSError:
            pass
        job.mark_stage("audio", time.monotonic() - t0)

        # -- 4) transcribe ------------------------------------------------------
        self._check_cancel(job)
        job.stage = "تشخیص گفتار"
        if not self.engine.is_loaded:
            await reporter.update(
                M.LOADING_MODEL.format(model=self.engine.model_name), force=True)
        t0 = time.monotonic()

        def asr_progress(fraction: float) -> None:
            asyncio.get_running_loop().create_task(reporter.update(
                M.TRANSCRIBING.format(bar=M.progress_bar(fraction))))

        transcript = await self.engine.transcribe(
            wav, cancel_event=job.cancel_event, progress=asr_progress)
        job.mark_stage("asr", time.monotonic() - t0)
        if media_duration <= 0:
            media_duration = transcript.duration
        try:
            wav.unlink()
        except OSError:
            pass

        lang_fa = M.language_name(transcript.language)
        log.info("transcription complete", extra={
            "job_id": job.id, "language": transcript.language,
            "segments": len(transcript.segments),
            "duration": round(transcript.duration, 1),
        })
        if transcript.is_empty:
            await reporter.finalize(M.NO_SPEECH)
            return media_duration

        # -- 5) build cues -------------------------------------------------------
        self._check_cancel(job)
        job.stage = "ساخت زیرنویس"
        await reporter.update(
            M.BUILDING + "\n" + M.LANG_LINE.format(lang=lang_fa), force=True)
        cues = build_cues(transcript.segments, BuilderOptions(
            max_chars=settings.max_chars_per_line * settings.max_lines,
            max_duration=settings.max_cue_duration,
        ))
        if not cues:
            await reporter.finalize(M.NO_SPEECH)
            return media_duration

        # -- 6) translate ----------------------------------------------------------
        needs_translation = (
            settings.translate_enabled
            and self.agent is not None
            and self.agent.configured
            and transcript.language != settings.target_language
        )
        translation_ok = False
        if needs_translation:
            self._check_cancel(job)
            job.stage = "ترجمه به فارسی"
            await reporter.update(
                M.TRANSLATING.format(bar=M.progress_bar(0)), force=True)
            t0 = time.monotonic()

            def tr_progress(fraction: float) -> None:
                asyncio.get_running_loop().create_task(reporter.update(
                    M.TRANSLATING.format(bar=M.progress_bar(fraction))))

            try:
                failed_lines = await self.agent.translate_cues(
                    cues, transcript.language, progress=tr_progress)
                self._check_cancel(job)
                job.stage = "کنترل کیفیت"
                await reporter.update(M.QUALITY_CHECK, force=True)
                await quality_pass(cues, self.agent, transcript.language)
                translation_ok = True
                if failed_lines:
                    log.warning("some lines kept original text", extra={
                        "job_id": job.id, "failed_lines": failed_lines})
            except TranslationError as exc:
                log.error("translation failed entirely", extra={
                    "job_id": job.id, "error": str(exc)})
                translation_ok = False
            job.mark_stage("translate", time.monotonic() - t0)

        # -- 7) write files -----------------------------------------------------------
        self._check_cancel(job)
        base = sanitize_stem(job.file_name)
        render = RenderOptions(
            max_chars_per_line=settings.max_chars_per_line,
            max_lines=settings.max_lines,
            rtl_marks=settings.rtl_marks,
        )
        files = write_outputs(cues, workdir, base, settings.subtitle_formats,
                              transcript.language, translated=translation_ok,
                              opts=render)

        # -- 8) send ---------------------------------------------------------------------
        self._check_cancel(job)
        job.stage = "ارسال فایل"
        await reporter.update(M.UPLOADING, force=True)
        for path in files:
            caption = self._caption_for(path, lang_fa)
            await self.client.send_file(
                job.chat_id, str(path), caption=caption,
                force_document=True, reply_to=job.media_msg.id)

        await reporter.update(M.CLEANING, force=True)
        summary = M.DONE_SUMMARY.format(
            media_duration=M.fmt_seconds(media_duration),
            lang=lang_fa,
            lines=len(cues),
            total_time=M.fmt_seconds(time.monotonic() - (job.started_at or 0)),
            t_download=M.fmt_seconds(job.timings.get("download", 0)),
            t_audio=M.fmt_seconds(job.timings.get("audio", 0)),
            t_asr=M.fmt_seconds(job.timings.get("asr", 0)),
            t_translate=M.fmt_seconds(job.timings.get("translate", 0)),
        )
        if needs_translation and not translation_ok:
            summary += "\n\n" + M.ERR_TRANSLATION_PARTIAL
        await reporter.finalize(summary)
        return media_duration

    @staticmethod
    def _caption_for(path: Path, lang_fa: str) -> str:
        name = path.name
        if ".fa." in name:
            fmt = name.rsplit(".", 1)[-1].upper()
            if fmt == "SRT":
                return M.CAPTION_PERSIAN
            return M.CAPTION_PERSIAN_FMT.format(fmt=fmt)
        return M.CAPTION_ORIGINAL.format(lang=lang_fa)
