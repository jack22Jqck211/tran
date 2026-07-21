"""Application configuration loaded from environment variables (12-factor style).

Every runtime option of the bot is controlled through environment variables so
the same image can be deployed to Railway, Docker Compose or bare metal
without code changes.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

log = logging.getLogger("app.config")

KNOWN_WHISPER_MODELS = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v1", "large-v2", "large-v3",
    "large-v3-turbo", "turbo",
    "distil-small.en", "distil-medium.en", "distil-large-v3",
}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _str(key: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(key)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _require(key: str) -> str:
    value = _str(key)
    if not value:
        raise ConfigError("Missing required environment variable: %s" % key)
    return value


def _bool(key: str, default: bool = False) -> bool:
    value = _str(key)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _int(key: str, default: int) -> int:
    value = _str(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        log.warning("Invalid integer for %s=%r, using default %s", key, value, default)
        return default


def _float(key: str, default: float) -> float:
    value = _str(key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        log.warning("Invalid float for %s=%r, using default %s", key, value, default)
        return default


def _ids(key: str) -> Set[int]:
    value = _str(key)
    if not value:
        return set()
    out: Set[int] = set()
    for part in value.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            log.warning("Ignoring non-numeric id %r in %s", part, key)
    return out


def _list(key: str, default: str) -> List[str]:
    value = _str(key, default) or ""
    return [p.strip().lower() for p in value.split(",") if p.strip()]


# ---------------------------------------------------------------------------
# Translation providers
# ---------------------------------------------------------------------------

@dataclass
class ProviderConfig:
    """One OpenAI-compatible translation API provider.

    Adding a new provider never requires code changes: either extend
    ``PROVIDERS_JSON`` or point ``AI_BASE_URL`` at any OpenAI-compatible API
    (OpenAI, Gemini-compatible gateways, OpenRouter, Azure, Ollama, vLLM,
    LM Studio, LibreTranslate proxies, custom REST gateways...).
    """

    name: str
    base_url: str
    api_key: str
    models: List[str]
    priority: int = 0
    max_tokens: int = 8192
    temperature: float = 0.3
    timeout: float = 150.0


def _parse_provider_entry(name: str, entry: dict) -> Optional[ProviderConfig]:
    if not isinstance(entry, dict):
        return None
    base_url = entry.get("base_url") or entry.get("url")
    api_key = entry.get("api_key") or entry.get("key") or ""
    models = entry.get("models")
    if not models and entry.get("model"):
        models = [entry["model"]]
    if isinstance(models, str):
        models = [m.strip() for m in models.split(",") if m.strip()]
    if not base_url or not models:
        log.warning("Skipping provider %r: base_url and models are required", name)
        return None
    return ProviderConfig(
        name=str(entry.get("name") or name),
        base_url=str(base_url).rstrip("/"),
        api_key=str(api_key),
        models=[str(m) for m in models],
        priority=int(entry.get("priority", 0)),
        max_tokens=int(entry.get("max_tokens", 4096)),
        temperature=float(entry.get("temperature", 0.3)),
        timeout=float(entry.get("timeout", 150.0)),
    )


def load_providers() -> List[ProviderConfig]:
    providers: List[ProviderConfig] = []

    raw = _str("PROVIDERS_JSON")
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigError("PROVIDERS_JSON is not valid JSON: %s" % exc)
        if isinstance(data, dict) and "providers" in data:
            data = data["providers"]
        if isinstance(data, dict):
            for name, entry in data.items():
                parsed = _parse_provider_entry(name, entry)
                if parsed:
                    providers.append(parsed)
        elif isinstance(data, list):
            for i, entry in enumerate(data):
                parsed = _parse_provider_entry("provider_%d" % (i + 1), entry)
                if parsed:
                    providers.append(parsed)

    # Simple single-provider configuration (most common case).
    base_url = _str("AI_BASE_URL") or _str("OPENAI_BASE_URL")
    if base_url:
        parsed = _parse_provider_entry("primary", {
            "base_url": base_url,
            "api_key": _str("AI_API_KEY") or _str("OPENAI_API_KEY") or "",
            "models": _str("AI_MODELS") or _str("AI_MODEL") or "",
            "priority": -1,  # env-configured primary provider goes first
            "max_tokens": _int("AI_MAX_TOKENS", 8192),
            "temperature": _float("AI_TEMPERATURE", 0.3),
        })
        if parsed:
            providers.append(parsed)

    providers.sort(key=lambda p: p.priority)
    return providers


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    # Telegram / MTProto
    bot_token: str
    api_id: int
    api_hash: str
    admin_ids: Set[int]
    allowed_ids: Set[int]  # empty set = bot is open to everyone
    session_dir: Path

    # Files & storage
    temp_dir: Path
    max_upload_size: int
    delete_after_send: bool

    # Queue / concurrency
    max_concurrent_jobs: int
    queue_size: int
    user_cooldown_seconds: float
    job_timeout_minutes: int

    # Speech recognition
    whisper_model: str
    whisper_device: str      # auto | cpu | cuda
    whisper_compute: str     # auto | int8 | float16 | float32 | int8_float16
    whisper_beam_size: int
    vad_filter: bool
    audio_normalize: bool

    # Subtitles
    subtitle_formats: List[str]  # subset of srt, vtt, ass, txt
    max_chars_per_line: int
    max_lines: int
    max_cue_duration: float
    rtl_marks: bool

    # Translation
    translate_enabled: bool
    target_language: str
    providers: List[ProviderConfig] = field(default_factory=list)
    translation_workers: int = 4
    translation_chunk_size: int = 25

    # Misc
    log_level: str = "INFO"
    port: Optional[int] = None

    @property
    def max_upload_human(self) -> str:
        gib = self.max_upload_size / (1024 ** 3)
        if gib >= 1:
            return "%.1f GB" % gib
        return "%d MB" % (self.max_upload_size / (1024 ** 2))

    @classmethod
    def load(cls) -> "Settings":
        # Optional .env support for local development.
        try:
            from dotenv import load_dotenv  # type: ignore
            load_dotenv()
        except Exception:  # pragma: no cover - dotenv is optional
            pass

        bot_token = _require("BOT_TOKEN")
        try:
            api_id = int(_require("API_ID"))
        except ValueError:
            raise ConfigError("API_ID must be an integer")
        api_hash = _require("API_HASH")

        # Session directory: prefer a persistent volume when mounted.
        default_session = "/data/session" if Path("/data").is_dir() else "./.session"
        session_dir = Path(_str("SESSION_DIR") or default_session)
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            session_dir = Path("./.session")
            session_dir.mkdir(parents=True, exist_ok=True)

        # Keep Whisper model cache on the persistent volume when available.
        if Path("/data").is_dir() and not os.environ.get("HF_HOME"):
            os.environ["HF_HOME"] = "/data/hf"

        temp_dir = Path(_str("TEMP_DIRECTORY") or "/tmp/jobs")

        whisper_model = _str("WHISPER_MODEL", "small") or "small"
        if whisper_model not in KNOWN_WHISPER_MODELS and "/" not in whisper_model:
            log.warning("WHISPER_MODEL=%r is not a known model name; "
                        "faster-whisper will try to resolve it anyway", whisper_model)

        formats = [f for f in _list("SUBTITLE_FORMATS", "srt")
                   if f in {"srt", "vtt", "ass", "ssa", "txt"}]
        if not formats:
            formats = ["srt"]

        providers = load_providers()
        translate_enabled = _bool("TRANSLATE_ENABLED", True)
        if translate_enabled and not providers:
            log.warning("Translation enabled but no AI provider configured; "
                        "subtitles will be delivered untranslated")
            translate_enabled = False

        port_raw = _str("PORT")
        port: Optional[int] = None
        if port_raw:
            try:
                port = int(port_raw)
            except ValueError:
                port = None

        return cls(
            bot_token=bot_token,
            api_id=api_id,
            api_hash=api_hash,
            admin_ids=_ids("ADMIN_USER_IDS") | _ids("ADMIN_ID"),
            allowed_ids=_ids("ALLOWED_USER_IDS"),
            session_dir=session_dir,
            temp_dir=temp_dir,
            max_upload_size=_int("MAX_UPLOAD_SIZE", 2147483648),
            delete_after_send=_bool("DELETE_AFTER_SEND", True),
            max_concurrent_jobs=max(1, _int("MAX_CONCURRENT_JOBS", 1)),
            queue_size=max(1, _int("QUEUE_SIZE", 20)),
            user_cooldown_seconds=_float("USER_COOLDOWN_SECONDS", 20.0),
            job_timeout_minutes=max(5, _int("JOB_TIMEOUT_MINUTES", 180)),
            whisper_model=whisper_model,
            whisper_device=(_str("WHISPER_DEVICE", "auto") or "auto").lower(),
            whisper_compute=(_str("WHISPER_COMPUTE", "auto") or "auto").lower(),
            whisper_beam_size=max(1, _int("WHISPER_BEAM_SIZE", 5)),
            vad_filter=_bool("VAD_FILTER", True),
            audio_normalize=_bool("AUDIO_NORMALIZE", True),
            subtitle_formats=formats,
            max_chars_per_line=max(20, _int("MAX_CHARS_PER_LINE", 42)),
            max_lines=max(1, _int("MAX_SUBTITLE_LINES", 2)),
            max_cue_duration=_float("MAX_CUE_DURATION", 6.0),
            rtl_marks=_bool("RTL_MARKS", True),
            translate_enabled=translate_enabled,
            target_language=(_str("TARGET_LANGUAGE", "fa") or "fa").lower(),
            providers=providers,
            translation_workers=max(1, _int("TRANSLATION_WORKERS", 4)),
            translation_chunk_size=max(5, _int("TRANSLATION_CHUNK_SIZE", 15)),
            log_level=(_str("LOG_LEVEL", "INFO") or "INFO").upper(),
            port=port,
        )
