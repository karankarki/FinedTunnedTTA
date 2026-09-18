# Production Multi-Stage Dockerfile for High-Performance Neural Voice API
FROM python:3.12-slim

# System dependencies: ffmpeg, espeak-ng, and libsndfile for audio processing & MP3 encoding
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    espeak-ng \
    libsndfile1 \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/ ./app/
COPY cli.py .

# Create persistent outputs directory
RUN mkdir -p /app/outputs

ENV PORT=10000
EXPOSE 10000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:${PORT}/api/status || exit 1

# Production lightweight Uvicorn server (<100MB RAM, avoids 512MB free tier OOM)
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
