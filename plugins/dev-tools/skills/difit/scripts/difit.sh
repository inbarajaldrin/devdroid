#!/usr/bin/env bash
# Reference: https://github.com/yoshiko-pg/difit
#
# Run difit for a review: one background viewer per repository, tracked by its
# own process id, so nothing else on the machine is touched.
#
#   difit.sh up [target...] [-- difit flags]   start this repository's viewer
#   difit.sh check                             list the files in the diff
#   difit.sh comments                          print the person's comments
#   difit.sh down                              stop this repository's viewer
set -uo pipefail

DIFIT=(npx --yes difit@5)
command -v difit >/dev/null 2>&1 && DIFIT=(difit)

root="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "not inside a git repository" >&2; exit 2; }
state_dir="${HOME}/.devdroid/difit"
state="${state_dir}/$(printf '%s' "$root" | cksum | cut -d' ' -f1).json"

field() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$state" "$1"; }

running() {
  [ -f "$state" ] || return 1
  local pid; pid="$(field pid)" || return 1
  ps -o command= -p "$pid" 2>/dev/null | grep -q difit
}

require() { running || { echo "no viewer running for $root" >&2; exit 3; }; }

stop() {
  if running; then
    local pid; pid="$(field pid)"
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
  fi
  rm -f "$state"
}

files() {
  curl -fsS "$(field url)/api/diff" | python3 -c '
import json, sys
found = json.load(sys.stdin).get("files", [])
print(f"{len(found)} file(s):")
for item in found:
    print(" ", item.get("status", ""), item.get("path", ""))
'
}

verb="${1:-up}"; [ $# -gt 0 ] && shift
case "$verb" in
  up)
    targets=(); flags=()
    while [ $# -gt 0 ] && [ "$1" != "--" ]; do targets+=("$1"); shift; done
    [ "${1:-}" = "--" ] && shift
    flags=("$@")
    case " ${flags[*]:-} " in *" --pr "*) ;; *) [ ${#targets[@]} -gt 0 ] || targets=(.) ;; esac
    # difit waits on a yes/no prompt about untracked files, which would hang here.
    case "${targets[*]:-}" in
      .|working) [ -z "$(git -C "$root" ls-files --others --exclude-standard)" ] || flags+=(--include-untracked) ;;
    esac
    stop
    mkdir -p "$state_dir"
    cd "$root" || exit 2
    out="$("${DIFIT[@]}" ${targets[@]+"${targets[@]}"} ${flags[@]+"${flags[@]}"} --background </dev/null 2>&1)"
    json="$(printf '%s\n' "$out" | grep -E '^\{.*"pid"' | tail -1)"
    if [ -z "$json" ]; then echo "difit did not start:" >&2; printf '%s\n' "$out" >&2; exit 1; fi
    printf '%s\n' "$json" > "$state"
    echo "Review at: $(field url)"
    files
    ;;
  check) require; files ;;
  comments) require; curl -fsS "$(field url)/api/comments-output" ;;
  down) stop; echo "stopped" ;;
  *) echo "usage: difit.sh {up [target...] [-- flags] | check | comments | down}" >&2; exit 2 ;;
esac
