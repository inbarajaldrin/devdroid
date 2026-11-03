#!/usr/bin/env python3
"""cq — Claude Query: structured search & inspection of AI session transcripts.

Combines FTS5 search (cross-project, shallow/deep cascade) with a Turn/Block/ToolCall
parser (ported from claude-replay) for structured querying.

Supports Claude Code, Cursor, and Codex CLI transcript formats.

Usage:
    cq search "query" [options]       # FTS5 full-text search across all sessions
    cq list [options]                 # List sessions with metadata
    cq recent [N]                     # Last N active sessions with resume commands
    cq turns <session> [options]      # Show structured turns from a session
    cq tools <session> [options]      # Tool calls WITH results from a session
    cq thinking <session>             # Extract thinking/reasoning blocks
    cq files <session>                # Files touched (from tool inputs)
    cq context <session> <turn-num>   # Full turn detail with all blocks
    cq why <session> <turn>           # Follow revisions to the latest evidence
    cq user <session>                 # All user messages
    cq resume <session>               # Print resume command
    cq stats                          # Index statistics
    cq index [--reindex]              # Force re-index
"""

from __future__ import annotations
import argparse
import base64
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# Provider registry — each agent CLI (Claude Code, Cursor, Codex, …) is an
# AgentProvider in scripts/providers/. The core below iterates the registry
# instead of hardcoding three providers. To add a new provider, drop a file
# in providers/ and register it in providers/__init__.py.
sys.path.insert(0, str(Path(__file__).parent))
from providers import REGISTRY as PROVIDERS, by_group_label, detect as detect_provider


# Agents pipe stdout. Suppress ANSI + per-result resume hints in non-TTY to
# keep output parseable. NO_COLOR escape hatch (https://no-color.org/).
USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
AGENT_MODE = not sys.stdout.isatty()


def _bold(s):
    return f"\033[1m{s}\033[0m" if USE_COLOR else s


def _dim(s):
    return f"\033[2m{s}\033[0m" if USE_COLOR else s


_REMOTE_CACHE = Path.home() / ".claude" / ".search-cache" / "remote"


def _remote_locate_session(host: str, session_id: str) -> str | None:
    """SSH to host and locate a JSONL by partial session-id match. Returns the
    remote absolute path, or None if not found / SSH failed."""
    remote_cmd = (
        "find ~/.claude/projects -maxdepth 3 -type f -name "
        f"{shlex.quote(session_id + '*.jsonl')} -print -quit"
    )
    try:
        proc = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", host, remote_cmd],
            capture_output=True, text=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        print(f"ssh {host}: timed out locating session", file=sys.stderr)
        return None
    except FileNotFoundError:
        print("ssh not found on PATH", file=sys.stderr)
        return None
    if proc.returncode != 0:
        msg = proc.stderr.strip() or f"ssh exited {proc.returncode}"
        print(f"ssh {host}: {msg}", file=sys.stderr)
        return None
    path = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    return path or None


def _fetch_remote_session(host: str, session_id: str) -> dict | None:
    """Pull a remote session JSONL into the local cache.

    Returns a session dict in the same shape `find_session` produces, so every
    command that accepts a session by id can be reused unmodified.

    Cache path: ~/.claude/.search-cache/remote/<host>/<project>/<uuid>.jsonl
    The remote path encoding is preserved so multiple sessions from the same
    host don't collide.
    """
    remote_path = _remote_locate_session(host, session_id)
    if not remote_path:
        print(
            f"No session matching '{session_id}' under {host}:~/.claude/projects",
            file=sys.stderr,
        )
        return None
    parts = remote_path.split("/")
    try:
        project = parts[parts.index("projects") + 1]
    except (ValueError, IndexError):
        project = "remote"
    fname = Path(remote_path).name
    uuid = fname[:-len(".jsonl")] if fname.endswith(".jsonl") else fname
    cache_dir = _REMOTE_CACHE / host / project
    cache_dir.mkdir(parents=True, exist_ok=True)
    local_path = cache_dir / fname
    # Stream via ssh cat to avoid an scp dependency.
    with local_path.open("wb") as fh:
        proc = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
             host, "cat", shlex.quote(remote_path)],
            stdout=fh, stderr=subprocess.PIPE, timeout=300,
        )
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", errors="replace").strip()
        print(f"ssh {host}: fetch failed: {msg}", file=sys.stderr)
        try:
            local_path.unlink()
        except FileNotFoundError:
            pass
        return None
    return {
        "session_id": uuid,
        "project": project,
        "path": local_path,
        "group": f"{host}:claude",
        "remote_host": host,
        "remote_path": remote_path,
    }


def _resolve_jsonl_path(jsonl_path: str) -> dict | None:
    """Build a synthetic session dict pointing at an arbitrary JSONL path.

    Used by `--jsonl PATH` to inspect transcripts that aren't auto-discovered
    under ~/.claude/projects/ (e.g., a session JSONL received via Slack or
    Syncthing). Detects subagent layout when the path matches
    <project>/<parent-uuid>/subagents/agent-*.jsonl, and sniffs the runtime
    from the first JSON line so info/context emit the right group label and
    runtime-specific hints fire correctly.
    """
    p = Path(jsonl_path).expanduser().resolve()
    if not p.exists():
        print(f"--jsonl: file not found: {p}", file=sys.stderr)
        return None
    if not p.is_file():
        print(f"--jsonl: not a regular file: {p}", file=sys.stderr)
        return None
    name = p.name
    stem = name[:-len(".jsonl")] if name.endswith(".jsonl") else name
    kind = "subagent" if p.parent.name == "subagents" and stem.startswith("agent-") else "main"

    # Sniff runtime by asking each provider's detect() against the first
    # valid JSON line. Defaults to Claude Code if no provider claims it.
    group = "Claude Code"
    try:
        with open(p) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                provider = detect_provider(obj)
                if provider:
                    group = provider.group_label
                break
    except OSError:
        pass

    return {
        "path": p,
        "project": "[ad-hoc]",
        "group": group,
        "session_id": f"[ad-hoc:{stem}]",
        "kind": kind,
    }


