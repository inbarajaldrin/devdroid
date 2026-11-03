#!/usr/bin/env bash
# Print the current Claude Code session ID (the local UUID usable by
# /browse-chat-history and resume tooling).
#
# Resolution order (each earlier source is strictly more reliable):
#   1. CLAUDE_JOB_DIR (background jobs): the job id is the session-UUID
#      prefix; resolve the full UUID against ~/.claude/projects/*/*.jsonl.
#      This is the ONLY correct source under background jobs — after a
#      session continuation BOTH CLAUDE_CODE_SESSION_ID and the
#      ~/.claude/sessions/*.json map carry the PREDECESSOR session.
#   2. CLAUDE_CODE_SESSION_ID env var (interactive sessions).
#
# The bridge id (CLAUDE_CODE_BRIDGE_SESSION_ID, the session_01... used in
# commit-trailer provenance) is printed to stderr when present, but it is
# a DIFFERENT namespace — never substituted for the local UUID.
set -euo pipefail

if [[ -n "${CLAUDE_JOB_DIR:-}" ]]; then
  job_id=$(basename "$CLAUDE_JOB_DIR")
  # CLAUDE_JOB_DIR may be <...>/jobs/<id> or <...>/jobs/<id>/tmp.
  [[ "$job_id" == "tmp" ]] && job_id=$(basename "$(dirname "$CLAUDE_JOB_DIR")")
  matches=( ~/.claude/projects/*/"$job_id"*.jsonl )
  if [[ ${#matches[@]} -eq 1 && -e "${matches[0]}" ]]; then
    base=$(basename "${matches[0]}")
    echo "${base%.jsonl}"
    [[ -n "${CLAUDE_CODE_BRIDGE_SESSION_ID:-}" ]] \
      && echo "bridge: ${CLAUDE_CODE_BRIDGE_SESSION_ID}" >&2
    exit 0
  fi
fi

if [[ -n "${CLAUDE_CODE_SESSION_ID:-}" ]]; then
  echo "$CLAUDE_CODE_SESSION_ID"
  [[ -n "${CLAUDE_JOB_DIR:-}" ]] && \
    echo "warning: background job; env id may be a stale predecessor" >&2
  [[ -n "${CLAUDE_CODE_BRIDGE_SESSION_ID:-}" ]] \
    && echo "bridge: ${CLAUDE_CODE_BRIDGE_SESSION_ID}" >&2
  exit 0
fi

echo "cannot determine session ID (no CLAUDE_JOB_DIR match, no env var)" >&2
exit 1
