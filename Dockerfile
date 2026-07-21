# Production image — CPU inference (Railway-ready).
# For GPU hosts use Dockerfile.gpu / docker-compose.gpu.yml instead.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg: audio extraction | libgomp1: OpenMP runtime for CTranslate2
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgomp1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Non-root runtime user; writable dirs for jobs, session and model cache.
RUN useradd -m -u 10001 appuser \
    && mkdir -p /tmp/jobs /data \
    && chown -R appuser:appuser /tmp/jobs /data /app
USER appuser

ENV TEMP_DIRECTORY=/tmp/jobs

CMD ["python", "-m", "app.main"]
