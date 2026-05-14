#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python3 -m pip install -r requirements.txt

export CONFIG_CONVERT_PORT="${CONFIG_CONVERT_PORT:-5050}"
export CONFIG_CONVERT_OPEN_BROWSER="${CONFIG_CONVERT_OPEN_BROWSER:-0}"

echo "Starting Config_Converter_to_Ex3400"
echo "Open: http://127.0.0.1:${CONFIG_CONVERT_PORT}"
python3 app.py
