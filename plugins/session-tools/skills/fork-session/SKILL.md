---
name: fork-session
description: Fork or branch a Claude Code or Codex session at its end or a chosen turn, leaving the original untouched. Use for "fork from before X", "rewind without losing the original", "continue from turn 12", "split this session", or reviving an abandoned branch. Also use when Claude resume lost context or jumped to a compacted branch (repair), or to re-ask a Claude form or Codex prompt from an earlier point. Works when invoked from either Claude Code or Codex.
---

# Fork Session

Create a new session ID while preserving the original. Select the source agent
and boundary from the request; the invoking agent can fork either runtime.

| Agent | At the end (leaf) | At a chosen turn |
|---|---|---|
| Claude Code | `claude --resume <id> --fork-session` | `scripts/claude_fork.py` |
| Codex | `codex fork <id>` / `codex exec fork <id> [prompt]` | `scripts/codex_fork.py` |

**Continue a Claude session in another folder (same id, no fork):** `cd <folder> && claude --resume <id>`.
Resume-by-id finds the session from any folder, and its tools run in the new folder.
The interactive picker (`claude --resume` with no id) only lists the current folder's sessions, so after
moving a project folder, find the id with browse-chat-history (`cq list`, `cq search`) and resume by id.
To branch it into another folder under a new id instead, use `claude_fork.py … --target-cwd <folder>`.

Use native leaf commands for ordinary continuation from the end. Use a helper
for a specific earlier boundary; Claude repair and form-reask also use its helper.
Run helpers with `python3` (3.9+), from any cwd. Locate this skill's installed
directory from the loaded SKILL.md; resolve `scripts/` and `references/` relative
to it. No particular agent's skill installation path is required.

## Find the boundary

Use **browse-chat-history** for session and turn discovery:

```text
cq search "distinctive words" --provider claude
cq grep "exact phrase" --provider codex
cq context <session-id> @N
cq branches <claude-session-id>
```

`search` prints `msg[N]`, and `grep` prints line numbers. Use `context @N` to
inspect the containing turn; inspect the raw record to obtain its message UUID.
`branches` shows active and abandoned arms; use its UUIDs or raw-line drills to
choose a point on an abandoned arm. If the discovery skill is unavailable, use
a user-supplied source path/ID and boundary, or explain the missing dependency.

For Codex, translate the discovered text to a persisted **turn ID**:

```bash
python3 <skill-dir>/scripts/codex_fork.py --session <id> --find-text "distinctive words"
```

This reports source turn numbers, IDs, status and previews without creating a
fork. Turn numbers are for inspection; pass the full turn ID to the fork flags.
Explain the chosen inclusive/exclusive boundary when reporting the result.

## Claude: chosen point

```bash
python3 <skill-dir>/scripts/claude_fork.py <session-id-or-jsonl> --cut-at-uuid <message-uuid>
python3 <skill-dir>/scripts/claude_fork.py <session-id-or-jsonl> --truncate-at <source-line>
```

UUID is preferred. Each cut walks `parentUuid` from the chosen leaf to root,
preserving that arm, including abandoned history, and excluding sibling arms.
Line cuts are 1-based and inclusive; the last UUID at or before that line is the
leaf. Choose a completed assistant reply. A dangling tool call is refused;
select its completed result/reply instead. Cuts never silently include later turns.

Backups finish before any fork write, under `~/.devdroid/fork-session/backups/`.
The helper clones available project/subagent, session-env, tasks and file-history
sidecars; backup paths include each sidecar's parent kind to avoid collisions.
It rewrites session IDs in the new transcript, repairs known messageId/parent
corruption, and prints the new ID, JSONL path, checks and exact resume command:

```bash
cd <saved-cwd> && claude --resume <new-id>
```

The saved cwd is read verbatim, including spaces and hyphens. To place the fork
in a different existing absolute cwd, add `--target-cwd <directory>`; only the
new transcript's cwd and project storage change. `--project-dir` overrides the
**source storage directory**. `--label` labels its unique backup directory.

## Codex: chosen turn

```bash
python3 <skill-dir>/scripts/codex_fork.py --session <id> --fork-through <turn-id> --name "label"
python3 <skill-dir>/scripts/codex_fork.py --session <id> --fork-before <turn-id> --name "label"
```

Through includes the completed turn and its answer; before excludes that turn
and everything later, useful for re-asking a prompt. Through refuses interrupted
or in-progress turns. IDs, unique ID prefixes and exact unique names are accepted.
The helper calls the official `thread/fork` API with `lastTurnId` or `beforeTurnId`.
It reads rollouts only for discovery/validation and never rewrites or clones them.
It prints the new ID and a `codex --remote unix:// resume <new-id>` command, and
copies the command when a clipboard is available (`--no-clipboard` disables it).

Requires `codex` on PATH with daemon and boundary-fork support. If the daemon
socket is unreachable, it runs `codex app-server daemon start` once and retries
for a bounded interval. `--socket` selects another control socket; the printed
resume command uses the same socket. `CODEX_HOME` is respected.

## Repair: Claude resume lost context

If resume jumped to a compacted branch or lost earlier context, run
`python3 <skill-dir>/scripts/claude_fork.py <id> --label repair`.
This forks the latest parent chain with snapshot-messageId collision and missing
parent fixes. If the desired context is on an abandoned arm, choose its UUID
explicitly using `cq branches`. It cannot reconstruct deleted history or exceed
the model's context window. See [Claude details](references/claude-details.md).

## Re-ask a Claude form

Run `python3 <skill-dir>/scripts/claude_fork.py <id> --reask-form [tool_use_id]`.
No ID selects the last recorded AskUserQuestion; an ID targets a specific form,
including one on an abandoned arm. It drops the answer and later conversation,
balances pending calls with rejected tool results, then appends an interrupt.
Resume and prompt "re-ask what you just asked". See [form-reask](references/form-reask.md).

## Failures and limits

- Unknown/ambiguous IDs, invalid JSON, cycles, invalid cuts and unbalanced tools:
  stop before creating a fork; inspect the boundary with browse-chat-history.
- Claude "No conversation found": check the printed JSONL project and resume cwd.
  A fork may compact when loaded into a smaller context window.
- Codex socket/start failure: report stderr and the requested socket; fix daemon
  availability. Do not unlink sockets or restart/interrupt other sessions.
- Unsupported API boundaries or a stale/corrupt index: stop and preserve the
  source. Do not work around it by editing rollout files. If naming fails after
  creation, use the already-printed new ID to resume or clean up that fork.
- Report source ID, chosen point, new ID and resume command. Verify the fork
  starts at the intended point before further work.

This skill does not rewind the original, restore working-tree files, merge
conversations, fork Cursor/other formats, or manage pinned forks, presets or
launchers. Backups contain private history; retain or remove them deliberately.
