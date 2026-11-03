"""Evidence retrieval for cq why; no index or model calls."""

from __future__ import annotations

import json
import builtins
import keyword
import math
import os
import re
import shlex
from bisect import bisect_right


USER_REVISION = re.compile(
    r"\b(?:no\b|that['’]s not it|actually|wrong|not (?:quite|really|that)|"
    r"instead|you misunderstood|I meant|stop)\b", re.I)
ASSISTANT_REVISION = re.compile(
    r"\b(?:root cause|the real issue|turns out|I was wrong|instead|"
    r"first (?:\w+ ){0,3}(?:trip|arrival)|only arms|confirmed the bug|the real cause|"
    r"my (?:earlier|initial|previous) (?:diagnosis|assumption) was)\b", re.I)

TOPIC_GAP_TURNS = 3
STOP_WORDS = frozenset("""
a about above actually after again all already also am an and any are as at back be
because been before being below both but by can cannot check checking checked code
could did do does doing done down each else even every few find finding findings
first for found from further get getting give go got had has have having here how
i if in into is it its just know last later let lets like long longer look looking
made make making many may me might more most much must my need next no nor not now
of off on once one only or other our out over own per please previous probably quite
rather really right run running said same say see seems set she should since so some
still such take than that thats the their them then there these they thing things
think this those through time to too tried try trying two under until up us use used
using very want was way we well were what when where which while who why will with
without work worked working would wrong you your yours seconds points narrowing
pin threshold change changes changed text statement message assistant user turn
failed failing fails failure successful success
analysis confirm finish implementation parallel prompt logic cause plan steps
""".split())
CODE_NOISE = frozenset(name.lower() for name in dir(builtins)) | frozenset(keyword.kwlist) | frozenset("""
os sys json re math pathlib subprocess shlex tempfile threading time datetime
typing collections itertools functools unittest contextlib argparse sqlite3
image imagechops imagestat pil rgb rgba stat convert crop open read write
command description timeout input file_path stdout stderr dev null tmp var
src repo tests test python python3 bash shell env
""".split())
INJECTED_USER = re.compile(
    r"<(?:task-notification|system-reminder|local-command-[\w-]+|command-name|"
    r"command-message|command-args|teammate-message)\b|"
    r"INTER[- ]SESSION MESSAGE|INTER-AGENT|━━━|^Base directory for this skill|"
    r"^This session is being continued from a previous conversation", re.I)


def injected_user(text):
    return bool(INJECTED_USER.search(text))


def useful_handle(token):
    token = token.strip()
    if len(token) < 3 or len(token) > 160 or not re.search(r"[A-Za-z]", token):
        return False
    exception_name = bool(re.fullmatch(r"[A-Z]\w*(?:Error|Exception|Failure)", token))
    if token.lower() in STOP_WORDS | CODE_NOISE and not exception_name:
        return False
    if re.search(r"\$|\bmktemp\b", token):
        return False
    if token.startswith(("/tmp/", "/private/tmp/", "/var/tmp/", "/var/folders/",
                         "/private/var/folders/", "/dev/", "tmp/")):
        return False
    # Dotted library members are code details rather than topic names.
    if token.split(".")[0].lower() in CODE_NOISE and not exception_name:
        return False
    return True


def extract_handles(text, commands=()):
    """Concrete paths, exceptions, quoted names, symbols and command strings."""
    handles = []
    variables = set(re.findall(r"(?:^|[\s;])([A-Za-z_]\w*)\s*=", text))
    variables.update(re.findall(r"\$\{?([A-Za-z_]\w*)", text))
    patterns = (
        r'''["'`]((?:~?/|\.{1,2}/)[^"'`\n]{1,260})["'`]''',
        r"(?:~?/|\.{1,2}/|[A-Za-z_][\w.-]*/)[\w./@+-]+",
        r"\b[\w-]+\.(?:py|js|ts|tsx|jsx|rb|go|rs|sh|jsonl?|ya?ml|toml|md|txt|cpp|h)\b",
        r"\b[A-Z][A-Za-z0-9_]*(?:Error|Exception|Failure)\b",
        r"\b(?:ERROR|Error|[A-Za-z]+Error):[^\n]{3,120}",
        r"`([^`\n]{2,160})`",
        r'''["']([A-Za-z_][\w.:-]{1,100})["'](?!\s*:)''',
        r"\b(?:def|class|function)\s+([A-Za-z_]\w*)",
        r"\b([A-Za-z_]\w*(?:\.\w+)*)\s*\(",
        r"\b[a-zA-Z][a-zA-Z0-9]*_[\w]+\b",
        r"\b[A-Za-z]\w+(?:-[A-Za-z]\w+)+\b",
        r"\b(?:run|running|command:)\s+([\w./-]+)",
    )
    for pattern in patterns:
        for token in re.findall(pattern, text):
            token = token.strip().rstrip(".,:;")
            if useful_handle(token) and token not in variables and token not in handles:
                handles.append(token)
    for command in commands:
        command = " ".join(command.split())
        if useful_handle(command) and "\n" not in command and len(command) < 120 and command not in handles:
            handles.append(command)
    return handles


