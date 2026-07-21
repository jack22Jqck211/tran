"""Telethon MTProto client factory.

Using MTProto (instead of the HTTP Bot API) lets the bot download media up to
2 GB directly from Telegram's DCs — no Local Bot API server required. The same
code also covers the Local-Bot-API use case's goal (large files) with fewer
moving parts. cryptg accelerates AES for multi-MB/s downloads.
"""
from __future__ import annotations

from telethon import TelegramClient

from ..config import Settings


def create_client(settings: Settings) -> TelegramClient:
    session_file = settings.session_dir / "bot"
    client = TelegramClient(
        str(session_file),
        settings.api_id,
        settings.api_hash,
        # Higher throughput for large media downloads.
        receive_updates=True,
        auto_reconnect=True,
        request_retries=6,
        connection_retries=None,  # retry forever (worker survives network blips)
        flood_sleep_threshold=60,
    )
    client.parse_mode = "html"
    return client
