"""Claude Code provider — sessions under ~/.claude/projects/<encoded-cwd>/*.jsonl.

Subagent transcripts live one level deeper at
<project>/<parent-uuid>/subagents/agent-*.jsonl and are tagged with
kind='subagent' so they can be filtered independently of inline
<teammate-message> sidechain rows.
"""

from __future__ import annotations

from pathlib import Path

from .base import AgentProvider, clip_deep


class ClaudeCodeProvider(AgentProvider):
    name = "claude-code"
    group_label = "Claude Code"
    sessions_root = Path.home() / ".claude" / "projects"

    def discover(self, project_filter=None):
        sessions = []
        if not self.sessions_root.exists():
            return sessions
        for proj_dir in sorted(self.sessions_root.iterdir()):
            if not proj_dir.is_dir() or proj_dir.name.startswith("."):
                continue
            if project_filter and project_filter.lower() not in proj_dir.name.lower():
                continue
            for f in sorted(proj_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):
                sessions.append({
                    "path": f,
                    "project": proj_dir.name,
                    "group": self.group_label,
                    "session_id": f.stem,
                    "kind": "main",
                })
            for sub_jsonl in sorted(self.subagent_paths(proj_dir),
                                    key=lambda p: p.stat().st_mtime):
                sessions.append({
                    "path": sub_jsonl,
                    "project": proj_dir.name,
                    "group": self.group_label,
                    "session_id": sub_jsonl.stem,  # "agent-<hex>"
                    "kind": "subagent",
                })
        return sessions

    def subagent_paths(self, project_dir):
        return list(project_dir.glob("*/subagents/agent-*.jsonl"))

    def detect(self, first_obj):
        return first_obj.get("type") in ("user", "assistant")

    def extract_searchable(self, entry):
        """Claude Code: top-level type=user|assistant, message.content."""
        entry_type = entry.get("type", "")
        # A message the human typed while the agent was mid-turn is stored as an
        # `attachment` of type queued_command, not a user entry — index it as
        # user prose or the human's own words are unsearchable.
        if entry_type == "attachment":
            att = entry.get("attachment")
            if isinstance(att, dict) and att.get("type") == "queued_command":
                prompt = att.get("prompt")
                if isinstance(prompt, list):  # pasted images arrive as content blocks
                    prompt = "\n".join(b.get("text", "") for b in prompt
                                       if isinstance(b, dict) and isinstance(b.get("text"), str))
                if isinstance(prompt, str) and prompt.strip():
                    return ("user", prompt, "")
            return ("", "", "")
        if entry_type not in ("user", "assistant"):
            return ("", "", "")
        message = entry.get("message", {})
        content = message.get("content", "")
        shallow_parts: list[str] = []
        deep_parts: list[str] = []
        role = entry_type

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
                        for k, v in inp.items():
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

        # AskUserQuestion (form) answers: the human's selections / typed text live
        # in entry['toolUseResult']['answers'] ({question: answer}), NOT in a text
        # block. Promote them to the SHALLOW (user-prose) tier so a form answer is
        # findable as human-authored content, not buried as opaque tool output.
        tur = entry.get("toolUseResult")
        if entry_type == "user" and isinstance(tur, dict):
            answers = tur.get("answers")
            if isinstance(answers, dict):
                for ans in answers.values():
                    if isinstance(ans, str) and ans.strip() and ans.strip() != "(notes only)":
                        shallow_parts.append(ans.strip())
            # Free-text notes (the real content when answers == "(notes only)")
            annotations = tur.get("annotations")
            if isinstance(annotations, dict):
                for ann in annotations.values():
                    if isinstance(ann, dict) and isinstance(ann.get("notes"), str) and ann["notes"].strip():
                        shallow_parts.append(ann["notes"].strip())

        return (role, "\n".join(shallow_parts), "\n".join(clip_deep(p) for p in deep_parts))

    def build_resume_cmd(self, session_id, cwd):
        cmd = f"claude --resume {session_id}"
        if cwd:
            return f'cd "{cwd}" && {cmd}'
        return cmd

    def is_turn_boundary(self, entry):
        """User-message entries that aren't tool-result-only start a Turn."""
        if entry.get("type") != "user":
            return False
        content = (entry.get("message") or {}).get("content", "")
        # Tool-result-only messages are continuations, not new turns.
        if isinstance(content, list) and all(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in content
        ):
            return False
        return True
