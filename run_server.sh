#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

export CONFIG_CONVERT_HOST="${CONFIG_CONVERT_HOST:-0.0.0.0}"
export CONFIG_CONVERT_PORT="${CONFIG_CONVERT_PORT:-5050}"
export CONFIG_CONVERT_OPEN_BROWSER=0
export CONFIG_CONVERT_PRODUCTION=1

exec ./run.sh "$@"
