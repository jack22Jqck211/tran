# Stage 1 — minimal Dockerfile
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY app.py .

EXPOSE 8080

CMD ["python", "app.py"]
