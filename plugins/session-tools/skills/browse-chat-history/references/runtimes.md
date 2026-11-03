# Multi-runtime install paths + cross-host setup

`cq.py` runs the same in every host runtime, but the script lives at a different path per install method.

## Path matrix

| Runtime | Install method | `cq.py` path |
|---|---|---|
| Claude Code | plugin (`/plugin install`) | `${CLAUDE_PLUGIN_ROOT}/skills/browse-chat-history/scripts/cq.py` |
| Claude Code | user-scope (manual / `cp -R`) | `~/.claude/skills/browse-chat-history/scripts/cq.py` |
| Codex CLI | `npx --yes skills@latest add` | `~/.codex/skills/browse-chat-history/scripts/cq.py` |
| Cursor | `npx --yes skills@latest add` | `~/.cursor/skills/browse-chat-history/scripts/cq.py` |

When `${CLAUDE_PLUGIN_ROOT}` isn't set (Codex / Cursor / user-scope Claude Code), substitute the literal path. The script itself is portable Python 3 — no runtime-specific code paths.

## Data sources (shared across runtimes)

All sessions live in fixed user-data paths regardless of which runtime invokes `cq`:

| Source | Path |
|---|---|
| Claude Code sessions | `~/.claude/projects/<encoded-cwd>/*.jsonl` |
| Claude Code subagent transcripts | `~/.claude/projects/<encoded-cwd>/<parent-uuid>/subagents/agent-*.jsonl` |
| Cursor agent transcripts | `~/.cursor/projects/<encoded-cwd>/agent-transcripts/<id>/<id>.jsonl` |
| Codex CLI sessions | `~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl` |
| FTS5 index | `~/.claude/.search-cache/sessions-fts.db` |
| Remote-host cache | `~/.claude/.search-cache/remote/<host>/...` |

The FTS5 index path is intentionally under `~/.claude/.search-cache/` (not under any plugin dir) so it persists across reinstalls and is shared by every runtime's `cq` invocation.

## Codex CLI sandbox

Codex's `--sandbox workspace-write` mode blocks reads outside the project workspace, which includes `~/.claude/.search-cache/`. Two fallbacks:

1. **`--sandbox danger-full-access`** — full FTS5 power, recommended for trusted sessions
2. **Filesystem fallback** — when the DB can't be opened, `cq` falls back to `find ~/.claude/projects -name '*.jsonl' -path '*<keyword>*'`. Slower and coarser, but doesn't require sandbox elevation.

Provenance (`cq provenance`) doesn't touch the FTS5 DB — it walks raw JSONLs directly. It does need read access to `~/.claude/projects/` (and `~/.cursor/`, `~/.codex/`). Same sandbox considerations apply.

## Cross-host (--host)

Session-scoped commands (`turns`, `tools`, `thinking`, `files`, `context`, `user`, `raw`, `resume`, `info`), `search`, `grep` and `provenance` accept `--host <ssh-target>` to operate against a remote SSH host's `~/.claude/projects/`.

### Mechanics

1. `ssh <host> find ~/.claude/projects -name '<uuid>*.jsonl' -print -quit` locates the file
2. `ssh <host> cat <remote-path>` streams it into `~/.claude/.search-cache/remote/<host>/<encoded-project>/<uuid>.jsonl`
3. The local parser path operates on the cached file — no code-path divergence

`resume --host` auto-wraps the resume command in `ssh -t <host> '<cmd>'`. `provenance --host` repeats the walk per remote.

### Requirements

- **Key-based SSH** — `BatchMode=yes` prevents password prompts from hanging the agent
- **`python3` + `find` + `cat`** on the remote (all standard on Linux/macOS)
- **No remote `cq` install required** — the remote is treated as a passive transcript store

### Scope notes

- `--host` covers session-scoped commands, `search`, `grep` and `provenance`
- `--host` for `search`, `grep` and `provenance` runs **cq on the remote** when `~/.claude/skills/browse-chat-history/scripts/cq.py` exists there (ranked FTS, Claude + Codex); otherwise `search` falls back to a remote grep chain and `provenance` to fetching Claude JSONLs
- `list` and `recent` do not support `--host`
- Without cq on the remote host, only Claude Code transcripts (and their subagents) are walked; Codex and Cursor transcripts there need the host's own cq

## NO_COLOR

`cq` honors the `NO_COLOR` environment variable (https://no-color.org/) — set it to disable ANSI even in TTY mode.
