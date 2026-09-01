#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD:${DAYTONA_SDK_PATH:-/Users/zjing/Documents/git/daytona/libs/sdk-python/src}:${PYTHONPATH:-}"
args=(app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8787}")
if [[ "${RELOAD:-0}" == "1" ]]; then
  args+=(--reload)
fi
exec python3 -m uvicorn "${args[@]}"
