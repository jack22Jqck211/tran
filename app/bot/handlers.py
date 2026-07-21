"""Telegram event handlers — fully Persian UX, English internals."""
from __future__ import annotations

import logging
import time
from typing import Optional

from telethon import Button, TelegramClient, events

from ..config import KNOWN_WHISPER_MODELS, Settings
from ..core.errors import ValidationError
from ..core.job import Job
from ..core.queue import (JobQueue, QueueFullError, RateLimitedError,
                          UserBusyError)
from ..core.stats import Stats
from ..media.validator import validate_incoming
from ..speech.engine import SpeechEngine
from ..translation.agent import TranslationAgent
from . import messages as M

log = logging.getLogger("app.handlers")

KNOWN_COMMANDS = ("/start", "/help", "/status", "/cancel", "/stats", "/model")


def _display_name(msg) -> str:
    try:
        if msg.file and msg.file.name:
            return msg.file.name
    except Exception:
        pass
    return "video"


def register_handlers(client: TelegramClient, settings: Settings,
                      queue: JobQueue, stats: Stats, engine: SpeechEngine,
                      agent: Optional[TranslationAgent]) -> None:

    def is_admin(user_id: int) -> bool:
        return user_id in settings.admin_ids

    def is_allowed(user_id: int) -> bool:
        if not settings.allowed_ids:
            return True
        return user_id in settings.allowed_ids or is_admin(user_id)

    # -- commands -------------------------------------------------------------
    @client.on(events.NewMessage(pattern=r"^/start", incoming=True,
                                 func=lambda e: e.is_private))
    async def cmd_start(event) -> None:
        if not is_allowed(event.sender_id):
            await event.respond(M.NOT_ALLOWED)
            return
        await event.respond(
            M.WELCOME.format(max_size=settings.max_upload_human))

    @client.on(events.NewMessage(pattern=r"^/help", incoming=True,
                                 func=lambda e: e.is_private))
    async def cmd_help(event) -> None:
        await event.respond(M.HELP)

    @client.on(events.NewMessage(pattern=r"^/status", incoming=True,
                                 func=lambda e: e.is_private))
    async def cmd_status(event) -> None:
        job = queue.job_for_user(event.sender_id)
        if job is None:
            await event.respond(M.STATUS_IDLE)
        elif job.status == "queued":
            await event.respond(M.STATUS_QUEUED.format(
                position=max(1, queue.queued_count)))
        else:
            await event.respond(M.STATUS_ACTIVE.format(
                name=job.file_name, stage=job.stage,
                elapsed=M.fmt_seconds(job.elapsed)))

    @client.on(events.NewMessage(pattern=r"^/cancel", incoming=True,
                                 func=lambda e: e.is_private))
    async def cmd_cancel(event) -> None:
        if queue.cancel_user(event.sender_id):
            await event.respond(M.CANCEL_REQUESTED)
        else:
            await event.respond(M.NOTHING_TO_CANCEL)

    @client.on(events.NewMessage(pattern=r"^/stats", incoming=True,
                                 func=lambda e: e.is_private))
    async def cmd_stats(event) -> None:
        if not is_admin(event.sender_id):
            await event.respond(M.ADMIN_ONLY)
            return
        await event.respond(M.STATS_TEMPLATE.format(
            started=time.strftime("%Y-%m-%d %H:%M",
                                  time.localtime(stats.started_at)),
            total=stats.total, ok=stats.ok, failed=stats.failed,
            cancelled=stats.cancelled,
            media_time=M.fmt_seconds(stats.media_seconds),
            proc_time=M.fmt_seconds(stats.processing_seconds),
            model=engine.model_name,
            device=engine.resolved_device,
            queued=queue.queued_count, active=queue.active_count))

    @client.on(events.NewMessage(pattern=r"^/model(\s+\S+)?", incoming=True,
                                 func=lambda e: e.is_private))
    async def cmd_model(event) -> None:
        if not is_admin(event.sender_id):
            await event.respond(M.ADMIN_ONLY)
            return
        arg = (event.pattern_match.group(1) or "").strip()
        if not arg:
            await event.respond(M.MODEL_USAGE.format(current=engine.model_name))
            return
        if arg not in KNOWN_WHISPER_MODELS and "/" not in arg:
            await event.respond(M.MODEL_INVALID.format(
                models=", ".join(sorted(KNOWN_WHISPER_MODELS))))
            return
        engine.set_model(arg)
        await event.respond(M.MODEL_CHANGED.format(model=arg))

    # -- media intake -----------------------------------------------------------
    def _has_media(event) -> bool:
        if not event.is_private:
            return False
        msg = event.message
        return bool(msg.video or msg.audio or msg.voice or msg.video_note
                    or msg.document)

    @client.on(events.NewMessage(incoming=True, func=_has_media))
    async def on_media(event) -> None:
        user_id = event.sender_id
        if not is_allowed(user_id):
            await event.reply(M.NOT_ALLOWED)
            return

        msg = event.message
        file_name = _display_name(msg)
        file_size = int(getattr(msg.file, "size", 0) or 0)
        mime_type = str(getattr(msg.file, "mime_type", "") or "")

        try:
            validate_incoming(file_name, mime_type, file_size,
                              settings.max_upload_size)
        except ValidationError as exc:
            await event.reply(exc.user_message)
            return

        status_msg = await event.reply(M.QUEUED.format(position="…"))
        job = Job(user_id=user_id, chat_id=event.chat_id, media_msg=msg,
                  status_msg=status_msg, file_name=file_name,
                  file_size=file_size, mime_type=mime_type)
        try:
            position = queue.submit(job)
        except UserBusyError:
            await status_msg.edit(M.USER_BUSY)
            return
        except RateLimitedError as exc:
            await status_msg.edit(M.RATE_LIMITED.format(
                seconds=int(exc.wait_seconds) + 1))
            return
        except QueueFullError:
            await status_msg.edit(M.QUEUE_FULL)
            return
        try:
            await status_msg.edit(
                M.QUEUED.format(position=position),
                buttons=[[Button.inline(M.CANCEL_BUTTON,
                                        data="cancel:%s" % job.id)]])
        except Exception:
            pass

    # -- inline cancel button ------------------------------------------------------
    @client.on(events.CallbackQuery(pattern=rb"^cancel:"))
    async def on_cancel_button(event) -> None:
        job_id = event.data.decode("utf-8", "ignore").split(":", 1)[-1]
        outcome = queue.cancel_job(job_id, event.sender_id,
                                   is_admin(event.sender_id))
        if outcome is None:
            await event.answer("این عملیات دیگه فعال نیست.")
        elif outcome is False:
            await event.answer(M.CANCEL_DENIED, alert=True)
        else:
            await event.answer("🛑 لغو شد")

    # -- fallback text ----------------------------------------------------------------
    @client.on(events.NewMessage(incoming=True,
                                 func=lambda e: e.is_private and not _has_media(e)))
    async def on_text(event) -> None:
        text = (event.message.message or "").strip()
        if not text:
            return
        if text.startswith("/"):
            if not any(text.startswith(cmd) for cmd in KNOWN_COMMANDS):
                await event.respond(M.UNKNOWN_COMMAND)
            return
        await event.respond(M.HINT_SEND_MEDIA)
