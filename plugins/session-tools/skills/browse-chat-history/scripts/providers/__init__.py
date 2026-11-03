"""Agent provider registry — single source of truth for which CLIs cq knows.

Adding a new provider: create `providers/<name>.py` with a subclass of
AgentProvider, then append an instance to REGISTRY below. No other files
need to change — the core in cq.py iterates this list.
"""

from __future__ import annotations

from .base import AgentProvider
from .claude_code import ClaudeCodeProvider
from .codex import CodexProvider
from .cursor import CursorProvider


REGISTRY: list[AgentProvider] = [
    ClaudeCodeProvider(),
    CursorProvider(),
    CodexProvider(),
]


def by_name(name: str) -> AgentProvider | None:
    """Lookup provider by its short name (e.g. 'claude-code', 'cursor')."""
    for p in REGISTRY:
        if p.name == name:
            return p
    return None


def by_group_label(label: str) -> AgentProvider | None:
    """Lookup provider by display label (e.g. 'Claude Code', 'Codex CLI')."""
    for p in REGISTRY:
        if p.group_label == label:
            return p
    return None


def detect(first_obj: dict) -> AgentProvider | None:
    """Return the first provider whose `detect()` returns True for first_obj."""
    for p in REGISTRY:
        try:
            if p.detect(first_obj):
                return p
        except Exception:
            continue
    return None


__all__ = [
    "AgentProvider",
    "ClaudeCodeProvider",
    "CursorProvider",
    "CodexProvider",
    "REGISTRY",
    "by_name",
    "by_group_label",
    "detect",
]
