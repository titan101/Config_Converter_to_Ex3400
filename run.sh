#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python3 was not found. Ask your server admin for Python 3.10+ with venv support." >&2
  exit 1
fi

if [ ! -d ".venv" ]; then
  if ! "$PYTHON_BIN" -m venv .venv; then
    echo "Could not create .venv. Ask your server admin to enable the Python venv module." >&2
    exit 1
  fi
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

export CONFIG_CONVERT_HOST="${CONFIG_CONVERT_HOST:-127.0.0.1}"
export CONFIG_CONVERT_PORT="${CONFIG_CONVERT_PORT:-5050}"
export CONFIG_CONVERT_OPEN_BROWSER="${CONFIG_CONVERT_OPEN_BROWSER:-0}"
export CONFIG_CONVERT_PRODUCTION="${CONFIG_CONVERT_PRODUCTION:-0}"

echo "Starting EX3400 Config Converter"
echo "Local URL: http://127.0.0.1:${CONFIG_CONVERT_PORT}"
if [ "$CONFIG_CONVERT_HOST" = "0.0.0.0" ]; then
  echo "Server URL: http://SERVER_IP:${CONFIG_CONVERT_PORT}"
fi
echo "Press Ctrl+C to stop."

.venv/bin/python app.py "$@"