def _resolve_session(args):
    """Branch on --jsonl / --host: ad-hoc path, remote fetch, or local lookup.

    Drop-in replacement for `find_session(args.session, args.project)` in every
    cmd_* handler. The returned dict shape is identical so downstream code
    (`session["path"].read_text()`, etc.) doesn't need to know whether the
    session is local, remote-cached, or an arbitrary file the caller pointed at.

    Exits with a friendly error if neither `session` (positional) nor `--jsonl`
    was provided — argparse can't enforce "XOR" on a positional cleanly.
    """
    jsonl = getattr(args, "jsonl", None)
    if jsonl:
        return _resolve_jsonl_path(jsonl)
    session = getattr(args, "session", None)
    if not session:
        print("error: provide a session ID (positional) or --jsonl PATH.",
              file=sys.stderr)
        sys.exit(1)
    host = getattr(args, "host", None)
    if host:
        found = _fetch_remote_session(host, session)
    else:
        found = find_session(session, args.project)
    if not found:
        sys.exit(1)  # the lookup already said why; a missing session is an error, not "no output"
    return found


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_iso_date(value):
    """argparse type for YYYY-MM-DD. Returns the original string so it can be
    used directly in lexicographic SQL comparisons against ISO-8601 timestamps.
    """
    if not _ISO_DATE_RE.match(value or ""):
        raise argparse.ArgumentTypeError(
            f"expected YYYY-MM-DD, got {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"not a valid calendar date: {value!r}")
    return value


def _before_to_exclusive_upper(before_str):
    """Translate --before YYYY-MM-DD (inclusive-of-that-day) into a strict-<
    upper bound by adding one day. Ensures '2026-04-12' means 'through the end
    of Apr 12'. Handles month/year rollover via datetime.date arithmetic.
    """
    if not before_str:
        return None
    next_day = date.fromisoformat(before_str) + timedelta(days=1)
    return next_day.isoformat()


# ── Config ─────────────────────────────────────────────────────────────────
# Provider-specific paths (sessions roots, resume formats, JSONL shapes) all
# live in scripts/providers/. Anything below here is provider-agnostic.

# Provider name (CLI flag) → group label produced by discover_sessions().
# Derived from the registry so adding a provider doesn't require touching this.
PROVIDER_GROUPS = {p.name.split("-")[0]: p.group_label for p in PROVIDERS}

# CQ_CACHE_DIR points a test run at a scratch index instead of the shared one.
CACHE_DIR = Path(os.environ.get("CQ_CACHE_DIR") or Path.home() / ".claude" / ".search-cache")
FTS_DB = CACHE_DIR / "sessions-fts.db"


# ── Resume-command construction (multi-runtime) ────────────────────────────


def build_resume_command(session_id, cwd, group):
    """Construct the runtime-correct resume command for a session.

    Delegates to the AgentProvider registered for `group`. Falls back to
    Claude Code's resume shape if the group is unknown (defensive — keeps
    behaviour stable when a session somehow has a misspelled or stale
    group label).
    """
    provider = by_group_label(group)
    if provider is None:
        provider = by_group_label("Claude Code")
    return provider.build_resume_cmd(session_id, cwd)


# ── Turn Parser (ported from claude-replay parser.mjs) ─────────────────────


def clean_system_tags(text):
    """Strip system tags, extract slash commands, clean up notifications."""
    # Task notifications → compact marker
    text = re.sub(
        r"<task-notification>\s*<task-id>[^<]*</task-id>\s*"
        r"<output-file>[^<]*</output-file>\s*"
        r"<status>([^<]*)</status>\s*"
        r"<summary>([^<]*)</summary>\s*</task-notification>",
        lambda m: f"[bg-task: {m.group(2)}]",
        text,
    )
    text = re.sub(r"\n*Read the output file to retrieve the result:[^\n]*", "", text)
    # Cursor user_query unwrap
    text = re.sub(
        r"<user_query>([\s\S]*?)</user_query>\s*",
        lambda m: m.group(1).strip(),
        text,
    )
    # Remove system/IDE/caveat blocks
    text = re.sub(r"<system-reminder>[\s\S]*?</system-reminder>\s*", "", text)
    text = re.sub(r"<ide_opened_file>[\s\S]*?</ide_opened_file>\s*", "", text)
    text = re.sub(r"<local-command-caveat>[\s\S]*?</local-command-caveat>\s*", "", text)
    # Slash commands
    text = re.sub(
        r"<command-name>([\s\S]*?)</command-name>\s*",
        lambda m: m.group(1).strip() + "\n",
        text,
    )
    text = re.sub(r"<command-message>[\s\S]*?</command-message>\s*", "", text)
    text = re.sub(r"<command-args>\s*</command-args>\s*", "", text)
    text = re.sub(
        r"<command-args>([\s\S]*?)</command-args>\s*",
        lambda m: (m.group(1).strip() + "\n") if m.group(1).strip() else "",
        text,
    )
    text = re.sub(r"<local-command-stdout>[\s\S]*?</local-command-stdout>\s*", "", text)
    return text.strip()


def extract_user_text(content):
    """Extract plain text from user message content (string or block array)."""
    if isinstance(content, str):
        return clean_system_tags(content)
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return clean_system_tags("\n".join(parts))


def is_tool_result_only(content):
    """Check if message contains only tool_result blocks."""
    if isinstance(content, str):
        return False
    if not isinstance(content, list):
        return False
    return all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def extract_form_answers(entry):
    """Surface AskUserQuestion (form) answers as human-authored text.

    A form answer is stored as a tool_result under a user-role entry. The human's
    input lives in ``entry['toolUseResult']``: ``answers`` ({question: selection})
    AND/OR ``annotations`` ({question: {notes: "free text"}}) — NOT in a ``text``
    block. When the human only typed free-text notes, ``answers`` is the sentinel
    "(notes only)" and the REAL content is in ``annotations[q]['notes']`` — so both
    must be read or the substance is lost. Without this the whole entry is skipped
    in turn assembly and invisible to ``cq user`` / ``cq context``. '' when not a form.
    """
    tur = entry.get("toolUseResult")
    if not isinstance(tur, dict):
        return ""
    answers = tur.get("answers") if isinstance(tur.get("answers"), dict) else {}
    annotations = tur.get("annotations") if isinstance(tur.get("annotations"), dict) else {}
    if not answers and not annotations:
        return ""
    questions = list(answers.keys()) + [q for q in annotations if q not in answers]
    parts = []
    for q in questions:
        a = answers.get(q)
        a = a.strip() if isinstance(a, str) else a
        ann = annotations.get(q)
        note = (ann.get("notes") or "").strip() if isinstance(ann, dict) else ""
        val = a if (a and a != "(notes only)") else ""
        if note:
            val = f"{val} — notes: {note}" if val else note
        if val:
            parts.append(f"[form answer] {val}" + (f"  (Q: {q})" if q else ""))
    return "\n".join(parts)


def _form_answers_in_blocks(blocks):
    """(user_text, timestamp) for each AskUserQuestion answer stashed on a block's
    tool_call by attach_tool_results — the human's form input, surfaced as a turn."""
    out = []
    for b in blocks:
        tc = b.get("tool_call") if isinstance(b, dict) else None
        if isinstance(tc, dict) and tc.get("form_answers"):
            out.append((tc["form_answers"], tc.get("result_timestamp", "")))
    return out


def detect_format(text):
    """Detect transcript format from first valid JSON line.

    Asks each provider's detect() in registry order and returns the first
    match's `name`. "replay" is a legacy pre-parser format kept as a
    hardcoded special case (no provider).
    """
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if "user_text" in obj and "blocks" in obj:
            return "replay"
        provider = detect_provider(obj)
        if provider:
            return provider.name
    return "unknown"


def parse_jsonl(text):
    """Read JSONL and return normalized entries + detected format."""
    entries = []
    fmt = "unknown"
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue

        top_type = obj.get("type")
        if top_type in ("user", "assistant"):
            if fmt == "unknown":
                fmt = "claude-code"
            entries.append(obj)
        elif top_type is None:
            # Cursor format: { role, message: { content } }
            role = (obj.get("message") or {}).get("role") or obj.get("role")
            if role in ("user", "assistant"):
                if fmt == "unknown":
                    fmt = "cursor"
                entries.append({
                    "type": role,
                    "message": {"role": role, "content": (obj.get("message") or {}).get("content", "")},
                    "timestamp": obj.get("timestamp"),
                })
    return entries, fmt


def collect_assistant_blocks(entries, start):
    """Collect all assistant content blocks starting from index `start`.
    Returns (blocks, next_index)."""
    blocks = []
    seen = set()
    i = start

    while i < len(entries):
        entry = entries[i]
        role = (entry.get("message") or {}).get("role") or entry.get("type")
        if role != "assistant":
            break

        ts = entry.get("timestamp")
        content = (entry.get("message") or {}).get("content", [])
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = (block.get("text") or "").strip()
                    if not text or text == "No response requested.":
                        continue
                    key = f"text:{text}"
                    if key in seen:
                        continue
                    seen.add(key)
                    blocks.append({"kind": "text", "text": text, "tool_call": None, "timestamp": ts})
                elif btype == "thinking":
                    text = (block.get("thinking") or "").strip()
                    if not text:
                        continue
                    key = f"thinking:{text}"
                    if key in seen:
                        continue
                    seen.add(key)
                    blocks.append({"kind": "thinking", "text": text, "tool_call": None, "timestamp": ts})
                elif btype == "tool_use":
                    tool_id = block.get("id", "")
                    key = f"tool_use:{tool_id}"
                    if key in seen:
                        continue
                    seen.add(key)
                    blocks.append({
                        "kind": "tool_use",
                        "text": "",
                        "tool_call": {
                            "tool_use_id": tool_id,
                            "name": block.get("name", ""),
                            "input": block.get("input", {}),
                            "result": None,
                            "result_timestamp": None,
                            "is_error": False,
                        },
                        "timestamp": ts,
                    })
        i += 1
    return blocks, i


def attach_tool_results(blocks, entries, result_start):
    """Match tool_result blocks to tool_use blocks by ID. Returns next index."""
    pending = {}
    for b in blocks:
        if b["kind"] == "tool_use" and b["tool_call"]:
            pending[b["tool_call"]["tool_use_id"]] = b["tool_call"]
    if not pending:
        return result_start

    i = result_start
    while i < len(entries) and pending:
        entry = entries[i]
        role = (entry.get("message") or {}).get("role") or entry.get("type")
        if role == "assistant":
            break
        if role == "user":
            content = (entry.get("message") or {}).get("content", "")
            if isinstance(content, list):
                has_tool_result = False
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        has_tool_result = True
                        tid = block.get("tool_use_id", "")
                        if tid in pending:
                            rc = block.get("content", "")
                            if isinstance(rc, list):
                                result_text = "\n".join(
                                    p.get("text", "")
                                    for p in rc
                                    if isinstance(p, dict) and p.get("type") == "text"
                                )
                            elif isinstance(rc, str):
                                result_text = rc
                            else:
                                result_text = str(rc)
                            # Strip tool_use_error wrapper
                            result_text = re.sub(
                                r"^<tool_use_error>([\s\S]*)</tool_use_error>$",
                                r"\1",
                                result_text,
                            )
                            pending[tid]["result"] = result_text
                            pending[tid]["result_timestamp"] = entry.get("timestamp")
                            pending[tid]["is_error"] = bool(block.get("is_error"))
                            # AskUserQuestion answers ride in on this tool_result's
                            # entry; stash them so parse_transcript can emit the
                            # human's form input as its own turn (else it's invisible).
                            fa = extract_form_answers(entry)
                            if fa:
                                pending[tid]["form_answers"] = fa
                            del pending[tid]
                if not has_tool_result:
                    break
            else:
                break
        i += 1
    return i


def extract_codex_user_text(text):
    """Extract actual user request from Codex messages (strips system prefix)."""
    for marker in ("## My request for Codex:", "## My request for Codex"):
        idx = text.find(marker)
        if idx != -1:
            after = text[idx + len(marker):]
            return re.sub(r"^:?\s*", "", after).strip()
    return text.strip()


def parse_codex_patch(patch_str):
    """Parse Codex apply_patch into Edit/Write-compatible input."""
    lines = patch_str.split("\n")
    while lines and lines[-1] == "":
        lines.pop()

    file_path = ""
    is_new = False
    old_lines = []
    new_lines = []

    for line in lines:
        if line.startswith("*** Begin Patch") or line.startswith("*** End Patch"):
            continue
        if line.startswith("*** Add File:"):
            file_path = line.replace("*** Add File:", "").strip()
            is_new = True
            continue
        if line.startswith("*** Update File:"):
            file_path = line.replace("*** Update File:", "").strip()
            is_new = False
            continue
        if line.startswith("@@"):
            continue
        if line.startswith("+"):
            new_lines.append(line[1:])
        elif line.startswith("-"):
            old_lines.append(line[1:])
        else:
            old_lines.append(line)
            new_lines.append(line)

    if is_new:
        return {"file_path": file_path, "content": "\n".join(new_lines), "is_new": True}
    return {
        "file_path": file_path,
        "old_string": "\n".join(old_lines),
        "new_string": "\n".join(new_lines),
        "is_new": False,
    }


def parse_codex_transcript(text):
    """Parse Codex CLI event-based JSONL into Turn[].

    Turn boundaries come from the CodexProvider's is_turn_boundary() —
    each assistant `response_item message` starts a new Turn, with
    subsequent tool calls / outputs attaching to it. Initial user
    prompt populates Turn 1's user_text. This gives Codex sessions a
    navigable per-narrative-beat structure rather than collapsing
    everything between task_started and task_complete into 1 Turn.
    """
    from providers.codex import CodexProvider
    codex_provider = CodexProvider()

    events = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except (json.JSONDecodeError, TypeError):
            continue

    turns = []
    turn_index = 0
    current_user_text = ""
    current_timestamp = ""
    current_blocks = []
    pending_calls = {}
    in_turn = False

    def _flush_turn():
        nonlocal turn_index, current_user_text, current_blocks, current_timestamp
        if current_user_text or current_blocks:
            turn_index += 1
            turns.append({
                "index": turn_index,
                "user_text": current_user_text,
                "blocks": current_blocks,
                "timestamp": current_timestamp,
            })
        current_user_text = ""
        current_blocks = []

    for evt in events:
        etype = evt.get("type", "")
        payload = evt.get("payload", {})
        ts = evt.get("timestamp")

        if etype == "event_msg" and payload.get("type") == "task_started":
            in_turn = True
            current_user_text = ""
            current_timestamp = ts or ""
            current_blocks = []
            pending_calls = {}
            continue

        if etype == "event_msg" and payload.get("type") == "task_complete":
            if in_turn:
                _flush_turn()
            in_turn = False
            continue

        if not in_turn:
            continue

        # Boundary check — provider decides what starts a new Turn.
        is_boundary = codex_provider.is_turn_boundary(evt)

        if etype == "event_msg" and payload.get("type") == "user_message":
            msg = payload.get("message", "")
            extracted = extract_codex_user_text(msg)
            # Mid-task user_message → finalize previous turn, start new.
            if current_blocks or current_user_text:
                _flush_turn()
            current_user_text = extracted
            if ts:
                current_timestamp = ts
            continue

        if etype == "response_item":
            ptype = payload.get("type")
            role = payload.get("role", "")
            phase = payload.get("phase", "")

            if ptype == "message" and role == "user":
                content = payload.get("content", [])
                if isinstance(content, list):
                    text_parts = [b.get("text", "") for b in content if b.get("type") == "input_text"]
                    raw = "\n".join(text_parts)
                    extracted = extract_codex_user_text(raw)
                    if extracted and not current_user_text:
                        current_user_text = extracted
                continue

            if ptype == "message" and role == "developer":
                continue

            if ptype == "message" and role == "assistant":
                content = payload.get("content", [])
                text_parts = []
                if isinstance(content, list):
                    for b in content:
                        if b.get("type") == "output_text":
                            text_parts.append(b.get("text", ""))
                block_text = "\n".join(text_parts).strip()
                if not block_text:
                    continue
                kind = "thinking" if phase == "commentary" else "text"
                # Boundary fired → finalize previous turn, start fresh.
                # Tool calls accumulated before the first assistant message
                # attach to Turn 1; subsequent assistant messages each get
                # their own turn with no tool calls attached yet (they'll
                # accumulate in this new turn).
                if is_boundary and (current_blocks or current_user_text):
                    _flush_turn()
                    current_timestamp = ts or current_timestamp
                current_blocks.append({"kind": kind, "text": block_text, "tool_call": None, "timestamp": ts})
                continue

            if ptype == "reasoning":
                continue

            if ptype == "function_call":
                call_id = payload.get("call_id", "")
                name = payload.get("name", "unknown")
                try:
                    inp = json.loads(payload.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    inp = {"raw": payload.get("arguments")}
                if name == "exec_command" and inp.get("cmd"):
                    cmd = f"cd {inp['workdir']} && {inp['cmd']}" if inp.get("workdir") else inp["cmd"]
                    inp = {"command": cmd}
                tool_call = {
                    "tool_use_id": call_id,
                    "name": "Bash" if name == "exec_command" else name,
                    "input": inp,
                    "result": None,
                    "result_timestamp": None,
                    "is_error": False,
                }
                current_blocks.append({"kind": "tool_use", "text": "", "tool_call": tool_call, "timestamp": ts})
                pending_calls[call_id] = tool_call
                continue

            if ptype == "function_call_output":
                call_id = payload.get("call_id", "")
                output = payload.get("output", "")
                # Some Codex record kinds carry a non-string / dict / None `output`
                # (function_call_output payloads, reasoning chunks). re.sub on a non-str
                # raises TypeError and killed `cq context` for the entire Codex corpus.
                if not isinstance(output, str):
                    output = json.dumps(output) if output is not None else ""
                cleaned = re.sub(r"^Chunk ID:.*\n?", "", output, flags=re.MULTILINE)
                cleaned = re.sub(r"^Wall time:.*\n?", "", cleaned, flags=re.MULTILINE)
                cleaned = re.sub(r"^Process exited with code \d+\n?", "", cleaned, flags=re.MULTILINE)
                cleaned = re.sub(r"^Original token count:.*\n?", "", cleaned, flags=re.MULTILINE)
                cleaned = re.sub(r"^Output:\n?", "", cleaned, flags=re.MULTILINE)
                cleaned = cleaned.strip()
                if call_id in pending_calls:
                    tc = pending_calls[call_id]
                    tc["result"] = cleaned
                    tc["result_timestamp"] = ts
                    tc["is_error"] = "Process exited with code" in output and "code 0" not in output
                    del pending_calls[call_id]
                continue

            if ptype == "custom_tool_call":
                call_id = payload.get("call_id", "")
                name = payload.get("name", "unknown")
                if name == "apply_patch":
                    parsed = parse_codex_patch(payload.get("input", ""))
                    mapped_name = "Write" if parsed.get("is_new") else "Edit"
                    inp = parsed
                else:
                    mapped_name = name
                    inp = {"raw": payload.get("input", "")}
                tool_call = {
                    "tool_use_id": call_id,
                    "name": mapped_name,
                    "input": inp,
                    "result": None,
                    "result_timestamp": None,
                    "is_error": False,
                }
                current_blocks.append({"kind": "tool_use", "text": "", "tool_call": tool_call, "timestamp": ts})
                pending_calls[call_id] = tool_call
                continue

            if ptype == "custom_tool_call_output":
                call_id = payload.get("call_id", "")
                output_val = payload.get("output", "")
                if isinstance(output_val, str):
                    out_text = output_val
                elif isinstance(output_val, dict) and output_val.get("output"):
                    out_text = output_val["output"]
                else:
                    out_text = str(output_val)
                if call_id in pending_calls:
                    tc = pending_calls[call_id]
                    tc["result"] = out_text.strip()
                    tc["result_timestamp"] = ts
                    tc["is_error"] = isinstance(output_val, dict) and output_val.get("metadata", {}).get("exit_code", 0) != 0
                    del pending_calls[call_id]
                continue

    # Handle session ending without task_complete
    if in_turn and (current_user_text or current_blocks):
        _flush_turn()

    # Drop empty turns, re-index
    filtered = [
        t for t in turns
        if t["user_text"]
        or any(
            b["kind"] == "tool_use" or (b["kind"] == "text" and b["text"]) or (b["kind"] == "thinking" and b["text"])
            for b in t["blocks"]
        )
    ]
    for j, t in enumerate(filtered):
        t["index"] = j + 1
    return filtered


def parse_transcript(text):
    """Parse any supported transcript format into Turn[]. Main entry point."""
    fmt = detect_format(text)
    if fmt == "codex":
        return parse_codex_transcript(text), fmt
    if fmt == "replay":
        turns = []
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if "user_text" in obj or "blocks" in obj:
                    turns.append(obj)
            except (json.JSONDecodeError, TypeError):
                continue
        return turns, fmt

    entries, fmt = parse_jsonl(text)
    turns = []
    i = 0
    turn_index = 0

    while i < len(entries):
        entry = entries[i]
        role = (entry.get("message") or {}).get("role") or entry.get("type")

        if role == "user":
            content = (entry.get("message") or {}).get("content", "")
            if is_tool_result_only(content):
                # AskUserQuestion (form) answers ride in as a tool_result; surface
                # the human's entry as its own turn instead of dropping it silently.
                form_text = extract_form_answers(entry)
                if form_text:
                    turn_index += 1
                    turns.append({
                        "index": turn_index,
                        "user_text": form_text,
                        "blocks": [],
                        "timestamp": entry.get("timestamp", ""),
                        "is_sidechain": bool(entry.get("isSidechain")),
                    })
                i += 1
                continue
            user_text = extract_user_text(content)
            timestamp = entry.get("timestamp", "")
            is_sidechain = bool(entry.get("isSidechain"))
            i += 1

            # Absorb consecutive non-tool-result user messages
            while i < len(entries):
                nxt = entries[i]
                nxt_role = (nxt.get("message") or {}).get("role") or nxt.get("type")
                if nxt_role != "user":
                    break
                nxt_content = (nxt.get("message") or {}).get("content", "")
                if is_tool_result_only(nxt_content):
                    break
                nxt_text = extract_user_text(nxt_content)
                if nxt_text:
                    user_text = f"{user_text}\n{nxt_text}" if user_text else nxt_text
                i += 1

            # Extract system events
            system_events = []
            user_text = re.sub(
                r"\[bg-task:\s*(.+)\]",
                lambda m: (system_events.append(m.group(1)), "")[1],
                user_text,
            )
            user_text = user_text.strip()

            assistant_blocks, i = collect_assistant_blocks(entries, i)
            i = attach_tool_results(assistant_blocks, entries, i)

            turn_index += 1
            turn = {
                "index": turn_index,
                "user_text": user_text,
                "blocks": assistant_blocks,
                "timestamp": timestamp,
                "is_sidechain": is_sidechain,
            }
            if system_events:
                turn["system_events"] = system_events
            turns.append(turn)
            for fa_text, fa_ts in _form_answers_in_blocks(assistant_blocks):
                turn_index += 1
                turns.append({
                    "index": turn_index, "user_text": fa_text, "blocks": [],
                    "timestamp": fa_ts, "is_sidechain": is_sidechain,
                })

        elif role == "assistant":
            assistant_blocks, i = collect_assistant_blocks(entries, i)
            i = attach_tool_results(assistant_blocks, entries, i)

            if turns:
                turns[-1]["blocks"].extend(assistant_blocks)
            else:
                turn_index += 1
                turns.append({
                    "index": turn_index,
                    "user_text": "",
                    "blocks": assistant_blocks,
                    "timestamp": entry.get("timestamp", ""),
                    "is_sidechain": bool(entry.get("isSidechain")),
                })
            for fa_text, fa_ts in _form_answers_in_blocks(assistant_blocks):
                turn_index += 1
                turns.append({
                    "index": turn_index, "user_text": fa_text, "blocks": [],
                    "timestamp": fa_ts, "is_sidechain": bool(entry.get("isSidechain")),
                })
        else:
            i += 1

    # Drop empty turns
    filtered = [
        t for t in turns
        if t.get("user_text")
        or t.get("system_events")
        or any(
            b["kind"] == "tool_use"
            or (b["kind"] == "text" and b["text"] and b["text"] != "No response requested.")
            or (b["kind"] == "thinking" and b["text"])
            for b in t["blocks"]
        )
    ]
    for j, t in enumerate(filtered):
        t["index"] = j + 1

    # Cursor: all assistant blocks except last per turn → thinking
    if fmt == "cursor":
        for turn in filtered:
            for j in range(len(turn["blocks"]) - 1):
                if turn["blocks"][j]["kind"] == "text":
                    turn["blocks"][j]["kind"] = "thinking"

    return filtered, fmt


# ── Session Discovery (ported from claude-replay resolve-session.mjs) ──────


def discover_sessions(project_filter=None):
    """Find all session files by asking each registered provider.

    Each provider's discover() returns session dicts in a uniform shape;
    the core just concatenates them. To add another agent CLI, register
    a new AgentProvider in providers/__init__.py — no edit here.
    """
    sessions: list[dict] = []
    for provider in PROVIDERS:
        sessions.extend(provider.discover(project_filter))
    return sessions


def find_session(session_id, project_filter=None):
    """Find a session file by partial ID match. Returns path or None."""
    all_sessions = discover_sessions(project_filter)
    # Exact match
    for s in all_sessions:
        if s["session_id"] == session_id:
            return s
    # Prefix match
    matches = [s for s in all_sessions if s["session_id"].startswith(session_id)]
    if len(matches) == 1:
        return matches[0]
    # Substring match
    if not matches:
        matches = [s for s in all_sessions if session_id in s["session_id"]]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Multiple matches for '{session_id}':", file=sys.stderr)
        for m in matches[:10]:
            print(f"  {m['group']:12} {m['session_id'][:12]}  {decode_project(m['project'])}", file=sys.stderr)
        return None
    print(f"No session found matching '{session_id}'", file=sys.stderr)
    return None


def get_session_info(session):
    """Extract session metadata: size, date, cwd, first user message."""
    f = session["path"]
    size_bytes = f.stat().st_size
    size_kb = size_bytes / 1024
    mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")

    preview = ""
    date_str = ""
    cwd = ""
    with open(f) as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not date_str and obj.get("timestamp"):
                date_str = obj["timestamp"][:10]
            if not cwd and obj.get("cwd"):
                cwd = obj["cwd"]
            # Codex sessions nest cwd inside payload (session_meta line)
            if not cwd:
                payload = obj.get("payload")
                if isinstance(payload, dict) and payload.get("cwd"):
                    cwd = payload["cwd"]
            if obj.get("type") == "user":
                content = (obj.get("message") or {}).get("content", "")
                text = extract_user_text(content).replace("\n", " ")
                if text.startswith("<"):
                    preview = "(automated)"
                elif len(text) > 10:
                    preview = text[:80]
                break

    return {
        **session,
        "size_bytes": size_bytes,
        "size_str": f"{size_kb / 1024:.1f}MB" if size_kb > 1024 else f"{size_kb:.0f}KB",
        "date": date_str or mtime,
        "cwd": cwd,
        "preview": preview,
    }


# ── FTS5 Search Engine ────────────────────────────────────────────────────


def extract_searchable_text(entry, provider=None):
    """Two-tier text extraction: (role, shallow, deep).

    `provider` is the session's provider from discovery. Only fall back to
    per-entry detect() when it's unknown: detect() identifies a FILE from its
    first line, and Codex's matches only `session_meta` — so per-entry
    dispatch silently indexed zero rows for every Codex transcript after its
    May-2026 format change. Each provider's extract_searchable() returns raw
    (role, shallow, deep) with every deep part already clipped (clip_deep);
    the cleanup + entry-level cap runs once here.

    SHALLOW_MAX = 200 KB so long pasted docs stay searchable. DEEP_MAX is a
    backstop for entries with many tool parts.
    """
    if provider is None:
        provider = detect_provider(entry)
    if provider is None:
        return ("", "", "")
    role, shallow, deep = provider.extract_searchable(entry)

    SHALLOW_MAX = 200_000
    DEEP_MAX = 48_000

    def clean(text, limit):
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
        text = re.sub(r"<system-reminder>[\s\S]*?</system-reminder>", "", text)
        text = re.sub(r"<task-notification>[\s\S]*?</task-notification>", "", text)
        return text[:limit]

    return (role, clean(shallow, SHALLOW_MAX), clean(deep, DEEP_MAX))


SCHEMA_VERSION = 5


def migrate_schema(conn):
    """Non-destructive forward migration driven by PRAGMA user_version.

    v0 / v1 → v2: add `timestamp` and `is_sidechain` columns to `messages`.
    v2 → v3: add `kind` column ('main' | 'subagent') for source-of-file
             classification. Orthogonal to is_sidechain (which marks inline
             teammate blocks). Existing rows default to 'main'.
    v4 → v5: `tail_hash` (fingerprint of the bytes before byte_offset, so a
             rewritten transcript isn't mistaken for an append) and an in-place
             re-flag of teammate relays as sidechain. No re-index.
    v3 → v4: add `byte_offset` / `line_count` to `indexed_sessions` so a
             growing transcript is indexed incrementally (append-only JSONL),
             and re-extract everything for the Codex/queued-command/per-part
             clipping fixes in extract_searchable_text.
    Uses ALTER TABLE ADD COLUMN (non-destructive, preserves existing rows).
    Clears `indexed_sessions` so refresh_index() re-indexes each session
    (in the background when the backlog is large; searches keep using the
    existing rows until each session is replaced). The destructive
    rebuild path (DELETE FROM messages_fts; DELETE FROM messages; …) is
    intentionally NOT used — an interrupted rebuild would leave the cache
    empty; this path is resumable.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    cols = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "timestamp" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN timestamp TEXT")
    if "is_sidechain" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN is_sidechain INTEGER DEFAULT 0")
    if "kind" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN kind TEXT DEFAULT 'main'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_sidechain ON messages(is_sidechain)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_kind ON messages(kind)")
    icols = {row[1] for row in conn.execute("PRAGMA table_info(indexed_sessions)")}
    if "byte_offset" not in icols:
        conn.execute("ALTER TABLE indexed_sessions ADD COLUMN byte_offset INTEGER")
    if "line_count" not in icols:
        conn.execute("ALTER TABLE indexed_sessions ADD COLUMN line_count INTEGER")
    if "tail_hash" not in icols:
        conn.execute("ALTER TABLE indexed_sessions ADD COLUMN tail_hash TEXT")
    if current < 4:
        # Force lazy re-index of existing sessions so the new columns get
        # populated for their rows. The indexer's per-session remove+insert
        # cycle handles this cleanly.
        conn.execute("DELETE FROM indexed_sessions")
    else:
        # v5 only re-flags teammate relays in place (they open with "Another
        # Claude session sent a message:", so v4's startswith test missed them).
        conn.execute(
            "UPDATE messages SET is_sidechain = 1 WHERE COALESCE(kind, 'main') != 'subagent' "
            "AND (session_id, msg_index) IN (SELECT session_id, msg_index FROM messages "
            "WHERE depth = 0 AND instr(substr(content, 1, 300), '<teammate-message') > 0)")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def create_db():
    """Create or open the FTS5 database."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(FTS_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 10000")  # wait up to 10s for write lock
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL,
            session_id TEXT NOT NULL,
            msg_index INTEGER NOT NULL,
            role TEXT,
            depth INTEGER DEFAULT 0,
            content TEXT,
            timestamp TEXT,
            is_sidechain INTEGER DEFAULT 0,
            kind TEXT DEFAULT 'main'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content,
            tokenize='porter unicode61'
        );
        CREATE TABLE IF NOT EXISTS indexed_sessions (
            session_path TEXT PRIMARY KEY,
            mtime REAL,
            msg_count INTEGER
        );
        -- remove_session_from_index() deletes by (session_id, project) on every
        -- changed transcript; without this it full-scans `messages` each time,
        -- which hung every cq command for minutes on a ~800k-row index.
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, project);
    """)
    # NOTE: indexes on `timestamp` / `is_sidechain` live in migrate_schema()
    # so they can't fire against a pre-v2 DB where those columns don't exist
    # yet. migrate_schema() runs right below and is idempotent.
    conn.commit()
    migrate_schema(conn)
    return conn


# Transcripts are append-only JSONL, so a session that grew since it was last
# indexed only needs its new lines read. Small backlogs are indexed inline;
# anything bigger goes to one detached background worker so a query never
# waits on it (the bug that made every cq call hang for minutes).
INLINE_REFRESH_BYTES = 32 * 1024 * 1024
INDEX_LOCK = CACHE_DIR / "index.lock"
INDEX_STATUS = CACHE_DIR / "indexer.status.json"
INDEX_LOG = CACHE_DIR / "indexer.log"


def _ends_line_at(path, offset):
    """True if `offset` sits right after a complete record: a newline, or the
    closing brace of a final line written without one (Cursor transcripts
    don't end in a newline)."""
    try:
        with open(path, "rb") as f:
            f.seek(offset - 1)
            return f.read(1) in (b"\n", b"}")
    except OSError:
        return False


def _tail_hash(path, offset):
    """Fingerprint of the 256 bytes before `offset` — detects a transcript that
    was rewritten rather than appended to."""
    import hashlib
    try:
        with open(path, "rb") as f:
            f.seek(max(0, offset - 256))
            return hashlib.blake2b(f.read(offset - max(0, offset - 256)), digest_size=8).hexdigest()
    except OSError:
        return None


def plan_reindex(conn, sessions, force=False):
    """Work list for bringing the index up to date.

    Returns [(session, mode, byte_offset, line_count, pending_bytes)] where
    mode is 'append' (index only bytes after byte_offset) or 'full' (drop the
    session's rows and re-read it — new file, rewritten/truncated file, or a
    row from before byte offsets were tracked).
    """
    known = {}
    if not force:
        for p, mtime, off, lines, th in conn.execute(
                "SELECT session_path, mtime, byte_offset, line_count, tail_hash FROM indexed_sessions"):
            known[p] = (mtime, off, lines, th)
    plan = []
    for s in sessions:
        path = s["path"]
        try:
            st = path.stat()
        except OSError:
            continue
        row = known.get(str(path))
        if row is None:
            plan.append((s, "full", 0, 0, st.st_size))
            continue
        mtime, off, lines, th = row
        if off is None:
            plan.append((s, "full", 0, 0, st.st_size))
        elif st.st_size == off and (st.st_mtime <= mtime or th is None or _tail_hash(path, off) == th):
            continue  # nothing new (a bare touch doesn't need work)
        elif 0 < off < st.st_size and _ends_line_at(path, off) \
                and (th is None or _tail_hash(path, off) == th):
            plan.append((s, "append", off, lines or 0, st.st_size - off))
        else:
            plan.append((s, "full", 0, 0, st.st_size))
    return plan


def remove_session_from_index(conn, path, project_name, session_id):
    """Remove all rows for a session from the index. Caller must pass the
    explicit (project_name, session_id) — these can't be inferred from
    path.stem because Cursor uses transcript.jsonl and Codex uses
    rollout-<uuid>.jsonl, so the filename stem isn't the session ID."""
    conn.execute(
        "DELETE FROM messages_fts WHERE rowid IN (SELECT id FROM messages WHERE session_id = ? AND project = ?)",
        (session_id, project_name),
    )
    conn.execute("DELETE FROM messages WHERE session_id = ? AND project = ?", (session_id, project_name))
    conn.execute("DELETE FROM indexed_sessions WHERE session_path = ?", (str(path),))
    conn.commit()


def index_session(conn, path, project_name, session_id, kind="main", provider=None,
                  start_offset=0, start_line=0):
    """Index a session's JSONL from byte `start_offset` (line `start_line`) to
    the last complete line, and record how far it got.

    Required: explicit project_name and session_id from discover_sessions()
    (don't infer from path.stem — that breaks Cursor and Codex). `provider` is
    the session's provider; see extract_searchable_text for why it matters.

    kind: 'main' (top-level session JSONL) or 'subagent' (Claude Code
    spawned-agent transcript under <project>/<parent-uuid>/subagents/).
    Stored on every row so search can filter the two independently of
    is_sidechain (which marks inline <teammate-message> blocks).

    A trailing line without a newline is indexed only if it parses as
    complete JSON (Cursor never writes a final newline); otherwise it's still
    being written and is left for the next refresh. msg_index stays the
    0-based line number, which is what `cq raw N` and `cq context @N` address.
    """
    count = 0
    # Subagent JSONLs all carry isSidechain=true natively. Suppress that
    # flag for kind='subagent' rows so the two filters stay orthogonal —
    # kind='subagent' is hidden by --include-subagents alone, not by the
    # legacy --include-sidechain flag.
    suppress_sidechain = kind == "subagent"
    with open(path, "rb") as f:
        mtime = os.fstat(f.fileno()).st_mtime
        mid_line = False
        if start_offset > 0:
            f.seek(start_offset - 1)
            mid_line = f.read(1) != b"\n"  # last run indexed a final line that had no newline
        f.seek(start_offset)
        offset, i = start_offset, start_line
        for raw in f:
            if mid_line:
                # The rest of that already-indexed line (normally just its "\n").
                mid_line = False
                offset += len(raw)
                continue
            if not raw.endswith(b"\n"):
                try:
                    json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    break  # still being written
            lineno = i
            offset += len(raw)
            i += 1
            line = raw.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                continue
            if not isinstance(entry, dict):
                continue
            role, shallow, deep = extract_searchable_text(entry, provider)
            timestamp = entry.get("timestamp") or ""
            # Sidechain = the jsonl-level isSidechain flag, or a message that IS
            # a <teammate-message> relay. Only the message's own prose counts: a
            # tool result that merely contains the tag (a grep over transcripts)
            # used to hide the whole entry from default search.
            if suppress_sidechain:
                is_sidechain = 0
            else:
                is_sidechain = 1 if entry.get("isSidechain") else 0
                # (relays usually open with "Another Claude session sent a message:")
                if not is_sidechain and "<teammate-message" in shallow[:300]:
                    is_sidechain = 1
            for depth, text in ((0, shallow), (1, deep)):
                if not text.strip():
                    continue
                cur = conn.execute(
                    "INSERT INTO messages (project, session_id, msg_index, role, depth, content, timestamp, is_sidechain, kind) VALUES (?,?,?,?,?,?,?,?,?)",
                    (project_name, session_id, lineno, role, depth, text, timestamp, is_sidechain, kind),
                )
                conn.execute("INSERT INTO messages_fts (rowid, content) VALUES (?,?)", (cur.lastrowid, text))
                count += 1
    conn.execute(
        "INSERT INTO indexed_sessions (session_path, mtime, msg_count, byte_offset, line_count, tail_hash) "
        "VALUES (?,?,?,?,?,?) ON CONFLICT(session_path) DO UPDATE SET "
        "mtime=excluded.mtime, msg_count=indexed_sessions.msg_count + excluded.msg_count, "
        "byte_offset=excluded.byte_offset, line_count=excluded.line_count, tail_hash=excluded.tail_hash",
        (str(path), mtime, count, offset, i, _tail_hash(path, offset)),
    )
    conn.commit()
    return count


def _provider_for(session):
    return by_group_label(session.get("group", "")) if session.get("group") else None


def run_index_plan(conn, plan, quiet=False, status_path=None, deadline=None):
    """Execute a plan_reindex() work list. Returns how many items were done.
    Writes progress to `status_path` (background worker) so `cq stats` can
    report it. Stops early at `deadline` (time.time() value) if given."""
    t0 = time.time()
    total_bytes = sum(p[4] for p in plan) or 1
    done_bytes = msgs = done = 0
    by_group = {}
    last_status = 0.0
    for s, mode, off, lines, pending in plan:
        if deadline is not None and time.time() > deadline:
            break
        if mode == "append":
            # The plan may be stale: another cq process can have indexed this
            # session since it was built. Appending from an old offset would
            # insert the same lines twice, so resume from what's stored now.
            row = conn.execute("SELECT byte_offset, line_count FROM indexed_sessions WHERE session_path = ?",
                               (str(s["path"]),)).fetchone()
            if row is None or row[0] is None:
                mode = "full"
            elif row[0] != off:
                off, lines = row[0], row[1] or 0
                try:
                    if s["path"].stat().st_size <= off:
                        continue
                except OSError:
                    continue
        # Another process (a schema upgrade, an inline refresh) can hold the
        # write lock past busy_timeout; retry the session instead of letting
        # one locked moment kill a 20-minute background run.
        for attempt in range(6):
            try:
                if mode == "full":
                    remove_session_from_index(conn, s["path"], s["project"], s["session_id"])
                    off, lines = 0, 0
                    mode = "append-after-remove"
                msgs += index_session(conn, s["path"], s["project"], s["session_id"],
                                      kind=s.get("kind", "main"), provider=_provider_for(s),
                                      start_offset=off, start_line=lines)
                break
            except OSError:  # transcript vanished/unreadable mid-run
                attempt = None
                break
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) or attempt == 5:
                    raise
                conn.rollback()
                time.sleep(2 ** attempt)
        else:
            continue
        if attempt is None:
            continue
        done += 1
        done_bytes += pending
        by_group[s["group"]] = by_group.get(s["group"], 0) + 1
        if status_path and time.time() - last_status > 2:
            last_status = time.time()
            _write_status(status_path, state="running", done=done, total=len(plan),
                          done_mb=round(done_bytes / 1e6), total_mb=round(total_bytes / 1e6),
                          started=t0)
    if not quiet and done:
        breakdown = ", ".join(f"{k}: {v}" for k, v in by_group.items())
        print(f"Indexed {done} file(s) ({msgs} msgs, {done_bytes / 1e6:.0f} MB) in "
              f"{time.time() - t0:.1f}s ({breakdown})", file=sys.stderr)
    return done


