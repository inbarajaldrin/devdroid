#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
: "${FUNNEL_PORT:?Choose a Funnel port}"
: "${SERVER_PORT:=7490}"

case "${1:-}" in
  on)
    : "${CAREER_HOME:?Choose the owned workspace}"
    "${CAREER_PYTHON:-python3}" - "$CAREER_HOME" <<'PY'
import json, sys
from pathlib import Path
settings = json.loads((Path(sys.argv[1]) / 'settings.json').read_text())
if settings.get('remote_access') != 'Public HTTPS':
    raise SystemExit('Choose Public HTTPS in setup before publishing this API to the internet.')
PY
    tailscale funnel --bg --https="$FUNNEL_PORT" --set-path=/app "http://127.0.0.1:$SERVER_PORT/app"
    tailscale funnel --bg --https="$FUNNEL_PORT" --set-path=/web "http://127.0.0.1:$SERVER_PORT/web"
    tailscale funnel --bg --https="$FUNNEL_PORT" --set-path=/api "http://127.0.0.1:$SERVER_PORT/api"
    ;;
  off)
    tailscale funnel --https="$FUNNEL_PORT" --set-path=/app off
    tailscale funnel --https="$FUNNEL_PORT" --set-path=/web off
    tailscale funnel --https="$FUNNEL_PORT" --set-path=/api off
    ;;
  status) tailscale funnel status ;;
  *) echo "usage: $0 on|off|status" >&2; exit 2 ;;
esac
