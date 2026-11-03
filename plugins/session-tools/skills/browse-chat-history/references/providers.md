# Provider adapters — adding a new agent CLI

`cq` indexes and parses transcripts from multiple agent CLIs through a thin adapter pattern. Each provider lives in `scripts/providers/<name>.py` as a subclass of `AgentProvider`; the registry in `scripts/providers/__init__.py` is the single source of truth for "which CLIs does cq know about."

Adding support for a new agent (Aider, Continue.dev, OpenHands, Goose, custom internal CLI, …) is a single-file change plus one line in the registry.

## The interface

```python
class AgentProvider:
    name: str             # short slug — "claude-code", "cursor", "codex"
    group_label: str      # display label — "Claude Code", "Codex CLI"
    sessions_root: Path   # discovery root — ~/.<cli>/sessions or similar

    def discover(self, project_filter=None) -> list[dict]: ...
    def detect(self, first_obj: dict) -> bool: ...
    def extract_searchable(self, entry: dict) -> tuple[str, str, str]: ...
    def build_resume_cmd(self, session_id: str, cwd: str) -> str: ...
    def subagent_paths(self, project_dir: Path) -> list[Path]:  # optional, default []
        ...
```

## What each method does

| Method | Returns | Used by |
|---|---|---|
| `discover(project_filter)` | List of `{path, project, group, session_id, kind}` dicts | `discover_sessions()` → `list`, `recent`, `search`, indexer |
| `detect(first_obj)` | `True` if the first JSON line of a transcript matches this provider's shape | `detect_format()`, `extract_searchable_text()` dispatch |
| `extract_searchable(entry)` | `(role, shallow_text, deep_text)` for one JSONL entry | FTS5 indexer |
| `build_resume_cmd(session_id, cwd)` | Shell command string (e.g. `cd "<cwd>" && <cli> resume <id>`) | `resume`, inline resume hints in `search`/`recent` output |
| `subagent_paths(project_dir)` | List of subagent transcript paths under one project dir | `discover()` (Claude Code), provenance walk |

## The contract

- **Detection has to be unambiguous.** If your provider's shape overlaps another's, your `detect()` must explicitly exclude the overlap (see `CursorProvider.detect`, which returns False when Claude Code's `type=user|assistant` is present even though Cursor's `role` field is also there).
- **`extract_searchable` returns raw text.** Cleanup (ANSI stripping, system-reminder removal, length caps) happens once in `cq.py`'s shared wrapper — don't duplicate it per provider.
- **`role` must be `"user"`, `"assistant"`, or `""`.** Empty string means "skip this entry" (e.g., Codex `task_started` events have no searchable text).
- **`kind` is `"main"` or `"subagent"`.** Provider decides which based on the transcript's filesystem location, not its contents.

## Adding a new provider — step by step

1. **Create `scripts/providers/<name>.py`** with one class subclassing `AgentProvider`. Set the 3 class attributes + implement the 4 required methods.
2. **Register it in `scripts/providers/__init__.py`** — add the import and append an instance to `REGISTRY`. Order matters when shapes overlap: list more-specific detectors first.
3. **(Optional) Override `subagent_paths()`** if the CLI stores spawned-agent transcripts as separate JSONLs.
4. **Smoke test:** `python3 scripts/cq.py info --jsonl /path/to/your/sample.jsonl` should report `runtime: <your group_label>` and parse without errors.
5. **Index test:** `python3 scripts/cq.py index --reindex --project <your-test-project>` should pull your sessions into the FTS5 cache. `cq stats` then shows them under your `group_label`.

## Reference implementations

| File | What it shows |
|---|---|
| `providers/claude_code.py` | Standard JSONL shape (`type` + `message.content` blocks) + subagent layout via `subagent_paths()` |
| `providers/cursor.py` | Variant shape (`role` instead of `type`) with an *anti-claim* in `detect()` to avoid colliding with Claude Code |
| `providers/codex.py` | Fundamentally different shape (`response_item` + `event_msg` events with `payload.type` dispatch); also overrides `build_resume_cmd` to extract a UUID from a longer filename |

The three reference providers cover the common patterns. New providers usually start by copying the one whose shape is closest, then adjusting.

## What stays in `cq.py` (NOT in providers/)

- FTS5 indexing logic (provider-agnostic — every provider's `(role, shallow, deep)` tuple flows through the same DB schema)
- Turn/Block/ToolCall normalization for display (uses provider's `extract_searchable` output but renders identically)
- Search ranking, dedup, multi-concept AND, date filters, `--host` SSH walks, `--jsonl PATH` ad-hoc inspection, provenance walks
- All CLI argument parsing, output formatting, ANSI handling

If something feels provider-specific but doesn't fit the 4 methods above, that's a signal the base interface needs a new method — not that you should add a branch in `cq.py`. Open a discussion before adding methods to `AgentProvider` to avoid the interface drifting back into a god-object.
