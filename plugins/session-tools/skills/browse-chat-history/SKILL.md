---
name: browse-chat-history
description: Search, inspect, and trace authorship of AI session transcripts (Claude Code, Cursor, Codex CLI). Make sure to use this skill whenever the user asks who wrote, edited, or authored a file/line/decision, why or when something was added, which session/agent created X, or wants to find a past conversation, recall a working command, find the first or last time something came up, count how often something was asked, search across all chat history, or inspect tool calls or thinking blocks from prior sessions. Trigger liberally on any mention of "previous session", "old chat", "who authored", "who edited", "trace this file", "when did we add", "why did the agent do X", "find that conversation where", or any provenance question about code, configs, docs, or skills.
---

# Browse Chat History

Search, inspect, and trace authorship of AI session transcripts across Claude Code, Cursor, and Codex CLI. Powered by `cq.py` (Claude Query).

## Tool

```
${CLAUDE_PLUGIN_ROOT}/skills/browse-chat-history/scripts/cq.py
```

Runtime-specific paths in `references/runtimes.md`. `--json` and `--host` work in **any position**. ANSI and per-result resume hints are auto-suppressed when stdout isn't a TTY (agent mode).

## Pick the search

| You want… | Use | Why |
|---|---|---|
| the conversation(s) *about* a topic, ranked | `cq search "<words>"` | full-text index, ~1 s, one hit per session |
| where an exact string appears — a command, path, flag, error, id | `cq grep "<literal>"` | scans the raw files (~10 s), nothing skipped |
| the **first** time something came up | `cq grep "<literal>" --first` | sessions ordered by earliest match |
| how **often** / in how many sessions | `cq grep "<literal>" --count` | match count per session |
| activity in a time window | `cq grep "<literal>" --since 3d` (or `12h`, `2026-09-01`) / `cq search … --after/--before` | |
| who **wrote** a file and why | `cq provenance <path>` | see below |
| how an initial diagnosis was revised | `cq why <session> <turn>` (alias `resolve`) | kept-path timeline, latest statement and evidence; `--term X` narrows the topic |

```
cq search "<query>" [--tier deep|shallow] [--phrase] [--provider claude|codex|cursor|all] [--user]
                    [--include-subagents] [--include-self] [--limit N] [--after/--before YYYY-MM-DD]
cq grep "<literal>" [--first|--last|--count] [--since 3d|12h|YYYY-MM-DD] [-i] [--provider …]
                    [--project <cwd substring>] [--per-session K] [--all-fields] [--include-self] [--limit N]
cq list | cq recent [N]
```

Search defaults & gotchas:
- **Scope: Claude main sessions, every folder, this machine.** Codex/Cursor (`--provider all`) and subagent transcripts (`--include-subagents`) are excluded, but every search prints a `Not shown: N in other providers / N in subagent transcripts` footer when matches were hidden — follow it rather than concluding "not found". `--user` limits to human-typed prompts (including messages typed while the agent was busy); tool output is excluded.
- **Match the scope to what the user said.** "in this project", "here", "earlier in this repo" → add `--project <this folder's name>` (search) or `--project <cwd substring>` (grep). A named folder or project → `--project <that name>`. "on the workstation", "on the other machine" → `--host <ssh-target>`. Otherwise leave scope open: search every folder.
- **Your own session is hidden** (and its subagents, and cq's own commands/output) — otherwise half the top hits are the query echoing back. `--include-self` restores them.
- **`--limit` counts sessions** (default 15), one best hit each.
- **Multi-word = AND**, cascading exact → prefix → OR, so it rarely returns 0 — judge relevance from the snippets, and drop to 1–2 distinctive tokens if they're off.
- **What's indexed:** chat text, thinking, tool inputs and tool outputs (each output clipped to its first 6 KB + last 2 KB). Not indexed: full-file copies after edits, attachments, binary/built artifacts (`.usd`, compiled output) — inspect those directly. When a search misses an exact identifier, `cq grep` it.
- **Index refresh never blocks.** New transcript lines are indexed incrementally; a big backlog (first run, schema upgrade) is handed to a background indexer and the search answers from the existing index with a `cq: … background indexer` note on stderr. `cq stats` shows the backlog/progress.

`cq grep` notes: default scope is **everything** (all providers, subagents included; only your own session excluded). Each hit is labelled by where it sits — `user`, `assistant`, `thinking`, `tool-input:<Tool>`, `tool-output`, `user (queued)`. Matches that only sit in duplicate/metadata fields (a full copy of an edited file, the skill list) are hidden; `--all-fields` counts them like raw grep would.

Every hit — from `search` (`msg[N]`) or `grep` (`LN`) — prints a ready drill line: `cq context <session> @N` opens the turn containing that line; `cq raw <session> N` prints the line itself.

## Inspect one session

```
cq info <session>                    # compact metadata (aliases: inspect, show)
cq turns <session> [--range N-M]     # structured turn list
cq tools <session> [--name X] [--errors]
cq context <session> <N | N-M | @LINE | msg[LINE]>   # full turn detail; @LINE = turn containing that JSONL line
cq files <session>                   # files touched (grouped)
cq user <session> [--limit N]        # user messages only (last N)
cq thinking <session>                # reasoning blocks
cq raw <session> START [END]         # raw JSONL lines (same as --start/--end)
cq resume <session> [--from-subagent]
cq subagents <session>               # this session's spawned-agent sidechains (alias: sub)
cq commits <session> [--match "<subj>"]  # git commits this session RAN (alias: commit)
cq branches <session>                # rewind FORKS: active timeline vs abandoned arms (aliases: forks, fork)
```

Session ids accept any unique prefix; Codex (`rollout-…`) and subagent (`agent-…`) ids are printed whole because their prefixes collide.

**`cq subagents <session>`** — lists the session's spawned-agent transcripts (`<projdir>/<uuid>/subagents/agent-*.jsonl`) with dispatch description, line count, task + final-digest snippets, and a ready `tools --jsonl` drill. Use it when a session's real work ran inside a Task subagent — those are **invisible to `cq tools/files` on the parent**.

**`cq commits <session>`** — every `git commit` the session actually RAN (turn, UTC, subject, parsed from `-m`/`-am`/`-F -`). **`--match "<subject>"` sets the exit code** (0 = ≥1 match, 1 = none) — the mechanized *"did THIS session author that commit?"* test when git's author is uniform across agents.

**`cq branches <session>`** — Claude Code's in-TUI rewind (and `/timetravel` detours) branch **within one jsonl** (same session UUID, divergent `parentUuid` chains), so the flat commands can't tell the path you took from the arm you abandoned. This walks `parentUuid` back from the last message to find the active timeline; each fork prints where it diverges, the abandoned branch's prompt(s) and turn count (cross-checked against Claude Code's `last-prompt` log), a `cq raw` drill, and what the active timeline took. Abandoned arms are still searchable with `search`/`grep`.

