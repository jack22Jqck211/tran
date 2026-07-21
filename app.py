"""Stage 2 — Telegram bot base + health server.

Adds a minimal Telegram bot that responds to /start with a Persian welcome.
The health server (from stage 1) keeps running so Railway healthcheck stays green.

Stage 2 verifies:
- BOT_TOKEN env var is wired correctly
- python-telegram-bot installs cleanly on Railway
- Long-polling bot starts without crashing
- Healthcheck at /health still passes
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)


# ---------------------------------------------------------------------------
# Health server (kept from stage 1, runs in background thread)
# ---------------------------------------------------------------------------
class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/", "/health", "/healthz"):
            body = json.dumps({
                "status": "ok",
                "stage": 2,
                "service": "telegram-subtitle-bot",
                "bot_wired": bool(os.getenv("BOT_TOKEN")),
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
    port = int(os.getenv("PORT") or os.getenv("HEALTH_PORT") or "8080")
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    print(f"[stage2] health server on :{port}", flush=True)
    server.serve_forever()


# ---------------------------------------------------------------------------
# Telegram bot
# ---------------------------------------------------------------------------
WELCOME_FA = (
    "👋 به ربات زیرنویس فارسی خوش آمدید!\n\n"
    "📥 این مرحله فقط تست راه‌اندازی است.\n"
    "به‌زودی قابلیت دریافت ویدیو و تولید زیرنویس اضافه خواهد شد."
)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(WELCOME_FA)


async def cmd_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text("✅ ربات زنده است.")


def build_application() -> Application:
    token = os.getenv("BOT_TOKEN", "")
    if not token:
        raise RuntimeError("BOT_TOKEN env var is not set")
    app = (
        ApplicationBuilder()
        .token(token)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(60)
        .pool_timeout(60)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("health", cmd_health))
    return app


def main() -> None:
    # 1) Start health server in a daemon thread
    threading.Thread(target=start_health_server, daemon=True).start()

    # 2) Build & start Telegram bot in foreground
    print("[stage2] building Telegram application...", flush=True)
    application = build_application()
    print("[stage2] starting polling...", flush=True)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
