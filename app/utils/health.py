"""Tiny dependency-free HTTP health endpoint for platform health checks."""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("app.health")

_BODY = b"ok"
_RESPONSE = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"Content-Length: " + str(len(_BODY)).encode() + b"\r\n"
    b"Connection: close\r\n\r\n" + _BODY
)


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await asyncio.wait_for(reader.read(2048), timeout=5)
        writer.write(_RESPONSE)
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def start_health_server(port: int) -> "asyncio.AbstractServer":
    server = await asyncio.start_server(_handle, host="0.0.0.0", port=port)
    log.info("Health endpoint listening", extra={"port": port})
    return server
