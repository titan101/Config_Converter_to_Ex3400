#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found. On Ubuntu/WSL, run: ./install_wsl_prereqs.sh" >&2
  exit 1
fi

if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "python3 pip was not found. On Ubuntu/WSL, run: ./install_wsl_prereqs.sh" >&2
  exit 1
fi

python3 -m pip install -r requirements.txt

export CONFIG_CONVERT_PORT="${CONFIG_CONVERT_PORT:-5050}"
export CONFIG_CONVERT_HOST="${CONFIG_CONVERT_HOST:-0.0.0.0}"
export CONFIG_CONVERT_OPEN_BROWSER="${CONFIG_CONVERT_OPEN_BROWSER:-0}"

echo "Starting Config_Converter_to_Ex3400"
echo "Open: http://127.0.0.1:${CONFIG_CONVERT_PORT}"
python3 app.py
