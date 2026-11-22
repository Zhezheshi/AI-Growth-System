#!/bin/sh
set -eu
cd "$(dirname "$0")"
for option in "$@"; do
  case "$option" in
    --demo) export DESK_DEMO=1 DESK_DATABASE="${DESK_DATABASE:-runtime/demo.db}";;
    --codex) export DESK_AI_PROVIDER=codex;;
    *) echo "Usage: ./dev.sh [--demo] [--codex]" >&2; exit 2;;
  esac
done
if [ "${DESK_AI_PROVIDER:-}" = codex ]; then codex login status; fi
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
.venv/bin/python -m pip --disable-pip-version-check -q install -r requirements.txt
exec .venv/bin/python -m uvicorn desk.main:app --host 127.0.0.1 --port "${PORT:-8794}"
