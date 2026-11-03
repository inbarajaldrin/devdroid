"""Codex CLI provider — sessions under ~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-*.jsonl.

Codex uses a fundamentally different JSONL shape from Claude/Cursor:
top-level events are either `response_item` (LLM-side messages, tool
calls, reasoning) or `event_msg` (user-facing chat events). The
extraction logic mirrors what the old `_extract_codex` did in cq.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from .base import AgentProvider, clip_deep


# Codex stores sessions as rollout-<timestamp>-<UUID>.jsonl. The CLI's
# `codex resume` command takes only the UUID, not the full filename.
_CODEX_UUID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)


class CodexProvider(AgentProvider):
    name = "codex"
    group_label = "Codex CLI"
    sessions_root = Path.home() / ".codex" / "sessions"

    def discover(self, project_filter=None):
        sessions = []
        if not self.sessions_root.exists():
            return sessions
        for year_dir in sorted(self.sessions_root.iterdir()):
            if not year_dir.is_dir():
                continue
            for month_dir in sorted(year_dir.iterdir()):
                if not month_dir.is_dir():
                    continue
                for day_dir in sorted(month_dir.iterdir()):
                    if not day_dir.is_dir():
                        continue
                    for f in sorted(day_dir.glob("*.jsonl")):
                        sessions.append({
                            "path": f,
                            "project": f"{year_dir.name}-{month_dir.name}-{day_dir.name}",
                            "group": self.group_label,
                            "session_id": f.stem,
                            "kind": "main",
                        })
        return sessions

    def detect(self, first_obj):
        return first_obj.get("type") == "session_meta"

    def extract_searchable(self, entry):
        """Codex CLI events: response_item (LLM-side) or event_msg (user-facing)."""
        entry_type = entry.get("type", "")
        payload = entry.get("payload") or {}
        if not isinstance(payload, dict):
            return ("", "", "")
        ptype = payload.get("type", "")
        role = ""
        shallow_parts: list[str] = []
        deep_parts: list[str] = []

        if entry_type == "event_msg":
            if ptype == "user_message":
                msg = payload.get("message", "")
                if isinstance(msg, str):
                    # Strip Codex's "## My request for Codex:" header.
                    marker = "## My request for Codex:"
                    if marker in msg:
                        msg = msg.split(marker, 1)[1]
                    shallow_parts.append(msg)
                    role = "user"
            elif ptype == "agent_message":
                msg = payload.get("message", "")
                if isinstance(msg, str):
                    shallow_parts.append(msg)
                    role = "assistant"
            return (role, "\n".join(shallow_parts), "\n".join(clip_deep(p) for p in deep_parts))

        if entry_type == "response_item":
            if ptype == "message":
                msg_role = payload.get("role", "")
                content = payload.get("content", [])
                if isinstance(content, list):
                    if msg_role == "user":
                        text = "\n".join(b.get("text", "") for b in content
                                         if isinstance(b, dict) and b.get("type") == "input_text")
                        if text.strip():
                            shallow_parts.append(text)
                            role = "user"
                    elif msg_role == "assistant":
                        text = "\n".join(b.get("text", "") for b in content
                                         if isinstance(b, dict) and b.get("type") == "output_text")
                        if text.strip():
                            # phase='commentary' = thinking-style; treat as deep
                            if payload.get("phase") == "commentary":
                                deep_parts.append(text)
                            else:
                                shallow_parts.append(text)
                            role = "assistant"
            elif ptype == "function_call":
                role = "assistant"
                name = payload.get("name", "")
                args = payload.get("arguments", "")
                if name:
                    deep_parts.append(name)
                if isinstance(args, str):
                    deep_parts.append(args)
            elif ptype == "function_call_output":
                role = "user"  # tool results come back under user role conceptually
                output = payload.get("output", "")
                if isinstance(output, str):
                    deep_parts.append(output)
            elif ptype == "custom_tool_call":
                role = "assistant"
                name = payload.get("name", "")
                inp = payload.get("input", "")
                if name:
                    deep_parts.append(name)
                if isinstance(inp, str):
                    deep_parts.append(inp)
            elif ptype == "custom_tool_call_output":
                role = "user"
                output = payload.get("output", "")
                if isinstance(output, str):
                    deep_parts.append(output)
            elif ptype == "web_search_call":
                role = "assistant"
                query = payload.get("query") or payload.get("search_query") or ""
                if isinstance(query, str):
                    deep_parts.append(query)
            elif ptype == "reasoning":
                role = "assistant"
                summary = payload.get("summary", [])
                if isinstance(summary, list):
                    for item in summary:
                        if isinstance(item, dict) and item.get("type") == "summary_text":
                            text = item.get("text", "")
                            if isinstance(text, str):
                                deep_parts.append(text)
            return (role, "\n".join(shallow_parts), "\n".join(clip_deep(p) for p in deep_parts))

        return ("", "", "")

    def build_resume_cmd(self, session_id, cwd):
        # Extract UUID from rollout-<timestamp>-<UUID> filename.
        m = _CODEX_UUID_RE.search(session_id)
        uuid = m.group(1) if m else session_id
        cmd = f"codex resume {uuid}"
        if cwd:
            return f'cd "{cwd}" && {cmd}'
        return cmd

    def is_turn_boundary(self, entry):
        """Each narrative beat starts a new Turn for Codex.

        Codex sessions are typically one-shot — a single `task_started` →
        `task_complete` cycle with many response_items. Rather than
        bundling everything into 1 Turn, split per narrative beat so
        `cq context <session> <N>` actually navigates:

          - `event_msg user_message` (rare; mid-task user injection)
          - `response_item message role=assistant` (each agent reply)

        Tool calls and outputs attach to the most recent turn and don't
        start new ones. `task_started` / `task_complete` are session-
        level markers handled by the parser, not turn boundaries.
        """
        etype = entry.get("type", "")
        payload = entry.get("payload", {}) or {}
        if etype == "event_msg" and payload.get("type") == "user_message":
            return True
        if etype == "response_item" and payload.get("type") == "message":
            return payload.get("role") == "assistant"
        return False