def build_index(conn, force=False, project_filter=None, quiet=False):
    """Synchronously bring the index up to date (used by `cq index` and the
    background worker). Walks every session discovered across Claude Code,
    Cursor, and Codex CLI — discover_sessions() is the single source of truth
    so the indexer and the search-side --provider filter agree."""
    sessions = discover_sessions(project_filter)
    if not sessions:
        if not quiet:
            print("No sessions found across Claude Code, Cursor, or Codex.", file=sys.stderr)
        return
    if force:
        conn.executescript("DELETE FROM messages_fts; DELETE FROM messages; DELETE FROM indexed_sessions;")
        conn.commit()
    run_index_plan(conn, plan_reindex(conn, sessions, force), quiet=quiet)


def _write_status(path, **fields):
    try:
        fields["updated"] = time.time()
        fields["pid"] = os.getpid()
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(fields))
        tmp.replace(path)
    except OSError:
        pass


def _acquire_index_lock(blocking=False):
    """Exclusive lock on the index writer role. Returns an open file (keep it
    to hold the lock) or None if another cq process is already writing."""
    import fcntl
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(INDEX_LOCK, "a")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return fh
    except OSError:
        fh.close()
        return None


def _spawn_index_worker():
    """Start a detached `cq index --worker`, unless one already holds the lock.
    Returns True if this call started it."""
    probe = _acquire_index_lock()
    if probe is None:
        return False
    probe.close()  # the worker takes the lock itself; a racing spawn just exits
    log = open(INDEX_LOG, "a")
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "index", "--worker"],
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return True


def index_backlog(conn, project_filter=None):
    """(transcripts pending, MB pending, background worker running?) — cheap."""
    plan = plan_reindex(conn, discover_sessions(project_filter))
    running = False
    probe = _acquire_index_lock()
    if probe is None:
        running = True
    else:
        probe.close()
    return len(plan), sum(p[4] for p in plan) / 1e6, running


def refresh_index(conn, force=False, project_filter=None, quiet=False):
    """Bring the index up to date WITHOUT making the caller wait on a big job.

    Small backlogs (≤ INLINE_REFRESH_BYTES of new transcript bytes — the usual
    case once the index is warm, thanks to append-only indexing) are indexed
    inline. Anything larger is handed to one detached background worker and
    the query runs against the existing index, with a notice on stderr.
    Returns the number of transcripts still pending after this call.
    """
    if force:
        lock = _acquire_index_lock(blocking=True)
        try:
            build_index(conn, force=True, project_filter=project_filter, quiet=quiet)
        finally:
            lock.close()
        return 0
    plan = plan_reindex(conn, discover_sessions(project_filter))
    if not plan:
        return 0
    pending = sum(p[4] for p in plan)
    if pending <= INLINE_REFRESH_BYTES:
        lock = _acquire_index_lock()
        if lock is None:
            return len(plan)  # a background worker is already writing
        try:
            plan = plan_reindex(conn, discover_sessions(project_filter))  # fresh, under the lock
            run_index_plan(conn, plan, quiet=True)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e):
                raise
            return len(plan)
        finally:
            lock.close()
        return 0
    started = _spawn_index_worker()
    print(f"cq: {len(plan)} transcript(s) ({pending / 1e6:.0f} MB) are new or changed since the last "
          f"index — {'started a background indexer' if started else 'the background indexer is on it'}. "
          f"These results come from the existing index and may miss that activity; "
          f"`cq stats` shows progress, `cq grep` scans the raw files.", file=sys.stderr)
    return len(plan)


def build_fts_query(query, phrase_mode=False):
    """Build FTS5 query variants.

    Default order: exact AND → prefix AND → prefix OR. Exact-stem matches
    run first so they aren't buried under noisy prefix expansions.

    phrase_mode=True returns a single FTS5 phrase query "tok1 tok2" with
    no fallback. Adjacent-token match only — strict by design.
    """
    tokens = re.split(r"[\s\-]+", query.strip())
    tokens = [t for t in tokens if t]
    if not tokens:
        return []

    if phrase_mode:
        # FTS5 phrase syntax: a quoted multi-word string is treated as
        # an ordered phrase. Inner quotes are escaped per token.
        inner = " ".join(t.replace('"', '""') for t in tokens)
        return ['"' + inner + '"']

    quoted = ['"' + t.replace('"', '""') + '"' for t in tokens]
    prefixed = ['"' + t.replace('"', '""') + '"*' for t in tokens]
    # Exact AND first, then prefix AND, then prefix OR if multi-token
    queries = [" ".join(quoted), " ".join(prefixed)]
    if len(tokens) > 1:
        queries.append(" OR ".join(prefixed))
    return queries


def fts_search(conn, query, limit, depth_filter=None, project_filter=None, roles=None,
               session_ids=None, include_sidechain=False, include_subagents=False,
               after=None, before=None, exclude_ids=None, hide_echo=False):
    """Run one FTS5 MATCH query, optionally restricted by depth/project/roles/session_ids.

    session_ids is a set of full session IDs (from --session and/or --provider).
    Empty set short-circuits to []. None means no session restriction.

    include_sidechain: when False (default), excludes rows where is_sidechain = 1.
                      COALESCE handles legacy rows where the column is NULL.

    include_subagents: when False (default), excludes rows from spawned-agent
                      JSONLs (kind='subagent'). COALESCE handles pre-v3 rows.

    after / before: lexicographic ISO-8601 bounds. `after` should be '>=' start-of-day
                    (e.g. '2026-04-10'), `before` should be '<' day-after-end (e.g. the
                    caller should pass '2026-04-13' when the user said --before 2026-04-12).
                    When either bound is set, rows with NULL timestamp are excluded.

    exclude_ids: session IDs to leave out (the caller's own session + its subagents).
    hide_echo:   drop rows that are cq itself — a `cq.py …` command or its printed
                 results (tool rows), or the loaded SKILL.md. Without this, half of
                 all top hits were the searching session echoing its own query.
    """
    if session_ids is not None and len(session_ids) == 0:
        return []

    depth_clause = " AND m.depth = ?" if depth_filter is not None else ""
    project_clause = " AND m.project LIKE ?" if project_filter else ""
    role_clause = ""
    if roles:
        role_clause = f" AND m.role IN ({','.join('?' * len(roles))})"

    sorted_ids = None
    session_clause = ""
    if session_ids:
        # Sort for prepared-statement cache locality (same set → same params)
        sorted_ids = sorted(session_ids)
        placeholders = ",".join("?" * len(sorted_ids))
        session_clause = f" AND m.session_id IN ({placeholders})"

    sidechain_clause = "" if include_sidechain else " AND COALESCE(m.is_sidechain, 0) = 0"
    subagent_clause = "" if include_subagents else " AND COALESCE(m.kind, 'main') != 'subagent'"
    excluded = sorted(exclude_ids or ())
    exclude_clause = (f" AND m.session_id NOT IN ({','.join('?' * len(excluded))})"
                      if excluded else "")
    # cq's own footprint: its commands and printed results (tool rows) and the
    # SKILL.md injected when the skill loads. Prose that merely mentions cq.py stays.
    echo_clause = (" AND NOT (m.depth = 1 AND m.content LIKE '%cq.py%')"
                   " AND m.content NOT LIKE 'Base directory for this skill%browse-chat-history%'"
                   if hide_echo else "")

    date_clause = ""
    if after is not None:
        date_clause += " AND m.timestamp IS NOT NULL AND m.timestamp >= ?"
    if before is not None:
        date_clause += " AND m.timestamp IS NOT NULL AND m.timestamp < ?"

    sql = f"""
        SELECT m.project, m.session_id, m.msg_index, m.role, m.depth,
               snippet(messages_fts, -1, '→', '←', '…', 40), rank
        FROM messages_fts f
        JOIN messages m ON f.rowid = m.id
        WHERE messages_fts MATCH ?{depth_clause}{project_clause}{role_clause}{session_clause}{sidechain_clause}{subagent_clause}{date_clause}{exclude_clause}{echo_clause}
        ORDER BY rank LIMIT ?
    """
    params = [query]
    if depth_filter is not None:
        params.append(depth_filter)
    if project_filter:
        params.append(f"%{project_filter}%")
    if roles:
        params.extend(roles)
    if sorted_ids:
        params.extend(sorted_ids)
    if after is not None:
        params.append(after)
    if before is not None:
        params.append(before)
    params.extend(excluded)
    params.append(limit)
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def search_cascade(conn, query, limit, project_filter=None, roles=None,
                   tier="all", phrase_mode=False, session_ids=None,
                   include_sidechain=False, include_subagents=False,
                   after=None, before=None, exclude_ids=None, hide_echo=False):
    """Run the search with optional tier/phrase modes.

    tier:
      - 'all'     blended single-pass (depth filter dropped, BM25 ranks both
                  shallow chat text and deep tool/thinking together)
      - 'shallow' chat text only (depth=0)
      - 'deep'    tool inputs/results/thinking only (depth=1)

    phrase_mode:
      - False  default cascade: exact AND → prefix AND → prefix OR (first
               non-empty wins). Variant cascade is preserved within whatever
               tier is selected, so exact stem matches outrank prefix noise.
      - True   strict adjacent-token phrase query, no fallback. Empty results
               stay empty.
    """
    queries = build_fts_query(query, phrase_mode=phrase_mode)
    if not queries:
        return []

    if session_ids is not None and len(session_ids) == 0:
        return []

    if tier == "shallow":
        depth = 0
    elif tier == "deep":
        depth = 1
    else:  # 'all' — blended single-pass
        depth = None

    for q in queries:
        results = fts_search(conn, q, limit, depth_filter=depth,
                             project_filter=project_filter, roles=roles,
                             session_ids=session_ids,
                             include_sidechain=include_sidechain,
                             include_subagents=include_subagents,
                             after=after, before=before,
                             exclude_ids=exclude_ids, hide_echo=hide_echo)
        if results:
            return results
    return []


# Internal per-concept set-building limit. Must be high enough that
# intersection isn't truncated to the top-N-per-concept hit list. 10k
# comfortably covers the largest projects in practice; the indexed DB
# has ~188k rows total, so this is still fast enough.
_MULTI_CONCEPT_SET_LIMIT = 10_000


def multi_concept_search(conn, concepts, user_limit,
                         project_filter=None, roles=None, tier="all",
                         phrase_mode=False, session_ids=None,
                         include_sidechain=False, include_subagents=False,
                         after=None, before=None, exclude_ids=None, hide_echo=False):
    """AND-across-sessions search.

    For each concept, run a separate search_cascade() and collect the set
    of session_ids that matched. Intersect those sets, then run one final
    search_cascade against the first concept restricted to the intersection
    and ranked for display. Returns [] if any concept returned zero sessions.
    """
    if len(concepts) < 2:
        return []

    # Per-concept session set building — use a high limit so we don't lose
    # valid intersections to top-N truncation per concept.
    per_concept_sessions = []
    for concept in concepts:
        rows = search_cascade(
            conn, concept, _MULTI_CONCEPT_SET_LIMIT,
            project_filter=project_filter, roles=roles,
            tier=tier, phrase_mode=phrase_mode,
            session_ids=session_ids,
            include_sidechain=include_sidechain,
            include_subagents=include_subagents,
            after=after, before=before,
            exclude_ids=exclude_ids, hide_echo=hide_echo,
        )
        sid_set = {r[1] for r in rows}  # row[1] is session_id
        if not sid_set:
            return []
        per_concept_sessions.append(sid_set)

    intersection = set.intersection(*per_concept_sessions)
    if not intersection:
        return []

    # Final ranked query using the first concept, restricted to the intersection.
    # Display snippets are anchored to concept[0] — a richer multi-concept
    # snippet format is a possible follow-up.
    return search_cascade(
        conn, concepts[0], user_limit,
        project_filter=project_filter, roles=roles,
        tier=tier, phrase_mode=phrase_mode,
        session_ids=intersection,
        include_sidechain=include_sidechain,
        include_subagents=include_subagents,
        after=after, before=before,
        exclude_ids=exclude_ids, hide_echo=hide_echo,
    )


# ── Display Helpers ────────────────────────────────────────────────────────


def decode_project(encoded):
    """Convert -Users-foo-bar to /Users/foo/bar."""
    if encoded.startswith("-"):
        return "/" + encoded[1:].replace("-", "/")
    return encoded.replace("-", "/")


def short_project(encoded, max_len=40):
    decoded = decode_project(encoded)
    if len(decoded) > max_len:
        return "…" + decoded[-(max_len - 1):]
    return decoded


def truncate(text, max_len=120):
    text = text.replace("\n", " ").strip()
    return text[:max_len] + "…" if len(text) > max_len else text


def format_tool_input(inp, max_len=200):
    """Format tool input for display."""
    if not isinstance(inp, dict):
        return str(inp)[:max_len]
    # Show key fields first
    priority_keys = ["command", "file_path", "pattern", "query", "description", "content", "prompt"]
    parts = []
    for key in priority_keys:
        if key in inp:
            val = str(inp[key])
            if len(val) > 100:
                val = val[:100] + "…"
            parts.append(f"{key}={val}")
    for key, val in inp.items():
        if key not in priority_keys:
            val = str(val)
            if len(val) > 60:
                val = val[:60] + "…"
            parts.append(f"{key}={val}")
    result = ", ".join(parts)
    return result[:max_len] + "…" if len(result) > max_len else result


def format_json(data):
    """Format data as JSON for agent consumption."""
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


# ── Commands ───────────────────────────────────────────────────────────────


def _try_build_index(conn, force=False, project_filter=None, quiet=False):
    """Back-compat name for refresh_index()."""
    return refresh_index(conn, force=force, project_filter=project_filter, quiet=quiet)


def cmd_search_remote(args):
    """Remote search: SSH to <host> and grep across ~/.claude/projects/.

    Returns the matching JSONL paths and derived session IDs. This is a
    coarser tool than the local FTS5 path (no tokenization, no ranking, no
    tier filters), but it works against any host with SSH + grep — no
    remote cq install or index required. Use it to locate a session on a
    remote machine, then drill in with `cq turns --host <host> <session>`.
    """
    # Prefer the remote's own cq: ranked FTS hits, same flags as local. The
    # grep chain below is the fallback for hosts without a cq install — it
    # returned no hits for 59% of remote searches.
    rc = _run_remote_cq(args.host, args._argv, fallback_ok=True)
    if rc is not None:
        sys.exit(rc)
    if not args.query:
        sys.exit("error: --host search requires a positional query "
                 "(--concept / --phrase modes not supported remotely)")
    # Tokenize like the local FTS path: a multi-word query is an AND across tokens
    # (each token must appear SOMEWHERE in the file), not a literal contiguous-string
    # grep. The old `grep -rl '<whole query>'` matched the query verbatim on one line,
    # so any multi-word query (e.g. "urdf usd parity") silently returned 0 even when
    # every token existed — the #1 remote-search failure. Tokens are matched
    # case-insensitively; the recursive walk still includes subagents/ transcripts.
    if getattr(args, "phrase", False):
        tokens = [args.query.strip()]  # --phrase: keep the literal string
    else:
        tokens = [t for t in re.split(r"[\s\-]+", args.query.strip()) if t]
    # roots by provider — DEFAULT claude-only (mirrors the local default); the user's
    # interaction is always with Claude, codex/cursor are subagent-like. --provider all
    # widens to every runtime.
    prov = getattr(args, "provider", "claude") or "claude"
    _roots = {"claude": ["~/.claude/projects"], "codex": ["~/.codex/sessions"],
              "cursor": ["~/.cursor"],
              "all": ["~/.claude/projects", "~/.codex/sessions", "~/.cursor"]}
    roots = _roots.get(prov, ["~/.claude/projects"])
    roots_sh = " ".join(roots)
    # 1) fast AND-filter with grep (C speed) → NUL-separated candidate file list.
    if len(tokens) <= 1:
        grep_chain = f"grep -rliZ --include='*.jsonl' {shlex.quote(tokens[0])} {roots_sh} 2>/dev/null"
    else:
        gparts = [f"grep -rliZ --include='*.jsonl' {shlex.quote(tokens[0])} {roots_sh} 2>/dev/null"]
        for t in tokens[1:]:
            gparts.append(f"xargs -0 grep -liZ {shlex.quote(t)} 2>/dev/null")
        grep_chain = " | ".join(gparts)
    # 2) snippet extractor: reads the NUL file list on stdin, pulls a cleaned window
    #    around a token hit, ranks by mtime (recency). This is what makes remote search
    #    USABLE for discovery — the old version returned bare file paths, so you could
    #    never see WHAT matched without a manual drill-down per file.
    extractor = (
        "import os,sys,re,json\n"
        "toks=[t.lower() for t in sys.argv[1:-1]]; lim=int(sys.argv[-1])\n"
        "res=[]\n"
        # cap candidate files HERE (in python) rather than `head -z` in the shell —
        # `head -z` is GNU-only and silently breaks on macOS/BSD remotes (returns empty).
        "for fp in sys.stdin.read().split(chr(0))[:200]:\n"
        " fp=fp.strip()\n"
        " if not fp: continue\n"
        " try: d=open(fp,errors='ignore').read()\n"
        " except: continue\n"
        " low=d.lower(); offs=[low.find(t) for t in toks if low.find(t)>=0]\n"
        " i=min(offs) if offs else 0\n"
        " seg=d[max(0,i-110):i+200]\n"
        " seg=re.sub(r'\\\\[a-z]',' ',seg); seg=re.sub(r'[\"{}\\[\\]\\\\]',' ',seg); seg=re.sub(r'\\s+',' ',seg).strip()\n"
        " try: mt=os.path.getmtime(fp)\n"
        " except: mt=0\n"
        " sid=os.path.basename(fp)[:-6]; m=re.search(r'/(projects|sessions)/([^/]+)/',fp); proj=(m.group(2) if m else '')\n"
        " res.append((mt,sid,proj,fp,seg[:260]))\n"
        "res.sort(reverse=True)\n"
        "for mt,sid,proj,fp,seg in res[:lim]:\n"
        " print(json.dumps({'mt':mt,'sid':sid,'proj':proj,'path':fp,'snip':seg}))\n"
    )
    b64 = base64.b64encode(extractor.encode()).decode()
    tok_args = " ".join(shlex.quote(t) for t in tokens)
    remote = (f"{grep_chain} | "
              f"python3 -c \"$(printf %s {shlex.quote(b64)} | base64 -d)\" {tok_args} {args.limit}")
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", args.host, "bash -lc " + shlex.quote(remote)]
    try:
        out = subprocess.check_output(cmd, text=True, timeout=60)
    except subprocess.CalledProcessError as e:
        if e.returncode == 1:
            out = ""  # grep returns 1 on no-match — not an error
        else:
            sys.exit(f"error: remote search failed (exit {e.returncode}): {e}")
    except subprocess.TimeoutExpired:
        sys.exit("error: remote search timed out (>60s)")
    rows = []
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    if not rows:
        if args.json:
            print(format_json([]))
        else:
            print(f"(no matches on {args.host} for '{args.query}')")
        return
    if args.json:
        print(format_json([{"host": args.host, "session_id": r["sid"], "project": r["proj"],
                            "path": r["path"], "snippet": r["snip"]} for r in rows]))
        return
    print(f"Remote search on {args.host}: {len(rows)} match(es) for '{args.query}' (recency-ranked, with snippets)\n")
    for r in rows:
        print(f"  session={r['sid']}")
        print(f"    project={r['proj']}")
        print(f"    snippet: …{r['snip']}…")
        print(f"    drill: python3 {sys.argv[0]} turns --host {args.host} {r['sid'][:8]}")
        print()