def ranked_handles(anchor, user_text, tools, events, context=()):
    """Prefer the claim's prose, then human context, then concrete tool details.

    Document frequency uses kept narrative blocks rather than logs or code. A
    minimum sample keeps short fixtures from making every topic word 'common'.
    """
    weights = {}

    def add(text, weight, plain=False):
        candidates = extract_handles(text)
        if plain:
            candidates += re.findall(r"\b[A-Za-z][A-Za-z0-9_-]{2,}\b", text)
        for token in candidates:
            if useful_handle(token):
                key = token.lower()
                old = weights.get(key)
                value = weight * (1.4 if token[0].isupper() or "_" in token or "-" in token else 1)
                if old is None or value > old[1]:
                    weights[key] = (token, value)

    add(anchor["text"], 10, plain=True)
    rich_claim = len(weights) >= 3
    add(user_text, 3, plain=True)
    for narrative in context:
        add(narrative["text"], 5)
    for tool in tools:
        inp = tool["input"]
        add(inp.get("description", ""), 2)
        for key in ("file_path", "path", "command"):
            value = inp.get(key)
            if isinstance(value, str):
                if not rich_claim:
                    add(value, 2)
                for command in re.findall(r"\b(?:killall|pytest|npm|cargo|go|git)\s+[A-Za-z][\w.-]*", value):
                    add('`' + command + '`', 5)
    documents = [e["text"] for e in events if not e["abandoned"] and not e.get("injected")
                 and e["kind"] in ("text", "thinking") and e["role"] in ("user", "assistant")]
    ranked = []
    for token, weight in weights.values():
        frequency = sum(mentions(text, [token]) for text in documents)
        if len(documents) >= 20 and frequency >= len(documents) * 0.4:
            continue
        score = weight * (1 + math.log((len(documents) + 1) / (frequency + 1)))
        ranked.append((score, token))
    ranked.sort(key=lambda pair: (-pair[0], pair[1].lower()))
    threshold = ranked[0][0] * 0.4 if ranked else 0
    return [token for score, token in ranked[:10] if score >= threshold]


def mentions(text, handles):
    """Token boundaries prevent e.g. ValueError matching NotValueError."""
    return any(re.search(r"(?<![\w])" + re.escape(h) + r"(?![\w])", text, re.I)
               for h in handles)


def project_file(path, cwd):
    """Recognize concrete, non-temporary file operands within the session cwd."""
    if not cwd or not useful_handle(path) or not ("/" in path or re.search(r"\.\w+$", path)):
        return False
    full = os.path.abspath(os.path.join(os.path.expanduser(cwd), os.path.expanduser(path)))
    return os.path.commonpath([full, os.path.abspath(os.path.expanduser(cwd))]) == os.path.abspath(os.path.expanduser(cwd))


def result_status(event):
    text = event["text"]
    code = re.search(r'(?:exit(?:ed with)?(?: code|_code)?[\s"=:]+)(-?\d+)', text, re.I)
    if event.get("is_error") or (code and int(code[1]) != 0):
        return "fail"
    if re.search(r"\b(?:FAILED|FAILURE|Traceback)\b|\b[1-9]\d* (?:failed|errors?)\b", text):
        return "fail"
    if code or re.search(r"(?m)^OK\s*$|\b(?:[1-9]\d* passed|tests? pass(?:ed)?|successfully)\b", text, re.I):
        return "pass"
    return None


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return json.dumps(content, ensure_ascii=False)


