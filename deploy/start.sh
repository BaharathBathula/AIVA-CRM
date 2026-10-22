#!/usr/bin/env bash
# Starts AIVA CRM in one container: migrations, first-run demo data,
# the API on 127.0.0.1:8000 and the web app on $PORT.
set -euo pipefail

export PORT="${PORT:-10000}"
export APP_BASE_URL="${APP_BASE_URL:-${RENDER_EXTERNAL_URL:-http://localhost:${PORT}}}"
export AIVA_INTERNAL_API_URL="http://127.0.0.1:8000/api/v1"
home="${AIVA_HOME:-/app}"

cd "$home/api"
echo "Applying database migrations..."
alembic upgrade head
python -m app.scripts.bootstrap_dev --if-new

uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log &
api_pid=$!

for _ in $(seq 1 60); do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2)" 2>/dev/null; then
    break
  fi
  sleep 1
done

cd "$home/web"
HOSTNAME=0.0.0.0 node server.js &
web_pid=$!

trap 'kill -TERM "$api_pid" "$web_pid" 2>/dev/null' TERM INT
# If either process stops, stop the container so the host restarts it.
wait -n "$api_pid" "$web_pid"
status=$?
kill -TERM "$api_pid" "$web_pid" 2>/dev/null || true
exit "$status"