def _self_session_ids():
    """The calling Claude Code session and its subagent transcripts, from
    CLAUDE_CODE_SESSION_ID. Empty outside Claude Code."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if not sid:
        return set()
    ids = {sid}
    root = Path.home() / ".claude" / "projects"
    for sub in root.glob(f"*/{sid}/subagents/agent-*.jsonl"):
        ids.add(sub.stem)
    return ids


def _short_sid(session_id):
    """Shortest unambiguous display form: Codex `rollout-<ts>-<uuid>` and
    subagent `agent-<hex>` ids share long prefixes, so keep those whole."""
    if session_id.startswith(("rollout-", "agent-")):
        return session_id
    return session_id[:8]


def _hidden_match_counts(conn, run, shown_ids, provider, include_subagents, in_scope=None):
    """How many more sessions match outside the default filters, so a search
    never silently hides where the answer is. `run(**overrides)` re-runs the
    same query with filters widened."""
    notes = []
    cap = 2000
    if provider != "all":
        other = {r[1] for r in run(session_ids=None, limit=cap)} - shown_ids - (in_scope or set())
        if other:
            notes.append(f"{len(other)} in other providers — add --provider all")
    if not include_subagents:
        subs = {r[1] for r in run(include_subagents=True, limit=cap)} - shown_ids
        subs = {s for s in subs if s.startswith("agent-")}
        if subs:
            notes.append(f"{len(subs)} in subagent transcripts — add --include-subagents")
    return notes


def cmd_search(args):
    """FTS5 full-text search. Defaults to Claude-only (--provider claude); the user's
    interaction is always with Claude and codex/cursor are subagent-like."""
    if getattr(args, "user_only", False):
        args.role = ["user"]  # prioritize human-typed prompts
        # Claude stores tool results under role=user; keep only chat text so --user
        # means what the human typed, not tool output.
        args.tier = "shallow"
    if getattr(args, "host", None):
        return cmd_search_remote(args)
    conn = create_db()
    pending = refresh_index(conn, force=args.reindex, project_filter=args.project, quiet=not args.reindex)

    # Resolve session/provider filters into a single set of allowed
    # session IDs that we'll AND-restrict the SQL query to.
    #   --session <id>      → set of one (after find_session disambiguation)
    #   --provider <name>   → set of all sessions belonging to that provider
    #   both                → intersection (so a Codex session + provider=claude
    #                         yields empty, which is the right semantics)
    #   neither             → None (no session_id restriction)
    session_ids = None
    single_session_mode = False  # tracked for the dedup decision below

    if getattr(args, "session", None):
        match = find_session(args.session, project_filter=args.project)
        if match is None:
            sys.exit(1)  # find_session already printed the error
        session_ids = {match["session_id"]}
        single_session_mode = True

    provider = getattr(args, "provider", None)
    if provider and provider != "all":
        target_group = PROVIDER_GROUPS.get(provider.lower())
        if target_group is None:
            print(f"Unknown provider: {provider}. Choices: all, claude, codex, cursor", file=sys.stderr)
            sys.exit(1)
        provider_ids = {s["session_id"] for s in discover_sessions(args.project) if s["group"] == target_group}
        session_ids = provider_ids if session_ids is None else (session_ids & provider_ids)

    # Backwards compat: --deep-only and --all map to --tier deep / --tier all.
    # Their semantics changed slightly (the new "all" is blended single-pass,
    # while the old --all was sequential), but the user-facing intent is the
    # same: "search both shallow and deep" or "search only deep".
    tier = getattr(args, "tier", "all") or "all"
    if getattr(args, "deep_only", False):
        tier = "deep"
    if getattr(args, "all", False):
        tier = "all"

    # Date filters: --after is passed through as-is (lexicographic >= match),
    # --before is rewritten to day-after (strict <) so the user's date is inclusive.
    after_bound = getattr(args, "after", None)
    before_bound = _before_to_exclusive_upper(getattr(args, "before", None))

    # --limit counts SESSIONS (results are one row per session), so fetch enough
    # rows that one chatty session can't use up the whole quota before dedup.
    fetch_limit = args.limit if single_session_mode else min(max(args.limit * 20, 200), 5000)
    include_self = getattr(args, "include_self", False)
    exclude_ids = set() if (include_self or single_session_mode) else _self_session_ids()
    hide_echo = not include_self

    # Multi-concept AND: if ≥ 2 --concept flags are given, intersect sessions
    # across all concepts. The positional `query` is ignored in that path.
    concepts = getattr(args, "concepts", None) or []
    concepts = [c.strip() for c in concepts if c and c.strip()]

    if concepts:
        if len(concepts) < 2:
            print(f"Multi-concept search requires at least 2 --concept flags. Got {len(concepts)}.",
                  file=sys.stderr)
            sys.exit(1)
        results = multi_concept_search(
            conn, concepts, fetch_limit,
            project_filter=args.project,
            roles=args.role,
            tier=tier,
            phrase_mode=getattr(args, "phrase", False),
            session_ids=session_ids,
            include_sidechain=getattr(args, "include_sidechain", False),
            include_subagents=getattr(args, "include_subagents", False),
            after=after_bound,
            before=before_bound,
            exclude_ids=exclude_ids,
            hide_echo=hide_echo,
        )
    else:
        if not args.query:
            print("search: provide a positional query or use --concept X --concept Y.",
                  file=sys.stderr)
            sys.exit(1)
        results = search_cascade(
            conn, args.query, fetch_limit,
            project_filter=args.project,
            roles=args.role,
            tier=tier,
            phrase_mode=getattr(args, "phrase", False),
            session_ids=session_ids,
            include_sidechain=getattr(args, "include_sidechain", False),
            include_subagents=getattr(args, "include_subagents", False),
            after=after_bound,
            before=before_bound,
            exclude_ids=exclude_ids,
            hide_echo=hide_echo,
        )

    # Deduplicate by session_id (keep highest-ranked hit per session).
    # Skip dedup when the user explicitly filters to one single session —
    # they want every hit inside it, not collapsed to one.
    if single_session_mode:
        deduped = list(results)
    else:
        seen_sessions = {}
        deduped = []
        for row in results:
            sid = row[1]
            if sid not in seen_sessions:
                seen_sessions[sid] = True
                deduped.append(row)
        deduped = deduped[:args.limit]

    # Matches the default filters hid (other providers, subagent transcripts).
    hidden_notes = []
    if not concepts and not single_session_mode:
        def _rerun(**over):
            kw = dict(project_filter=args.project, roles=args.role, tier=tier,
                      phrase_mode=getattr(args, "phrase", False), session_ids=session_ids,
                      include_sidechain=getattr(args, "include_sidechain", False),
                      include_subagents=getattr(args, "include_subagents", False),
                      after=after_bound, before=before_bound,
                      exclude_ids=exclude_ids, hide_echo=hide_echo)
            limit = over.pop("limit", fetch_limit)
            kw.update(over)
            return search_cascade(conn, args.query, limit, **kw)
        hidden_notes = _hidden_match_counts(
            conn, _rerun, {r[1] for r in deduped}, provider or "all",
            getattr(args, "include_subagents", False), in_scope=session_ids)

    # Look up cwd + group for each unique session (cross-runtime — sessions
    # can come from Claude Code, Codex CLI, or Cursor).
    # Build a one-shot session_id → metadata map via discover_sessions() so
    # we don't pay O(N*M) for per-hit lookups.
    all_sessions_by_id = {s["session_id"]: s for s in discover_sessions()}
    session_meta = {}
    for _, session_id, *_ in deduped:
        if session_id in session_meta:
            continue
        s = all_sessions_by_id.get(session_id)
        cwd = ""
        group = "Claude Code"  # default if session not found (defensive)
        if s:
            group = s["group"]
            session_path = s["path"]
            if session_path.exists():
                try:
                    with open(session_path) as fh:
                        for line in fh:
                            try:
                                obj = json.loads(line)
                            except (json.JSONDecodeError, TypeError):
                                continue
                            if obj.get("cwd"):
                                cwd = obj["cwd"]
                                break
                            # Codex nests cwd inside payload (session_meta)
                            payload = obj.get("payload")
                            if isinstance(payload, dict) and payload.get("cwd"):
                                cwd = payload["cwd"]
                                break
                except OSError:
                    pass
        session_meta[session_id] = {"cwd": cwd, "group": group}

    if args.json:
        rows = []
        for project, session_id, msg_index, role, depth, snippet, rank in deduped:
            meta = session_meta.get(session_id, {"cwd": "", "group": "Claude Code"})
            resume = build_resume_command(session_id, meta["cwd"], meta["group"])
            rows.append({
                "project": meta["cwd"] or decode_project(project),
                "session_id": session_id,
                "msg_index": msg_index,
                "role": role,
                "depth": "deep" if depth else "shallow",
                "snippet": snippet.replace("→", "<mark>").replace("←", "</mark>"),
                "score": round(-rank, 3),
                "resume": resume,
            })
        print(format_json(rows))
        for note in hidden_notes:
            print(f"cq: not shown: {note}", file=sys.stderr)
        return

    if not deduped:
        print("  No results found" + (" in the indexed transcripts." if pending else "."))
        for note in hidden_notes:
            print(f"  Not shown: {note}")
        if pending:
            print(f"  ({pending} transcript(s) are still being indexed — retry shortly.)")
        print("  For an exact scan of the raw files (commands, paths, error text), use `cq grep`.")
        return

    resume_hints = []
    for i, (project, session_id, msg_index, role, depth, snippet, rank) in enumerate(deduped, 1):
        depth_tag = " [DEEP]" if depth else ""
        if USE_COLOR:
            snippet_display = snippet.replace("→", "\033[1m").replace("←", "\033[0m").replace("\n", " ")
        else:
            snippet_display = snippet.replace("→", "").replace("←", "").replace("\n", " ")
        meta = session_meta.get(session_id, {"cwd": "", "group": "Claude Code"})
        proj = meta["cwd"] or short_project(project)
        resume = build_resume_command(session_id, meta["cwd"], meta["group"])
        print(f"  [{i}] {role:9}{depth_tag}  {proj}")
        sid_disp = _short_sid(session_id) if session_id.startswith(("rollout-", "agent-")) else session_id[:12] + "…"
        print(f"      session={sid_disp}  msg[{msg_index}]  score={-rank:.2f}")
        print(f"      {snippet_display}")
        # Drill-in: msg[N] is a flat BLOCK index, NOT a turn number, so `cq context
        # <session> <N>` fails ("Turn N not found"). `cq raw` takes the msg index
        # directly, so it's the reliable copy-paste drill from a search hit.
        print(f"      drill: python3 {sys.argv[0]} context {_short_sid(session_id)} @{msg_index}"
              f"   |   raw: python3 {sys.argv[0]} raw {_short_sid(session_id)} {msg_index}")
        if not AGENT_MODE:
            print(f"      {_dim(resume)}")
        else:
            resume_hints.append(f"  [{i}] {resume}")
        print()

    print(f"  ({len(deduped)} session(s) shown)")
    for note in hidden_notes:
        print(f"  Not shown: {note}")
    if AGENT_MODE and resume_hints:
        print()
        print("Resume commands:")
        for line in resume_hints:
            print(line)


REMOTE_CQ = "~/.claude/skills/browse-chat-history/scripts/cq.py"


def _run_remote_cq(host, argv, timeout=240, fallback_ok=False, capture=False):
    """Run cq itself on `host` (its own index, its own non-blocking refresh)
    and stream the output. Returns the exit code, or None when the remote has
    no cq install and `fallback_ok` lets the caller fall back. With capture=True
    returns (exit code, stdout) instead of printing."""
    remote = (f"f={REMOTE_CQ}; [ -f $f ] || exit 86; "
              f"exec python3 $f {' '.join(shlex.quote(a) for a in argv)}")
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, "bash -lc " + shlex.quote(remote)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        sys.exit(f"error: cq on {host} timed out (>{timeout}s)")
    if r.returncode == 86:
        if fallback_ok:
            return None
        sys.exit(f"error: no cq install on {host} ({REMOTE_CQ} missing)")
    if r.returncode == 255:
        if fallback_ok:
            print(f"# ssh {host} failed: {r.stderr.strip()[-200:]}", file=sys.stderr)
            return None
        sys.exit(f"error: ssh to {host} failed: {r.stderr.strip()[-300:]}")
    if capture:
        return r.returncode, r.stdout
    sys.stdout.write(r.stdout)
    if r.stderr:
        sys.stderr.write(r.stderr)
    if r.stdout:
        print(f"  (ran on {host}; drill into a hit from here with: cq <command> --host {host} <session>)",
              file=sys.stderr)
    return r.returncode


# ── cq grep: literal search over the raw transcript files ──────────────────
# The FTS index answers "which conversations were about X"; grep answers
# "where exactly does this string appear, first/last/how often" — the
# question behind most of the raw `grep ~/.claude/projects` workarounds.
# It scans the JSONL files themselves, so it can't miss what the index
# skipped, then labels each hit by where it sits in the transcript.

# Fields that only duplicate content found elsewhere (a full copy of an
# edited file, the session-start skill list, …). Hidden unless --all-fields.
_GREP_NOISE = "noise"


def _parse_since(value):
    """'3d' / '12h' / '90m' / 'YYYY-MM-DD' → UTC ISO string for comparison."""
    if not value:
        return None
    m = re.fullmatch(r"(\d+)\s*([mhdw])", value.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n),
                 "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
        from datetime import timezone
        return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%S")
    if not _ISO_DATE_RE.match(value.strip()):
        sys.exit(f"grep: --since '{value}' isn't a date (YYYY-MM-DD) or a span like 90m / 12h / 3d / 2w")
    return value.strip()


def _grep_locate(entry, pat):
    """Where in a transcript entry the pattern matches.
    Returns (label, text_of_matching_field) for the most meaningful field,
    or (_GREP_NOISE, text) when it only matches duplicate/metadata fields."""
    t = entry.get("type")
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else None

    def hit(s):
        return isinstance(s, str) and pat.search(s) is not None

    # Claude Code / Cursor message entries
    msg = entry.get("message") if isinstance(entry.get("message"), dict) else None
    role = t if t in ("user", "assistant") else entry.get("role")
    if msg is not None and role in ("user", "assistant"):
        content = msg.get("content")
        if hit(content):
            label = role
            if role == "user" and content.lstrip().startswith("Base directory for this skill"):
                label = "skill-doc"
            return label, content
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text" and hit(b.get("text")):
                    txt = b["text"]
                    label = "skill-doc" if txt.lstrip().startswith("Base directory for this skill") else role
                    return label, txt
                if bt == "thinking" and hit(b.get("thinking")):
                    return "thinking", b["thinking"]
                if bt == "tool_use":
                    inp = b.get("input")
                    for v in (inp.values() if isinstance(inp, dict) else [inp]):
                        if not isinstance(v, str):  # MultiEdit edits, TodoWrite todos, …
                            v = json.dumps(v, ensure_ascii=False)
                        if hit(v):
                            return f"tool-input:{b.get('name', '?')}", v
                if bt == "tool_result":
                    out = b.get("content")
                    if hit(out):
                        return "tool-output", out
                    if isinstance(out, list):
                        for ob in out:
                            if isinstance(ob, dict) and hit(ob.get("text")):
                                return "tool-output", ob["text"]
        return _GREP_NOISE, json.dumps(entry, ensure_ascii=False)
    if t == "attachment":
        att = entry.get("attachment") if isinstance(entry.get("attachment"), dict) else {}
        if att.get("type") == "queued_command" and hit(json.dumps(att.get("prompt"), ensure_ascii=False)):
            p = att.get("prompt")
            return "user (queued)", p if isinstance(p, str) else json.dumps(p, ensure_ascii=False)
        return _GREP_NOISE, json.dumps(entry, ensure_ascii=False)

    # Codex CLI
    if payload is not None:
        pt = payload.get("type")
        if t == "response_item":
            if pt == "message":
                r = payload.get("role")
                for b in payload.get("content") or []:
                    if isinstance(b, dict) and hit(b.get("text")):
                        label = {"user": "user", "assistant": "assistant"}.get(r, _GREP_NOISE)
                        if label == "assistant" and payload.get("phase") == "commentary":
                            label = "thinking"
                        return label, b["text"]
            if pt in ("function_call", "custom_tool_call"):
                v = payload.get("arguments") if pt == "function_call" else payload.get("input")
                if hit(v):
                    return f"tool-input:{payload.get('name', '?')}", v
            if pt in ("function_call_output", "custom_tool_call_output"):
                v = payload.get("output")
                if not isinstance(v, str):
                    v = json.dumps(v, ensure_ascii=False)
                if hit(v):
                    return "tool-output", v
            if pt == "reasoning":
                return "thinking", json.dumps(payload.get("summary"), ensure_ascii=False)
        if t == "event_msg" and pt in ("user_message", "agent_message") and hit(payload.get("message")):
            return ("user" if pt == "user_message" else "assistant"), payload["message"]
    return _GREP_NOISE, json.dumps(entry, ensure_ascii=False)


def _grep_snippet(text, pat, width=220):
    m = pat.search(text)
    if not m:
        return truncate(text, width)
    a = max(0, m.start() - width // 3)
    s = text[a:a + width].replace("\n", " ")
    return ("…" if a else "") + re.sub(r"\s+", " ", s).strip() + "…"


def _grep_scan_file(job):
    """Worker (one transcript file): literal-match its bytes, then label every
    matching line. Runs in a process pool — a parallel mmap scan of ~11 GB
    takes ~5 s here, where BSD grep took ~70 s for the same needle."""
    import mmap
    path, needle, ignore_case, since, all_fields = job
    flags = re.IGNORECASE if ignore_case else 0
    # The files are JSON: a quote, backslash, tab or newline in the needle is
    # stored escaped, and non-ASCII may be raw UTF-8 or \\uXXXX. Match any form.
    forms = {needle.encode(), json.dumps(needle)[1:-1].encode(),
             json.dumps(needle, ensure_ascii=False)[1:-1].encode()}
    bpat = re.compile(b"|".join(re.escape(f) for f in sorted(forms)), flags)
    try:
        with open(path, "rb") as f:
            if os.fstat(f.fileno()).st_size == 0:
                return None
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
                if len(forms) == 1 and not ignore_case:
                    if m.find(needle.encode()) == -1:
                        return None
                elif not bpat.search(m):
                    return None
    except (OSError, ValueError):
        return None
    pat = re.compile(re.escape(needle), flags)
    matches, last_ts, noise, cwd = [], "", 0, ""
    with open(path, "rb") as f:
        for lineno, raw in enumerate(f):
            if not cwd and lineno < 50 and b'"cwd"' in raw:
                m = re.search(rb'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
                if m:
                    cwd = json.loads(b'"' + m.group(1) + b'"')
            if not bpat.search(raw):
                continue
            try:
                entry = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(entry, dict):
                continue
            ts = entry.get("timestamp") or last_ts
            last_ts = ts or last_ts
            if since and ts and ts < since:
                continue
            label, text = _grep_locate(entry, pat)
            if label == _GREP_NOISE and not all_fields:
                noise += 1
                continue
            matches.append({"line": lineno, "ts": ts or "", "where": label,
                            "snippet": _grep_snippet(text, pat)})
    return path, matches, noise, cwd


def cmd_grep(args):
    """Literal search over raw transcripts with first/last/count and drill lines."""
    if getattr(args, "host", None):
        sys.exit(_run_remote_cq(args.host, args._argv) or 0)
    needle = args.text
    if not needle:
        sys.exit("grep: provide the literal text to find")
    since = _parse_since(args.since)

    # --project is matched against each session's real cwd after the scan (and
    # the Claude project dir name): Codex keeps transcripts in date folders, so
    # filtering at discovery would silently drop every Codex lane.
    sessions = discover_sessions()
    if args.provider != "all":
        group = PROVIDER_GROUPS.get(args.provider)
        sessions = [s for s in sessions if s["group"] == group]
    if not args.include_self:
        mine = _self_session_ids()
        sessions = [s for s in sessions if s["session_id"] not in mine]
    if since:
        cutoff = datetime.fromisoformat(since[:19]).timestamp() - 86400  # mtime is local; be generous
        sessions = [s for s in sessions if s["path"].stat().st_mtime >= cutoff]
    by_path = {str(s["path"]): s for s in sessions}
    jobs = [(p, needle, args.ignore_case, since, args.all_fields) for p in by_path]
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor() as pool:
        scanned = [r for r in pool.map(_grep_scan_file, jobs, chunksize=32) if r]

    results = []
    noise_only = 0
    for fp, matches, noise, cwd in scanned:
        s = by_path[fp]
        if args.project and args.project.lower() not in (cwd + " " + s["project"]).lower():
            continue
        if not matches:
            noise_only += 1 if noise else 0
            continue
        stamps = [m["ts"] for m in matches if m["ts"]]
        results.append({
            "session_id": s["session_id"], "provider": s["group"], "kind": s.get("kind", "main"),
            "project": cwd or decode_project(s["project"]),
            "path": fp, "count": len(matches),
            "first": min(stamps) if stamps else "", "last": max(stamps) if stamps else "",
            "matches": matches,
        })

    if args.first:
        results.sort(key=lambda r: r["first"] or "9999")
    else:
        results.sort(key=lambda r: r["last"], reverse=True)
    if args.count:
        results.sort(key=lambda r: r["count"], reverse=True)
    total_matches = sum(r["count"] for r in results)
    shown = results[:args.limit]

    if args.json:
        for r in shown:
            ms = r["matches"]
            r["matches"] = (ms[:args.per_session] if args.first else ms[-args.per_session:])
        print(format_json({"query": needle, "sessions": len(results), "matches": total_matches,
                           "results": shown}))
        return

    scope = "all providers" if args.provider == "all" else args.provider
    if not results:
        print(f"  No literal match for '{needle}' ({scope}{', since ' + args.since if since else ''}).")
        if noise_only:
            print(f"  ({noise_only} file(s) contain it only in duplicate/metadata fields — add --all-fields.)")
        return
    order = "earliest first" if args.first else ("by count" if args.count else "most recent first")
    print(f"  '{needle}': {total_matches} match(es) in {len(results)} session(s) ({scope}; {order})\n")
    me = sys.argv[0]
    for r in shown:
        sid = r["session_id"]
        short = _short_sid(sid)
        span = r["first"][:16].replace("T", " ")
        if r["last"][:16] != r["first"][:16]:
            span += " → " + r["last"][:16].replace("T", " ")
        tag = f"{r['provider']}{' subagent' if r['kind'] == 'subagent' else ''}"
        print(f"  {sid}  [{tag}]  {r['count']}×  {span}")
        print(f"      {r['project']}")
        if args.count:
            print()
            continue
        ms = r["matches"][:args.per_session] if args.first else r["matches"][-args.per_session:]
        for m in ms:
            print(f"      L{m['line']}  {m['ts'][:16].replace('T', ' ')}  {m['where']}: {m['snippet']}")
        print(f"      drill: python3 {me} context {short} @{ms[-1]['line']}"
              f"   |   raw: python3 {me} raw {short} --start {ms[-1]['line']} --end {ms[-1]['line']}")
        print()
    if len(results) > len(shown):
        print(f"  … {len(results) - len(shown)} more session(s) — raise --limit")
    if noise_only:
        print(f"  ({noise_only} more file(s) match only in duplicate/metadata fields — --all-fields shows them)")


def cmd_list(args):
    """List sessions with metadata."""
    sessions = discover_sessions(args.project)
    if not getattr(args, "include_subagents", False):
        sessions = [s for s in sessions if s.get("kind", "main") != "subagent"]
    if not sessions:
        print("No sessions found.")
        return

    if args.json:
        rows = []
        for s in sessions:
            info = get_session_info(s)
            rows.append({
                "session_id": info["session_id"],
                "group": info["group"],
                "project": decode_project(info["project"]),
                "size": info["size_str"],
                "date": info["date"],
                "cwd": info["cwd"],
                "preview": info["preview"],
            })
        print(format_json(rows))
        return

    for s in sessions:
        info = get_session_info(s)
        cwd_suffix = f"  [{info['cwd']}]" if info["cwd"] else ""
        print(f"{info['size_str']:>9}  {info['date']}  {info['group']:12}  {info['session_id'][:12]}…  {info['preview']}{cwd_suffix}")


def cmd_recent(args):
    """Show last N active sessions with resume commands."""
    sessions = discover_sessions(args.project)
    if not getattr(args, "include_subagents", False):
        sessions = [s for s in sessions if s.get("kind", "main") != "subagent"]
    active = [s for s in sessions if s["path"].stat().st_size > 10240]
    recent = active[-args.count:]

    if not recent:
        print("No active sessions found.")
        return

    if args.json:
        rows = []
        for s in recent:
            info = get_session_info(s)
            rows.append({
                "session_id": info["session_id"],
                "group": info["group"],
                "project": decode_project(info["project"]),
                "size": info["size_str"],
                "date": info["date"],
                "cwd": info["cwd"],
                "preview": info["preview"],
                "resume": build_resume_command(info["session_id"], info["cwd"], info["group"]),
            })
        print(format_json(rows))
        return

    print(f"Last {len(recent)} active sessions:\n")
    for i, s in enumerate(recent, 1):
        info = get_session_info(s)
        print(f"# {i}. {info['date']} | {info['size_str']:>7} | {info['group']:12} | {info['preview']}")
        print(build_resume_command(info["session_id"], info["cwd"], info["group"]))
        print()


def cmd_turns(args):
    """Show structured turns from a session."""
    session = _resolve_session(args)
    if not session:
        return

    text = session["path"].read_text()
    turns, fmt = parse_transcript(text)

    # Exclude inline teammate/sidechain turns unless user opts in.
    # Skip this filter when the whole file IS a subagent transcript —
    # every entry carries isSidechain=true by construction, so filtering
    # would strip everything. The user already opted in by addressing
    # the subagent session directly.
    if (not getattr(args, "include_sidechain", False)
            and session.get("kind", "main") != "subagent"):
        turns = [t for t in turns if not t.get("is_sidechain")]

    # Apply range filter
    if args.range:
        parts = args.range.split("-")
        start, end = int(parts[0]), int(parts[1])
        turns = [t for t in turns if start <= t["index"] <= end]

    if args.json:
        print(format_json(turns))
        return

    print(f"Session: {session['session_id'][:12]}… ({session['group']}, {fmt})")
    print(f"Turns: {len(turns)}\n")

    for turn in turns:
        ts = (turn.get("timestamp") or "")[:19]
        user = truncate(turn.get("user_text", ""), 100)
        n_text = sum(1 for b in turn["blocks"] if b["kind"] == "text")
        n_think = sum(1 for b in turn["blocks"] if b["kind"] == "thinking")
        n_tool = sum(1 for b in turn["blocks"] if b["kind"] == "tool_use")
        n_err = sum(1 for b in turn["blocks"] if b["kind"] == "tool_use" and b.get("tool_call", {}).get("is_error"))

        blocks_summary = []
        if n_text:
            blocks_summary.append(f"{n_text} text")
        if n_think:
            blocks_summary.append(f"{n_think} thinking")
        if n_tool:
            tool_str = f"{n_tool} tool"
            if n_err:
                tool_str += f" ({n_err} err)"
            blocks_summary.append(tool_str)

        print(f"  [{turn['index']:3}] {ts}  [{', '.join(blocks_summary)}]")
        if user:
            print(f"        User: {user}")

        # Show tool calls in compact form
        for b in turn["blocks"]:
            if b["kind"] == "tool_use" and b.get("tool_call"):
                tc = b["tool_call"]
                err_mark = " ✗" if tc.get("is_error") else ""
                inp_preview = format_tool_input(tc.get("input", {}), 80)
                print(f"        → {tc['name']}{err_mark}: {inp_preview}")
        print()


def cmd_tools(args):
    """Show tool calls WITH results from a session."""
    session = _resolve_session(args)
    if not session:
        return

    text = session["path"].read_text()
    turns, fmt = parse_transcript(text)

    tool_calls = []
    for turn in turns:
        for b in turn["blocks"]:
            if b["kind"] != "tool_use" or not b.get("tool_call"):
                continue
            tc = b["tool_call"]
            if args.name and args.name.lower() not in tc["name"].lower():
                continue
            if args.errors and not tc.get("is_error"):
                continue
            tool_calls.append({
                "turn": turn["index"],
                "timestamp": b.get("timestamp"),
                **tc,
            })

    if args.json:
        print(format_json(tool_calls))
        return

    if not tool_calls:
        print("No matching tool calls found.")
        return

    print(f"Session: {session['session_id'][:12]}… — {len(tool_calls)} tool calls\n")
    for tc in tool_calls:
        ts = (tc.get("timestamp") or "")[:19]
        err_mark = " [ERROR]" if tc.get("is_error") else ""
        print(f"  Turn {tc['turn']:3} | {ts} | {tc['name']}{err_mark}")
        print(f"    input:  {format_tool_input(tc.get('input', {}), 150)}")
        result = tc.get("result")
        if result:
            result_preview = truncate(result, 150)
            print(f"    result: {result_preview}")
        print()


def cmd_thinking(args):
    """Extract thinking/reasoning blocks from a session."""
    session = _resolve_session(args)
    if not session:
        return

    text = session["path"].read_text()
    turns, fmt = parse_transcript(text)

    thinking_blocks = []
    for turn in turns:
        for b in turn["blocks"]:
            if b["kind"] == "thinking" and b.get("text"):
                thinking_blocks.append({
                    "turn": turn["index"],
                    "timestamp": b.get("timestamp"),
                    "text": b["text"],
                })

    if args.json:
        print(format_json(thinking_blocks))
        return

    if not thinking_blocks:
        print("No thinking blocks found (may not have extended thinking enabled).")
        return

    print(f"Session: {session['session_id'][:12]}… — {len(thinking_blocks)} thinking blocks\n")
    for tb in thinking_blocks:
        ts = (tb.get("timestamp") or "")[:19]
        preview = truncate(tb["text"], 200)
        print(f"  Turn {tb['turn']:3} | {ts}")
        print(f"    {preview}")
        print()


def cmd_files(args):
    """Show files touched in a session (from tool inputs)."""
    session = _resolve_session(args)
    if not session:
        return

    text = session["path"].read_text()
    turns, fmt = parse_transcript(text)

    file_ops = []
    for turn in turns:
        for b in turn["blocks"]:
            if b["kind"] != "tool_use" or not b.get("tool_call"):
                continue
            tc = b["tool_call"]
            inp = tc.get("input", {})
            fp = inp.get("file_path") or inp.get("path")
            if not fp:
                continue
            file_ops.append({
                "turn": turn["index"],
                "tool": tc["name"],
                "file": fp,
                "error": tc.get("is_error", False),
            })

    if args.json:
        print(format_json(file_ops))
        return

    if not file_ops:
        print("No file operations found.")
        return

    # Group by file
    by_file = {}
    for op in file_ops:
        by_file.setdefault(op["file"], []).append(op)

    print(f"Session: {session['session_id'][:12]}… — {len(by_file)} files, {len(file_ops)} operations\n")
    for fp, ops in sorted(by_file.items()):
        tools_used = sorted(set(op["tool"] for op in ops))
        errors = sum(1 for op in ops if op["error"])
        err_mark = f" ({errors} err)" if errors else ""
        print(f"  {fp}")
        print(f"    {', '.join(tools_used)} × {len(ops)}{err_mark}")


def _turn_containing_line(path, lineno):
    """Turn number that contains JSONL line `lineno` (what search prints as
    msg[N] and grep as LN): parse the transcript up to that line and take
    the last turn."""
    with open(path) as f:
        head = []
        for i, line in enumerate(f):
            head.append(line)
            if i >= lineno:
                break
    turns, _ = parse_transcript("".join(head))
    return turns[-1]["index"] if turns else None


def cmd_context(args):
    """Show full turn detail with all blocks.

    The turn can be a number, a range `N-M`, or a JSONL line reference —
    `@N` or `msg[N]`, exactly as `cq search` / `cq grep` print them — which
    resolves to the turn containing that line."""
    # turn can come from positional `turn` or --turn flag (used with --jsonl).
    spec = args.turn if args.turn is not None else getattr(args, "turn_flag", None)
    if spec is None:
        print("context: provide a turn number, N-M range, or @LINE / msg[LINE] "
              "(positional, or --turn with --jsonl).", file=sys.stderr)
        sys.exit(1)
    spec = str(spec).strip()

    session = _resolve_session(args)
    if not session:
        return

    line_ref = re.fullmatch(r"(?:@|msg\[|line:?)(\d+)\]?", spec)
    rng = re.fullmatch(r"(\d+)\s*[-:.]+\s*(\d+)", spec)
    if line_ref:
        lineno = int(line_ref.group(1))
        turn_no = _turn_containing_line(session["path"], lineno)
        if turn_no is None:
            print(f"Line {lineno} is not inside any turn of this session.")
            return
        print(f"(line {lineno} is in turn {turn_no})\n", file=sys.stderr)
        wanted = [turn_no]
    elif rng:
        a, b = int(rng.group(1)), int(rng.group(2))
        wanted = list(range(min(a, b), max(a, b) + 1))
    elif spec.isdigit():
        wanted = [int(spec)]
    else:
        sys.exit(f"context: can't read turn '{spec}' — use N, N-M, @LINE or msg[LINE]")

    text = session["path"].read_text()
    turns, fmt = parse_transcript(text)
    for n in wanted:
        args.turn = n
        _print_context_turn(args, session, turns)


def cmd_why(args):
    """Retrieve a diagnosis's later revisions and evidence on the kept path."""
    import why

    spec = args.turn if args.turn is not None else args.turn_flag
    if spec is None:
        sys.exit("why: provide a turn or @LINE (use --turn with --jsonl)")
    if args.limit < 1:
        sys.exit("why: --limit must be at least 1")
    session = _resolve_session(args)
    if not session:
        sys.exit(1)
    try:
        result = why.resolve(session, spec, args.term, args.limit, sys.modules[__name__])
        result["followed_sessions"] = why.follow_sessions(
            session, result["handles"], sys.modules[__name__], args.limit) if args.follow_sessions else []
    except (OSError, ValueError) as e:
        sys.exit(f"why: {e}")
    if args.json:
        print(format_json(result))
        return
    print(f"Session: {result['session']}")
    print("Handles: " + (", ".join(result["handles"]) or "(none)"))

    def show(label, item):
        if item:
            print(f"{label}: {item['timestamp']} {item['role']} [{item['kind']}] {item['snippet']}")
            print(f"  {item['drill']}")

    show("Anchor", result["anchor"])
    for item in result["revisions"]:
        show("→", item)
    if result["truncated"]:
        print(f"… {result['total_revisions'] - args.limit} more candidates (--limit to see)")
    show("Last statement about the topic", result["last_statement"])
    for label, key in (("Last passing run", "last_passing_run"),
                       ("Last edit", "last_edit"), ("Last commit command", "last_commit")):
        show(label, result["evidence"][key])
    for item in result["followed_sessions"]:
        show("Later session " + item["session"], item)
    for warning in result["warnings"]:
        print(f"Warning: {warning}")
    if result["stopped"]:
        stop = result["stopped"]
        print(f"Stopped before turn {stop['turn']} (@{stop['line']}): {stop['reason']}.")
    print(result["note"])


