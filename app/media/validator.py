"""Incoming file validation: type, size and safe naming."""
from __future__ import annotations

import re
from typing import Optional

from ..core.errors import ValidationError
from ..bot import messages as M

VIDEO_EXTENSIONS = {
    "mp4", "mkv", "avi", "mov", "webm", "flv", "mpeg", "mpg", "m4v", "ts",
    "wmv", "3gp", "3g2", "ogv", "mxf", "vob", "asf", "m2ts", "mts", "divx",
    "f4v", "rm", "rmvb", "ogm", "dv", "y4m",
}
AUDIO_EXTENSIONS = {
    "mp3", "wav", "m4a", "aac", "flac", "ogg", "oga", "opus", "wma", "amr",
    "aiff", "ac3", "dts", "mka",
}
ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

_SAFE_STEM = re.compile(r"[^\w\-. ]+", re.UNICODE)


def extension_of(filename: Optional[str]) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower().strip()


def sanitize_stem(filename: Optional[str], fallback: str = "subtitle") -> str:
    """Produce a safe file stem for output subtitle files (no path tricks)."""
    if not filename:
        return fallback
    stem = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    stem = _SAFE_STEM.sub("_", stem).strip(" ._")
    return stem[:60] or fallback


def validate_incoming(filename: Optional[str], mime: Optional[str],
                      size: Optional[int], max_size: int) -> None:
    """Raise ValidationError (Persian message) when the file is unacceptable."""
    if not size or size <= 0:
        raise ValidationError(M.ERR_EMPTY_FILE, detail="empty or unknown size")
    if size > max_size:
        raise ValidationError(
            M.ERR_TOO_BIG.format(limit="%.1f GB" % (max_size / (1024 ** 3))),
            detail="size %d > %d" % (size, max_size),
        )

    ext = extension_of(filename)
    mime = (mime or "").lower()
    mime_ok = mime.startswith("video/") or mime.startswith("audio/")
    ext_ok = ext in ALLOWED_EXTENSIONS
    if not (mime_ok or ext_ok):
        raise ValidationError(M.ERR_UNSUPPORTED_TYPE,
                              detail="mime=%r ext=%r" % (mime, ext))