**Skim a range compactly** — `cq raw` prints whole JSONL records (one tool result can be 40 KB). For "what did this session do between 05:30Z and 06:05Z":

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/browse-chat-history/scripts/skim.py <session.jsonl> <start> <end> [width]
```

One line per event — `[line] timestamp TEXT|TOOL <name>|RESULT|QUEUE: …` — the `[line]` index is the same one `search`/`grep` print, so search → skim → `context @N` narrows in three steps. `scripts/session_tree.py <session.jsonl> <out.html>` renders a session's full branch tree as an interactive HTML page.

## Trace authorship

`cq provenance <path> [--host <host>...] [--show-mentions]` finds which agent sessions created, edited, read or ran a file, and why (the session's first request).

- **Authored** — Edit/Write, Codex `apply_patch`, or shell writes.
- **Inspected** — Read/NotebookRead.
- **Shell-touched** — commands ran or referenced the file; rows show intent, first command and a drill.
- **Mentioned** — other tool inputs (`--show-mentions`).
- **On abandoned branches** — operations on rewound conversation branches; inline sidechain work stays credited.

Next: `cq context <id> @N` opens the turn; `cq why <id> @N` follows changes in reasoning; `cq grep "<path>"` finds every raw hit.

Limits: only agent changes recorded in transcripts are visible; your manual edits, git checkouts/pulls and other apps' file changes are invisible.
“Abandoned” means rewound in the conversation, not reverted on disk. First/Last authored exclude abandoned edits.
Matching is by path fragment after resolving shell cwd/assignments; a bare filename is a broad match across directories (labelled in the header).

## Cross-machine + ad-hoc transcripts

- `--host <ssh-target>` on `search`, `grep`, and `provenance` **runs cq on that host** (its own index, Claude + Codex), falling back to a grep/fetch walk if the host has no cq install. A host typed where the command goes (`cq workstation search foo`) is understood.
- `--host` on session commands (`turns`, `context`, `raw`, …) fetches that session's JSONL into a local cache and inspects it here.
- `--jsonl <path>` (every session-scoped command) inspects an arbitrary JSONL file — transcripts received via Slack/Syncthing/email.

## When results look wrong

- **Don't hide errors.** Never append `2>/dev/null` to cq, and check the exit code when piping into `head` — a bad flag or a missing session is an error on stderr, not "0 results". Empty results say so explicitly and tell you what was hidden or still being indexed.
- `cq stats` — index size, backlog, background-indexer progress (instant; never indexes). `--full` adds the per-project/shallow/deep counts (scans the whole index).
- `cq index` — bring the index up to date in the foreground (waits for a running background indexer). `cq index --reindex` rebuilds from scratch.
- A search that can't find an exact identifier you know exists → `cq grep` it; `grep` reads the raw files and can't be stale.

## References

- `references/commands.md` — every subcommand with all flags, filters, tokenizer caveats, examples
- `references/provenance.md` — provenance op-kind taxonomy, output format, worked examples
- `references/schema.md` — FTS5 schema, kind/sidechain semantics, Turn/Block/ToolCall layout
- `references/runtimes.md` — multi-runtime install paths, sandbox notes, cross-host SSH expectations
- `references/providers.md` — how to add support for a new agent CLI (Aider, Continue.dev, etc.) via the adapter interface