def _print_context_turn(args, session, turns):
    """Render one turn (args.turn) of an already-parsed transcript."""
    turn = next((t for t in turns if t["index"] == args.turn), None)
    if not turn:
        # Surface tool-call count + per-runtime hint. Agents calling
        # `cq context <session> N` for a Codex session often had a Claude-
        # shaped mental model (many user turns) — for Codex the navigation
        # surface is `cq tools` / `cq raw` and turn structure follows
        # assistant narrative beats, not user-message boundaries.
        tool_calls = sum(
            1 for t in turns for b in t.get("blocks", [])
            if b.get("kind") == "tool_use"
        )
        msg = f"Turn {args.turn} not found (session has {len(turns)} turn{'s' if len(turns) != 1 else ''}"
        if tool_calls:
            msg += f", {tool_calls} tool call{'s' if tool_calls != 1 else ''} across them"
        msg += ")."
        print(msg)
        print(f"→ If {args.turn} came from a search/grep hit (msg[{args.turn}] / L{args.turn}), that's a "
              f"JSONL line, not a turn: `cq context {session['session_id'][:12]} @{args.turn}`.")
        if session.get("group") == "Codex CLI" and len(turns) <= 1:
            print(
                "→ Codex sessions are typically one-shot tasks with many "
                "tool calls under a single narrative. Try `cq tools "
                f"{session['session_id'][:12]}` or `cq turns "
                f"{session['session_id'][:12]}` to navigate."
            )
        return

    if args.json:
        print(format_json(turn))
        return

    ts = (turn.get("timestamp") or "")[:19]
    print(f"Turn {turn['index']} | {ts} | {session['session_id'][:12]}…\n")

    if turn.get("user_text"):
        print("  USER:")
        for line in turn["user_text"].split("\n"):
            print(f"    {line}")
        print()

    if turn.get("system_events"):
        print(f"  SYSTEM EVENTS: {', '.join(turn['system_events'])}")
        print()

    for i, b in enumerate(turn["blocks"]):
        kind = b["kind"].upper()
        bts = (b.get("timestamp") or "")[:19]

        if b["kind"] == "text":
            print(f"  [{i + 1}] TEXT ({bts})")
            for line in b["text"].split("\n")[:20]:
                print(f"    {line}")
            if b["text"].count("\n") > 20:
                print(f"    ... ({b['text'].count(chr(10)) - 20} more lines)")
            print()

        elif b["kind"] == "thinking":
            print(f"  [{i + 1}] THINKING ({bts})")
            for line in b["text"].split("\n")[:10]:
                print(f"    {line}")
            if b["text"].count("\n") > 10:
                print(f"    ... ({b['text'].count(chr(10)) - 10} more lines)")
            print()

        elif b["kind"] == "tool_use":
            tc = b.get("tool_call", {})
            err_mark = " [ERROR]" if tc.get("is_error") else ""
            print(f"  [{i + 1}] TOOL: {tc.get('name', '?')}{err_mark} ({bts})")
            print(f"    input: {format_tool_input(tc.get('input', {}), 300)}")
            result = tc.get("result")
            if result:
                lines = result.split("\n")
                for line in lines[:15]:
                    print(f"    > {line}")
                if len(lines) > 15:
                    print(f"    > ... ({len(lines) - 15} more lines)")
            print()


def cmd_user(args):
    """Show all user messages from a session."""
    session = _resolve_session(args)
    if not session:
        return

    text = session["path"].read_text()
    turns, fmt = parse_transcript(text)

    # Exclude inline teammate/sidechain user-role entries unless user opts in.
    # Skip when the whole file is a subagent transcript (see cmd_turns).
    if (not getattr(args, "include_sidechain", False)
            and session.get("kind", "main") != "subagent"):
        turns = [t for t in turns if not t.get("is_sidechain")]

    if getattr(args, "limit", None):
        turns = [t for t in turns if t.get("user_text")][-args.limit:]

    if args.json:
        messages = [
            {"turn": t["index"], "timestamp": t.get("timestamp"), "text": t.get("user_text", "")}
            for t in turns if t.get("user_text")
        ]
        print(format_json(messages))
        return

    for turn in turns:
        ut = turn.get("user_text", "")
        if not ut or len(ut) < 5:
            continue
        ts = (turn.get("timestamp") or "")[:19]
        print(f"  [{turn['index']:3}] {ts}")
        print(f"    {truncate(ut, 300)}")
        print()


def cmd_raw(args):
    """Return raw JSONL lines at exact positions. Agent-friendly mode."""
    if getattr(args, "jsonl", None) and str(getattr(args, "session", "") or "").isdigit():
        args.pos = [int(args.session)] + list(getattr(args, "pos", None) or [])
        args.session = None
    session = _resolve_session(args)
    if not session:
        return

    path = session["path"]
    start = getattr(args, "start", None) or 0
    end = getattr(args, "end", None)
    pos = getattr(args, "pos", None) or []
    if pos:
        start = pos[0]
        end = pos[1] if len(pos) > 1 else (end if end is not None else pos[0])

    with open(path) as f:
        for lineno, line in enumerate(f):
            if lineno < start:
                continue
            if end is not None and lineno > end:
                break
            sys.stdout.write(line)


def cmd_resume(args):
    """Print resume command for a session.

    For remote sessions (--host), wrap the resume command in an ssh call so the
    user can run it directly. The cwd is the remote-side cwd recorded in the
    JSONL — Claude Code resolves it inside the remote shell.
    """
    session = _resolve_session(args)
    if not session:
        return

    # Subagent transcripts can't be resumed directly — claude --resume only
    # accepts top-level UUID-named JSONLs, and subagent files live one level
    # deeper under <parent-uuid>/subagents/. Two behaviors:
    #
    #   --from-subagent OFF (default): redirect to parent session. Every
    #     entry in a subagent JSONL carries the parent's UUID in `sessionId`,
    #     so we read line 1 to recover it.
    #
    #   --from-subagent ON: copy the subagent JSONL to a new top-level
    #     <new-uuid>.jsonl in the same project dir, rewriting sessionId and
    #     isSidechain on every line so `claude --resume <new-uuid>` loads the
    #     subagent's transcript as conversation history. Caveat: the resumed
    #     session runs the main agent's system prompt and tools, NOT the
    #     subagent's. Transcript context is intact; agent identity is not.
    if session.get("kind") == "subagent":
        if getattr(args, "from_subagent", False):
            import uuid as _uuid
            new_uuid = str(_uuid.uuid4())
            src = session["path"]
            project_dir = src.parent.parent.parent  # <project>/<parent-uuid>/subagents/<file>
            dst = project_dir / f"{new_uuid}.jsonl"
            # Byte-copy fails because claude --resume matches sessions by the
            # `sessionId` field inside the JSONL (not by filename), and every
            # entry still points at the parent UUID. It also filters
            # isSidechain=true entries during resume, which would empty the
            # transcript. Rewrite both fields on every line.
            try:
                with open(src) as fin, open(dst, "w") as fout:
                    for line in fin:
                        line = line.rstrip("\n")
                        if not line.strip():
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            fout.write(line + "\n")
                            continue
                        entry["sessionId"] = new_uuid
                        entry["isSidechain"] = False
                        fout.write(json.dumps(entry) + "\n")
            except OSError as e:
                print(f"# Failed to write resumable copy: {e}", file=sys.stderr)
                return
            # Recover cwd from first entry; fall back to project name decode.
            cwd = None
            try:
                with open(src) as f:
                    cwd = json.loads(f.readline()).get("cwd")
            except (OSError, json.JSONDecodeError):
                pass
            if not cwd:
                cwd = decode_project(project_dir.name)
            print(f"# Copied {src.name} → {dst.name}", file=sys.stderr)
            print(f"# Caveat: resume runs as MAIN agent (not the subagent persona).",
                  file=sys.stderr)
            print(f"# To clean up later: rm {dst}", file=sys.stderr)
            print(build_resume_command(new_uuid, cwd, session["group"]))
            return

        parent_uuid = None
        try:
            with open(session["path"]) as f:
                first = json.loads(f.readline())
            parent_uuid = first.get("sessionId")
        except (OSError, json.JSONDecodeError):
            pass
        if not parent_uuid:
            print(f"# Could not recover parent session for {session['session_id']}",
                  file=sys.stderr)
            return
        parent = find_session(parent_uuid)
        if not parent:
            print(f"# Parent session {parent_uuid} not found on disk", file=sys.stderr)
            return
        parent_info = get_session_info(parent)
        local_cmd = build_resume_command(parent_info["session_id"],
                                         parent_info["cwd"], parent_info["group"])
        print(f"# {session['session_id']} is a subagent transcript — "
              f"resuming parent session {parent_uuid[:8]}… instead.",
              file=sys.stderr)
        print(f"# (Use --from-subagent to reopen the subagent's transcript instead.)",
              file=sys.stderr)
        print(local_cmd)
        return

    info = get_session_info(session)
    local_cmd = build_resume_command(info["session_id"], info["cwd"], info["group"])
    host = session.get("remote_host")
    if host:
        # `bash -lc` forces a login shell on the remote so `~/.local/bin`,
        # nvm/npm dirs, and any PATH augmentation from .profile / .bash_profile
        # are picked up. Without it, non-interactive SSH bypasses .bashrc and
        # `claude`/`codex`/`cursor-agent` typically aren't on PATH.
        print(f"ssh -t {host} {shlex.quote(f'bash -lc {shlex.quote(local_cmd)}')}")
    else:
        print(local_cmd)


def cmd_info(args):
    """Compact metadata for a single session.

    Companion to `cq list`/`recent`/`search` for the "I have a known UUID,
    what is this session" lookup. Use this before drilling into `cq turns`
    when you want kind/runtime/project/timestamps without reading 200 turns.
    """
    session = _resolve_session(args)
    if not session:
        return
    info = get_session_info(session)
    msg_count = 0
    file_count = 0
    last_ts = ""
    file_paths = set()
    with open(info["path"]) as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            msg_count += 1
            if obj.get("timestamp"):
                last_ts = obj["timestamp"][:19]
            msg = obj.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        inp = block.get("input") or {}
                        for key in ("file_path", "path", "notebook_path"):
                            if inp.get(key):
                                file_paths.add(inp[key])
    file_count = len(file_paths)

    if args.json:
        print(format_json({
            "session_id": info["session_id"],
            "kind": info.get("kind", "main"),
            "group": info["group"],
            "project": info.get("project", ""),
            "cwd": info.get("cwd", ""),
            "first_ts": info["date"],
            "last_ts": last_ts,
            "msg_count": msg_count,
            "unique_files_touched": file_count,
            "preview": info.get("preview", ""),
            "size": info.get("size_str", ""),
            "path": str(info["path"]),
        }))
        return

    print(f"session     {info['session_id']}")
    print(f"kind        {info.get('kind', 'main')}")
    print(f"runtime     {info['group']}")
    print(f"project     {info.get('project', '')}")
    if info.get("cwd"):
        print(f"cwd         {info['cwd']}")
    print(f"first_ts    {info['date']}")
    if last_ts:
        print(f"last_ts     {last_ts}")
    print(f"messages    {msg_count}")
    print(f"files       {file_count} unique paths touched")
    print(f"size        {info.get('size_str', '?')}")
    if info.get("preview"):
        print(f"intent      {info['preview']}")


# ── Provenance: "which agent/session wrote, edited, or read this path" ────

# Tool-name → op-kind classification. Authored (edit) means the tool can
# create or modify file content. Inspected (read) means the tool reads
# content. Anything else with a path field is a `mention` — file appears
# in input metadata but the op didn't change or read it (rare; e.g.
# `Glob` patterns).
_PROVENANCE_AUTHORED = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Update"}
_PROVENANCE_INSPECTED = {"Read", "NotebookRead"}

# Tool-input fields known to carry file paths. Searched substring-wise.
_PROVENANCE_PATH_FIELDS = ("file_path", "path", "notebook_path", "notebookPath",
                           "filePath", "filepath")


_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$|^\*\*\* Move to:\s*(.+?)\s*$", re.M)


