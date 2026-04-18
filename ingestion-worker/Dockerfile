FROM python:3.11-slim

WORKDIR /app

# System deps for yt-dlp and ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi \
    uvicorn[standard] \
    asyncpg \
    pydantic-settings \
    faster-whisper \
    yt-dlp \
    tiktoken \
    anthropic \
    httpx

COPY . .

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1"]
