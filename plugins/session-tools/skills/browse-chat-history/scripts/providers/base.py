"""AgentProvider — the adapter interface every agent CLI implements.

The core `cq.py` iterates a registry of these instead of hardcoding
provider-specific branches. To add a new agent (Aider, Continue.dev,
OpenHands, etc.), drop a new file in this directory and register it
in `__init__.py`. Five methods + a small spec are the entire contract.

Design notes:
- Discovery is per-provider (not a shared spec) because each agent's
  filesystem layout diverges enough that a "root + glob" abstraction
  leaks (Claude has subagent walks, Cursor has nested transcripts,
  Codex has YYYY/MM/DD partitioning).
- The two extraction methods (`extract_searchable`, `extract_user_text`)
  return a uniform shape so the rest of cq.py is provider-agnostic.
- `detect` runs against the first valid JSON line of a transcript —
  used by `--jsonl PATH` and `parse_transcript` to identify ad-hoc files.
"""

from __future__ import annotations

from pathlib import Path


DEEP_PART_HEAD = 6_000
DEEP_PART_TAIL = 2_000


def clip_deep(text: str) -> str:
    """Bound ONE tool input/output/thinking part for the deep index tier.

    Clipping per part (not per entry) keeps a long Write body or log dump
    from pushing the command/path that follows it out of the index, and
    keeping the tail preserves the traceback/error line at the end of a
    long tool result — the part people search for.
    """
    if len(text) <= DEEP_PART_HEAD + DEEP_PART_TAIL:
        return text
    return text[:DEEP_PART_HEAD] + "\n…\n" + text[-DEEP_PART_TAIL:]


class AgentProvider:
    """Contract for an agent-CLI transcript adapter.

    Subclasses must override `name`, `group_label`, `sessions_root`, and
    the four methods below. The class is intentionally minimal — anything
    that's provider-specific lives here; everything else is in core cq.py.
    """

    # ── Identity ────────────────────────────────────────────────────────
    name: str = ""                     # short, lowercase: "claude-code", "cursor", "codex"
    group_label: str = ""              # display string: "Claude Code", "Cursor", "Codex CLI"
    sessions_root: Path = Path("/")    # absolute discovery root

    # ── Discovery ───────────────────────────────────────────────────────
    def discover(self, project_filter: str | None = None) -> list[dict]:
        """Walk the provider's sessions root, return session dicts.

        Each dict has:
          - path: Path to JSONL
          - project: project-name key (encoded cwd, or YYYY-MM-DD for Codex)
          - group: self.group_label (display name)
          - session_id: filename stem
          - kind: "main" or "subagent"

        Implementations are free to add provider-specific extras (e.g.
        Codex date partitioning), but the five keys above are required.
        """
        raise NotImplementedError

    # ── Format detection ────────────────────────────────────────────────
    def detect(self, first_obj: dict) -> bool:
        """True if `first_obj` (parsed first JSON line) is this format."""
        raise NotImplementedError

    # ── Per-entry text extraction (for FTS5 indexing) ───────────────────
    def extract_searchable(self, entry: dict) -> tuple[str, str, str]:
        """Return (role, shallow_text, deep_text) for one JSONL entry.

        - role: "user" | "assistant" | "" (skip when empty)
        - shallow_text: user-facing chat content (what shows in the UI)
        - deep_text: tool inputs/outputs/thinking (only searched with --tier deep)

        Empty strings are valid — the indexer just skips empty rows.
        """
        raise NotImplementedError

    # ── Resume CLI builder ──────────────────────────────────────────────
    def build_resume_cmd(self, session_id: str, cwd: str) -> str:
        """Construct the shell command to resume this session.

        Each runtime has its own CLI (`claude --resume <uuid>`,
        `codex resume <uuid>`, `cursor-agent --resume <chatId>`). The
        `cwd` parameter prefixes a `cd "<cwd>" &&` when non-empty so the
        resume picks up the right project context.
        """
        raise NotImplementedError

    # ── Subagent / sidechain layout (optional) ──────────────────────────
    def subagent_paths(self, project_dir: Path) -> list[Path]:
        """Return spawned-agent transcript paths under a project dir.

        Default: no subagent layout. Override when the provider stores
        subagent transcripts as separate JSONLs (Claude Code does this
        at <project>/<parent-uuid>/subagents/agent-*.jsonl).
        """
        return []

    # ── Turn boundaries (semantic split points) ─────────────────────────
    def is_turn_boundary(self, entry: dict) -> bool:
        """True if `entry` starts a new logical Turn in the transcript.

        Used by parse_transcript / parse_codex_transcript to decide where
        to break the entry stream into navigable Turns. The default
        (Claude/Cursor-shaped) treats user messages as boundaries. Codex
        overrides because its transcripts are typically one-shot with
        many assistant beats per task — splitting per assistant beat
        gives a more useful navigation surface.

        Default returns False; concrete providers override.
        """
        return False
