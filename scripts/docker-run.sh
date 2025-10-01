#!/usr/bin/env bash
set -euo pipefail

IMAGE="python:3.12-slim"
APP_DIR="/app"
STATE_VOLUME="personal_assistant_state"
CACHE_VOLUME="personal_assistant_pip_cache"
PORT="8000"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required to run this script" >&2
  exit 1
fi

docker volume inspect "${STATE_VOLUME}" >/dev/null 2>&1 || docker volume create "${STATE_VOLUME}" >/dev/null
docker volume inspect "${CACHE_VOLUME}" >/dev/null 2>&1 || docker volume create "${CACHE_VOLUME}" >/dev/null

docker run --rm \
  -it \
  -v "${PWD}:${APP_DIR}" \
  -v "${STATE_VOLUME}:/root/.graphiti_sync" \
  -v "${CACHE_VOLUME}:/root/.cache/pip" \
  -w "${APP_DIR}" \
  -p "${PORT}:${PORT}" \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONUNBUFFERED=1 \
  "${IMAGE}" \
  bash -lc "python -m pip install --upgrade pip && pip install -r requirements.txt && uvicorn graphiti.web_admin.app:create_app --factory --host 0.0.0.0 --port ${PORT}"
