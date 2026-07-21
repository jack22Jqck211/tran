"""Application entry point.

Stage 3 wires:
- HTTP health server (background)
- Telegram Application with /start, /health, /help, and a video handler
- FFmpeg service for media probing + audio extraction

Heavy components (Whisper, translation, subtitle writers) are stubbed to keep
stage 3 minimal — they land in stages 4-6.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .handlers.ui import (
    PROGRESS_BUILD,
    PROGRESS_CLEANUP,
    PROGRESS_DONE,
    PROGRESS_EXTRACT,
    PROGRESS_RECEIVE,
    PROGRESS_TRANSCRIBE,
    PROGRESS_TRANSLATE,
    PROGRESS_VALIDATE,
    WELCOME,
)
from .services.ffmpeg.ffmpeg_service import FFmpegService, MediaInfo
from .utils.files import cleanup_path, file_size_str, new_job_id
from .utils.logging import get_logger, setup_logging
from .utils.validators import SUPPORTED_VIDEO_EXTS, validate_video_file


# ---------------------------------------------------------------------------
# Configuration (very small, env-driven)
# ---------------------------------------------------------------------------
MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE") or 2147483648)
TEMP_DIRECTORY = os.getenv("TEMP_DIRECTORY") or "/tmp/jobs"
BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
HEALTH_PORT = int(os.getenv("PORT") or os.getenv("HEALTH_PORT") or "8080")


# ---------------------------------------------------------------------------
# Health server (background thread, Railway-compatible)
# ---------------------------------------------------------------------------
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/", "/health", "/healthz"):
            body = json.dumps({
                "status": "ok",
                "stage": 3,
                "service": "telegram-subtitle-bot",
                "bot_wired": bool(BOT_TOKEN),
                "ffmpeg_available": _FFMPEG.available(),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        return


def start_health_server() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", HEALTH_PORT), _HealthHandler)
    print(f"[stage3] health server on :{HEALTH_PORT}", flush=True)
    server.serve_forever()


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------
_FFMPEG = FFmpegService()


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(WELCOME)


async def cmd_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"✅ ربات زنده است.\n"
        f"FFmpeg: {'✅' if _FFMPEG.available() else '❌'}\n"
        f"مرحله: ۳ (استخراج صدا)"
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "📊 وضعیت:\n"
        f"FFmpeg: {'✅ نصب است' if _FFMPEG.available() else '❌ نیست'}\n"
        f"حداکثر حجم فایل: {file_size_str(MAX_UPLOAD_SIZE)}\n"
        f"فهرست موقت: {TEMP_DIRECTORY}"
    )


async def on_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg:
        return

    # Resolve video
    tg_file, file_name, file_size, mime = await _resolve_video(update)
    if tg_file is None:
        await msg.reply_text("❌ فایل ویدیویی معتبر نیست.")
        return

    # Validate
    validation = validate_video_file(file_name, mime, file_size, MAX_UPLOAD_SIZE)
    if not validation.ok:
        await msg.reply_text(f"❌ {validation.reason}")
        return

    job_id = new_job_id()
    workdir = Path(TEMP_DIRECTORY) / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    video_path = workdir / file_name

    status_msg = await msg.reply_text(PROGRESS_RECEIVE)
    try:
        # Download
        await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        tg_file_obj = await tg_file.get_file()
        await tg_file_obj.download_to_drive(custom_path=str(video_path))

        if not video_path.exists() or video_path.stat().st_size == 0:
            await status_msg.edit_text("❌ دانلود فایل ناموفق بود.")
            return

        # Validate media + extract info
        await status_msg.edit_text(PROGRESS_VALIDATE)
        info = await _FFMPEG.probe(video_path)

        # Extract audio
        await status_msg.edit_text(PROGRESS_EXTRACT)
        audio_path = workdir / "audio.wav"
        audio_result = await _FFMPEG.extract_audio(video_path, audio_path)

        # Stage 3 stops here — tell the user we got the audio ready for stage 4
        await status_msg.edit_text(PROGRESS_TRANSCRIBE)
        await asyncio.sleep(0.5)
        await status_msg.edit_text(PROGRESS_TRANSLATE)
        await asyncio.sleep(0.5)
        await status_msg.edit_text(PROGRESS_BUILD)
        await asyncio.sleep(0.5)
        await status_msg.edit_text(PROGRESS_CLEANUP)

        # Report
        await ctx.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "✅ مرحله ۳ کامل شد!\n\n"
                f"📁 فایل: {file_name}\n"
                f"📦 حجم: {file_size_str(file_size)}\n"
                f"🎬 codec: {info.codec}\n"
                f"📐 ابعاد: {info.width}×{info.height}\n"
                f"⏱ مدت: {info.duration:.1f} ثانیه\n"
                f"🔊 صدا استخراج شد: {file_size_str(audio_path.stat().st_size)}\n"
                f"🆔 job_id: {job_id[:8]}\n\n"
                "⚠️ در مرحله بعد، تشخیص گفتار و ترجمه اضافه خواهد شد."
            ),
        )
        await status_msg.edit_text(PROGRESS_DONE)
    except Exception as e:
        get_logger(__name__).exception("stage3.job_failed")
        await status_msg.edit_text(f"❌ خطا: {e}")
    finally:
        # Cleanup
        cleanup_path(workdir)
        cleanup_path(video_path)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _resolve_video(update: Update, *_args: Any):
    msg = update.effective_message
    if msg.video:
        return msg.video, msg.video.file_name or "video.mp4", msg.video.file_size, "video/mp4"
    if msg.document:
        doc = msg.document
        return doc, doc.file_name or "video.bin", doc.file_size or 0, doc.mime_type or "application/octet-stream"
    return None, "", 0, ""


# ---------------------------------------------------------------------------
# Build & run
# ---------------------------------------------------------------------------
def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN env var is not set")
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .read_timeout(120)
        .write_timeout(120)
        .connect_timeout(60)
        .pool_timeout(60)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO | filters.Document.ALL, on_video))
    return app


def main() -> None:
    setup_logging("INFO", "plain")
    threading.Thread(target=start_health_server, daemon=True).start()
    print("[stage3] building Telegram application...", flush=True)
    application = build_application()
    print("[stage3] starting polling...", flush=True)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
