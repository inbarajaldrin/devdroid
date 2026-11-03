# Schema reference

The Turn/Block/ToolCall data model `cq` uses for structured inspection, plus the FTS5 schema backing the search index.

## Multi-format support

| Format | Storage | Detection |
|---|---|---|
| Claude Code | `~/.claude/projects/<encoded-cwd>/*.jsonl` | `type: "user" \| "assistant"` |
| Cursor | `~/.cursor/projects/<encoded-cwd>/agent-transcripts/<id>/` | `role: "user" \| "assistant"` |
| Codex CLI | `~/.codex/sessions/<YYYY>/<MM>/<DD>/` | `type: "session_meta"` |

All three are auto-detected and normalized into the same Turn/Block/ToolCall shape.

## Turn

```
Turn {
  index: int,
  user_text: str,
  blocks: [Block],
  timestamp: str,
  is_sidechain: bool,         # inline teammate/subagent block in main session
  system_events?: [str]       # extracted [bg-task: ...] markers
}
```

A Turn = one user message + the assistant's response blocks until the next user message.

## Block

```
Block {
  kind: "text" | "thinking" | "tool_use",
  text?: str,                 # for kind == "text" or "thinking"
  tool_call?: ToolCall,       # for kind == "tool_use"
  timestamp: str
}
```

## ToolCall

```
ToolCall {
  name: str,                  # Bash, Read, Edit, Write, MultiEdit, ...
  input: dict,                # tool-specific inputs
  result: str | null,         # output (null if not captured)
  is_error: bool
}
```

## kind vs is_sidechain (orthogonal)

`cq` tracks two independent dimensions for "this is auxiliary content":

| Column | Source | Marks |
|---|---|---|
| `kind` | session-level: which JSONL file the entry came from | `'main'` (top-level session JSONL) or `'subagent'` (spawned-agent transcript at `<project>/<parent-uuid>/subagents/agent-*.jsonl`) |
| `is_sidechain` | row-level: per-message flag inside a main-session JSONL | `1` when `isSidechain=true` in JSONL OR the row contains an inline `<teammate-message>` block |

The two are orthogonal because the same project can have:

1. A main-session JSONL with some inline sidechain rows (the user spawned a subagent that wrote back into the parent transcript)
2. A separate `subagents/agent-<hex>.jsonl` file (the full spawned-agent transcript stored independently)

Both are excluded from default search; pass `--include-sidechain` to surface inline rows, `--include-subagents` to surface the separate JSONLs. They opt-in independently.

For subagent JSONLs, every row carries `isSidechain=true` natively (by construction). The indexer suppresses that flag for `kind='subagent'` rows so the two filters stay clean — `--include-subagents` alone surfaces them, not `--include-sidechain`.

## FTS5 schema

```
CREATE TABLE messages (
  rowid INTEGER PRIMARY KEY,
  project TEXT,
  session_id TEXT,
  msg_index INTEGER,
  role TEXT,
  depth INTEGER,              # 0 = shallow (chat text), 1 = deep (tool I/O + thinking)
  content TEXT,
  timestamp TEXT,
  is_sidechain INTEGER DEFAULT 0,
  kind TEXT DEFAULT 'main'    # 'main' | 'subagent'
);
CREATE VIRTUAL TABLE messages_fts USING fts5(content, content='messages');
```

```
CREATE TABLE indexed_sessions (
  session_path TEXT PRIMARY KEY,
  mtime REAL,
  msg_count INTEGER,
  byte_offset INTEGER,        # how far the file has been indexed
  line_count INTEGER          # next msg_index (0-based JSONL line)
);
CREATE INDEX idx_messages_session ON messages(session_id, project);
```

`SCHEMA_VERSION = 4`. Upgrades are auto-applied on each `cq` invocation via `ALTER TABLE` migrations; a migration that changes extraction clears `indexed_sessions`, and the background indexer re-indexes everything while searches keep using the old rows.

**Refresh:** transcripts are append-only, so a grown file is indexed from `byte_offset` (only new complete lines). A truncated/rewritten file is re-read in full. Backlogs ≤ 32 MB are indexed inline; larger ones go to one detached `cq index --worker` (lock: `index.lock`, progress: `indexer.status.json`, log: `indexer.log` in the cache dir). `CQ_CACHE_DIR` points cq at a scratch index for testing.

**Extraction:** each session is indexed with ITS provider's extractor (from discovery) — per-line `detect()` only recognises a file's first line. Deep parts (each tool input/output/thinking block) keep their first 6 KB + last 2 KB. Claude `queued_command` attachments (messages typed mid-turn) index as user text. A row is a sidechain only if its own prose starts with `<teammate-message`.

## Format-specific normalization notes

- **Claude Code JSONL** → `type: "user"` is a Turn boundary; `type: "assistant"` blocks become entries in `Turn.blocks`.
- **Cursor JSONL** → `role: "user"` boundaries; `role: "assistant"` content normalizes to `Block` entries.
- **Codex CLI JSONL** → `type: "session_meta"` is the file header; subsequent `type: "message"` and `type: "function_call"` events normalize into Turn/Block/ToolCall.

Normalization is intentionally lossy for some format-specific fields (e.g., Codex's tool-call parameter shape is flattened into the same `input: dict` used by Claude Code). For format-specific raw access, use `cq raw <session>` and pipe to `jq`.

## System event markers

Background task notifications are compressed into single-line markers:

```
[bg-task: <summary>]
```

These appear in `Turn.system_events`, not as separate blocks. Useful for spotting background work without expanding the full notification XML.
