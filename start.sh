#!/usr/bin/env bash
set -e

# Directory of this script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

# Apple Silicon GPU acceleration fallback
export PYTORCH_ENABLE_MPS_FALLBACK=1

# Check virtual environment
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

echo "=================================================="
echo "  Starting Kokoro Voice Studio (Neural TTS 82M)   "
echo "  URL: http://localhost:$PORT                       "
echo "  Apple Silicon MPS: Enabled                       "
echo "=================================================="

# Run FastAPI server
exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
