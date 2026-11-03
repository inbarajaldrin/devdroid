# `cq` — full command reference

`<cq>` is shorthand for the script path. See `references/runtimes.md` for runtime-specific paths.

Global flag: `--json`, in any position, for structured agent output. ANSI escape codes and per-result resume hints are auto-suppressed when stdout isn't a TTY — agent invocations get clean output by default, no flag required.

## Tokenizer caveat (read first)

FTS5's default tokenizer treats `_` and `-` as word separators. This means **code identifiers like `cmd_search_remote` or flag names like `include-subagents` won't match as a single term**. Workarounds:

- Split into separate terms: `cq search "search remote"` instead of `cq search "cmd_search_remote"`
- Use `--phrase` for strict-adjacent multi-word matches
- Fall back to `cq provenance <path>` (raw-JSONL walk, no tokenizer involved) when looking for *file* authorship rather than *text* mentions

---

## Search commands

### Basic FTS5 search

```bash
cq search "<query>"
cq search "<query>" --project <project-name>
cq search "<query>" --role assistant --limit 20
```

BM25-ranked. Each result shows session ID, message index, score, and a snippet of the matched text. In TTY mode the session resume command appears inline; in agent mode (non-TTY) resume commands collect into a single trailing block.

### Multi-concept AND

Sessions matching ALL concepts (intersected by `session_id`). Requires ≥ 2 `--concept` flags.

```bash
cq search --concept "auth" --concept "middleware" --concept "JWT"
```

The positional `query` is ignored when `--concept` is used.

### Date-bounded

```bash
cq search "<query>" --after 2026-01-01 --before 2026-01-31
```

Both bounds inclusive of the full day. ISO date format only.

### Sidechain / subagent filters

```bash
cq search "<query>"                       # exclude subagent + sidechain by default
cq search "<query>" --include-sidechain   # add inline <teammate-message> sidechain rows
cq search "<query>" --include-subagents   # add spawned-agent transcript rows (kind='subagent')
```

`--include-sidechain` and `--include-subagents` are **orthogonal**. Sidechain marks *inline* teammate blocks embedded in main-session JSONLs; subagent marks *separate* `<project>/<parent-uuid>/subagents/agent-*.jsonl` files.

### Provider filter

```bash
cq search "<query>" --provider claude   # Claude Code only (default)
cq search "<query>" --provider codex    # Codex CLI only
cq search "<query>" --provider cursor   # Cursor only
cq search "<query>" --provider all      # every provider
```

### Single-session search

```bash
cq search "<query>" --session <session-id>
```

When `--session` is passed, every hit is shown — no dedup-to-one-per-session collapse.

### Tier filter

```bash
cq search "<query>" --tier all       # blended (default) — single BM25 pass
cq search "<query>" --tier shallow   # chat text only
cq search "<query>" --tier deep      # tool inputs/results/thinking only
```

### Phrase mode

```bash
cq search "<phrase>" --phrase
```

Strict adjacent-token match. No fallback cascade — empty results stay empty.

### Remote search (--host)

```bash
cq search "<query>" --host user@workstation
```

Runs `cq search` **on the host** over SSH (its own index — ranked hits, every flag works, Claude + Codex). Only if the host has no cq install does it fall back to a remote grep AND-chain over `~/.claude/projects/` (coarser: no ranking, no tier). Drill in with `cq context --host <host> <session-id> @N`.

### Literal search (`cq grep`)

```bash
cq grep "<literal>"                       # where it appears, most recent sessions first
cq grep "<literal>" --first               # sessions ordered by their EARLIEST match
cq grep "<literal>" --count               # match count per session
cq grep "<literal>" --since 3d            # 90m / 12h / 3d / 2w / YYYY-MM-DD
cq grep "<literal>" -i --provider codex --per-session 5 --limit 30
cq grep "<literal>" --all-fields          # also count duplicate/metadata fields (raw-grep equivalent)
cq grep "<literal>" --host workstation     # run on another machine
```

Scans the raw JSONL files in parallel (mmap; ~5–10 s for ~11 GB), so it sees everything the index skips. Default scope is all providers including subagents; only the calling session is excluded (`--include-self`). Each hit is labelled `user` / `assistant` / `thinking` / `tool-input:<Tool>` / `tool-output` / `user (queued)` and comes with `context @LINE` and `raw` drill lines. Counts are matching transcript lines, not occurrences.

