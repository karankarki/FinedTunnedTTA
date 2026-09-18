# Production Multi-Stage Dockerfile for High-Performance Neural Voice API
FROM python:3.12-slim

# System dependencies: ffmpeg and libsndfile for audio processing & MP3 encoding
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir gunicorn

# Copy application source
COPY app/ ./app/
COPY cli.py .

# Create persistent outputs directory
RUN mkdir -p /app/outputs

EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:${PORT:-8000}/api/status || exit 1

# Production Gunicorn with Uvicorn workers
# Dynamically binds to $PORT (Render/Koyeb/Hugging Face) or 8000
CMD exec gunicorn app.main:app \
     --workers 2 \
     --worker-class uvicorn.workers.UvicornWorker \
     --bind "0.0.0.0:${PORT:-8000}" \
     --timeout 120 \
     --keep-alive 65 \
     --access-logfile - \
     --error-logfile -
