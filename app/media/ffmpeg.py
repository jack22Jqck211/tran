"""FFmpeg / ffprobe wrappers: media inspection and audio extraction."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from ..core.errors import PipelineError
from ..bot import messages as M

log = logging.getLogger("app.ffmpeg")

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")


@dataclass
class MediaInfo:
    duration: float
    has_audio: bool
    has_video: bool
    format_name: str
    audio_codec: Optional[str] = None
    video_codec: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


async def _run(cmd: List[str], timeout: float = 600.0) -> Tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise PipelineError(M.ERR_FFMPEG, detail="ffmpeg timed out: %s" % cmd[:3])
    return proc.returncode or 0, stdout, stderr


async def probe(path: Path) -> MediaInfo:
    """Inspect a media file; raises PipelineError for corrupt/unreadable files."""
    cmd = [
        FFPROBE_BIN, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    rc, stdout, stderr = await _run(cmd, timeout=120.0)
    if rc != 0:
        raise PipelineError(M.ERR_CORRUPT_FILE,
                            detail="ffprobe rc=%d: %s" % (rc, stderr[-400:]))
    try:
        data = json.loads(stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        raise PipelineError(M.ERR_CORRUPT_FILE, detail="ffprobe returned non-JSON")

    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    duration = 0.0
    try:
        duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    info = MediaInfo(duration=duration, has_audio=False, has_video=False,
                     format_name=str(fmt.get("format_name") or ""))
    for stream in streams:
        ctype = stream.get("codec_type")
        if ctype == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = stream.get("codec_name")
            if duration <= 0:
                try:
                    info.duration = float(stream.get("duration") or 0.0)
                except (TypeError, ValueError):
                    pass
        elif ctype == "video" and not info.has_video:
            info.has_video = True
            info.video_codec = stream.get("codec_name")
            info.width = stream.get("width")
            info.height = stream.get("height")
    return info


async def extract_audio(src: Path, dst: Path, normalize: bool = True,
                        timeout_minutes: int = 60) -> None:
    """Extract mono 16 kHz PCM WAV — the ideal input for Whisper models."""
    args: List[str] = [
        FFMPEG_BIN, "-hide_banner", "-nostdin", "-y",
        "-i", str(src),
        "-vn", "-sn", "-dn",
        "-map", "0:a:0?",
        "-ac", "1", "-ar", "16000",
    ]
    if normalize:
        # Single-pass EBU R128 loudness normalization improves ASR on quiet mixes.
        args += ["-af", "loudnorm=I=-16:LRA=11:TP=-1.5"]
    args += ["-c:a", "pcm_s16le", "-f", "wav", str(dst)]

    rc, _stdout, stderr = await _run(args, timeout=timeout_minutes * 60.0)
    if rc != 0 or not dst.exists() or dst.stat().st_size < 1024:
        tail = stderr.decode("utf-8", "replace")[-500:]
        log.error("ffmpeg extraction failed", extra={"rc": rc, "stderr_tail": tail})
        raise PipelineError(M.ERR_AUDIO_EXTRACT, detail="rc=%d %s" % (rc, tail))