def _path_matches(candidate, path_query, base_dir=""):
    """Does a path written in a command/patch refer to path_query? Paths are
    compared whole (or as a suffix at a `/` boundary for a fragment query),
    never as substrings — `cq.py.bak` is not `cq.py`. A relative path is
    resolved against base_dir (the command's current `cd`, else the session
    cwd). A bare filename only matches when the QUERY is a bare filename."""
    candidate = os.path.expanduser(candidate.strip().strip("'\""))
    query = path_query.rstrip("/")
    if not candidate or not query:
        return False
    if "/" not in query:
        return os.path.basename(candidate.rstrip("/")) == query
    full = candidate
    if not candidate.startswith("/") and base_dir:
        full = os.path.normpath(os.path.join(os.path.expanduser(base_dir), candidate))
    full = full.rstrip("/")
    if full == query:
        return True
    if not query.startswith("/") and full.endswith("/" + query):
        return True  # fragment query: site/index.html
    if not full.startswith("/") and "/" in full and query.endswith("/" + full):
        return True  # relative path we couldn't resolve, multi-component
    return False


def _patch_files(text):
    # Codex's `exec` tool carries the patch inside a JS string literal, so its
    # newlines arrive as the two characters backslash-n.
    text = (text or "").replace("\\n", "\n")
    return [a or b for a, b in _PATCH_FILE_RE.findall(text)]


_ASSIGN_RE = re.compile(r"""(?:^|[\s;&|(])([A-Za-z_][A-Za-z0-9_]*)=(?:"([^"]*)"|'([^']*)'|([^\s;&|)]+))""")
_CD_RE = re.compile(r"""(?:^|[\s;&|(])cd\s+("[^"]+"|'[^']+'|[^\s;&|)]+)""")


def _expand_shell_vars(cmd):
    """Substitute variables assigned in the same command (D=/x; cat > $D/f)."""
    env = {}
    for name, dq, sq, bare in _ASSIGN_RE.findall(cmd):
        env[name] = dq or sq or bare
    if not env:
        return cmd
    def sub(m):
        return env.get(m.group(1) or m.group(2), m.group(0))
    for _ in range(2):  # one level of nesting (B=$A/sub)
        cmd = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", sub, cmd)
    return cmd


def _sed_targets(words):
    """File operands of an in-place sed/perl, skipping options and the script."""
    files, script_given, i = [], False, 1
    while i < len(words):
        w = words[i]
        if w in ("-e", "-f", "--expression", "--file"):
            script_given = True
            i += 2
            continue
        if w.startswith("-") or w in ("''", '""'):
            i += 1
            continue
        if not script_given:
            script_given = True  # first operand is the script
        else:
            files.append(w)
        i += 1
    return files


def _shell_segments(cmd, cwd=""):
    """Yield (cwd, segment, words), resolving assignments and leading cd's.

    These are transcript heuristics, not shell execution. shlex preserves quoted
    paths with spaces; incomplete heredoc/code fragments use a token fallback.
    """
    base = cwd
    for seg in re.split(r"&&|\|\||[;|\n]", _expand_shell_vars(cmd)):
        try:
            words = shlex.split(seg)
        except ValueError:
            words = seg.split()
        if not words:
            continue
        if words[0] == "cd" and len(words) > 1:
            target = os.path.expanduser(words[1])
            base = os.path.normpath(os.path.join(base, target))
        yield base, seg, words


def _shell_references(cmd, path_query, cwd=""):
    """Match shell paths with the same resolution used for shell writes.

    A fragment query must match a resolved path, never merely its basename.
    Bare queries deliberately retain the broad literal filename prefilter.
    """
    if not path_query or not cmd:
        return False
    if "/" not in path_query:
        return path_query in cmd
    for base, seg, words in _shell_segments(cmd, cwd):
        candidates = list(words)
        candidates += re.findall(r'''["']([^"'\n]+)["']''', seg)
        candidates += re.findall(r"(?:~?/|\.{1,2}/|[A-Za-z_][\w.-]*/)[\w./@+~$:-]+", seg)
        for candidate in candidates:
            # Shell assignments, --file=PATH, and attached redirect operands.
            if "=" in candidate:
                candidate = candidate.split("=", 1)[1]
            candidate = re.sub(r"^[0-9]*[<>]+", "", candidate).strip("'\"(),")
            if _path_matches(candidate, path_query, base):
                return True
    return False


def _shell_writes(cmd, path_query, cwd=""):
    """True if a shell command WRITES path_query (vs. merely mentioning it):
    redirects, tee, the destination of cp/mv/install/rsync, sed -i / perl -i,
    Python open(..., 'w') / write_text, or an apply_patch body inside the
    command. Segments are walked in order so each write resolves against the
    `cd` in effect at that point."""
    if not cmd:
        return False
    for base, seg, words in _shell_segments(cmd, cwd):
        if any(_path_matches(f, path_query, base) for f in _patch_files(seg)):
            return True
        verb = os.path.basename(words[0])
        if verb == "cd":
            continue
        for m in re.finditer(r"(?<![<>&0-9])>>?\s*(\"[^\"]+\"|'[^']+'|[^\s;&|<>()]+)", seg):
            if _path_matches(m.group(1), path_query, base):
                return True
        if verb == "tee" and any(_path_matches(w, path_query, base) for w in words[1:] if not w.startswith("-")):
            return True
        if verb in ("cp", "mv", "install", "rsync", "ln") and len(words) > 2 \
                and _path_matches(words[-1], path_query, base):
            return True
        if verb in ("sed", "perl") and any(w.startswith("-i") or w == "-pi" for w in words[1:]) \
                and any(_path_matches(w, path_query, base) for w in _sed_targets(words)):
            return True
        python_targets = re.findall(r'''open\(\s*["']([^"']+)["']\s*,\s*["'][wax]''', seg)
        python_targets += re.findall(r'''["']([^"']+)["']\s*\)\s*\.write_(?:text|bytes)\(''', seg)
        if any(_path_matches(target, path_query, base) for target in python_targets):
            return True
    return False


def _codex_command_text(name, arguments):
    """Shell/JS text of a Codex function_call or custom_tool_call input."""
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return arguments
    else:
        parsed = arguments
    if isinstance(parsed, dict):
        for k in ("cmd", "command", "input", "script", "patch"):
            v = parsed.get(k)
            if isinstance(v, list):
                return " ".join(str(x) for x in v)
            if isinstance(v, str):
                return v
        return json.dumps(parsed)
    return str(parsed)


def _session_graph(path):
    """Reconstruct Claude's kept parentUuid path (shared with branches).

    Physical line indices remain zero-based, including malformed/metadata lines.
    Sidechains, meta messages and compact summaries cannot select the leaf.
    """
    # GPT-review fix 1: a "branch-structural" entry is a MAIN-conversation user/
    # assistant turn — NOT a subagent sidechain row, NOT a harness-injected meta
    # event, NOT a compact summary. Including those let a trailing sidechain row
    # hijack the active-leaf pick (backwalk follows the subagent's chain → the
    # whole main conversation gets mislabeled "dead" → false forks), and let meta
    # rows manufacture phantom rewinds. The parentUuid backwalk still uses
    # uuid_to_any (ALL types), so the chain stays intact even though these are
    # excluded from fork detection.
    def _branch_entry(d):
        return (d.get("type") in ("user", "assistant")
                and not d.get("isSidechain")
                and not d.get("isMeta")
                and not d.get("isCompactSummary"))

    warnings = []  # surfaced (not silently swallowed) — duplicate uuids, cycles, …
    sidechain_seen = False
    last_markers = []  # Claude Code's own (leafUuid, prompt) checkpoint records

    # Phase 1: index every line; uuid→entry (all types), branch turns in file order.
    all_entries, uuid_to_any = [], {}
    try:
        with open(path) as fh:
            for idx, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                d["_lineIdx"] = idx
                if d.get("isSidechain"):
                    sidechain_seen = True
                # `last-prompt` sidecar: {type, leafUuid, sessionId, lastPrompt}.
                # Claude Code writes one per user submission. A marker whose
                # leafUuid ends up OFF the active chain = an abandoned prompt —
                # an INDEPENDENT, prompt-level fork signal that cross-checks the
                # parentUuid reconstruction (verified: monotonic in file order,
                # so it's a retrospective record, not a live rewind jump).
                if d.get("type") == "last-prompt":
                    last_markers.append((idx, d.get("leafUuid"), d.get("lastPrompt") or ""))
                    continue
                u = d.get("uuid")
                if u:
                    if u in uuid_to_any:
                        warnings.append({"type": "duplicate_uuid", "uuid": u,
                                         "lines": [uuid_to_any[u]["_lineIdx"], idx]})
                    else:
                        uuid_to_any[u] = d
                if _branch_entry(d):
                    all_entries.append(d)
    except OSError:
        raise
    # Phase 2: active chain = backwalk parentUuid from the last MAIN-conv entry.
    active, leaf = set(), None
    for e in reversed(all_entries):
        if e.get("uuid"):
            leaf = e["uuid"]
            break
    cur = leaf
    while cur:
        if cur in active:
            warnings.append({"type": "parent_cycle", "uuid": cur})
            break
        active.add(cur)
        e = uuid_to_any.get(cur)
        if e is None:
            if cur != leaf:
                warnings.append({"type": "missing_parent", "uuid": cur})
            break
        cur = e.get("parentUuid")

    # Phase 3: active entries in file order (deduped). Phase 4: dead line set.
    seen, active_dedup = set(), []
    for e in all_entries:
        u = e.get("uuid")
        if u and u in active and u not in seen:
            seen.add(u)
            active_dedup.append(e)
    dead_lines = {e["_lineIdx"] for e in all_entries
                  if not e.get("uuid") or e["uuid"] not in active}

    return {"entries": all_entries, "by_uuid": uuid_to_any,
            "active": active, "leaf": leaf, "kept": active_dedup,
            "dead_lines": dead_lines, "markers": last_markers,
            "warnings": warnings, "sidechains": sidechain_seen}


def _provenance_intent(text):
    """First real request, excluding injected instructions and routing wrappers."""
    text = text.strip()
    if text.startswith("━━━ INTER-SESSION MESSAGE"):
        text = text.split("\n\n", 1)[1] if "\n\n" in text else ""
    if (text.startswith(("<", "Base directory for this skill"))
            or re.match(r"^#+\s+AGENTS\.md instructions\b", text, re.I)):
        return ""
    text = " ".join(text.split())
    if len(text) <= 10 or (text.startswith("/") and len(text.split()) <= 3):
        return ""
    return text[:120]


def _provenance_walk_jsonl(jsonl_path, path_query, host_label, runtime_label):
    """Walk one JSONL, returning kept/abandoned per-session records for path_query hits.

    Returns a list of records with: session_id, kind, host, runtime, first_ts, last_ts,
    intent, edits, reads, mentions, project, cwd. Returns [] on no hit.
    """
    counters = ("edits", "reads", "mentions", "shell", "shell_writes")
    buckets = {abandoned: dict.fromkeys(counters, 0) for abandoned in (False, True)}
    try:
        graph = _session_graph(jsonl_path) if runtime_label in ("claude", "claude-code") else None
    except OSError:
        return []
    authored_times = {False: [], True: []}
    first_commands = {False: None, True: None}
    first_ts = ""
    last_ts = ""
    intent = ""
    cwd = ""
    session_id = ""
    parent_uuid = ""
    kind = "main"
    # Detect subagent layout from path.
    p = Path(jsonl_path)
    if p.parent.name == "subagents" and p.stem.startswith("agent-"):
        kind = "subagent"
        parent_uuid = p.parent.parent.name

    def remember_command(command, lineno, abandoned, timestamp):
        if first_commands[abandoned] is None:
            first_commands[abandoned] = (" ".join(command.split())[:120], lineno, timestamp)

    try:
        with open(jsonl_path) as fh:
            for lineno, line in enumerate(fh):
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(obj, dict):
                    continue
                abandoned = bool(not obj.get("isSidechain")
                                 and graph and graph["active"] and obj.get("uuid")
                                 and obj["uuid"] not in graph["active"])
                counts = buckets[abandoned]
                if not session_id:
                    sid = obj.get("sessionId") or obj.get("session_id") or ""
                    if sid:
                        session_id = sid
                ts = obj.get("timestamp", "")
                if ts:
                    if not first_ts:
                        first_ts = ts[:19]
                    last_ts = ts[:19]
                if not cwd and obj.get("cwd"):
                    cwd = obj["cwd"]

                # Codex CLI: authored via apply_patch, or via shell/exec commands.
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else None
                if payload is not None:
                    ptype = payload.get("type")
                    if obj.get("type") == "session_meta":
                        session_id = payload.get("id") or session_id
                        cwd = payload.get("cwd") or cwd
                    elif obj.get("type") == "response_item":
                        if (not intent and ptype == "message" and payload.get("role") == "user"):
                            text = "\n".join(b.get("text", "") for b in payload.get("content") or []
                                             if isinstance(b, dict))
                            intent = _provenance_intent(extract_codex_user_text(text))
                        if ptype in ("function_call", "custom_tool_call"):
                            name = payload.get("name", "")
                            raw = payload.get("arguments") if ptype == "function_call" else payload.get("input")
                            text = _codex_command_text(name, raw)
                            files = _patch_files(text)
                            if name == "apply_patch" or (files and "*** Begin Patch" in text):
                                n = sum(1 for f in files if _path_matches(f, path_query, cwd))
                                if n:
                                    counts["edits"] += n
                                    authored_times[abandoned].append(ts[:19])
                                    continue
                            writes = _shell_writes(text, path_query, cwd)
                            if writes or _shell_references(text, path_query, cwd):
                                remember_command(text, lineno, abandoned, ts)
                                if writes:
                                    counts["shell_writes"] += 1
                                    authored_times[abandoned].append(ts[:19])
                                else:
                                    counts["shell"] += 1
                    continue

                # Capture intent = first real user message (skip system/automated
                # text, injected skill docs, and a bare `/model opus`-style command).
                if not intent and obj.get("type") == "user":
                    content = (obj.get("message") or {}).get("content", "")
                    intent = _provenance_intent(extract_user_text(content))

                # Look for tool_use blocks whose input mentions path_query.
                msg = obj.get("message") or {}
                content = msg.get("content")
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") != "tool_use":
                            continue
                        tool = block.get("name", "")
                        inp = block.get("input") or {}
                        hit = False
                        for key in _PROVENANCE_PATH_FIELDS:
                            val = inp.get(key)
                            if isinstance(val, str) and path_query in val:
                                hit = True
                                break
                        if not hit:
                            # Shell-touched: a Bash command that references the path
                            # anywhere in its string inputs (command, env-prefixed vars,
                            # redirects). Robotics/ML create+read files via subprocess
                            # (`ros2 bag record`, `cp`, `>`), which file-tool provenance
                            # was totally blind to — undercounting massively.
                            if tool == "Bash":
                                blob = inp.get("command", "")
                                if not isinstance(blob, str):
                                    continue
                                writes = _shell_writes(blob, path_query, cwd)
                                if writes or _shell_references(blob, path_query, cwd):
                                    remember_command(blob, lineno, abandoned, ts)
                                    if writes:
                                        counts["shell_writes"] += 1
                                        authored_times[abandoned].append(ts[:19])
                                    else:
                                        counts["shell"] += 1
                            continue
                        if tool in _PROVENANCE_AUTHORED:
                            counts["edits"] += 1
                            authored_times[abandoned].append(ts[:19])
                        elif tool in _PROVENANCE_INSPECTED:
                            counts["reads"] += 1
                        else:
                            counts["mentions"] += 1
    except OSError:
        return []

    if not session_id:
        session_id = p.stem
    records = []
    for abandoned, counts in buckets.items():
        if not any(counts.values()):
            continue
        times = [t for t in authored_times[abandoned] if t]
        command, command_line, command_ts = first_commands[abandoned] or ("", None, "")
        records.append({
            "session_id": session_id, "kind": kind, "parent_uuid": parent_uuid,
            "host": host_label, "runtime": runtime_label,
            "first_ts": first_ts, "last_ts": last_ts, "intent": intent,
            "cwd": cwd, "jsonl_path": str(jsonl_path), "abandoned": abandoned,
            "first_authored_ts": min(times) if times else "",
            "last_authored_ts": max(times) if times else "", **counts,
            "first_command": command, "first_command_line": command_line,
            "_first_command_ts": command_ts,
        })
    return records


def _provenance_local_candidates(path_query):
    """grep -rl candidates from every registered provider's sessions_root.

    Provider-agnostic: adding a new agent CLI means it automatically
    appears in provenance walks. The runtime label comes from the
    provider's short name.
    """
    sessions = discover_sessions()
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor() as pool:
        # Prefilter on the basename: Codex patches name files relative to the
        # session cwd, so an absolute query may never appear verbatim; the walk
        # (_path_matches) does the real resolution.
        needle = os.path.basename(path_query.rstrip("/")) or path_query
        hits = list(pool.map(_file_contains, [(str(s["path"]), needle) for s in sessions],
                             chunksize=32))
    label = "mac" if sys.platform == "darwin" else (os.uname().nodename.split(".")[0] or "local")
    return [(str(s["path"]), label, (_provider_for(s).name if _provider_for(s) else "?"))
            for s, hit in zip(sessions, hits) if hit]


def _file_contains(job):
    """Worker: does this file contain the literal text? (mmap; ~5 s for 11 GB
    in parallel, vs ~70 s for BSD `grep -rl` on the same tree)."""
    import mmap
    path, needle = job
    try:
        with open(path, "rb") as f:
            if os.fstat(f.fileno()).st_size == 0:
                return False
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
                return m.find(needle.encode()) != -1
    except (OSError, ValueError):
        return False


def _provenance_remote_candidates(host, path_query):
    """grep -rl on a remote host's ~/.claude/projects, fetch hits to cache.

    Returns list of (local_cached_path, host, "claude"). Cursor + Codex remote
    walks aren't supported in v1 — colleagues tend to keep those CLIs Mac-side.
    """
    out = []
    try:
        proc = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", host,
             f"grep -rl --include='*.jsonl' {shlex.quote(path_query)} "
             f"~/.claude/projects/ 2>/dev/null"],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print(f"# ssh {host}: provenance walk failed/timeout", file=sys.stderr)
        return out
    remote_paths = [p for p in proc.stdout.strip().splitlines() if p]
    if not remote_paths:
        return out
    cache_dir = _REMOTE_CACHE / host / "provenance"
    cache_dir.mkdir(parents=True, exist_ok=True)
    for rpath in remote_paths:
        local = cache_dir / Path(rpath).name
        try:
            with local.open("wb") as fh:
                subprocess.run(
                    ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                     host, "cat", shlex.quote(rpath)],
                    stdout=fh, stderr=subprocess.DEVNULL, timeout=120,
                )
            if local.stat().st_size > 0:
                out.append((str(local), host, "claude"))
        except (OSError, subprocess.TimeoutExpired):
            continue
    return out


