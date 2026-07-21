"""File validation — MIME, extension, size."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Set


SUPPORTED_VIDEO_EXTS: Set[str] = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".mpeg", ".mpg",
    ".m4v", ".ts", ".wmv", ".3gp", ".ogv", ".mxf", ".vob", ".asf",
}

ALLOWED_MIME_PREFIXES: Set[str] = {
    "video/", "application/octet-stream", "application/x-matroska",
}


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str
    ext: str = ""
    mime: str = ""


def validate_video_file(filename: str, mime: str, size: int, max_size: int) -> ValidationResult:
    if size <= 0:
        return ValidationResult(False, "فایل خالی است.", "", mime)
    if size > max_size:
        return ValidationResult(False, f"حجم فایل بیشتر از حد مجاز ({max_size // (1024*1024)} مگابایت) است.", "", mime)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_VIDEO_EXTS:
        return ValidationResult(False, f"فرمت {ext or 'نامشخص'} پشتیبانی نمی‌شود.", ext, mime)
    if mime and not any(mime.startswith(p) for p in ALLOWED_MIME_PREFIXES):
        return ValidationResult(False, f"نوع فایل غیرمجاز: {mime}", ext, mime)
    return ValidationResult(True, "ok", ext, mime)