def read_events(path, cq):
    """Normalize raw blocks, retaining physical lines and context's turn starts.

    Parse a copy with line markers as timestamps to reuse the exact turn parser
    (including merged user messages, form answers and Codex boundaries). The raw
    transcript and normal context output are never changed.
    """
    objects, marked, events = [], [], []
    with open(path) as stream:
        for line, raw in enumerate(stream):
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            objects.append((line, obj))
            marked.append(json.dumps(dict(obj, timestamp="cq-line:" + str(line))))
    turns, fmt = cq.parse_transcript("\n".join(marked))
    starts = []
    for turn in turns:
        timestamp = turn.get("timestamp") or ""
        if timestamp.startswith("cq-line:"):
            starts.append((turn["index"], int(timestamp.split(":")[1])))
    graph = cq._session_graph(path) if fmt == "claude-code" else None
    kept = {e["_lineIdx"] for e in graph["kept"]} if graph else set()
    cwd = ""
    for line, obj in objects:
        abandoned = bool(graph and graph["active"] and line not in kept)
        role = obj.get("type") or obj.get("role") or (obj.get("message") or {}).get("role")
        content = (obj.get("message") or {}).get("content", "")
        payload = obj.get("payload") or {}
        cwd = obj.get("cwd") or payload.get("cwd") or cwd
        blocks = []
        if fmt == "codex":
            if obj.get("type") != "response_item":
                continue
            ptype = payload.get("type")
            if ptype == "message":
                role = payload.get("role")
                blocks = [{"type": "text", "text": _content_text(payload.get("content", []))}]
            elif ptype in ("function_call", "custom_tool_call"):
                raw = payload.get("arguments") if ptype == "function_call" else payload.get("input")
                blocks = [{"type": "tool_use", "id": payload.get("call_id"),
                           "name": payload.get("name", ""),
                           "input": {"command": cq._codex_command_text(payload.get("name"), raw)}}]
                role = "assistant"
            elif ptype in ("function_call_output", "custom_tool_call_output"):
                output = payload.get("output", "")
                if isinstance(output, dict):
                    code = (output.get("metadata") or {}).get("exit_code")
                    output = json.dumps(output)
                else:
                    code = None
                blocks = [{"type": "tool_result", "tool_use_id": payload.get("call_id"),
                           "content": output, "is_error": code is not None and code != 0}]
        elif role in ("user", "assistant"):
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
            form = cq.extract_form_answers(obj)
            if form:
                blocks = list(blocks) + [{"type": "text", "text": form}]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            event = {"line": line, "timestamp": obj.get("timestamp") or "",
                     "role": role, "kind": kind, "abandoned": abandoned, "cwd": cwd,
                     "injected": bool(kind in ("text", "thinking") and role == "user" and (
                         obj.get("isMeta") or obj.get("isSidechain")
                         or injected_user(_content_text(content))
                         or (isinstance(content, list) and any(
                             isinstance(b, dict) and b.get("type") == "tool_result" for b in content))))}
            if kind in ("text", "thinking"):
                event["text"] = block.get(kind, "")
            elif kind == "tool_use":
                event.update(tool=block.get("name", ""), input=block.get("input") or {},
                             call_id=block.get("id"), text=json.dumps(block.get("input") or {}, ensure_ascii=False))
            elif kind == "tool_result":
                event.update(role="tool", call_id=block.get("tool_use_id"),
                             text=_content_text(block.get("content", "")), is_error=block.get("is_error", False))
            else:
                continue
            if event["text"]:
                events.append(event)
    positions = [line for _, line in starts]
    for event in events:
        position = bisect_right(positions, event["line"]) - 1
        event["turn"] = starts[position][0] if position >= 0 else 0
    return events, starts, graph["warnings"] if graph else []


def drill(session, line):
    if session.get("project") == "[ad-hoc]":
        return f"cq context --jsonl {shlex.quote(str(session['path']))} --turn @{line}"
    host = f" --host {shlex.quote(session['remote_host'])}" if session.get("remote_host") else ""
    return f"cq context {shlex.quote(session['session_id'])} @{line}{host}"


def row(event, kind, session):
    return {"line": event["line"], "timestamp": event["timestamp"], "role": event["role"],
            "kind": kind, "snippet": " ".join(event["text"].split())[:200],
            "drill": drill(session, event["line"])}


def topic_boundary(events, anchor, handles):
    """Trim at the first of three consecutive human turns without handles.

    Injected turns do not advance the gap. Trimming retrospectively to its start
    avoids presenting unrelated conversation while waiting to confirm the gap.
    """
    groups = {}
    for event in events:
        if event["abandoned"] or event.get("injected") or event["line"] < anchor["line"]:
            continue
        groups.setdefault(event["turn"], []).append(event)
    gap = []
    for turn, group in groups.items():
        if turn == anchor["turn"]:
            continue
        human = any(e["role"] == "user" and e["kind"] == "text" for e in group)
        if any(e["kind"] != "tool_result" and mentions(e["text"], handles) for e in group):
            gap = []
        elif human:
            gap.append((turn, min(e["line"] for e in group)))
            if len(gap) == TOPIC_GAP_TURNS:
                return {"turn": gap[0][0], "line": gap[0][1], "gap_turns": TOPIC_GAP_TURNS,
                        "reason": f"{TOPIC_GAP_TURNS} consecutive human turns without topic handles"}
    return None