def cmd_provenance(args):
    """Trace authorship of a filesystem path across sessions.

    Walks every JSONL across configured runtimes (Claude/Codex/Cursor on
    local, optionally Claude on --host targets), parses tool_use entries,
    and reports which sessions authored (Edit/Write/MultiEdit), inspected
    (Read), or mentioned the path. Each row includes the first user message
    of that session as inline intent — so the answer to "why was X added"
    is one column away from "who added X".
    """
    path_query = args.path
    hosts = args.host or []

    candidates = _provenance_local_candidates(path_query)
    remote_records = []
    for h in hosts:
        # Prefer the remote's own cq (walks its Claude AND Codex transcripts
        # in place); fall back to fetching its Claude JSONLs here.
        got = _run_remote_cq(h, ["provenance", path_query, "--json"], fallback_ok=True, capture=True)
        if got is not None and got[0] == 0:
            try:
                doc = json.loads(got[1])
                if isinstance(doc, list):  # remote found nothing
                    continue
                rows = [r for key in ("authored_by", "inspected_by", "shell_touched_by", "mentioned_by")
                        for r in doc.get(key, [])]
                if any(not isinstance(r.get("abandoned"), bool) for r in rows):
                    # Older remote cq flattens branches. Fetch the JSONLs rather
                    # than silently crediting its rows as kept-path authorship.
                    raise ValueError("remote cq lacks branch classification")
                if any((r.get("shell") or r.get("shell_writes")) and
                       (not r.get("first_command") or r.get("first_command_line") is None) for r in rows):
                    raise ValueError("remote cq lacks shell command details")
                for key in ("authored_by", "inspected_by", "shell_touched_by", "mentioned_by"):
                    for r in doc.get(key, []):
                        r["host"] = h
                        remote_records.append(r)
                continue
            except (ValueError, AttributeError):
                pass
        candidates.extend(_provenance_remote_candidates(h, path_query))

    if not candidates and not remote_records:
        msg = f"No sessions found mentioning '{path_query}'."
        if args.json:
            print(format_json([]))
        else:
            print(msg)
        return

    records = list(remote_records)
    for jsonl_path, host_label, runtime_label in candidates:
        rec = _provenance_walk_jsonl(jsonl_path, path_query, host_label, runtime_label)
        if rec:
            records.extend(rec)

    # Dedupe by (session_id, host) — forked-clone sessions share session_id
    # across multiple JSONLs (a fork copies its source session). Without this, the
    # output inflates with the same session repeated once per fork.
    merged = {}
    for r in records:
        r.setdefault("abandoned", False)
        r.setdefault("first_command", "")
        r.setdefault("first_command_line", None)
        key = (r["session_id"], r["host"], r["abandoned"])
        if key not in merged:
            merged[key] = dict(r)
            merged[key]["jsonl_paths"] = [r["jsonl_path"]]
        else:
            m = merged[key]
            m["edits"] += r["edits"]
            m["reads"] += r["reads"]
            m["mentions"] += r["mentions"]
            m["shell"] = m.get("shell", 0) + r.get("shell", 0)
            m["shell_writes"] = m.get("shell_writes", 0) + r.get("shell_writes", 0)
            m["jsonl_paths"].append(r["jsonl_path"])
            if r["first_command"] and (not m["first_command"] or
                    (r.get("_first_command_ts") or r["first_ts"], r["first_command_line"]) <
                    (m.get("_first_command_ts") or m["first_ts"], m["first_command_line"])):
                for field in ("first_command", "first_command_line", "_first_command_ts"):
                    m[field] = r.get(field)
            for field, choose in (("first_authored_ts", min), ("last_authored_ts", max)):
                values = [t for t in (m.get(field), r.get(field)) if t]
                m[field] = choose(values) if values else ""
            # Keep earliest first_ts, latest last_ts, longest intent.
            if r["first_ts"] and (not m["first_ts"] or r["first_ts"] < m["first_ts"]):
                m["first_ts"] = r["first_ts"]
            if r["last_ts"] and (not m["last_ts"] or r["last_ts"] > m["last_ts"]):
                m["last_ts"] = r["last_ts"]
            if r["intent"] and len(r["intent"]) > len(m["intent"]):
                m["intent"] = r["intent"]
    records = list(merged.values())
    for r in records:
        r.pop("_first_command_ts", None)

    abandoned = sorted([r for r in records if r["abandoned"]], key=lambda r: r["first_ts"])
    records = [r for r in records if not r["abandoned"]]

    # Sort: authors first by first_ts ascending (provenance order), then readers,
    # then pure-mentions.
    def _wrote(r):
        return r["edits"] > 0 or r.get("shell_writes", 0) > 0
    authors = sorted([r for r in records if _wrote(r)], key=lambda r: r["first_ts"])
    readers = sorted([r for r in records if not _wrote(r) and r["reads"] > 0],
                     key=lambda r: r["first_ts"])
    # Shell-touched: no file-tool op, but a Bash command referenced the path.
    shell_touchers = sorted([r for r in records if not _wrote(r) and r["reads"] == 0
                             and r.get("shell", 0) > 0], key=lambda r: r["first_ts"])
    mentioners = sorted([r for r in records if not _wrote(r) and r["reads"] == 0
                         and r.get("shell", 0) == 0], key=lambda r: r["first_ts"])

    if args.json:
        print(format_json({
            "path": path_query,
            "authored_by": authors + [r for r in abandoned if _wrote(r)],
            "inspected_by": readers + [r for r in abandoned if not _wrote(r) and r["reads"]],
            "shell_touched_by": shell_touchers + [r for r in abandoned if not _wrote(r) and not r["reads"] and r.get("shell")],
            "mentioned_by": mentioners + [r for r in abandoned if not _wrote(r) and not r["reads"] and not r.get("shell")],
        }))
        return

    match_note = " (filename-only match; any directory)" if "/" not in path_query else ""
    print(f"Path: {path_query}{match_note}")
    print()

    def _fmt_row(r, count_label):
        sid = r["session_id"]  # full id: agents paste it into --resume / cq turns
        return (
            f"  {sid:36s}  {r['host']:11s}  {r['runtime']:8s}  "
            f"{r['first_ts']:19s}  ({count_label})"
        )

    def _shell_detail(r, show_intent=True):
        if show_intent and r["intent"]:
            print(f'    "{r["intent"]}"')
        if r.get("first_command"):
            print(f"    command: {r['first_command']}")
            remote = f" --host {shlex.quote(r['host'])}" if r["host"] in hosts else ""
            print(f"    cq context {shlex.quote(r['session_id'])} @{r['first_command_line']}{remote}")

    if authors:
        print("Authored (Edit/Write/MultiEdit, Codex apply_patch, or a shell write — >, tee, cp/mv, sed -i):")
        for r in authors:
            parts = []
            if r["edits"]:
                parts.append(f"{r['edits']} edits")
            if r.get("shell_writes"):
                parts.append(f"{r['shell_writes']} shell writes")
            print(_fmt_row(r, ", ".join(parts)))
            if r["intent"]:
                print(f"    \"{r['intent']}\"")
        print()
    if readers:
        print("Inspected (Read):")
        for r in readers[:args.limit_reads]:
            print(_fmt_row(r, f"{r['reads']} reads"))
        if len(readers) > args.limit_reads:
            print(f"  … +{len(readers) - args.limit_reads} more (--limit-reads to see)")
        print()
    if shell_touchers:
        print("Shell-touched (Bash — created/read/referenced via subprocess):")
        for r in shell_touchers[:args.limit_reads]:
            print(_fmt_row(r, f"{r['shell']} bash"))
            _shell_detail(r)
        if len(shell_touchers) > args.limit_reads:
            print(f"  … +{len(shell_touchers) - args.limit_reads} more (--limit-reads to see)")
        print()
    if mentioners and args.show_mentions:
        print("Mentioned (other tool inputs):")
        for r in mentioners[:args.limit_reads]:
            print(_fmt_row(r, f"{r['mentions']} mentions"))
        print()

    visible_abandoned = [r for r in abandoned if _wrote(r) or r["reads"] or r.get("shell") or args.show_mentions]
    if visible_abandoned:
        print("On abandoned branches (rewound away):")
        for r in visible_abandoned:
            labels = [f"{r[k]} {label}" for k, label in
                      (("edits", "edits"), ("shell_writes", "shell writes"),
                       ("reads", "reads"), ("shell", "bash"), ("mentions", "mentions"))
                      if r.get(k) and (k != "mentions" or args.show_mentions)]
            print(_fmt_row(r, ", ".join(labels)))
            if r["intent"]:
                print(f'    "{r["intent"]}"')
            _shell_detail(r, show_intent=False)
        print()

    if not (authors or readers or shell_touchers or visible_abandoned or (mentioners and args.show_mentions)):
        # Fail loud rather than silent-empty: the path matched a transcript (we had
        # candidates) but no file-tool/Bash op classified it.
        print("  (no Edit/Write/Read/Bash op classified this path; it may appear only in "
              "prose or tool RESULTS — try `cq search '" + os.path.basename(path_query) + "'`)")

    if authors:
        first = min(authors, key=lambda r: r.get("first_authored_ts") or r["first_ts"])
        last = max(authors, key=lambda r: r.get("last_authored_ts") or r["last_ts"])
        print(f"First authored: {first.get('first_authored_ts') or first['first_ts']} by {first['session_id']} on {first['host']}")
        print(f"Last authored:  {last.get('last_authored_ts') or last['last_ts']} by {last['session_id']} on {last['host']}")
    elif any(_wrote(r) for r in abandoned):
        print("First/Last authored: no kept-path authorship; only abandoned-branch edits exist.")


def cmd_stats(args):
    """Show index statistics plus the refresh backlog and background-indexer
    progress. Read-only — never indexes, so it answers instantly even while
    the index is catching up (it used to run a full refresh first and hang)."""
    conn = create_db()
    # Totals come from the per-session bookkeeping (instant). Counting the
    # messages table itself scans gigabytes, so that breakdown is --full only.
    sessions, total = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(msg_count), 0) FROM indexed_sessions").fetchone()
    projects = shallow = deep = None
    if getattr(args, "full", False):
        projects = conn.execute("SELECT COUNT(DISTINCT project) FROM messages").fetchone()[0]
        shallow = conn.execute("SELECT COUNT(*) FROM messages WHERE depth=0").fetchone()[0]
        deep = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] - shallow
    pending_n, pending_mb, running = index_backlog(conn)
    worker = {}
    try:
        worker = json.loads(INDEX_STATUS.read_text())
    except (OSError, ValueError):
        pass

    if args.json:
        print(format_json({
            "projects": projects,
            "sessions": sessions,
            "messages": total,
            "shallow": shallow,
            "deep": deep,
            "pending_transcripts": pending_n,
            "pending_mb": round(pending_mb, 1),
            "indexer_running": running,
            "indexer": worker,
            "db_path": str(FTS_DB),
            "db_size_mb": round(FTS_DB.stat().st_size / 1024 / 1024, 1) if FTS_DB.exists() else 0,
        }))
        return

    print(f"  Index: {sessions} sessions, {total} messages"
          + (f", {projects} projects" if projects is not None else ""))
    if shallow is not None:
        print(f"  Shallow: {shallow} | Deep: {deep}")
    if FTS_DB.exists():
        db_size = FTS_DB.stat().st_size / 1024 / 1024
        print(f"  DB size: {db_size:.1f}MB at {FTS_DB}")
    if pending_n:
        state = "background indexer running" if running else "will index on next search"
        print(f"  Backlog: {pending_n} transcript(s), {pending_mb:.0f} MB not yet indexed ({state})")
    else:
        print("  Backlog: none — index is up to date")
    if running and worker.get("total"):
        print(f"  Indexer: {worker.get('done', 0)}/{worker['total']} transcripts, "
              f"{worker.get('done_mb', 0)}/{worker.get('total_mb', 0)} MB "
              f"(log: {INDEX_LOG})")

    # Session discovery stats
    all_sessions = discover_sessions()
    groups = {}
    for s in all_sessions:
        groups.setdefault(s["group"], []).append(s)
    print(f"\n  Discovery:")
    for group, items in sorted(groups.items()):
        print(f"    {group}: {len(items)} sessions")


def cmd_index(args):
    """Update (or with --reindex, rebuild) the index in the foreground.
    `--worker` is the detached background mode refresh_index() spawns: it
    keeps re-planning until nothing is left, so transcripts that grew while
    it ran are caught too."""
    conn = create_db()
    if getattr(args, "worker", False):
        conn.execute("PRAGMA busy_timeout = 120000")  # nobody waits on the worker
        lock = None
        for _ in range(30):  # a `cq stats`/spawn probe can hold the lock for a moment
            lock = _acquire_index_lock()
            if lock is not None:
                break
            time.sleep(0.1)
        if lock is None:
            return  # another worker already owns the index
        try:
            _write_status(INDEX_STATUS, state="starting", started=time.time())
            for _ in range(5):
                plan = plan_reindex(conn, discover_sessions())
                if not plan:
                    break
                run_index_plan(conn, plan, quiet=False, status_path=INDEX_STATUS)
            _write_status(INDEX_STATUS, state="idle", finished=time.time())
        finally:
            lock.close()
        return
    lock = _acquire_index_lock(blocking=False)
    if lock is None:
        print("A background indexer is running — waiting for it to finish…", file=sys.stderr)
        lock = _acquire_index_lock(blocking=True)
    try:
        print(f"{'Re-indexing' if args.reindex else 'Updating index'}…")
        build_index(conn, force=args.reindex, project_filter=args.project)
    finally:
        lock.close()


def _msg_text(entry):
    """Best-effort plain-text of a transcript line's message content.

    Handles Claude Code's content-as-list-of-blocks and content-as-string;
    returns '' for tool_use/tool_result-only lines. Defensive: never raises.
    """
    try:
        m = entry.get("message", {}) if isinstance(entry, dict) else {}
        c = m.get("content", "") if isinstance(m, dict) else ""
        if isinstance(c, str):
            return c.strip()
        if isinstance(c, list):
            parts = [b.get("text", "") for b in c
                     if isinstance(b, dict) and b.get("type") == "text"]
            return " ".join(p for p in parts if p).strip()
    except Exception:
        pass
    return ""


def _first_user_text(path, skip_prefixes=("Base directory",)):
    """First substantive user-turn text in a transcript (the agent's task)."""
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("type") != "user":
                    continue
                t = _msg_text(e)
                if t and not any(t.startswith(p) for p in skip_prefixes):
                    return t
    except OSError:
        pass
    return ""


def _last_assistant_text(path):
    """Last substantive assistant-turn text in a transcript (its conclusion)."""
    last = ""
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("type") != "assistant":
                    continue
                t = _msg_text(e)
                if t:
                    last = t
    except OSError:
        pass
    return last


def cmd_subagents(args):
    """List the spawned-subagent (sidechain) transcripts under a session.

    Claude Code stores agents dispatched via the Agent/Task tool as SEPARATE
    transcripts at  <project>/<session-uuid>/subagents/agent-*.jsonl  — they are
    NOT inline in the parent jsonl (the parent only carries isSidechain rows for
    <teammate-message> blocks). Without this mode you have to `ls`/`find` for them
    by hand. Born from a real mining need (E2 sandcastle audit): a build agent's
    STEP-0 prior-art mining ran entirely inside such a sidechain, invisible to
    `cq tools`/`files` on the parent. For each subagent it prints the dispatch
    description (from agent-*.meta.json), line count, the task prompt + final
    digest snippet, and a ready-to-run drill command.
    """
    session = _resolve_session(args)
    if not session:
        sys.exit(1)
    main_path = Path(session["path"]).expanduser().resolve()
    # main transcript <dir>/<uuid>.jsonl → subagents at <dir>/<uuid>/subagents/.
    sub_dir = main_path.with_suffix("") / "subagents"
    if not sub_dir.is_dir() and main_path.parent.name == "subagents":
        sub_dir = main_path.parent  # already pointed at a subagent file
    if not sub_dir.is_dir():
        print(f"no subagents/ dir for {session.get('session_id', main_path.name)} "
              f"({sub_dir} absent) — it dispatched no Agent/Task sidechains "
              f"(or this is a remote/ad-hoc fetch that didn't pull them).")
        return
    agents = sorted(sub_dir.glob("agent-*.jsonl"))
    if not agents:
        print(f"no agent-*.jsonl under {sub_dir}")
        return
    rows = []
    for ap in agents:
        try:
            n = sum(1 for _ in open(ap))
        except OSError:
            n = -1
        meta = {}
        mp = ap.parent / (ap.stem + ".meta.json")
        if mp.exists():
            try:
                meta = json.loads(mp.read_text())
            except Exception:
                meta = {}
        rows.append({
            "path": str(ap),
            "lines": n,
            "agent_type": meta.get("agentType", ""),
            "description": meta.get("description", ""),
            "tool_use_id": meta.get("toolUseId", ""),
            "task": _first_user_text(ap)[:400],
            "digest": _last_assistant_text(ap)[:400],
        })
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return
    print(f"Session {session.get('session_id', main_path.name)} — "
          f"{len(rows)} subagent transcript(s) under {sub_dir}:\n")
    self_path = Path(__file__).resolve()
    for r in rows:
        head = f"  {Path(r['path']).name}  ({r['lines']} lines)"
        if r["description"]:
            head += f"  — {r['description']}"
        print(head)
        if r["agent_type"]:
            print(f"    type:   {r['agent_type']}")
        if r["task"]:
            print(f"    task:   {r['task'][:160]}")
        if r["digest"]:
            print(f"    digest: {r['digest'][:160]}")
        print(f"    drill:  python3 {self_path} tools --jsonl {r['path']}")
        print()


def cmd_branches(args):
    """Map the FORK structure of a session — the rewind branches it took.

    Claude Code's in-TUI rewind (and /timetravel detours) branch WITHIN one jsonl:
    same session UUID, divergent `parentUuid` chains. The ACTIVE timeline is found
    by walking `parentUuid` BACKWARD from the last user/assistant message; every
    other user/assistant entry is a DEAD (abandoned) branch. A fork = a contiguous
    block of dead entries between two active entries (or before the first). This is
    what `cq tools/files/search` cannot show: they read the file flat, parentUuid-blind,
    so they can't tell the path you took from the arm you abandoned.

    For each fork: where it diverges, the abandoned branch (first prompt + turn
    count + a `cq raw` drill to read it), and what the active timeline took.
    """
    session = _resolve_session(args)
    if not session:
        sys.exit(1)
    path = Path(session["path"]).expanduser().resolve()

    def _1l(s):
        return " ".join((s or "").split())

    _INJECTED = ("Base directory", "<command-name>", "<command-message>",
                 "<command-args>", "<task-notification>", "<system-reminder>",
                 "<local-command-", "@canvas:answered", "━━━ INTER-AGENT")

    try:
        graph = _session_graph(path)
    except OSError as e:
        sys.exit(f"error reading {path}: {e}")
    all_entries, uuid_to_any = graph["entries"], graph["by_uuid"]
    active, leaf, active_dedup = graph["active"], graph["leaf"], graph["kept"]
    dead_lines, last_markers = graph["dead_lines"], graph["markers"]
    warnings, sidechain_seen = graph["warnings"], graph["sidechains"]
    if not all_entries:
        msg = ("no main-conversation user/assistant entries "
               "(only sidechains/meta? — this may be a subagent transcript)")
        if getattr(args, "json", False):
            print(json.dumps({
                "session": session.get("session_id", path.stem),
                "branch_entries": 0, "active_timeline_entries": 0,
                "sidechains_excluded": sidechain_seen, "fork_count": 0,
                "warnings": warnings, "forks": [], "note": msg}, indent=2))
        else:
            print(msg)
        return

    # last-prompt markers whose recorded leaf is OFF the active chain = the
    # abandoned prompts, per Claude Code's own log. Keyed by the leaf's file line
    # so each can be attributed to the fork gap it falls in.
    dead_markers = []
    for _ml, leaf, prompt in last_markers:
        if leaf and leaf not in active:
            le = uuid_to_any.get(leaf)
            dead_markers.append({"leaf_line": le["_lineIdx"] if le else None,
                                 "prompt": " ".join((prompt or "").split())})

    # Phase 5: fork points (dead prefix + dead gaps between active entries).
    rewinds = []
    if active_dedup:
        first_active = active_dedup[0]["_lineIdx"]
        if any(e["_lineIdx"] < first_active and e["_lineIdx"] in dead_lines for e in all_entries):
            rewinds.append({"active_idx": 0, "dead_start": 0,
                            "dead_end": first_active - 1, "is_prefix": True})
        for i in range(1, len(active_dedup)):
            prev_line = active_dedup[i - 1]["_lineIdx"]
            cur_line = active_dedup[i]["_lineIdx"]
            if any(prev_line < e["_lineIdx"] < cur_line and e["_lineIdx"] in dead_lines
                   for e in all_entries):
                rewinds.append({"active_idx": i, "dead_start": prev_line + 1,
                                "dead_end": cur_line - 1, "is_prefix": False})

    def _first_real_user(entries):
        for e in entries:
            if e.get("type") == "user":
                t = _msg_text(e)
                if t and not any(t.lstrip().startswith(p) for p in _INJECTED):
                    return t
        return ""

    def _dead_in(lo, hi):
        """Summarize the dead (abandoned) branch turns within a physical gap.
        GPT-review fix 3+4: return the EXACT dead line indices (the raw range
        also spans system/summary/meta lines, so an agent needs the precise
        set) and EVERY real user prompt in the gap (one gap can hold >1
        abandoned arm — be honest rather than show only the first)."""
        ents = [e for e in all_entries
                if lo <= e["_lineIdx"] <= hi and e["_lineIdx"] in dead_lines]
        n_asst = sum(1 for e in ents if e.get("type") == "assistant" and _msg_text(e))
        prompts = []
        for e in ents:
            if e.get("type") == "user":
                t = _msg_text(e)
                if t and not any(t.lstrip().startswith(p) for p in _INJECTED):
                    prompts.append(t)
        return {
            "first_user": prompts[0] if prompts else "",
            "all_user_prompts": prompts,      # >1 ⇒ multiple abandoned arms merged
            "entries": len(ents),
            "assistant_turns": n_asst,
            "dead_line_indices": [e["_lineIdx"] for e in ents],
        }

    def _took_ref(active_idx):
        # the active entry that begins the branch the timeline continued with
        e = active_dedup[active_idx] if active_idx < len(active_dedup) else None
        return {
            "line": e["_lineIdx"] if e else None,
            "uuid": e.get("uuid") if e else None,
            "first_user": _first_real_user(active_dedup[active_idx:active_idx + 8]),
        }

    sid = session.get("session_id", path.stem)
    self_path = Path(__file__).resolve()

    def _raw_cmd(rp):
        return (f"python3 {self_path} raw {sid} "
                f"--start {rp['dead_start']} --end {rp['dead_end']}")

    def _diverges_ref(rp):
        if rp["is_prefix"]:
            return None
        e = active_dedup[rp["active_idx"] - 1]
        return {"line": e["_lineIdx"], "uuid": e.get("uuid"),
                "text": _1l(_msg_text(e))[:200]}

    def _logged_in(lo, hi):
        # Claude Code's OWN abandoned-prompt record (off-chain last-prompt
        # markers) falling in this fork's gap — the precise, independent list
        # that the parentUuid gap-merge can only count.
        return [m["prompt"] for m in dead_markers
                if m["leaf_line"] is not None and lo <= m["leaf_line"] <= hi and m["prompt"]]

    if getattr(args, "json", False):
        forks = []
        for rp in rewinds:
            d = _dead_in(rp["dead_start"], rp["dead_end"])
            forks.append({
                "is_prefix": rp["is_prefix"],
                "diverges_after": _diverges_ref(rp),
                "abandoned": {
                    "first_user": d["first_user"],
                    "all_user_prompts": d["all_user_prompts"],
                    "logged_abandoned_prompts": _logged_in(rp["dead_start"], rp["dead_end"]),
                    "entries": d["entries"],
                    "assistant_turns": d["assistant_turns"],
                    "raw_line_range": [rp["dead_start"], rp["dead_end"]],
                    "dead_line_indices": d["dead_line_indices"],
                },
                "took": _took_ref(0 if rp["is_prefix"] else rp["active_idx"]),
                "raw_command": _raw_cmd(rp),
            })
        print(json.dumps({
            "session": sid,
            "active_leaf_uuid": leaf,
            "branch_entries": len(all_entries),
            "active_timeline_entries": len(active_dedup),
            "sidechains_excluded": sidechain_seen,
            "fork_count": len(rewinds),
            "dead_prompt_markers": len(dead_markers),
            "warnings": warnings,
            "forks": forks,
        }, indent=2))
        return

    note = " · sidechains excluded" if sidechain_seen else ""
    print(f"Session {sid} — {len(all_entries)} branch msgs · active timeline "
          f"{len(active_dedup)} entries · {len(rewinds)} rewind fork(s){note}\n")
    for w in warnings:
        print(f"  ⚠ {w['type']}: {w.get('uuid','')} {w.get('lines','')}")
    if warnings:
        print()
    if not rewinds:
        print("  No rewind forks — straight-line conversation "
              "(active chain == file order).")
        return
    for k, rp in enumerate(rewinds, 1):
        d = _dead_in(rp["dead_start"], rp["dead_end"])
        where = ("(before the first active turn)" if rp["is_prefix"]
                 else f'after: "{_1l(_msg_text(active_dedup[rp["active_idx"] - 1]))[:70]}"')
        took = _first_real_user(
            active_dedup[(0 if rp["is_prefix"] else rp["active_idx"]):][:8])
        logged = _logged_in(rp["dead_start"], rp["dead_end"])
        print(f"  Fork {k} — diverges {where}")
        print(f"    ✗ abandoned : {d['assistant_turns']} asst turn(s), "
              f"{d['entries']} entr(ies) — \"{_1l(d['first_user'])[:80]}\"")
        if logged:
            # Claude Code's own record of the abandoned prompt(s) — precise,
            # not the parentUuid gap-merge guess.
            print(f"      abandoned prompt(s) [{len(logged)}, from last-prompt log]:")
            for lp in logged:
                print(f"        · \"{lp[:74]}\"")
        print(f"      read it  : {_raw_cmd(rp)}")
        if took:
            print(f"    ✓ took      : \"{_1l(took)[:80]}\"")
        print()
    print("  (cq turns/context show the ACTIVE timeline; abandoned arms are read "
          "via the raw drills — the raw range also spans system/meta lines, so "
          "--json carries exact dead_line_indices. abandoned prompt(s) come from "
          "Claude Code's own last-prompt log, cross-checking the parentUuid forks.)")


