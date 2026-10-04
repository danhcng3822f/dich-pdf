# Production image for the AI PDF Translator (FastAPI).
#
# This exists because Vercel Functions hard-limit the request body to 4.5 MB
# (413 FUNCTION_PAYLOAD_TOO_LARGE, enforced by AWS Lambda and not configurable),
# which caps uploads far below the 30 MB the app itself supports.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

# Dependencies first, so this layer stays cached across source-only changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# app/config.py derives STORAGE_DIR from the project root, so uploads, exports,
# fonts and model caches all land here. Mount a volume at this path to keep
# them across restarts and deploys.
RUN mkdir -p /app/storage

EXPOSE 8000

# --workers 1 is load-bearing, not a default: UPLOAD_STORE and JOB_STORE are
# in-process dicts, so a second worker would 404 on any translate or download
# request that lands on the worker that did not handle the upload.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