def resolve(session, spec, terms, limit, cq):
    events, starts, warnings = read_events(session["path"], cq)
    spec = str(spec).strip()
    line_ref = re.fullmatch(r"(?:@|msg\[|line:?)(\d+)\]?", spec)
    rng = re.fullmatch(r"(\d+)\s*[-:.]+\s*(\d+)", spec)
    if line_ref:
        line = int(line_ref[1])
        if not events or line > max(e["line"] for e in events):
            raise ValueError("anchor address is past the end of the transcript")
        number = next((n for n, start in reversed(starts) if start <= line), None)
    elif spec.isdigit() or rng:
        number = min(int(rng[1]), int(rng[2])) if rng else int(spec)
    else:
        raise ValueError("use N, N-M (anchors the first turn), @LINE or msg[LINE]")
    start = next((line for n, line in starts if n == number), None)
    if start is None:
        raise ValueError(f"turn {number} does not exist")
    end = next((line for n, line in starts if n > number), float("inf"))
    anchor_events = [e for e in events if start <= e["line"] < end and not e.get("injected")]
    if not anchor_events:
        raise ValueError("anchor has no message or tool blocks at this address")
    narratives = [e for e in anchor_events if e["role"] == "assistant" and e["kind"] in ("text", "thinking")]
    preceding = [e for e in narratives if line_ref and e["line"] <= line]
    anchor = preceding[-1] if preceding else next((e for e in narratives if e["kind"] == "text"), anchor_events[0])
    beat_end = next((e["line"] for e in narratives if e["line"] > anchor["line"]), end)
    tools = [e for e in anchor_events if e["kind"] == "tool_use" and anchor["line"] <= e["line"] < beat_end]
    user_text = next((e["text"] for e in reversed(events) if e["line"] <= anchor["line"]
                      and e["role"] == "user" and e["kind"] == "text" and not e.get("injected")
                      and not e["abandoned"]), "")
    handles = list(dict.fromkeys(t.strip() for t in terms if t.strip())) if terms else ranked_handles(
        anchor, user_text, tools, events,
        [e for e in narratives if e["line"] < anchor["line"] and not e["abandoned"]][-3:])
    stopped = topic_boundary(events, anchor, handles) if handles else None
    revisions, calls, statuses = [], {}, {}
    tracked_commands = {" ".join(e["input"]["command"].split()) for e in tools
                        if e["line"] <= (line if line_ref else anchor["line"])
                        and isinstance(e["input"].get("command"), str)}
    evidence = {"last_passing_run": None, "last_edit": None, "last_commit": None}
    last_statement = None
    staged = False
    for event in events:
        if stopped and event["line"] >= stopped["line"]:
            break
        if event["abandoned"] or event.get("injected"):
            continue
        topic = mentions(event["text"], handles)
        kind = "mention" if topic else None
        if event["kind"] in ("text", "thinking"):
            if topic and event["role"] == "user" and USER_REVISION.search(event["text"]):
                kind = "user-correction"
            elif topic and event["role"] == "assistant" and ASSISTANT_REVISION.search(event["text"]):
                kind = "assistant-revision"
        elif event["kind"] == "tool_use":
            calls[event.get("call_id")] = event
            inp, tool = event["input"], event["tool"]
            command = inp.get("command", "")
            files = [inp[k] for k in cq._PROVENANCE_PATH_FIELDS if isinstance(inp.get(k), str)]
            path_handles = [h for h in handles if ("/" in h or re.search(r"\.[A-Za-z0-9]+$", h)) and useful_handle(h)]
            writes = tool in cq._PROVENANCE_AUTHORED and any(
                any(cq._path_matches(f, h, event["cwd"]) for h in path_handles)
                or (topic and project_file(f, event["cwd"]))
                for f in files)
            if isinstance(command, str):
                writes = writes or any(cq._shell_writes(command, h, event["cwd"]) for h in path_handles)
                if topic:
                    writes = writes or any(cq._shell_writes(
                        command, os.path.abspath(os.path.join(os.path.expanduser(event["cwd"]), os.path.expanduser(f))), event["cwd"])
                                           for f in extract_handles(command) + cq._patch_files(command)
                                           if project_file(f, event["cwd"]))
                if re.search(r"\bgit\s+add\b", command) and topic:
                    staged = True
                if re.search(r"\bgit\s+commit\b", command):
                    if topic or staged:
                        kind = "commit"
                    staged = False
            if writes:
                kind = "file-write"
        elif event["kind"] == "tool_result":
            call = calls.get(event.get("call_id"))
            if call:
                command = call["input"].get("command", "")
                status = result_status(event)
                if command and status:
                    key = " ".join(command.split())
                    if topic or mentions(call["text"], handles):
                        tracked_commands.add(key)
                    if status == "pass" and key in tracked_commands:
                        kind = "fail-to-pass" if statuses.get(key) == "fail" else "passing-run"
                    statuses[key] = status
        if event["line"] < anchor["line"] or event is anchor:
            continue
        if not kind:
            continue
        item = row(event, kind, session)
        if kind in ("passing-run", "fail-to-pass"):
            item["command"] = command
            item["snippet"] = (" ".join(command.split()) + " → " + item["snippet"])[:200]
        # Multiple blocks on one line share a drill but retain their own evidence.
        revisions.append(item)
        if event["kind"] in ("text", "thinking") and event["role"] == "assistant" and topic:
            last_statement = item
        if kind in ("passing-run", "fail-to-pass"):
            evidence["last_passing_run"] = item
        elif kind == "file-write":
            evidence["last_edit"] = item
        elif kind == "commit":
            evidence["last_commit"] = item
    revised = any(r["kind"] in ("user-correction", "assistant-revision", "fail-to-pass", "file-write", "commit") for r in revisions)
    note = "Evidence candidates only; judge the explanation from the transcript."
    if not revised:
        note = "no later revision found on the kept path"
    if anchor["abandoned"]:
        note = "Anchor is on an abandoned branch; later rows follow the kept path. " + note
    if not handles:
        note += "; no concrete handles extracted — supply --term"
    # Show the early diagnosis changes before later evidence when rows are capped.
    selected = list(range(len(revisions)))
    if len(selected) > limit:
        significant = [i for i, r in enumerate(revisions) if r["kind"] in ("user-correction", "assistant-revision", "fail-to-pass")]
        selected = significant[:limit]
        if len(selected) < limit:
            selected += [i for i in range(len(revisions)) if i not in selected][:limit - len(selected)]
        selected.sort()
    return {"session": session["session_id"], "handles": handles,
            "anchor": dict(row(anchor, "anchor", session), abandoned=anchor["abandoned"],
                           turn=number, turn_start_line=start,
                           turn_end_line=int(end) - 1 if end != float("inf") else max(e["line"] for e in events)),
            "revisions": [revisions[i] for i in selected], "total_revisions": len(revisions),
            "truncated": len(revisions) > limit, "last_statement": last_statement,
            "evidence": evidence, "warnings": warnings, "note": note, "stopped": stopped}


