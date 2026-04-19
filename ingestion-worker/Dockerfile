FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20 LTS — required for yt-dlp n-challenge solving (EJS)
RUN curl -fsSL https://nodejs.org/dist/v20.18.0/node-v20.18.0-linux-x64.tar.gz \
    | tar -xz -C /usr/local/lib/ \
    && ln -s /usr/local/lib/node-v20.18.0-linux-x64/bin/node /usr/local/bin/node

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8002

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "1"]
