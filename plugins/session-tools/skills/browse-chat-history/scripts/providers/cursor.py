"""Cursor provider — transcripts under ~/.cursor/projects/<encoded-cwd>/agent-transcripts/<id>/.

Cursor's JSONL shape is *almost* identical to Claude Code's (same
message.content block layout) but uses a top-level `role` field instead
of `type`. Extraction reuses Claude Code's block logic after the role
synthesis.
"""

from __future__ import annotations

from pathlib import Path

from .base import AgentProvider, clip_deep


class CursorProvider(AgentProvider):
    name = "cursor"
    group_label = "Cursor"
    sessions_root = Path.home() / ".cursor" / "projects"

    def discover(self, project_filter=None):
        sessions = []
        if not self.sessions_root.exists():
            return sessions
        for proj_dir in sorted(self.sessions_root.iterdir()):
            if not proj_dir.is_dir():
                continue
            if project_filter and project_filter.lower() not in proj_dir.name.lower():
                continue
            transcripts_dir = proj_dir / "agent-transcripts"
            if not transcripts_dir.exists():
                continue
            for session_dir in sorted(transcripts_dir.iterdir()):
                if not session_dir.is_dir():
                    continue
                jsonl = session_dir / "transcript.jsonl"
                if not jsonl.exists():
                    jsonl = session_dir / f"{session_dir.name}.jsonl"
                if jsonl.exists():
                    sessions.append({
                        "path": jsonl,
                        "project": proj_dir.name,
                        "group": self.group_label,
                        "session_id": session_dir.name,
                        "kind": "main",
                    })
        return sessions

    def detect(self, first_obj):
        # Cursor uses top-level `role` (no `type`). Avoid claiming files that
        # ALSO have `type=user|assistant` — those are Claude Code's.
        return (first_obj.get("role") in ("user", "assistant")
                and first_obj.get("type") not in ("user", "assistant"))

    def extract_searchable(self, entry):
        role = entry.get("role")
        if role not in ("user", "assistant"):
            return ("", "", "")
        message = entry.get("message", {})
        content = message.get("content", "")
        shallow_parts: list[str] = []
        deep_parts: list[str] = []

        if isinstance(content, str):
            shallow_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    shallow_parts.append(block.get("text", ""))
                elif btype == "thinking":
                    deep_parts.append(block.get("thinking", ""))
                elif btype == "tool_use":
                    inp = block.get("input", {})
                    if isinstance(inp, dict):
                        for v in inp.values():
                            if isinstance(v, str):
                                deep_parts.append(v)
                elif btype == "tool_result":
                    out = block.get("content", "")
                    if isinstance(out, str):
                        deep_parts.append(out)
                    elif isinstance(out, list):
                        for b in out:
                            if isinstance(b, dict) and isinstance(b.get("text"), str):
                                deep_parts.append(b["text"])
        return (role, "\n".join(shallow_parts), "\n".join(clip_deep(p) for p in deep_parts))

    def build_resume_cmd(self, session_id, cwd):
        cmd = f"cursor-agent --resume {session_id}"
        if cwd:
            return f'cd "{cwd}" && {cmd}'
        return cmd

    def is_turn_boundary(self, entry):
        """User-role entries that aren't tool-result-only start a Turn."""
        if entry.get("role") != "user":
            return False
        content = (entry.get("message") or {}).get("content", "")
        if isinstance(content, list) and all(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in content
        ):
            return False
        return True
