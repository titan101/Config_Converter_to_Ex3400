#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found. Install Python 3 and try again." >&2
  echo "On Ubuntu/WSL, run: ./install_wsl_prereqs.sh" >&2
  exit 1
fi

if [[ ! -d ".venv" ]]; then
  if ! python3 -m venv .venv; then
    echo
    echo "Could not create a Python virtual environment." >&2
    echo "On Ubuntu/WSL, install prerequisites first:" >&2
    echo "  ./install_wsl_prereqs.sh" >&2
    echo
    echo "If venv support is intentionally unavailable, try:" >&2
    echo "  ./run_wsl_no_venv.sh" >&2
    exit 1
  fi
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

export CONFIG_CONVERT_PORT="${CONFIG_CONVERT_PORT:-5050}"
export CONFIG_CONVERT_HOST="${CONFIG_CONVERT_HOST:-0.0.0.0}"
export CONFIG_CONVERT_OPEN_BROWSER="${CONFIG_CONVERT_OPEN_BROWSER:-0}"

echo "Starting Config_Converter_to_Ex3400"
echo "Open: http://127.0.0.1:${CONFIG_CONVERT_PORT}"
python app.py
