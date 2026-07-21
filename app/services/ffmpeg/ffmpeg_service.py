"""FFmpeg service — media probing + audio extraction."""
from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from ...utils.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    codec: str
    width: int
    height: int
    has_audio: bool
    bitrate: int
    container: str


@dataclass(frozen=True)
class AudioExtractionResult:
    audio_path: Path
    duration: float


class FFmpegService:
    """Async wrapper around the ffmpeg / ffprobe binaries."""

    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe") -> None:
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path

    def available(self) -> bool:
        return shutil.which(self.ffmpeg) is not None and shutil.which(self.ffprobe) is not None

    async def _run(self, args: list[str], timeout: int = 600) -> tuple[int, str, str]:
        cmd = [self.ffmpeg, *args]
        log.info("ffmpeg.run", extra={"cmd": cmd})
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return proc.returncode, stdout.decode(errors="ignore"), stderr.decode(errors="ignore")

    async def probe(self, path: Path) -> MediaInfo:
        cmd = [
            self.ffprobe, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {stderr.decode(errors='ignore')}")
        data = json.loads(stdout.decode())
        streams = data.get("streams", []) or []
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
        duration = float(data.get("format", {}).get("duration", 0) or 0)
        if duration <= 0:
            duration = float(video_stream.get("duration", 0) or 0)
        return MediaInfo(
            duration=duration,
            codec=video_stream.get("codec_name", "unknown"),
            width=int(video_stream.get("width", 0) or 0),
            height=int(video_stream.get("height", 0) or 0),
            has_audio=audio_stream is not None,
            bitrate=int(data.get("format", {}).get("bit_rate", 0) or 0),
            container=data.get("format", {}).get("format_name", ""),
        )

    async def extract_audio(
        self,
        video_path: Path,
        output_path: Path,
        *,
        sample_rate: int = 16000,
        apply_denoise: bool = False,
        volume_norm: bool = True,
        timeout: int = 1800,
    ) -> AudioExtractionResult:
        """Extract normalized 16 kHz mono PCM WAV from video."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        filters = []
        if apply_denoise:
            filters.append("afftdn=nt=w")
        if volume_norm:
            filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        filters.append(f"aresample={sample_rate}")
        filter_chain = ",".join(filters)
        args = [
            "-y", "-i", str(video_path),
            "-vn",
            "-ac", "1",
            "-ar", str(sample_rate),
            "-acodec", "pcm_s16le",
            "-af", filter_chain,
            str(output_path),
        ]
        rc, _out, err = await self._run(args, timeout=timeout)
        if rc != 0 or not output_path.exists():
            raise RuntimeError(f"ffmpeg extract failed: {err[-2000:]}")
        info = await self.probe(output_path)
        return AudioExtractionResult(audio_path=output_path, duration=info.duration)