def follow_sessions(session, handles, cq, limit):
    """Best effort: later local sessions in the same cwd with two handles.

    Only kept message blocks count; a copied abandoned diagnosis is insufficient.
    Remote sessions are not searched beyond the fetched anchor transcript.
    """
    if session.get("remote_host") or len(handles) < 2:
        return []

    def metadata(path, full=False):
        cwd, first, last = "", "", ""
        with open(path) as stream:
            for raw in stream:
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                cwd = cwd or obj.get("cwd") or (obj.get("payload") or {}).get("cwd", "")
                timestamp = obj.get("timestamp") or ""
                if timestamp:
                    first = first or timestamp
                    last = timestamp
                if cwd and first and not full:
                    break
        return cwd, first, last

    cwd, _, end = metadata(session["path"], full=True)
    if not cwd or not end:
        return []
    found = []
    for candidate in cq.discover_sessions():
        if candidate.get("kind", "main") != "main" or candidate["path"] == session["path"]:
            continue
        try:
            # Claude project keys already encode cwd; skip other Claude projects.
            if (candidate["group"] == session["group"] == "Claude Code"
                    and session["project"] != "[ad-hoc]" and candidate["project"] != session["project"]):
                continue
            other_cwd, start, _ = metadata(candidate["path"])
            if other_cwd != cwd or not start or start <= end:
                continue
            events, _, _ = read_events(candidate["path"], cq)
        except (OSError, ValueError):
            continue
        matched = {h for h in handles if any(not e["abandoned"] and mentions(e["text"], [h]) for e in events)}
        if len(matched) >= 2:
            event = next(e for e in events if not e["abandoned"] and mentions(e["text"], matched))
            found.append(dict(row(event, "follow-session", candidate),
                              session=candidate["session_id"], handles=sorted(matched)))
    return sorted(found, key=lambda r: r["timestamp"])[:limit]
