#!/usr/bin/env bash
# Open the AirDrop picker with files loaded and wait for the transfer.
#
# Usage: airdrop.sh [--timeout seconds] <file> [more files...]
# Exit:  0 sent · 2 bad arguments or missing file · 4 timed out · 5 AirDrop failed
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER_SRC="$SCRIPT_DIR/airdrop_open.swift"
HELPER_BIN="$HOME/.devdroid/airdrop/airdrop_open"
TIMEOUT=300
FILES=()

while [ $# -gt 0 ]; do
  case "$1" in
    --timeout) TIMEOUT="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    --) shift; FILES+=("$@"); break ;;
    -*) echo "unknown flag: $1" >&2; exit 2 ;;
    *) FILES+=("$1"); shift ;;
  esac
done

case "$TIMEOUT" in ''|*[!0-9]*) echo "--timeout needs a number of seconds" >&2; exit 2 ;; esac
[ ${#FILES[@]} -gt 0 ] || { echo "no file given" >&2; exit 2; }
for f in "${FILES[@]}"; do
  [ -e "$f" ] || { echo "file not found: $f" >&2; exit 2; }
done

if [ ! -x "$HELPER_BIN" ] || [ "$HELPER_SRC" -nt "$HELPER_BIN" ]; then
  mkdir -p "$(dirname "$HELPER_BIN")"
  swiftc -O "$HELPER_SRC" -o "$HELPER_BIN" -framework AppKit || { echo "could not compile the AirDrop helper" >&2; exit 5; }
fi

"$HELPER_BIN" "${FILES[@]}" &
HELPER_PID=$!
trap 'kill "$HELPER_PID" 2>/dev/null || true' EXIT

for ((i = 0; i < TIMEOUT; i++)); do
  if ! kill -0 "$HELPER_PID" 2>/dev/null; then
    if wait "$HELPER_PID"; then exit 0; else exit 5; fi
  fi
  sleep 1
done

echo "timed out after ${TIMEOUT}s" >&2
exit 4