---

## Discovery

```bash
cq list                              # all sessions across all projects
cq list --project <project-name>     # filter by project
cq list --include-subagents          # include subagent files in listing
cq recent [N]                        # last N active sessions (default 10)
```

---

## Single-session inspection

All session-scoped commands accept:
- `--host <ssh-target>` — fetch from remote host's `~/.claude/projects/` into cache, then operate locally
- `--jsonl <path>` — inspect an arbitrary JSONL file by path (bypasses session-id resolution and `--host`)

### `info` — compact metadata

```bash
cq info <session>
cq info --jsonl /path/to/session.jsonl
```

Returns: session_id, kind (main/subagent), runtime, project, cwd, first/last timestamps, message count, unique files touched, size, first user message as `intent`. Use before `turns` when you want metadata without reading 200 turns.

### `turns` — structured turn list

```bash
cq turns <session>
cq turns <session> --range 5-15
cq turns <session> --include-sidechain
```

### `tools` — tool calls WITH results

```bash
cq tools <session>
cq tools <session> --name Bash      # filter by tool
cq tools <session> --errors         # only failed calls
```

### `thinking` — reasoning blocks

```bash
cq thinking <session>
```

### `files` — files touched (grouped)

```bash
cq files <session>
```

Shows files grouped by path with operation counts (`Read × 2`, `Edit × 5 (1 err)`).

### `context` — full turn detail

```bash
cq context <session> <turn>
cq context --jsonl /path/to/file.jsonl --turn 12
```

When using `--jsonl`, pass turn via `--turn N` since the positional `session` slot is absent.

### `why` / `resolve` — follow a diagnosis to later evidence

```bash
cq why <session> <turn>
cq resolve <session-prefix> @120 --term src/widget.py --term ValueError
cq why <session> 'msg[120]' --limit 10 --follow-sessions --json
cq why --jsonl /path/to/session.jsonl --turn 3
```

Reads transcripts directly, without the index or a model. The anchor accepts the
same addresses as `context`: turn `N` (1-based), `@LINE`, `msg[LINE]`, or
`line:LINE` (physical JSONL lines, 0-based). `N-M` anchors the first turn of the
range. All addresses resolve the **containing context turn**. A numeric turn uses
its first assistant text as the claim; a line address uses the nearest preceding
assistant text/thinking statement in that turn, falling back to its first text.
Thus a tool-call line anchors the diagnosis immediately before the call. The
whole turn remains the context; handles use that claim, preceding human text,
nearby prose identifiers, and the tools attached to the claim. Session prefixes,
`--project`, `--jsonl`, and `--host` work as for `context`.

| Flag | Behavior |
|---|---|
| `--term X` (repeatable) | Replace automatic handles with these literal topic tokens. Use paths, exception names, symbols, errors, or commands to narrow a noisy timeline. |
| `--limit N` | Cap timeline candidates (default 20, minimum 1). Prefer early corrections and fail-to-pass results, then fill with early topic mentions; keep chronological order. The final statement and evidence remain visible separately. |
| `--follow-sessions` | Best effort: list later local main sessions in the same cwd whose kept messages mention at least two handles; at most `--limit` sessions. Fetching a remote anchor does not search later remote sessions. |
| `--turn ADDRESS` | Address when using `--jsonl` instead of a positional session ID. |
| `--json` | Emit the same anchor, handles, timeline, final statement, evidence, warnings and follow-session rows as structured JSON. |

Automatic handles rank the claim's prose ahead of code: distinctive topic words,
paths, exception/error strings, quoted identifiers, function/class names, and
short commands. Builtins/library names, shell variables, numbers/single letters,
`/dev/*`, temp paths and mktemp patterns are filtered. Rarity is measured over
kept narrative blocks in the session; a handle present in 40% or more is dropped
once there are at least 20 blocks. Tool detail is a fallback when the claim has
few useful terms; `--term` remains an explicit override.

The forward scan keeps handle mentions plus topic-qualified human corrections
(`no`, `actually`, `wrong`), assistant text/thinking revision phrases
(`root cause`, `the real issue`, `turns out`, `I was wrong`, `instead`), relevant
file writes and commit commands, and results that change a previously failed
command to a passing result. Results are linked by tool-call ID; success requires
an explicit exit code or passing-test text. Write detection reuses provenance's
shell/patch heuristics. Only writes to concrete handle paths, or topic-related
files within the session cwd, count as file evidence; temporary image writes do
not. A commit row records the command invocation, not proof that it succeeded.
Revision phrases require at least one ranked handle. Harness-injected user
entries (task/system/local-command notifications, teammate/cross-session messages)
and tool results never count as human corrections.

