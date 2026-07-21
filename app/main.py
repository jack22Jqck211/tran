"""Application entrypoint: wire config, client, queue, pipeline and handlers."""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

import httpx

from .bot.client import create_client
from .bot.handlers import register_handlers
from .config import ConfigError, Settings
from .core.pipeline import Pipeline
from .core.queue import JobQueue
from .core.stats import Stats
from .speech.engine import SpeechEngine
from .translation.agent import TranslationAgent
from .utils.health import start_health_server
from .utils.logging import setup_logging

log = logging.getLogger("app.main")


async def run() -> None:
    settings = Settings.load()
    setup_logging(settings.log_level)
    settings.temp_dir.mkdir(parents=True, exist_ok=True)

    log.info("starting telegram-subtitle-bot", extra={
        "whisper_model": settings.whisper_model,
        "translate": settings.translate_enabled,
        "providers": [p.name for p in settings.providers],
        "formats": settings.subtitle_formats,
        "max_concurrent_jobs": settings.max_concurrent_jobs,
    })

    if settings.port:
        try:
            await start_health_server(settings.port)
        except OSError as exc:
            log.warning("health server failed to bind: %s", exc)

    http = httpx.AsyncClient(
        timeout=httpx.Timeout(180.0, connect=20.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    engine = SpeechEngine(
        model_name=settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute,
        beam_size=settings.whisper_beam_size,
        vad_filter=settings.vad_filter,
    )
    agent = TranslationAgent(
        settings.providers, http,
        workers=settings.translation_workers,
        chunk_size=settings.translation_chunk_size,
        target_language=settings.target_language,
    ) if settings.providers else None

    stats = Stats()
    queue = JobQueue(queue_size=settings.queue_size,
                     user_cooldown=settings.user_cooldown_seconds)

    client = create_client(settings)
    await client.start(bot_token=settings.bot_token)
    me = await client.get_me()
    log.info("bot authorized", extra={"username": getattr(me, "username", None),
                                      "id": getattr(me, "id", None)})

    pipeline = Pipeline(client, settings, engine, agent, stats)
    register_handlers(client, settings, queue, stats, engine, agent)
    await queue.start(settings.max_concurrent_jobs, pipeline.run)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                sig, lambda: asyncio.create_task(client.disconnect()))
        except NotImplementedError:  # pragma: no cover (Windows)
            pass

    log.info("bot is ready and polling for updates")
    try:
        await client.run_until_disconnected()
    finally:
        await http.aclose()
        log.info("bot stopped")


def main() -> None:
    try:
        asyncio.run(run())
    except ConfigError as exc:
        print("CONFIG ERROR: %s" % exc, file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
