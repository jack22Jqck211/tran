"""File utilities — temp dirs, cleanup, safe join."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path


def new_job_id() -> str:
    return uuid.uuid4().hex


def cleanup_path(path: Path) -> None:
    try:
        if path is None:
            return
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink(missing_ok=True)
    except Exception:
        pass


def file_size_str(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def safe_join(base: Path, *parts: str) -> Path:
    base_resolved = base.resolve()
    full = base
    for part in parts:
        full = full / part
    full = full.resolve()
    if not str(full).startswith(str(base_resolved)):
        raise ValueError(f"Path traversal detected for {parts!r}")
    return full