After **three consecutive human turns without handles**, the scan stops before
the first of those turns and reports its turn/line. Injected entries do not
advance this counter. Later returns to the topic are excluded after a confirmed
gap; `--follow-sessions` is a separate, optional lookup. This is a lexical boundary
heuristic; the reading agent still judges the explanation.

Claude's parentUuid kept path is shared with `branches` and `provenance`;
Codex/Cursor events are all kept. An abandoned anchor is labelled explicitly,
and only later kept entries are shown. Graph anomalies are reported as warnings.

Human output is a compact timeline:

```text
Anchor: <timestamp> assistant [anchor] The ValueError is probably a stale cache…
  cq context <session> @1
→: <timestamp> user [user-correction] No, that's not it. Actually the schema is wrong.
  cq context <session> @5
→: <timestamp> assistant [assistant-revision] Turns out the real issue is validation…
  cq context <session> @6
Last statement about the topic: …
Last passing run: …
Last edit: …
Last commit command: …
```

Every row has a roughly 200-character snippet and a runnable `context` drill.
JSON contains `session`, `handles`, `anchor` (including `abandoned`, `turn`,
`turn_start_line`, `turn_end_line`),
`revisions[]`, `total_revisions`, `truncated`, `last_statement`,
`evidence: {last_passing_run, last_edit, last_commit}`, `warnings`, `note`, and
`followed_sessions[]`, and `stopped` (`null` or `{turn, line, gap_turns, reason}`).
Rows contain `line`, `timestamp`, `role`, `kind`,
`snippet`, `drill`; passing-run rows also carry the associated `command`.
Missing final evidence is `null`. With no revision signal the output says
**"no later revision found on the kept path"**, even if there are plain mentions.
No extracted handles produces a suggestion to supply `--term`.

### `user` — user messages only

```bash
cq user <session>
cq user <session> --include-sidechain
```

### `raw` — raw JSONL lines

```bash
cq raw <session> --start 0 --end 5      # first 6 lines
cq raw <session> --start 100 --end 110
```

Line numbers match the `msg[N]` positions shown in search results.

### `resume`

```bash
cq resume <session>
cq resume <session> --from-subagent     # for subagent IDs
```

Default: emits a runtime-aware resume command (claude/codex/cursor) wrapped in the right `cd`. For subagent session IDs (`agent-<hex>`): without `--from-subagent`, redirects to the parent session; with the flag, copies the subagent JSONL to a new top-level UUID (rewriting `sessionId` + `isSidechain=false` on every line so `claude --resume` accepts it). Caveat: the resumed session loads the subagent's transcript as history but runs as the MAIN agent — the subagent persona/tools are not restored.

---

## Provenance

```bash
cq provenance <path>
cq provenance <path> --host user@workstation
cq provenance <path> --host h1 --host h2 --show-mentions
```

See `references/provenance.md` for the op-kind taxonomy, output format, and worked examples.
Rewound operations appear under **On abandoned branches (rewound away)**. Every
JSON row has an `abandoned` flag. First/Last authored exclude abandoned edits.
Shell path fragments use resolved paths rather than basename fallbacks; bare
filenames are explicitly labelled as broad matches. Shell rows show intent,
their first matching command and a physical-line drill. JSON includes `intent`,
`first_command` and `first_command_line` for these rows.

---

## Index management

```bash
cq stats                  # index statistics (sessions, messages, DB size, runtimes)
cq index --reindex        # force full rebuild
cq index --project X      # incrementally index only matching projects
```

`stats` is read-only and tolerates a locked DB — if another `cq` process holds the write lock, it reports against the current snapshot rather than crashing.

---

## JSON output

```bash
cq --json search "<query>"
cq --json provenance <path>
cq --json tools <session>
```

JSON is the recommended mode when piping into another tool. The non-TTY auto-detection gives most of the same benefits (no ANSI, single trailing resume block) but `--json` is strictly structured.

---

## Session ID matching

All commands accept partial session IDs (8+ chars usually unique). On ambiguity, the tool lists candidates and exits non-zero.