def _extract_commit_subject(cmd):
    """Pull the commit subject (first line of the message) out of a `git commit` shell command.
    Handles -m "…"/-m '…'/-am, and `-F -`/`-F file` heredocs (`git commit -F - <<'EOF' … EOF`)."""
    import re
    m = re.search(r"-a?m\s+\"((?:[^\"\\]|\\.)*)\"", cmd) or re.search(r"-a?m\s+'([^']*)'", cmd)
    if m:
        return m.group(1).strip().splitlines()[0].strip() if m.group(1).strip() else "(empty -m)"
    # heredoc-fed message: `git commit -F - <<'EOF'\n<subject>\n…`
    h = re.search(r"-F\s+-\s*<<-?\s*['\"]?(\w+)['\"]?\s*\n(.*)", cmd, re.S)
    if h:
        for ln in h.group(2).splitlines():
            if ln.strip() and ln.strip() != h.group(1):
                return ln.strip()
    return "(subject not parsed — see --json for full command)"


def cmd_commits(args):
    """List the `git commit` commands a session actually RAN, with subjects (+ optional --match filter).

    THE authorship cross-check: `git` does not record which Claude session authored a commit (one
    git user, concurrent sessions). The decisive evidence is which TRANSCRIPT contains the
    `git commit` tool-call that produced a given subject. Born from a real mining need (E4 sandcastle
    audit — the thesis epoch): two concurrent orchestrators (a9a451c7 + 808e0f85) committed in the same
    wall-clock span; whether the P1–P7 harness scripts were authored in E4 vs E5 turned entirely on
    grepping each session's jsonl for the commit subjects. This mode mechanizes that hand-grep:
    `cq commits <session>` lists every committed subject; `cq commits <session> --match "<subject>"`
    answers "did THIS session author that commit?" (exit 0 = ≥1 hit, exit 1 = none)."""
    session = _resolve_session(args)
    if not session:
        sys.exit(1)
    text = Path(session["path"]).read_text()
    turns, fmt = parse_transcript(text)
    rows = []
    for turn in turns:
        for b in turn["blocks"]:
            if b["kind"] != "tool_use" or not b.get("tool_call"):
                continue
            tc = b["tool_call"]
            if tc["name"] not in ("Bash", "bash"):
                continue
            cmd = (tc.get("input", {}) or {}).get("command", "") or ""
            if "git commit" not in cmd:
                continue
            subj = _extract_commit_subject(cmd)
            if getattr(args, "match", None) and args.match.lower() not in (subj + " " + cmd).lower():
                continue
            rows.append({"turn": turn["index"], "timestamp": (b.get("timestamp") or "")[:19],
                         "subject": subj, "command": cmd[:300].replace("\n", " ")})
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return
    sid = session.get("session_id", Path(session["path"]).name)[:12]
    if not rows:
        suffix = f" matching {args.match!r}" if getattr(args, "match", None) else ""
        print(f"Session {sid}… ran NO git commit{suffix}.")
        sys.exit(1 if getattr(args, "match", None) else 0)
    label = f" matching {args.match!r}" if getattr(args, "match", None) else ""
    print(f"Session {sid}… — {len(rows)} git commit(s){label}:\n")
    for r in rows:
        print(f"  Turn {r['turn']:3} | {r['timestamp']} | {r['subject']}")
    sys.exit(0)


# ── CLI Entry Point ────────────────────────────────────────────────────────


def _strip_host_args(argv):
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "--host":
            skip = True
            continue
        if a.startswith("--host="):
            continue
        out.append(a)
    return out


def main():
    parser = argparse.ArgumentParser(
        prog="cq",
        description="Claude Query — structured search & inspection of AI session transcripts",
    )
    parser.add_argument("--json", action="store_true", help="JSON output for agent consumption")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # search
    p = subparsers.add_parser("search", aliases=["s"], help="FTS5 full-text search")
    p.add_argument("query", nargs="?", default="",
                   help="Search query (optional when using --concept ≥ 2 times)")
    p.add_argument("--project", help="Filter by project path substring")
    p.add_argument("--session", help="Filter to a single session (partial ID prefix OK)")
    p.add_argument("--provider", choices=["all", "claude", "codex", "cursor"], default="claude",
                   help="Filter by provider. DEFAULT: claude (Claude Code) — your interaction is "
                        "always with Claude; codex/cursor are subagent-like and excluded unless asked. "
                        "Pass --provider codex|cursor for one of those, or --provider all for everything.")
    p.add_argument("--user", action="store_true", dest="user_only",
                   help="Limit to human-typed USER prompts (role=user, chat text only: tool results excluded). Use to hunt what "
                        "the USER asked/parked rather than assistant or tool output.")
    p.add_argument("--role", action="append", help="Filter: user|assistant (repeatable)")
    p.add_argument("--tier", choices=["all", "shallow", "deep"], default="all",
                   help="Tier: all=blended chat+tools (default), shallow=chat text only, deep=tool inputs/results/thinking only")
    p.add_argument("--phrase", action="store_true",
                   help="Strict adjacent-token phrase match. No fallback cascade — empty stays empty.")
    p.add_argument("--limit", "--max-results", "-n", type=int, default=15,
                   help="Max sessions to show (one hit per session; default 15)")
    p.add_argument("--include-self", action="store_true", dest="include_self",
                   help="Keep hits from the calling session (and its subagents) and cq's own "
                        "commands/output. Default: hide them — they're the query echoing back.")
    p.add_argument("--include-sidechain", action="store_true", dest="include_sidechain",
                   help="Include inline teammate/sidechain (isSidechain=true) rows. Default: exclude.")
    p.add_argument("--include-subagents", action="store_true", dest="include_subagents",
                   help="Include spawned-agent transcripts (kind='subagent'). Default: exclude.")
    p.add_argument("--after", type=validate_iso_date,
                   help="Only results with timestamp on/after this date (YYYY-MM-DD, inclusive)")
    p.add_argument("--before", type=validate_iso_date,
                   help="Only results with timestamp on/before this date (YYYY-MM-DD, inclusive of the full day)")
    p.add_argument("--concept", action="append", default=None, dest="concepts",
                   help="Repeatable. Supply ≥ 2 times for AND-across-sessions search — "
                        "returns only sessions matching ALL concepts. Positional query is ignored when used.")
    # Deprecated aliases for backwards compat — map to --tier internally
    p.add_argument("--deep-only", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--all", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--reindex", action="store_true", help="Force re-index before searching")
    p.add_argument("--host", help="SSH target. When given, runs `grep -rl` over the "
                                   "remote ~/.claude/projects/ instead of the local FTS5 "
                                   "index. Coarser than local search (no ranking, no tier), "
                                   "but works without a remote cq install. Drill in with "
                                   "`cq turns --host <host> <session-id-prefix>`.")

    # grep — literal scan of the raw transcript files
    p = subparsers.add_parser(
        "grep", aliases=["g"],
        help="Literal search over raw transcripts (commands, paths, errors) with --first/--last/--count")
    p.add_argument("text", help="Literal text to find (not a regex)")
    p.add_argument("-i", "--ignore-case", action="store_true", dest="ignore_case")
    p.add_argument("--provider", choices=["all", "claude", "codex", "cursor"], default="all",
                   help="DEFAULT: all — a literal scan should miss nothing")
    p.add_argument("--project", help="Only sessions whose working directory contains this (any provider)")
    p.add_argument("--since", help="Only matches at/after this time: YYYY-MM-DD, or relative 90m / 12h / 3d / 2w")
    p.add_argument("--first", action="store_true",
                   help="Order sessions by their EARLIEST match (answer 'when did this first come up')")
    p.add_argument("--last", action="store_true", help="Order by most recent match (the default)")
    p.add_argument("--count", action="store_true", help="One line per session with its match count")
    p.add_argument("--limit", "--max-results", "-n", type=int, default=15, help="Max sessions to show")
    p.add_argument("--per-session", type=int, default=3, dest="per_session",
                   help="Matches shown per session (default 3)")
    p.add_argument("--all-fields", action="store_true", dest="all_fields",
                   help="Also count hits that only sit in duplicate/metadata fields "
                        "(full-file copies after an edit, skill listings) — raw-grep equivalent")
    p.add_argument("--include-self", action="store_true", dest="include_self",
                   help="Include the calling session and its subagents")
    p.add_argument("--host", help="Run the grep on this SSH host's transcripts (needs cq installed there)")

    # list
    p = subparsers.add_parser("list", aliases=["ls"], help="List sessions with metadata")
    p.add_argument("--project", help="Filter by project path substring")
    p.add_argument("--after", type=validate_iso_date, help="Only sessions with first message on/after (YYYY-MM-DD)")
    p.add_argument("--before", type=validate_iso_date, help="Only sessions with first message on/before (YYYY-MM-DD)")
    p.add_argument("--include-sidechain", action="store_true", dest="include_sidechain",
                   help="(No-op for list — kept for flag parity with search.)")
    p.add_argument("--include-subagents", action="store_true", dest="include_subagents",
                   help="Include spawned-agent transcript files in the listing. Default: exclude.")

    # recent
    p = subparsers.add_parser("recent", aliases=["r"], help="Last N active sessions with resume commands")
    p.add_argument("count", nargs="?", type=int, default=10, help="Number of sessions (default: 10)")
    p.add_argument("--project", help="Filter by project path substring")
    p.add_argument("--after", type=validate_iso_date, help="Only sessions with first message on/after (YYYY-MM-DD)")
    p.add_argument("--before", type=validate_iso_date, help="Only sessions with first message on/before (YYYY-MM-DD)")
    p.add_argument("--include-sidechain", action="store_true", dest="include_sidechain",
                   help="(No-op for recent — kept for flag parity with search.)")
    p.add_argument("--include-subagents", action="store_true", dest="include_subagents",
                   help="Include spawned-agent transcript files. Default: exclude.")

    # Shared --host helper: every session-scoped subcommand accepts the same flag.
    _HOST_HELP = ("SSH target (e.g. workstation). Fetches the session JSONL from "
                  "the remote host's ~/.claude/projects into a local cache, then "
                  "operates on it.")
    _JSONL_HELP = ("Inspect an arbitrary JSONL transcript by path. Bypasses "
                   "session-id resolution and --host. Use this to read sessions "
                   "received via Slack/Syncthing/email without first importing "
                   "them into ~/.claude/projects/.")

    # turns
    p = subparsers.add_parser("turns", aliases=["t"], help="Structured turns from a session")
    p.add_argument("session", nargs="?", help="Session ID (partial match OK; omit when using --jsonl)")
    p.add_argument("--range", help="Turn range N-M")
    p.add_argument("--project", help="Filter session search by project")
    p.add_argument("--host", help=_HOST_HELP)
    p.add_argument("--jsonl", help=_JSONL_HELP)
    p.add_argument("--include-sidechain", action="store_true", dest="include_sidechain",
                   help="Include subagent/teammate turns. Default: exclude.")

    # tools
    p = subparsers.add_parser("tools", help="Tool calls WITH results")
    p.add_argument("session", nargs="?", help="Session ID (omit when using --jsonl)")
    p.add_argument("--name", "-n", help="Filter by tool name (substring)")
    p.add_argument("--errors", "-e", action="store_true", help="Only show errors")
    p.add_argument("--project", help="Filter session search by project")
    p.add_argument("--host", help=_HOST_HELP)
    p.add_argument("--jsonl", help=_JSONL_HELP)

    # thinking
    p = subparsers.add_parser("thinking", aliases=["think"], help="Extract thinking blocks")
    p.add_argument("session", nargs="?", help="Session ID (omit when using --jsonl)")
    p.add_argument("--project", help="Filter session search by project")
    p.add_argument("--host", help=_HOST_HELP)
    p.add_argument("--jsonl", help=_JSONL_HELP)

    # files
    p = subparsers.add_parser("files", aliases=["f"], help="Files touched in a session")
    p.add_argument("session", nargs="?", help="Session ID (omit when using --jsonl)")
    p.add_argument("--project", help="Filter session search by project")
    p.add_argument("--host", help=_HOST_HELP)
    p.add_argument("--jsonl", help=_JSONL_HELP)

    # context
    p = subparsers.add_parser("context", aliases=["ctx"], help="Full turn detail")
    p.add_argument("session", nargs="?", help="Session ID (omit when using --jsonl + --turn)")
    p.add_argument("turn", nargs="?",
                   help="Turn number, N-M range, or a line reference @LINE / msg[LINE] as printed "
                        "by `cq search` / `cq grep` (or use --turn with --jsonl)")
    p.add_argument("--turn", dest="turn_flag", help="Same forms as the positional turn (use with --jsonl)")
    p.add_argument("--project", help="Filter session search by project")
    p.add_argument("--host", help=_HOST_HELP)
    p.add_argument("--jsonl", help=_JSONL_HELP)

    # why — follow a diagnosis forward along the kept timeline
    p = subparsers.add_parser("why", aliases=["resolve"],
                             help="Follow an anchor to later revisions and evidence on the kept path")
    p.add_argument("session", nargs="?", help="Session ID (unique prefix; omit with --jsonl)")
    p.add_argument("turn", nargs="?", help="Turn N, N-M (first turn), @LINE or msg[LINE]")
    p.add_argument("--turn", dest="turn_flag", help="Anchor address when using --jsonl")
    p.add_argument("--term", action="append", default=[],
                   help="Explicit topic handle; repeatable, replaces automatic extraction")
    p.add_argument("--limit", type=int, default=20, help="Maximum timeline candidates (default 20)")
    p.add_argument("--follow-sessions", action="store_true",
                   help="Best effort: later local sessions in the same cwd mentioning at least two handles")
    p.add_argument("--project", help="Filter session search by project")
    p.add_argument("--host", help=_HOST_HELP)
    p.add_argument("--jsonl", help=_JSONL_HELP)

    # user
    p = subparsers.add_parser("user", aliases=["u"], help="All user messages")
    p.add_argument("session", nargs="?", help="Session ID (omit when using --jsonl)")
    p.add_argument("--limit", "-n", type=int, help="Only the last N user messages")
    p.add_argument("--project", help="Filter session search by project")
    p.add_argument("--host", help=_HOST_HELP)
    p.add_argument("--jsonl", help=_JSONL_HELP)
    p.add_argument("--include-sidechain", action="store_true", dest="include_sidechain",
                   help="Include subagent/teammate user-role entries. Default: exclude.")

    # raw — agent-friendly line-indexed JSONL access
    p = subparsers.add_parser("raw", help="Raw JSONL lines at exact positions (agent-friendly)")
    p.add_argument("session", nargs="?", help="Session ID (omit when using --jsonl)")
    p.add_argument("pos", nargs="*", type=int, metavar="LINE",
                   help="Optional START [END] — same as --start/--end")
    p.add_argument("--start", type=int, default=0, help="Start line number (0-indexed, inclusive)")
    p.add_argument("--end", type=int, default=None, help="End line number (0-indexed, inclusive)")
    p.add_argument("--project", help="Filter session search by project")
    p.add_argument("--host", help=_HOST_HELP)
    p.add_argument("--jsonl", help=_JSONL_HELP)

    # resume
    p = subparsers.add_parser("resume", help="Print resume command")
    p.add_argument("session", nargs="?", help="Session ID (omit when using --jsonl)")
    p.add_argument("--project", help="Filter session search by project")
    p.add_argument("--host", help=_HOST_HELP)
    p.add_argument("--jsonl", help=_JSONL_HELP)
    p.add_argument("--from-subagent", action="store_true", dest="from_subagent",
                   help="For subagent IDs: copy the subagent's JSONL to a new "
                        "top-level UUID file in the same project (rewriting "
                        "sessionId + isSidechain so claude --resume accepts "
                        "it) and emit a resume command for that file. The "
                        "resumed session loads the subagent's transcript as "
                        "history but runs as the MAIN agent — the subagent "
                        "persona/tools are not restored. Default (no flag): "
                        "redirect to the parent session instead.")

    # info — compact metadata for one known session
    p = subparsers.add_parser("info", help="Compact metadata for a single session")
    p.add_argument("session", nargs="?", help="Session ID (omit when using --jsonl)")
    p.add_argument("--project", help="Filter session search by project")
    p.add_argument("--host", help=_HOST_HELP)
    p.add_argument("--jsonl", help=_JSONL_HELP)

    # subagents — list a session's spawned sidechain transcripts (agent-*.jsonl)
    p = subparsers.add_parser(
        "subagents", aliases=["sub"],
        help="List a session's spawned-agent (sidechain) transcripts + drill commands")
    p.add_argument("session", nargs="?", help="Session ID (omit when using --jsonl)")
    p.add_argument("--project", help="Filter session search by project")
    p.add_argument("--host", help=_HOST_HELP)
    p.add_argument("--jsonl", help=_JSONL_HELP)

    # branches — map the rewind/fork structure (active timeline vs abandoned arms)
    p = subparsers.add_parser(
        "branches", aliases=["forks", "fork"],
        help="Map a session's rewind FORKS: active timeline vs abandoned branches + raw drills")
    p.add_argument("session", nargs="?", help="Session ID (omit when using --jsonl)")
    p.add_argument("--project", help="Filter session search by project")
    p.add_argument("--host", help=_HOST_HELP)
    p.add_argument("--jsonl", help=_JSONL_HELP)

    # commits — list the git-commit subjects a session actually ran (authorship cross-check)
    p = subparsers.add_parser(
        "commits", aliases=["commit"],
        help="List the git commit(s) a session ran (subjects); --match to test authorship of one")
    p.add_argument("session", nargs="?", help="Session ID (omit when using --jsonl)")
    p.add_argument("--match", help="Only commits whose subject/command contains this (case-insensitive); "
                                   "exit 0 if ≥1 hit, 1 if none — the 'did THIS session author it?' test")
    p.add_argument("--project", help="Filter session search by project")
    p.add_argument("--host", help=_HOST_HELP)
    p.add_argument("--jsonl", help=_JSONL_HELP)

    # provenance — trace authorship of a path across sessions/runtimes/hosts
    p = subparsers.add_parser(
        "provenance", aliases=["prov"],
        help="Trace which session(s) authored, read, or mentioned a path")
    p.add_argument("path", help="Filesystem path (substring match). Supports "
                                "any string the tool inputs would carry "
                                "(file_path, path, notebook_path).")
    p.add_argument("--host", action="append",
                   help="Additional SSH target to walk (repeatable). Walks the "
                        "remote ~/.claude/projects/ via grep+fetch. v1 covers "
                        "Claude Code remotely; Codex/Cursor only walked locally.")
    p.add_argument("--show-mentions", action="store_true",
                   help="Include sessions that mention the path in other tool "
                        "inputs (e.g., Glob patterns). Default: hide.")
    p.add_argument("--limit-reads", type=int, default=10,
                   help="Cap reader rows in human output (default 10).")

    # stats
    p = subparsers.add_parser("stats", help="Index size, refresh backlog, background-indexer progress")
    p.add_argument("--full", action="store_true",
                   help="Also count projects and shallow/deep rows (scans the whole index; slow)")

    # index
    p = subparsers.add_parser("index", help="Update/rebuild index")
    p.add_argument("--reindex", action="store_true", help="Force full rebuild")
    p.add_argument("--project", help="Only index matching projects")
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)

    # --json is a top-level flag, but agents naturally type it AFTER the subcommand
    # (`cq search foo --json`), which argparse rejected ("unrecognized arguments: --json").
    # Accept it in EITHER position by stripping it from argv and recording it ourselves.
    argv = [a for a in sys.argv[1:] if a != "--json"]
    json_flag = len(argv) != len(sys.argv[1:])

    # --host is a per-subcommand flag, but agents naturally type it BEFORE the
    # subcommand (`cq --host workstation search foo`), mirroring the docs/CLAUDE.md form
    # `cq --host workstation ...`, which argparse rejected ("invalid choice: 'workstation'").
    # Accept it in EITHER position: lift a global-position `--host <val>` / `--host=val`
    # (appearing before the subcommand) into a fallback, then apply it post-parse to the
    # subcommand's own `host` if that wasn't set explicitly. Per-subcommand `--host` after
    # the subcommand is untouched (it always wins). Mirrors the --json any-position fix.
    _SUBCMDS = {
        "search", "s", "grep", "g", "list", "ls", "recent", "r", "turns", "t", "tools", "thinking",
        "think", "files", "f", "context", "ctx", "user", "u", "raw", "resume", "info",
        "subagents", "sub", "provenance", "prov", "stats", "index",
        "commits", "commit", "branches", "forks", "fork", "why", "resolve",
    }
    # Subcommand names agents reach for that don't exist (audit: `inspect`,
    # `session(s)`, `read`, `show`) map to the command they meant.
    _CMD_ALIASES = {"inspect": "info", "session": "info", "show": "info",
                    "sessions": "list", "read": "raw", "find": "search", "query": "search"}
    global_host = None
    lifted_argv = []
    seen_cmd = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if not seen_cmd and a == "--host" and i + 1 < len(argv):
            global_host = argv[i + 1]; i += 2; continue
        if not seen_cmd and a.startswith("--host="):
            global_host = a.split("=", 1)[1]; i += 1; continue
        if not seen_cmd and not a.startswith("-") and a not in _SUBCMDS:
            nxt = argv[i + 1] if i + 1 < len(argv) else ""
            if a in _CMD_ALIASES:
                print(f"cq: '{a}' isn't a command — running `{_CMD_ALIASES[a]}`", file=sys.stderr)
                a = _CMD_ALIASES[a]
            elif nxt in _SUBCMDS or nxt in _CMD_ALIASES:
                # `cq workstation search foo` — a host typed where the command goes
                global_host = a; i += 1; continue
        if not a.startswith("-") and a in _SUBCMDS:
            seen_cmd = True
        lifted_argv.append(a); i += 1
    argv = lifted_argv

    args = parser.parse_args(argv)
    if json_flag:
        args.json = True
    # argv with --host removed: what a remote cq run gets (_run_remote_cq).
    args._argv = _strip_host_args(argv) + (["--json"] if json_flag else [])

    # Apply the lifted global --host as a fallback (only if the subcommand supports it and
    # the user didn't pass a per-subcommand --host). `provenance` uses action="append" → list.
    if global_host is not None and hasattr(args, "host") and getattr(args, "host") in (None, []):
        args.host = [global_host] if args.command in ("provenance", "prov") else global_host

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Normalize aliases
    cmd_map = {
        "search": cmd_search, "s": cmd_search,
        "grep": cmd_grep, "g": cmd_grep,
        "list": cmd_list, "ls": cmd_list,
        "recent": cmd_recent, "r": cmd_recent,
        "turns": cmd_turns, "t": cmd_turns,
        "tools": cmd_tools,
        "thinking": cmd_thinking, "think": cmd_thinking,
        "files": cmd_files, "f": cmd_files,
        "context": cmd_context, "ctx": cmd_context,
        "why": cmd_why, "resolve": cmd_why,
        "user": cmd_user, "u": cmd_user,
        "raw": cmd_raw,
        "resume": cmd_resume,
        "info": cmd_info,
        "subagents": cmd_subagents, "sub": cmd_subagents,
        "commits": cmd_commits, "commit": cmd_commits,
        "branches": cmd_branches, "forks": cmd_branches, "fork": cmd_branches,
        "provenance": cmd_provenance, "prov": cmd_provenance,
        "stats": cmd_stats,
        "index": cmd_index,
    }

    handler = cmd_map.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
