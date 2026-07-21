# 🎬 Telegram Subtitle Bot — ربات زیرنویس هوشمند

An AI-powered Telegram bot that turns any video (up to **2 GB**) into accurate,
naturally translated **Persian subtitles** — fully automatic, with a 100% Persian
(RTL) user interface.

یک ربات تلگرام مبتنی بر هوش مصنوعی که هر ویدیویی (تا ۲ گیگابایت) را به زیرنویس
دقیق و ترجمه‌شده‌ی **فارسی روان** تبدیل می‌کند — کاملاً خودکار، با رابط کاربری
تمام‌فارسی.

---

## ✨ Features / امکانات

| | |
|---|---|
| 📥 **2 GB media** | MTProto (Telethon) downloads — no Local Bot API server needed |
| 🎬 **Every FFmpeg format** | MP4, MKV, AVI, MOV, WEBM, FLV, TS, WMV, 3GP, … + audio files |
| 🎤 **faster-whisper ASR** | word-level timestamps, built-in VAD silence skipping, auto language detection (100 + languages), CUDA auto-detect with CPU fallback |
| 🌍 **AI translation agent** | any OpenAI-compatible API, unlimited providers via config, priority + automatic fallback chain, circuit breaker, retries with backoff |
| ⚡ **Parallel translation** | subtitle chunks translated concurrently with neighboring-dialogue context, then merged with numbering/timing preserved exactly |
| 🧪 **Quality controller** | detects refusals/leaks/untranslated lines and repairs them line-by-line, then normalizes Persian punctuation |
| 📝 **SRT / VTT / ASS / TXT** | identical timing across all formats, RTL-safe rendering, UTF-8 BOM for player compatibility |
| 📊 **Live Persian progress** | one status message edited through every stage, with progress bars and a cancel button |
| 🔐 **Hardened** | file validation, size caps, rate limiting, per-user limits, queue caps, guaranteed temp-file cleanup on success/failure/timeout/cancel |
| 🐳 **Docker-first** | CPU image for Railway/VPS, CUDA image for GPU hosts, persistent model cache volume |

## 🏗 Architecture / معماری

```
app/
├── bot/            Telegram layer — Telethon client, Persian UI, handlers, live progress
├── core/           job model, async queue (limits/cancel/timeout), pipeline orchestrator
├── media/          validation + ffprobe/FFmpeg audio extraction (16 kHz mono WAV, loudnorm)
├── speech/         faster-whisper engine (lazy load, GPU auto-detect, word timestamps)
├── subtitles/      cue builder (split/merge/reading-speed) + SRT/VTT/ASS/TXT writers
├── translation/    provider configs → OpenAI-compatible transport → translation agent → QC
└── utils/          JSON logging, Persian text processing, health endpoint
```

**Pipeline:** receive → download → validate → probe → extract audio → VAD +
transcribe (word timestamps) → build cues → parallel Persian translation →
quality pass → write subtitle files → send → cleanup.

## 🚀 Quick start (Docker)

```bash
git clone https://github.com/jack22Jqck211/tran.git
cd tran
cp .env.example .env      # fill in BOT_TOKEN, API_ID, API_HASH, AI_* ...
docker compose up -d --build
```

GPU host (NVIDIA toolkit installed):

```bash
docker compose -f docker-compose.gpu.yml up -d --build
```

## ☁️ Railway deployment

The repo ships with `railway.json` (Dockerfile builder + restart policy).

1. Create a new Railway project → **Deploy from GitHub repo**.
2. Add the environment variables from `.env.example` (at minimum:
   `BOT_TOKEN`, `API_ID`, `API_HASH`, `ADMIN_USER_IDS`, `AI_BASE_URL`,
   `AI_API_KEY`, `AI_MODELS`).
3. (Recommended) add a **Volume** mounted at `/data` so the Whisper model and
   the Telegram session survive restarts.
4. Deploy — the bot starts polling automatically; no public networking needed.

> ⚠️ **CPU reality check:** Railway has no GPU. `WHISPER_MODEL=small` is the
> sweet spot there (expect roughly real-time processing). On a GPU host,
> switch to `large-v3` for maximum accuracy.

## ⚙️ Configuration

All configuration is environment variables — see [`.env.example`](.env.example)
for the complete annotated list. Highlights:

| Variable | Default | Description |
|---|---|---|
| `BOT_TOKEN` | — | Bot token from @BotFather |
| `API_ID` / `API_HASH` | — | my.telegram.org credentials (2 GB downloads) |
| `ADMIN_USER_IDS` | — | comma-separated admin ids (`/stats`, `/model`) |
| `ALLOWED_USER_IDS` | empty (public) | restrict bot to specific users |
| `WHISPER_MODEL` | `small` | tiny/base/small/medium/large-v2/large-v3/turbo |
| `WHISPER_DEVICE` | `auto` | auto-detects CUDA, falls back to CPU |
| `AI_BASE_URL` | — | any OpenAI-compatible endpoint (`.../v1`) |
| `AI_MODELS` | — | comma-separated fallback chain |
| `PROVIDERS_JSON` | — | unlimited extra providers, zero code changes |
| `TRANSLATION_WORKERS` | `4` | parallel chunk translators |
| `SUBTITLE_FORMATS` | `srt` | `srt,vtt,ass,txt` |
| `MAX_UPLOAD_SIZE` | `2147483648` | 2 GB cap |
| `MAX_CONCURRENT_JOBS` | `1` | simultaneous ASR jobs |

### Adding a translation provider

No code changes — just configuration:

```json
PROVIDERS_JSON=[
  {"name": "openrouter", "base_url": "https://openrouter.ai/api/v1",
   "api_key": "sk-...", "models": ["meta-llama/llama-3.3-70b"], "priority": 1},
  {"name": "ollama-local", "base_url": "http://ollama:11434/v1",
   "api_key": "", "models": ["qwen2.5:14b"], "priority": 2}
]
```

The agent tries providers by priority and models in order, tracks failures,
cools down broken providers, and always falls back gracefully.

## 🤖 Bot commands / دستورات

| Command | Description |
|---|---|
| `/start` | خوش‌آمدگویی و شروع |
| `/help` | راهنمای کامل |
| `/status` | وضعیت پردازش و صف |
| `/cancel` | لغو عملیات در حال اجرا |
| `/stats` | آمار ربات (مدیر) |
| `/model large-v3` | تغییر مدل تشخیص گفتار (مدیر) |

## 🧪 Development

```bash
pip install -r requirements-dev.txt
pytest                # unit tests (builder, writers, parsing, textproc)
ruff check app tests  # lint
python -m app.main    # run locally (.env is honored)
```

## 🗺 Roadmap

Burn-in subtitles (FFmpeg), dual-language output, speaker diarization,
OCR subtitle extraction, web dashboard + REST API, Redis-backed queue,
PostgreSQL job history, object storage, webhook mode, plugin system.
The module boundaries (`speech/`, `translation/`, `core/queue`) are designed
so these land without restructuring.

## 📄 License

[MIT](LICENSE)
