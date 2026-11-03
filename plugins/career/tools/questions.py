#!/usr/bin/env python3
"""Local Questions inbox. Agent delivery uses /ask; notifications use /text.

Run questions.py --help for the store, thread, correction and pending-event commands.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
import yaml

from integrations import Agents, Text
from runtime import HOME, SETTING_OPTIONS, PERSON_BY, Refused, read_json, write_json, write_text, locked as workflow_locked
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HOME
QUESTIONS = WORKSPACE / "data" / "questions"
POSTINGS = WORKSPACE / "data" / "postings"
PROFILE = WORKSPACE / "profile.yml"
APPLICATIONS = WORKSPACE / "data" / "applications"
APP_LINK = os.environ.get("CAREER_APP_LINK", "http://127.0.0.1:7490/app/")


LEAD_SESSION = os.environ.get("CAREER_LEAD_SESSION", "")

TOPICS = ("application", "profile", "site", "other")
KINDS = ("choice", "multiselect", "yesno", "text", "confirm")
STATUSES = ("open", "answered", "dismissed")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,120}$")
POSTING_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,200}$")
RULING_FROM = {"new", "enriched", "approved", "skipped"}   # same as serve.py: statuses a ruling may come from
MAX_TITLE, MAX_QUESTION, MAX_ANSWER, MAX_NOTE, MAX_COMMAND = 60, 400, 64000, 2000, 4000
CONFIRM_WORDS = {"confirm", "confirmed", "yes", "ok", "approve", "approved"}
# Who answers from an app UI (the iPhone app, the web app in a laptop browser). Their events are delivered to the
# asking session; any other `by` was written from a session, which already knows.
APP_CLIENTS = ("phone", "web")


class AskError(Exception):
    """Bad input (unknown id, answer not among the options, …). serve.py maps it to 400/404."""

    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


@dataclass
class Paths:
    questions: Path = QUESTIONS
    postings: Path = POSTINGS
    profile: Path = PROFILE
    applications: Path = APPLICATIONS


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:n].rstrip("-")


def _write_json(p: Path, obj: dict) -> None:
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, p)


def _write_text(p: Path, text: str) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


@contextlib.contextmanager
def locked(paths: Paths):
    """Cross-process lock: the CLI and serve.py never write questions, profile.yml or a record at once."""
    paths.questions.mkdir(parents=True, exist_ok=True)
    with open(paths.questions / ".lock", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ── store ────────────────────────────────────────────────────────────

def q_path(paths: Paths, qid: str) -> Path:
    if not ID_RE.match(qid or ""):
        raise AskError(f"no such question: {qid!r}", 404)
    p = paths.questions / f"{qid}.json"
    if not p.is_file():
        raise AskError(f"no such question: {qid!r}", 404)
    return p


def read_raw(paths: Paths, qid: str) -> dict:
    with open(q_path(paths, qid), encoding="utf-8") as f:
        return json.load(f)


def resolve_id(paths: Paths, qid: str) -> str:
    """A merged record's id resolves to the thread it was folded into."""
    seen = set()
    while qid not in seen:
        seen.add(qid)
        nxt = read_raw(paths, qid).get("merged_into")
        if not nxt:
            return qid
        qid = nxt
    raise AskError(f"merge loop at {qid!r}", 500)


def read_q(paths: Paths, qid: str) -> dict:
    q = read_raw(paths, resolve_id(paths, qid))
    _ensure_thread(q)
    return q


def write_q(paths: Paths, q: dict) -> None:
    _write_json(paths.questions / f"{q['id']}.json", q)


def list_q(paths: Paths, status: str | None = None) -> list[dict]:
    out = []
    if not paths.questions.is_dir():
        return out
    for p in sorted(paths.questions.glob("*.json")):
        try:
            q = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[ask] skipping {p.name}: {e}", file=sys.stderr)
            continue
        if not isinstance(q, dict) or "id" not in q or q.get("merged_into"):
            continue
        _ensure_thread(q)
        if status and q.get("status") != status:
            continue
        q["_order"] = p.stat().st_birthtime if hasattr(p.stat(), "st_birthtime") else p.stat().st_ctime
        out.append(q)
    # Open first, oldest question first (a queue); then answered/dismissed, most recent first.
    opened = sorted((q for q in out if q.get("status") == "open"), key=lambda q: (q.get("asked_at") or "", q["_order"]))
    rest = sorted((q for q in out if q.get("status") != "open"),
                  key=lambda q: (q.get("answered_at") or q.get("dismissed_at") or q.get("asked_at") or ""), reverse=True)
    for q in out:
        q.pop("_order", None)
    return opened + rest


def new_id(paths: Paths, title: str) -> str:
    base = f"{datetime.now().strftime('%Y%m%d')}-{slug(title, 48) or 'question'}"
    qid, n = base, 2
    while (paths.questions / f"{qid}.json").exists():
        qid, n = f"{base}-{n}", n + 1
    return qid


def posting_links(paths: "Paths", posting_id: str) -> str:
    """Links to the posting and its app record for the question context."""
    try:
        r = json.loads((paths.postings / f"{posting_id}.json").read_text())
    except Exception:
        return ""
    p = r.get("posting") or {}
    url = r.get("apply_url") or p.get("apply_url") or r.get("url") or p.get("url")
    web = APP_LINK.replace("/app/", "/web/") + f"#/posting/{posting_id}"
    return "\n".join(x for x in (f"Posting: {url}" if url else "", f"In the app: {web}") if x)


def add(paths: Paths, *, topic: str, title: str, question: str, kind: str = "text", options=None,
        posting_id: str | None = None, context: str | None = None, applies_to: dict | None = None,
        by: str = "session", asked_by_session: str | None = None, command: str | None = None) -> dict:
    title, question = (title or "").strip(), (question or "").strip()
    if topic not in TOPICS:
        raise AskError(f"topic must be one of {', '.join(TOPICS)}")
    if not title or len(title) > MAX_TITLE:
        raise AskError(f"title must be 1–{MAX_TITLE} characters (got {len(title)})")
    if posting_id and not POSTING_RE.match(posting_id):
        raise AskError(f"bad posting id: {posting_id!r}")
    if posting_id and not (paths.postings / f"{posting_id}.json").is_file():
        raise AskError(f"no posting record for {posting_id!r}")
    question, options, applies_to = _check_ask(question, kind, options, applies_to, context, posting_id)
    command = _check_command(command)
    # a confirm question's context is the proposed answer itself, so links never go there
    if posting_id and kind != "confirm" and "/#/posting/" not in (context or ""):
        links = posting_links(paths, posting_id)
        context = ((context or "").strip() + ("\n\n" if (context or "").strip() else "") + links) if links else context
    with locked(paths):
        q = {
            "id": new_id(paths, title),
            "asked_at": now_iso(),
            "by": by,
            "topic": topic,
            "posting_id": posting_id,
            "title": title,
            "question": question,
            "context": (context or "").strip() or None,
            "kind": kind,
            "options": options if kind in ("choice","multiselect") else None,
            "answer": None,
            "answered_at": None,
            "answer_source": None,
            "applies_to": applies_to,
            "status": "open",
            "applied": None,
            "asked_by_session": asked_by_session or session_from_env() or LEAD_SESSION,
            "events": [],
        }
        if command:
            q["command"] = command
        _ensure_thread(q)
        write_q(paths, q)
    return q


def _check_ask(question, kind, options, applies_to, context, posting_id):
    """Validate one ask (the original or a `reply`); returns (question, options, applies_to)."""
    question = (question or "").strip()
    if kind not in KINDS:
        raise AskError(f"kind must be one of {', '.join(KINDS)}")
    if not question or len(question) > MAX_QUESTION:
        raise AskError(f"question must be 1–{MAX_QUESTION} characters (got {len(question)})")
    options = [str(o).strip() for o in (options or []) if str(o).strip()]
    if len(set(options))!=len(options):raise AskError('Options must have distinct labels.')
    if kind in ("choice","multiselect") and not options:
        raise AskError("a choice question needs at least one option")
    if kind not in ("choice","multiselect") and options:
        raise AskError("--options only applies to choice or multiselect")
    applies_to = applies_to or {"note": True}
    if not isinstance(applies_to, dict):
        raise AskError("--applies-to must be a JSON object")
    if applies_to.get("form_question") and not (applies_to.get("posting_id") or posting_id):
        raise AskError("a form_question needs a posting_id")
    if applies_to.get("ruling") and applies_to["ruling"] != "approve":
        raise AskError("ruling must be 'approve'")
    if applies_to.get("ruling") and not posting_id:
        raise AskError("a ruling question needs --posting")
    if applies_to.get("ruling") and kind not in ("yesno", "confirm"):
        raise AskError("a ruling question must be yesno or confirm")
    if kind == "confirm" and applies_to.get("form_question") and not (context or "").strip():
        raise AskError("a confirm-kind form_question needs the proposed answer as --context")
    return question, (options if kind in ("choice","multiselect") else None), applies_to


def _check_command(command) -> str | None:
    """An ask's `command`: one shell command, kept as written apart from surrounding blank space; None when empty."""
    command = (command or "").strip()
    if len(command) > MAX_COMMAND:
        raise AskError(f"command must be at most {MAX_COMMAND} characters (got {len(command)})")
    return command or None


def _mirror_command(q: dict, command: str | None) -> None:
    """Top-level `command` follows the latest ask; a record whose latest ask has none carries no key."""
    if command:
        q["command"] = command
    else:
        q.pop("command", None)


# ── threads ──────────────────────────────────────────────────────────

def _ask_turn(at, by, text, kind, options, context, applies_to, title=None, command=None) -> dict:
    t = {"role": "ask", "at": at, "by": by, "text": text, "kind": kind, "options": options,
         "context": (context or "").strip() or None, "applies_to": applies_to}
    if command:
        t["command"] = command
    if title:
        t["title"] = title
    return t


def _user_turn(at, by, answer_text, note) -> dict:
    return {"role": "user", "at": at, "by": by, "text": note, "answer": answer_text, "note": note}


def _renumber(thread: list) -> list:
    for i, t in enumerate(thread):
        t["n"] = i + 1
    return thread


def _ensure_thread(q: dict) -> list:
    """`thread`, building it for a record written before threads: turn 1 from the top-level ask, then one user
    turn per answer/comment event (and the stored answer, if no event carries it)."""
    if "thread" in q:
        return q["thread"]
    turns = [_ask_turn(q.get("asked_at"), q.get("by"), q.get("question"), q.get("kind"), q.get("options"),
                       q.get("context"), q.get("applies_to"), command=q.get("command"))]
    events = _ensure_events(q)
    for e in events:
        turns.append(_user_turn(e.get("at"), e.get("by"), e.get("answer") if e.get("type") == "answer" else None,
                                e.get("note")))
    if q.get("answer") is not None and not any(e.get("type") == "answer" for e in events):
        turns.append(_user_turn(q.get("answered_at"), q.get("answer_source"), q["answer"], None))
    turns[1:] = sorted(turns[1:], key=lambda t: t.get("at") or "")
    q["thread"] = _renumber(turns)
    return q["thread"]


def _append_turn(q: dict, turn: dict) -> dict:
    th = _ensure_thread(q)
    turn["n"] = len(th) + 1
    th.append(turn)
    return turn


def reply(paths: Paths, qid: str, *, text: str, kind: str | None = None, options=None, context: str | None = None,
          applies_to: dict | None = None, title: str | None = None, by: str = "session",
          command: str | None = None) -> dict:
    """The agent re-asks in the same thread: append an "ask" turn, reopen, clear the answer, and mirror the ask on
    the top-level fields. kind and applies_to default to the current ones; options only carry over with the kind.
    `command` belongs to this ask alone, like `context`: a re-ask without one clears the top-level command."""
    title = (title or "").strip() or None
    command = _check_command(command)
    if title and len(title) > MAX_TITLE:
        raise AskError(f"title must be 1–{MAX_TITLE} characters (got {len(title)})")
    with locked(paths):
        q = read_q(paths, qid)
        k = kind or q.get("kind") or "text"
        opts = options if options is not None else (q.get("options") if k == q.get("kind") else None)
        question, opts, applies = _check_ask(text, k, opts, applies_to or q.get("applies_to"), context,
                                             q.get("posting_id"))
        turn = _append_turn(q, _ask_turn(now_iso(), str(by or "session")[:40], question, k, opts, context, applies,
                                         title, command))
        q.update(question=question, kind=k, options=opts, context=turn["context"], applies_to=applies,
                 status="open", answer=None, answered_at=None, answer_source=None, applied=None)
        _mirror_command(q, command)
        if title:
            q["title"] = title
        for key in ("needs_reading", "apply_error", "dismissed_at", "dismissed_by"):
            q.pop(key, None)
        write_q(paths, q)
    return q


_PREAMBLE = re.compile(r"\AEarlier question: .*?\n\n(?:You said: \u201c.*?\u201d(?:\n\n|\Z))?", re.S)


def merge(paths: Paths, src_id: str, into_id: str) -> dict:
    """Fold an old follow-up record into the thread it re-asked: its ask becomes an "ask" turn (generated
    "Earlier question / You said" preamble stripped), its answers/comments become user turns, events and notes
    move over, and the thread mirrors the latest ask and the follow-up's state. The source keeps its file with
    `merged_into` and disappears from every list. Idempotent."""
    with locked(paths):
        src, dst = read_raw(paths, src_id), read_raw(paths, into_id)
        if src.get("merged_into") == into_id:
            return read_q(paths, into_id)
        if src.get("merged_into") or dst.get("merged_into") or src_id == into_id:
            raise AskError(f"cannot merge {src_id} into {into_id}")
        _ensure_thread(dst)
        s_thread = _ensure_thread(dict(src))
        ctx = _PREAMBLE.sub("", src.get("context") or "").strip() or None
        turns = [_ask_turn(src.get("asked_at"), src.get("by"), src.get("question"), src.get("kind"),
                           src.get("options"), ctx, src.get("applies_to"), src.get("title"), src.get("command"))]
        turns += [t for t in s_thread[1:] if t["role"] == "user"]
        dst["thread"] = _renumber(dst["thread"][:1] + sorted(dst["thread"][1:] + turns, key=lambda t: t.get("at") or ""))
        ev = _ensure_events(dst)
        for e in _ensure_events(dict(src)):
            ev.append(dict(e, n=len(ev) + 1, merged_from=src_id))
        dst["notes"] = sorted((dst.get("notes") or []) + (src.get("notes") or []), key=lambda n: n.get("at") or "")
        last_ask = [t for t in dst["thread"] if t["role"] == "ask"][-1]
        dst.update(question=last_ask["text"], kind=last_ask["kind"], options=last_ask["options"],
                   context=last_ask["context"], applies_to=last_ask["applies_to"])
        _mirror_command(dst, last_ask.get("command"))
        if last_ask.get("title"):
            dst["title"] = last_ask["title"]
        for key in ("status", "answer", "answered_at", "answer_source", "applied", "needs_reading", "apply_error"):
            if key in src:
                dst[key] = src[key]
            else:
                dst.pop(key, None)
        last_note = next((n for n in reversed(dst["notes"])), None)
        if last_note:
            dst["note"], dst["note_at"], dst["note_by"] = last_note.get("text"), last_note.get("at"), last_note.get("by")
        dst["asked_by_session"] = src.get("asked_by_session") or dst.get("asked_by_session") or LEAD_SESSION
        dst["merged_from"] = sorted(set((dst.get("merged_from") or []) + [src_id]))
        for key in ("superseded_by", "follow_up_of"):
            dst.pop(key, None)
        src["merged_into"] = into_id
        write_q(paths, dst)
        write_q(paths, src)
        return dst


def migrate(paths: Paths) -> int:
    """Write `thread` (and `events`) into every record that lacks them. Returns how many changed."""
    n = 0
    with locked(paths):
        for p in sorted(paths.questions.glob("*.json")):
            q = json.loads(p.read_text(encoding="utf-8"))
            if "thread" in q and "events" in q:
                continue
            _ensure_thread(q)
            write_q(paths, q)
            n += 1
    return n


def normalize_answer(q: dict, text) -> str:
    if q.get('kind')=='multiselect':
        from field_policy import multiselect_value
        try:
            result=multiselect_value(text,q.get('options') or [])
        except Refused as error:
            raise AskError(str(error)) from error
        if not result:raise AskError('Choose at least one option.')
        return result
    a = str(text if text is not None else "").strip()
    if not a:
        raise AskError("empty answer")
    if len(a) > MAX_ANSWER:
        raise AskError(f"answer longer than {MAX_ANSWER} characters")
    kind = q.get("kind")
    if kind == "yesno":
        low = a.lower()
        if low in ("yes", "y", "true"):
            return "Yes"
        if low in ("no", "n", "false"):
            return "No"
        raise AskError("answer must be Yes or No")
    if kind == "choice":
        for o in q.get("options") or []:
            if o.lower() == a.lower():
                return o
        raise AskError("answer must be one of: " + ", ".join(q.get("options") or []))
    if kind == "confirm" and a.lower() in CONFIRM_WORDS:
        return "Confirmed"
    return a      # text, or a confirm-kind question answered with an edited replacement


# ── delivery to the asking session ───────────────────────────────────

def session_from_env() -> str | None:
    return os.environ.get("CAREER_COORDINATOR_SESSION")


def _ensure_events(q: dict) -> list:
    """`events`, creating it for a record written before events existed: each stored comment becomes an
    undelivered comment event (phone) or a delivered one (written by a session)."""
    if "events" not in q:
        q["events"] = [{"n": i + 1, "at": n.get("at"), "by": n.get("by"), "type": "comment",
                        "answer": n.get("with_answer"), "note": n.get("text"),
                        "delivered_at": None if n.get("by") in APP_CLIENTS else n.get("at")}
                       for i, n in enumerate(q.get("notes") or [])]
    return q["events"]


def _add_event(q: dict, kind: str, by: str, answer_text, note) -> dict:
    ev = _ensure_events(q)
    at = now_iso()
    e = {"n": len(ev) + 1, "at": at, "by": by, "type": kind, "answer": answer_text, "note": note,
         "delivered_at": None}
    if by not in APP_CLIENTS:  # written from a session: the session already knows
        e["delivered_at"], e["delivered_via"] = at, "self"
    ev.append(e)
    return e


def event_line(q: dict, e: dict) -> str:
    def flat(x):
        return re.sub(r"\s+", " ", str(x)).strip() if x else "none"
    return f"[App] {q.get('title')} — answer: {flat(e.get('answer'))}; comment: {flat(e.get('note'))} (id {q['id']})"


def target_session(q: dict) -> str:
    return (q.get("asked_by_session") or LEAD_SESSION).lower()


def pending(paths: Paths, session: str | None = None) -> list[tuple[dict, dict]]:
    """(question, event) for every answer/comment event not yet delivered, oldest first; one session or all."""
    out = []
    for q in list_q(paths):
        if session and target_session(q) != session.lower():
            continue
        for e in _ensure_events(q):
            if not e.get("delivered_at"):
                out.append((q, e))
    return sorted(out, key=lambda qe: qe[1].get("at") or "")


def mark_delivered(paths: Paths, items, via: str = "pending") -> int:
    stamp = now_iso()
    n = 0
    with locked(paths):
        for qid in {q["id"] for q, _ in items}:
            want = {e["n"] for q, e in items if q["id"] == qid}
            q = read_q(paths, qid)
            for e in _ensure_events(q):
                if e["n"] in want and not e.get("delivered_at"):
                    e["delivered_at"], e["delivered_via"] = stamp, via
                    n += 1
            write_q(paths, q)
    return n


def deliver_new(paths: Paths, qid: str) -> list[str]:
    """Try once to post this question's undelivered, never-attempted events to the asking session.
    Returns the lines delivered. A failure is logged once and stays visible in `questions.py pending`."""
    with locked(paths):
        q = read_q(paths, qid)
        todo = [e for e in _ensure_events(q) if not e.get("delivered_at") and not e.get("delivery_error") and not e.get("submitted_at") and not e.get("submission_attempted_at")]
        for event in todo:
            event["submission_attempted_at"] = now_iso()
        if todo:
            write_q(paths, q)
    if not todo:
        return []
    results = {}
    settings = read_json(paths.profile.parent / "settings.json", {})
    for e in todo:
        try:
            Agents(settings).message(event_line(q, e))
            results[e["n"]] = (True, "")
        except Refused as error:
            results[e["n"]] = (False, error.code + ": " + str(error))
    stamp = now_iso()
    with locked(paths):
        q = read_q(paths, qid)
        for e in _ensure_events(q):
            if e["n"] in results and not e.get("delivered_at"):
                ok, err = results[e["n"]]
                if ok:
                    e["submitted_at"], e["submitted_via"] = stamp, "ask"
                    e.pop("delivery_error", None)
                else:
                    e["delivery_error"], e["delivery_attempted_at"] = err[:300], stamp
        write_q(paths, q)
    return [event_line(q, e) for e in q["events"] if e["n"] in results and results[e["n"]][0]]


def clean_note(note) -> str | None:
    """None for an absent or blank comment; the trimmed text otherwise."""
    n = str(note if note is not None else "").strip()
    if not n:
        return None
    if len(n) > MAX_NOTE:
        raise AskError(f"comment longer than {MAX_NOTE} characters")
    return n


def _attach_note(q: dict, note: str, by: str, with_answer: str | None) -> None:
    at = now_iso()
    q["note"], q["note_at"], q["note_by"] = note, at, by
    entry = {"at": at, "by": by, "text": note}
    if with_answer is not None:
        entry["with_answer"] = with_answer
    q.setdefault("notes", []).append(entry)


def comment(paths: Paths, qid: str, note, by: str = "session") -> dict:
    """A comment sent without a structured answer ("Answer with comment").
    Open or dismissed question: resolves it (answered, answer null, applied false, needs_reading true).
    Text kind: the comment is the answer, applied as usual. Already answered: stored as a comment only."""
    n = clean_note(note)
    if n is None:
        raise AskError("empty comment")
    by = str(by or "session")[:40]
    cur = read_q(paths, qid)
    if cur.get("kind") == "text" and cur.get("status") != "answered":
        return answer(paths, qid, n, by=by)        # on a text question the comment is the answer
    with locked(paths):
        q = read_q(paths, qid)                 # builds thread/events from history before the new note lands
        _attach_note(q, n, by, None)
        if q.get("status") != "answered":
            q["status"] = "answered"
            q["answer"] = None
            q["answered_at"] = q["note_at"]
            q["answer_source"] = by
            q["applied"] = False
            q["needs_reading"] = True
            q.pop("apply_error", None)
        _add_event(q, "comment", by, q.get("answer"), n)
        _append_turn(q, _user_turn(q["note_at"], by, None, n))
        write_q(paths, q)
    return q


def validate_form_action(paths, question, text):
    from field_policy import personal_control
    target=question.get("applies_to") or {}
    if (target.get("human_scope") or target.get("human_step") in ("SENSITIVE_FIELD","HUMAN_SIGN_IN","HUMAN_ACCOUNT")) and str(text).strip().casefold() not in ("confirmed","confirm","yes"):
        raise AskError("Never send passwords, codes or legal answers here. Perform the action yourself, then type exactly Confirmed.")
    names=[]
    if target.get("form_facts"):
        try:
            value=json.loads(question.get("context") if text == "Confirmed" and question.get("kind") == "confirm" else text)
            names=list(value) if isinstance(value,dict) else []
        except (ValueError,TypeError):
            pass
    elif target.get("form_question"):
        names=[target["form_question"]]
    elif str(target.get("profile_key", "")).startswith("standard_answers."):
        names=[target["profile_key"].removeprefix("standard_answers.")]
    pid=question.get("posting_id") or target.get("posting_id")
    fill=read_json(paths.applications / pid / "form_fill.json", {}) if pid else {}
    plan=read_json(paths.applications / pid / "plan.json", {}) if pid else {}
    fields={row["name"]:row for row in [*fill.get("rows",[]),*plan.get("rows",[])]}
    if any(personal_control(fields.get(name, {"name":name,"required":True})) for name in names):
        raise AskError("Passwords, codes and legal actions are never collected as answers. Handle the action yourself in the browser; confirm completion only.")


def answer(paths: Paths, qid: str, text, by: str = "session", apply_now: bool = True, note=None) -> dict:
    """Record the answer and (by default) act on it right away. Re-answering replaces the answer and re-applies.
    `note` is an optional comment stored alongside; without one, an earlier comment is kept."""
    n = clean_note(note)
    by = str(by or "session")[:40]
    with locked(paths):
        q = read_q(paths, qid)
        validate_form_action(paths, q, text)
        q["answer"] = normalize_answer(q, text)
        if n is not None:
            _attach_note(q, n, by, q["answer"])
        e = _add_event(q, "answer", by, q["answer"], n)
        _append_turn(q, _user_turn(e["at"], by, q["answer"], n))
        q["answered_at"] = now_iso()
        q["answer_source"] = by
        q["status"] = "answered"
        q["applied"] = None
        q.pop("apply_error", None)
        q.pop("needs_reading", None)
        write_q(paths, q)
        if apply_now:
            q = _apply_locked(paths, q)
    return q


def dismiss(paths: Paths, qid: str, by: str = "session") -> dict:
    with locked(paths):
        q = read_q(paths, qid)
        q["status"] = "dismissed"
        q["dismissed_at"] = now_iso()
        q["dismissed_by"] = str(by or "session")[:40]
        write_q(paths, q)
    return q


# ── apply ────────────────────────────────────────────────────────────

def apply_one(paths: Paths, qid: str) -> dict:
    with locked(paths):
        return _apply_locked(paths, read_q(paths, qid))


def apply_all(paths: Paths) -> list[dict]:
    done = []
    with locked(paths):
        for q in list_q(paths, "answered"):
            if not q.get("applied") and not q.get("needs_reading"):
                done.append(_apply_locked(paths, q))
    return done


def _apply_locked(paths: Paths, q: dict) -> dict:
    """Act on one answered question (caller holds the lock). Never raises for an effect that fails:
    the error lands in q["apply_error"] and `ask apply` retries it next time."""
    if q.get("status") != "answered" or q.get("applied") or q.get("needs_reading"):
        return q          # needs_reading: answered with free text; the agent that asked reads it
    try:
        result = _effect(paths, q)
    except (AskError, OSError, ValueError, KeyError, yaml.YAMLError) as e:
        q["apply_error"] = str(e)
        write_q(paths, q)
        return q
    q.pop("apply_error", None)
    q["applied"] = {"at": now_iso(), "result": result}
    write_q(paths, q)
    return q


def remember_fact(paths, pid, name, value, by, country=None):
    from workflow import Workflow, AUTH_FACT
    root = paths.profile.parent
    if (root / "settings.json").is_file() and (root / "profile.yml").is_file() and (root / "data/postings" / (pid + ".json")).is_file():
        workflow = Workflow(root)
        if AUTH_FACT.search(name) and country != workflow.country(workflow.record(pid)):
            raise AskError("The job country changed. Resume to refresh this Questions thread before answering authorization.")
        workflow.remember_answer(pid, name, value, by)


def _effect(paths: Paths, q: dict) -> str:
    validate_form_action(paths, q, q.get("answer"))
    to = q.get("applies_to") or {"note": True}
    ans = q.get("answer") or ""
    if (to.get("setup_object") or to.get("form_facts") or q.get("proposal_answer") is not None) and q.get("kind") == "confirm" and ans == "Confirmed":
        ans = q.get("context") or ""
    by = q.get("answer_source") or "session"
    if to.get("human_step") == "NAVIGATION_UNCERTAIN":
        if ans != "Confirmed":
            raise AskError("Inspect the transition outcome, then type exactly Confirmed to permit resuming.")
        from workflow import digest
        root = paths.profile.parent
        with workflow_locked(root / ".application", blocking=False):
            path = paths.applications / (q.get("posting_id") or to["posting_id"]) / "steps.json"
            journal = read_json(path, {})
            if digest(journal) != to.get("navigation_digest"):
                raise AskError("The navigation record changed; inspect its current Question.")
            journal.update(moving=None, resolved_by=by, resolved_at=now_iso())
            write_json(path, journal)
        return "navigation outcome inspected personally; resuming permitted"
    if to.get("setup_object") or to.get("setting"):
        root = paths.profile.parent
        with workflow_locked(root):
            if to.get("setup_object"):
                kind = to["setup_object"]
                if kind not in ("profile", "targets"):
                    raise AskError("unknown setup object")
                try:
                    value = json.loads(ans)
                except ValueError as error:
                    raise AskError("Correct the proposal as a JSON object.") from error
                if not isinstance(value, dict) or not value:
                    raise AskError("Supply a nonempty JSON object.")
                from workflow import digest
                target = paths.profile if kind == "profile" else root / "companies.yml"
                current = yaml.safe_load(target.read_text()) if target.exists() else None
                expected = q.get("setup_written_digest", to.get("base_digest"))
                if expected is not None and digest(current) != expected:
                    raise AskError("This data changed after the proposal. The current correction is retained; ask a new correction Question.")
                if current == value:
                    q["setup_written_digest"] = digest(current)
                    return kind + " retained unchanged"
                existing = bool(current) and (kind == "profile" or current.get("companies") or any(current.get(k) for k in current if k != "companies"))
                if existing and not q.get("setup_written_digest") and not to.get("replace"):
                    raise AskError("Existing data is retained. Setup does not overwrite it; use a separate correction Question.")
                if kind == "profile":
                    write_text(paths.profile, yaml.safe_dump(value, sort_keys=False))
                else:
                    if not isinstance(value.get("companies"), list):
                        raise AskError("Targets need a companies list and optional title_filter/location_filter.")
                    write_json(root / "targets.json", value)
                    write_text(root / "companies.yml", yaml.safe_dump(value, sort_keys=False))
                q["setup_written_digest"] = digest(value)
                return kind + " saved"
            key = to["setting"]
            choices = SETTING_OPTIONS
            if key not in choices:
                raise AskError("unknown setup choice")
            value = ans
            if key == "notifications":
                from notifications import configure
                configure(root, value, name=to.get('group_name') or 'Career updates')
                return "WhatsApp notification choice saved; see the app for group status"
            if choices[key] and value not in choices[key]:
                if key != "review_min_words":
                    raise AskError("Choose one of the explained setup options.")
            if key in ('drafter','reviewer'):
                from models import available, choice
                try:
                    value = choice(json.loads(ans))
                    if value['model'] not in [item['model'] for item in available(value['agent'])]:
                        raise AskError('Choose a model reported by the installed CLI.')
                except (ValueError,Refused) as error:
                    raise AskError(str(error)) from error
            if key == "coordinator":
                try:
                    value = json.loads(ans)
                except ValueError as error:
                    raise AskError("Supply coordinator JSON with agent and session.") from error
                if not isinstance(value, dict) or value.get("agent") not in ("codex", "claude") or not value.get("session"):
                    raise AskError("A coordinator needs a capable CLI and an exact session.")
            if key == "review_min_words":
                try:
                    value = int(ans)
                except ValueError as error:
                    raise AskError("Choose a positive word count.") from error
                if value < 1:
                    raise AskError("Choose a positive word count.")
            from models import roles
            settings = roles(read_json(root / "settings.json", {}))
            settings[key] = value
            write_json(root / "settings.json", settings)
            return key + " saved"
    if to.get("profile_key"):
        key = str(to["profile_key"])
        if key.startswith("standard_answers."):
            value = yaml.safe_load(paths.profile.read_text()) or {}
            value.setdefault("standard_answers", {})[key[len("standard_answers."):]] = ans
            _write_text(paths.profile, yaml.safe_dump(value, sort_keys=False))
            return "standard answer corrected"
        only_clear = q.get("kind") == "confirm" and ans == "Confirmed"
        text = paths.profile.read_text(encoding="utf-8")
        new, what = set_profile_value(text, key, None if only_clear else ans,
                                      note=f"answered {q['answered_at'][:10]}")
        if new != text:
            _check_yaml(new, key, None if only_clear else ans)
            _write_text(paths.profile, new)
        return f"profile.yml {key}: {what}"
    if to.get("job_site"):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", ans.strip()):
            raise AskError("Supply an enrolled sign-in site name.")
        pid = q.get("posting_id") or to.get("posting_id")
        def set_site(rec):
            rec["site"] = ans.strip()
            for key in ("submit_approved_at", "submit_approved_by", "submit_digest"):
                rec.setdefault("ruling", {}).pop(key, None)
        _update_record(paths, pid, set_site)
        return "employer identity chosen; resume the application"
    if to.get("job_country"):
        country = ans.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise AskError("Supply a two-letter country code, such as US or DE.")
        pid = q.get("posting_id") or to.get("posting_id")
        def set_country(rec):
            rec["country"] = country
            for key in ("submit_approved_at", "submit_approved_by", "submit_digest"):
                rec.setdefault("ruling", {}).pop(key, None)
        _update_record(paths, pid, set_country)
        from workflow import Workflow, AUTH_FACT
        workflow = Workflow(paths.profile.parent)
        for box in workflow.review(pid)["boxes"]:
            if AUTH_FACT.search(box["name"]) and box.get("state") == "approved" and box.get("approved_by") in PERSON_BY:
                workflow.remember_answer(pid, box["name"], box["value"], box["approved_by"], box)
        return "job country saved; resume to reuse its stored answers"
    if to.get("form_facts"):
        try:
            values = json.loads(ans)
        except ValueError as error:
            raise AskError("Ask your agent to resume this application and replace this grouped question with individual field questions.") from error
        if not isinstance(values, dict) or not values or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in values.items()):
            raise AskError("Each missing fact needs a named, nonempty text answer.")
        for name, value in values.items():
            pid = q.get("posting_id") or to.get("posting_id")
            remember_fact(paths, pid, name, value, by, to.get("country"))
            upsert_form_answer(paths, pid, name, value, q["id"], by)
        return "job facts saved for the working agent"
    if to.get("form_question"):
        pid = to.get("posting_id") or q.get("posting_id")
        body = (q.get("context") or "").strip() if (q.get("kind") == "confirm" and ans == "Confirmed") else ans
        if not body:
            raise AskError("nothing to write: confirmed, but the question has no context to use as the answer")
        rel = upsert_form_answer(paths, pid, str(to["form_question"]), body, q["id"], by)
        remember_fact(paths, pid, str(to["form_question"]), body, by, to.get("country"))
        return f"{rel}: ### {to['form_question']} set"
    if to.get("ruling") == "approve":
        pid = q.get("posting_id") or to.get("posting_id")
        if ans not in ("Yes", "Confirmed"):
            return f"answer {ans}: no ruling change; {pid} stays in the inbox"
        return approve_posting(paths, pid, by, q["id"])
    return "saved with the question; nothing else to write"


def _check_yaml(text: str, key: str, value) -> None:
    """Belt and braces when PyYAML happens to be importable: the edited file parses and holds the value."""
    try:
        import yaml
    except ImportError:
        return
    data = yaml.safe_load(text)
    cur = data
    for part in key.split("."):
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    if isinstance(cur, dict):
        if cur.get("confirm"):
            raise AskError(f"profile edit check failed: {key} still has confirm: true")
        cur = cur.get("value")
    if value is not None and str(cur) != str(value):
        raise AskError(f"profile edit check failed: {key} reads back {cur!r}, wanted {value!r}")


# ── posting records ──────────────────────────────────────────────────

def _record_path(paths: Paths, pid: str | None) -> Path:
    if not pid or not POSTING_RE.match(pid):
        raise AskError(f"bad posting id: {pid!r}")
    p = paths.postings / f"{pid}.json"
    if not p.is_file():
        raise AskError(f"no posting record for {pid!r}", 404)
    return p


def _update_record(paths: Paths, pid: str, fn) -> dict:
    with workflow_locked(paths.profile.parent / ".records"):
        p = _record_path(paths, pid)
        rec = json.loads(p.read_text(encoding="utf-8"))
        changed = fn(rec)
        if changed is not False:
            _write_json(p, rec)
        return rec


def approve_posting(paths: Paths, pid: str, by: str, qid: str) -> str:
    from workflow import Workflow
    workflow = Workflow(paths.profile.parent)
    rec = workflow.record(pid)
    if rec.get("status") == "approved" and rec.get("ruling", {}).get("decision") == "approved":
        return f"{pid} already approved"
    if rec.get("status") not in RULING_FROM:
        return f"{pid} is '{rec.get('status')}', past the ruling; nothing changed"
    workflow.dispatch("pick", pid, decision="approved", by=by)
    return f"{pid} approved"


def _career_rel(paths: Paths, p: Path) -> str:
    """A path under the applications dir → `applications/…` (how materials.* store it, relative to Career/)."""
    return "applications/" + str(p.resolve().relative_to(paths.applications.resolve()))


def _from_career_rel(paths: Paths, rel: str) -> Path | None:
    parts = (rel or "").strip().split("/")
    if not parts or parts[0] != "applications" or ".." in parts or "\\" in rel:
        return None
    return paths.applications / "/".join(parts[1:])


def answers_path(paths: Paths, rec: dict) -> Path:
    m = rec.get("materials") or {}
    if m.get("form_answers_md"):
        p = _from_career_rel(paths, m["form_answers_md"])
        if p:
            return p
    if m.get("application_dir"):
        d = _from_career_rel(paths, str(m["application_dir"]).rstrip("/"))
        if d:
            return d / "form_answers.md"
    # Same folder career.py's app_dir() derives: applications/<company>/<YYYY-MM>_<role>/
    company = rec["id"].split("--")[0]
    month = (rec.get("first_seen") or datetime.now().strftime("%Y-%m-%d"))[:7]
    return paths.applications / company / f"{month}_{slug(rec.get('title', ''), 40)}" / "form_answers.md"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(s or "").lower())).strip()


def upsert_section(text: str, header: str, body: str) -> str:
    """Replace the body under `### <header>` (matched after normalizing), or append the section."""
    lines = text.splitlines()
    want = _norm(header)
    start = next((i for i, ln in enumerate(lines) if ln.startswith("### ") and _norm(ln[4:]) == want), None)
    block = body.strip().splitlines()
    if start is None:
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", f"### {header}"] + block
    else:
        end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("### ")), len(lines))
        tail = lines[end:]
        lines = lines[:start + 1] + block + ([""] if tail else []) + tail
    return "\n".join(lines).rstrip("\n") + "\n"


def upsert_form_answer(paths: Paths, pid: str, question: str, body: str, qid: str, by: str) -> str:
    rec = json.loads(_record_path(paths, pid).read_text(encoding="utf-8"))
    path = answers_path(paths, rec)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        text = (f"# Form answers — {rec.get('title')} at {rec.get('company')}\n\n"
                f"Posting `{pid}`. Each `### ` header is a form question, verbatim; career.py consumes the corresponding Question answer.\n"
                f"Written from the Questions inbox (`tools/questions.py`).\n")
    new = upsert_section(text, question, body)
    if new != text:
        _write_text(path, new)
    rel = _career_rel(paths, path)

    def fn(r):
        m = r.setdefault("materials", {})
        changed = m.get("form_answers_md") != rel or new != text
        m["form_answers_md"] = rel
        if changed:
            r.setdefault("history", []).append(
                {"at": now_iso(), "event": f"form answer \"{question}\" set by {by} (question {qid})"})
        return changed

    _update_record(paths, pid, fn)
    return rel


# ── profile.yml text edit (comments and layout kept) ─────────────────

_KEY_RX = re.compile(r"^(?P<ind>\s*)(?P<dash>-\s+)?(?P<key>[A-Za-z0-9_]+):(?=\s|$)")


def _content(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith("#")


def _key_at(line: str):
    """(effective indent, key, end of `key:`) for a mapping line; a `- key:` list line counts at the dash's indent + 2."""
    m = _KEY_RX.match(line)
    if not m:
        return None
    ind = len(m.group("ind")) + (len(m.group("dash")) if m.group("dash") else 0)
    return ind, m.group("key"), m.end()


def _eff_indent(line: str) -> int:
    k = _key_at(line)
    if k:
        return k[0]
    return len(line) - len(line.lstrip(" "))


def _block_end(lines: list[str], i: int, indent: int, end: int) -> int:
    """First line after i (within end) holding content at an indent <= indent."""
    for j in range(i + 1, end):
        if _content(lines[j]) and _eff_indent(lines[j]) <= indent:
            if lines[j].lstrip().startswith("- ") and (len(lines[j]) - len(lines[j].lstrip(" "))) > indent:
                continue
            return j
    return end


def _locate(lines: list[str], parts: list[str]):
    """Walk dotted parts. Returns ("found", line, child_indent, block_start, block_end) or
    ("missing", parent_start, parent_end, child_indent) for a missing last part."""
    start, end, parent_indent = 0, len(lines), -2
    for n, part in enumerate(parts):
        first = next((i for i in range(start, end) if _content(lines[i])), None)
        if part.isdigit():
            if first is None:
                raise AskError(f"profile.yml: no list at {'.'.join(parts[:n]) or '(top)'}")
            ind = len(lines[first]) - len(lines[first].lstrip(" "))
            items = [i for i in range(start, end) if _content(lines[i]) and lines[i].lstrip().startswith("- ")
                     and len(lines[i]) - len(lines[i].lstrip(" ")) == ind]
            k = int(part)
            if k >= len(items):
                raise AskError(f"profile.yml: {'.'.join(parts[:n + 1])} does not exist")
            start = items[k]
            end = items[k + 1] if k + 1 < len(items) else _block_end(lines, start, ind - 1, end)
            parent_indent = ind
            continue
        child = _eff_indent(lines[first]) if first is not None else parent_indent + 2
        hit = None
        for i in range(start, end):
            if not _content(lines[i]):
                continue
            k = _key_at(lines[i])
            if k and k[0] == child and k[1] == part:
                hit = i
                break
        if hit is None:
            if n == len(parts) - 1:
                return ("missing", start, end, child)
            raise AskError(f"profile.yml: {'.'.join(parts[:n + 1])} does not exist")
        if n == len(parts) - 1:
            return ("found", hit, child, hit + 1, _block_end(lines, hit, child, end))
        start, end, parent_indent = hit + 1, _block_end(lines, hit, child, end), child
    raise AskError("profile.yml: empty key")


def _yaml_scalar(v: str) -> str:
    return json.dumps(str(v), ensure_ascii=False)   # a JSON string is a valid YAML double-quoted scalar


def _scan_scalar(s: str) -> int:
    """Length of the scalar at the start of s (quoted or plain), stopping before a ` #` comment."""
    if s.startswith('"'):
        i = 1
        while i < len(s):
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == '"':
                return i + 1
            i += 1
        return len(s)
    if s.startswith("'"):
        i = 1
        while i < len(s):
            if s[i] == "'":
                if i + 1 < len(s) and s[i + 1] == "'":
                    i += 2
                    continue
                return i + 1
            i += 1
        return len(s)
    m = re.search(r"\s+#", s)
    return (m.start() if m else len(s.rstrip()))


def _scan_flow(s: str) -> int:
    """Length of the `{…}` flow map at the start of s (quotes and nesting respected)."""
    depth, i = 0, 0
    while i < len(s):
        c = s[i]
        if c in "\"'":
            i += _scan_scalar(s[i:])
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise AskError("profile.yml: unterminated flow map")


def _split_flow(inner: str) -> list[tuple[str, str]]:
    entries, depth, buf, i = [], 0, "", 0
    while i < len(inner):
        c = inner[i]
        if c in "\"'":
            n = _scan_scalar(inner[i:])
            buf += inner[i:i + n]
            i += n
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        if c == "," and depth == 0:
            entries.append(buf)
            buf = ""
        else:
            buf += c
        i += 1
    if buf.strip():
        entries.append(buf)
    out = []
    for e in entries:
        k, _, v = e.strip().partition(":")
        out.append((k.strip(), v.strip()))
    return out


def set_profile_value(text: str, key: str, value: str | None, note: str = "answered") -> tuple[str, str]:
    """Set `key` (dotted, list indices allowed) to `value` and drop its `confirm: true`. value None = only drop
    the flag. Edits the lines in place; every comment and every other line is kept byte for byte."""
    # Empty inline parents cannot accept indented children.
    if re.search(r":\s*\{\}\s*(?:#.*)?$", text, re.M):
        data = yaml.safe_load(text) or {}
        node = data
        parts = key.split(".")
        for part in parts[:-1]:
            if not isinstance(node, dict) or part not in node:
                raise AskError("profile.yml: missing intermediate key")
            node = node[part]
        if isinstance(node, dict) and not node and value is not None:
            node[parts[-1]] = value
            return yaml.safe_dump(data, sort_keys=False), "added = " + repr(value)
    lines = text.split("\n")
    loc = _locate(lines, [p for p in key.split(".") if p])
    if loc[0] == "missing":
        if value is None:
            raise AskError(f"profile.yml: {key} does not exist, nothing to confirm")
        _, start, end, child = loc
        last = max((i for i in range(start, end) if _content(lines[i])), default=start - 1)
        leaf = key.split(".")[-1]
        lines.insert(last + 1, f"{' ' * child}{leaf}: {_yaml_scalar(value)}   # {note}")
        return "\n".join(lines), f"added = {value!r}"

    _, i, child, bstart, bend = loc
    line = lines[i]
    _, _, kend = _key_at(line)
    head, rest = line[:kend], line[kend:]
    lead = rest[:len(rest) - len(rest.lstrip())]
    body = rest.lstrip()

    if body.startswith("{"):                                   # flow map {value: …, confirm: true, note: …}
        n = _scan_flow(body)
        entries = _split_flow(body[1:n - 1])
        trailing = body[n:]
        had_confirm = any(k == "confirm" for k, _ in entries)
        entries = [(k, v) for k, v in entries if k != "confirm"]
        if value is not None:
            if any(k == "value" for k, _ in entries):
                entries = [(k, _yaml_scalar(value) if k == "value" else v) for k, v in entries]
            else:
                entries.insert(0, ("value", _yaml_scalar(value)))
        lines[i] = head + lead + "{" + ", ".join(f"{k}: {v}" for k, v in entries) + "}" + trailing
        what = (f"set = {value!r}" if value is not None else "kept value") + ("; confirm cleared" if had_confirm else "")
        return "\n".join(lines), what

    if body and not body.startswith("#"):                      # plain scalar: key: value   # comment
        if value is None:
            return text, "no confirm flag; unchanged"
        n = _scan_scalar(body)
        trailing = body[n:]
        if not trailing.strip():
            trailing = f"   # {note}"
        lines[i] = head + " " + _yaml_scalar(value) + trailing
        return "\n".join(lines), f"set = {value!r}"

    # block map:  key:\n    value: …\n    confirm: true\n    note: …
    kids = {}
    kid_indent = None
    for j in range(bstart, bend):
        if not _content(lines[j]):
            continue
        k = _key_at(lines[j])
        if k is None:
            continue
        if kid_indent is None:
            kid_indent = k[0]
        if k[0] == kid_indent:
            kids.setdefault(k[1], j)
    if kid_indent is None:                                     # `key:` with nothing under it → null
        if value is None:
            return text, "no confirm flag; unchanged"
        lines[i] = head + " " + _yaml_scalar(value) + (f"  {body}" if body else f"   # {note}")
        return "\n".join(lines), f"set = {value!r}"
    what = []
    if value is not None:
        if "value" in kids:
            j = kids["value"]
            _, _, ke = _key_at(lines[j])
            b = lines[j][ke:].lstrip()
            trailing = b[_scan_scalar(b):] if b and not b.startswith("#") else (f"  {b}" if b else "")
            lines[j] = lines[j][:ke] + " " + _yaml_scalar(value) + trailing
        else:
            lines.insert(i + 1, f"{' ' * kid_indent}value: {_yaml_scalar(value)}")
            kids = {k: (v + 1 if v > i else v) for k, v in kids.items()}
        what.append(f"set = {value!r}")
    else:
        what.append("kept value")
    if "confirm" in kids:
        del lines[kids["confirm"]]
        what.append("confirm cleared")
    return "\n".join(lines), "; ".join(what)


# ── WhatsApp ─────────────────────────────────────────────────────────

def notify(text: str, *, root=HOME) -> bool:
    """Use the same local outbox and explicit group as every other career message."""
    from workflow import Workflow
    row = Workflow(root).receipt(text)
    return row['state'] in ('sent', 'disabled', 'awaiting setup choice')


def app_link(qid: str | None = None) -> str:
    """https link that opens the app's Questions tab, at `qid` when given (see the module docstring)."""
    if not qid:
        return APP_LINK
    if not ID_RE.match(qid):
        raise AskError(f"bad question id: {qid!r}")
    return f"{APP_LINK}?q={qid}"


# ── CLI ──────────────────────────────────────────────────────────────

def _cell(s, n=60) -> str:
    s = re.sub(r"\s+", " ", str(s if s is not None else "")).replace("|", "\\|")
    return s if len(s) <= n else s[:n - 1] + "…"


def _paths(args) -> Paths:
    return Paths(questions=Path(args.questions), postings=Path(args.postings), profile=Path(args.profile),
                 applications=Path(args.applications))


def _command_arg(args) -> str | None:
    return Path(args.command_file).read_text(encoding="utf-8") if args.command_file else args.command


def cmd_add(args) -> int:
    ctx = args.context
    if args.context_file:
        ctx = Path(args.context_file).read_text(encoding="utf-8")
    applies = json.loads(args.applies_to) if args.applies_to else None
    opts = [o for o in (args.options or "").split(",")] if args.options else None
    q = add(_paths(args), topic=args.topic, title=args.title, question=args.question, kind=args.kind, options=opts,
            posting_id=args.posting, context=ctx, applies_to=applies, by=args.by, command=_command_arg(args))
    print(q["id"])
    if not args.quiet:
        notify(f"{q['title']} — answer in the app: {app_link(q['id'])}", root=_paths(args).profile.parent)
    return 0


def cmd_list(args) -> int:
    qs = list_q(_paths(args), "open" if args.open else None)
    if args.json:
        print(json.dumps(qs, indent=2, ensure_ascii=False))
        return 0
    print("| id | topic | kind | status | title | answer | note | turns |")
    print("|---|---|---|---|---|---|---|---|")
    for q in qs:
        flag = (" (comment)" if q.get("needs_reading") else " (applied)" if q.get("applied")
                else " (apply failed)" if q.get("apply_error") else "")
        th = q.get("thread") or []
        link = f"{len(th)} ({sum(t['role'] == 'ask' for t in th)} asks)" if len(th) > 1 else ""
        print(f"| {q['id']} | {q.get('topic')} | {q.get('kind')} | {q.get('status')}{flag} | "
              f"{_cell(q.get('title'))} | {_cell(q.get('answer'), 40)} | {_cell(q.get('note'), 30) if q.get('note') else ''} | "
              f"{link} |")
    return 0


def cmd_show(args) -> int:
    q = read_q(_paths(args), args.id)
    if q.get("needs_reading") and q.get("answer") is None:
        # Answered with a comment: put the note where a reader looks for the answer (the stored `answer` stays null).
        out = {}
        for k, v in q.items():
            out[k] = v
            if k == "answer":
                out["answer_from_comment"] = q.get("note")
        q = out
    print(json.dumps(q, indent=2, ensure_ascii=False))
    return 0


def cmd_answer(args) -> int:
    if args.by in PERSON_BY:
        if not sys.stdin.isatty() or input("Type yes to record this personal answer: ") != "yes":
            raise AskError("The person must answer in the app or confirm in a terminal.")
        args.by = "terminal-person"
    q = answer(_paths(args), args.id, " ".join(args.text), by=args.by, apply_now=not args.no_apply, note=args.note)
    print(f"{q['id']}: {q['answer']!r}" + (f"  note: {q['note']!r}" if args.note else ""))
    if q.get("applied"):
        print(f"applied: {q['applied']['result']}")
    if q.get("apply_error"):
        print(f"apply failed: {q['apply_error']}")
        return 1
    return 0


def cmd_comment(args) -> int:
    if args.by in PERSON_BY:
        if not sys.stdin.isatty() or input("Type yes to record this personal answer: ") != "yes":
            raise AskError("The person must answer in the app or confirm in a terminal.")
        args.by = "terminal-person"
    q = comment(_paths(args), args.id, " ".join(args.text), by=args.by)
    state = "answered with comment" if q.get("needs_reading") else q["status"]
    print(f"{q['id']}: note {q.get('note') or q.get('answer')!r} ({state})")
    return 0


def cmd_dismiss(args) -> int:
    q = dismiss(_paths(args), args.id, by=args.by)
    print(f"{q['id']}: dismissed")
    return 0


def cmd_apply(args) -> int:
    p = _paths(args)
    done = [apply_one(p, args.id)] if args.id else apply_all(p)
    if not done:
        print("nothing to apply")
        return 0
    print("| id | result |")
    print("|---|---|")
    bad = 0
    for q in done:
        if q.get("apply_error"):
            bad += 1
        print(f"| {q['id']} | {_cell((q.get('applied') or {}).get('result') or 'FAILED: ' + str(q.get('apply_error')), 110)} |")
    return 1 if bad else 0


def cmd_ping(args) -> int:
    n = len(list_q(_paths(args), "open"))
    text = f"{n} question{'s' if n != 1 else ''} waiting in the app: {app_link()}"
    print(text)
    return 0 if (n == 0 or notify(text, root=_paths(args).profile.parent)) else 1


def cmd_pending(args) -> int:
    session = None if args.all else (args.session or session_from_env())
    items = pending(_paths(args), session)
    if not items:
        print("nothing pending" + (f" for {session}" if session else ""))
        return 0
    for q, e in items:
        print(event_line(q, e) + (f"  [{e['at']}]" if e.get("at") else ""))
    if args.mark:
        print(f"marked {mark_delivered(_paths(args), items)} delivered")
    return 0


def cmd_reply(args) -> int:
    ctx = Path(args.context_file).read_text(encoding="utf-8") if args.context_file else args.context
    q = reply(_paths(args), args.id, text=args.text, kind=args.kind,
              options=args.options.split(",") if args.options else None, context=ctx,
              applies_to=json.loads(args.applies_to) if args.applies_to else None, title=args.title, by=args.by,
              command=_command_arg(args))
    print(f"{q['id']}: turn {len(q['thread'])} asked, status open")
    return 0


def cmd_merge(args) -> int:
    q = merge(_paths(args), args.src, args.into)
    print(f"{args.src} merged into {q['id']}: {len(q['thread'])} turns, status {q['status']}")
    return 0


def cmd_migrate(args) -> int:
    print(f"threads written: {migrate(_paths(args))}")
    return 0


def cmd_link(args) -> int:
    if args.id:
        q_path(_paths(args), args.id)          # raises on an unknown id: an error, not a dead link
    print(app_link(args.id))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Questions inbox for the career plugin.")
    ap.add_argument("--questions", default=str(QUESTIONS), help=argparse.SUPPRESS)
    ap.add_argument("--postings", default=str(POSTINGS), help=argparse.SUPPRESS)
    ap.add_argument("--profile", default=str(PROFILE), help=argparse.SUPPRESS)
    ap.add_argument("--applications", default=str(APPLICATIONS), help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add", help="ask the user something")
    s.add_argument("--topic", required=True, choices=TOPICS)
    s.add_argument("--title", required=True, help=f"≤ {MAX_TITLE} characters")
    s.add_argument("--question", required=True, help=f"plain text, ≤ {MAX_QUESTION} characters")
    s.add_argument("--kind", default="text", choices=KINDS)
    s.add_argument("--options", help="comma-separated, for --kind choice")
    s.add_argument("--posting", help="posting id the question belongs to")
    s.add_argument("--context", help="longer text or a link shown under the question")
    s.add_argument("--context-file", help="read the context from a file")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--command", help="one shell command for the user to run; the apps show it with a Copy button")
    g.add_argument("--command-file", help="read the command from a file")
    s.add_argument("--applies-to", help='JSON pointer the career agent acts on, e.g. {"profile_key":"answers.how_heard"}')
    s.add_argument("--by", default="session", help='"agent:<name>" or "session"')
    s.add_argument("--quiet", action="store_true", help="save without a /text notification")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("list", help="markdown table of questions")
    s.add_argument("--open", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("show")
    s.add_argument("id")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("answer", help="propose an answer; personal answers require app or terminal confirmation")
    s.add_argument("id")
    s.add_argument("text", nargs="+")
    s.add_argument("--by", default="session")
    s.add_argument("--no-apply", action="store_true", help="record only; `ask apply` acts on it later")
    s.add_argument("--note", help="a comment stored with the answer")
    s.set_defaults(fn=cmd_answer)

    s = sub.add_parser("comment", help="answer with a comment (resolves an open question; needs_reading)")
    s.add_argument("id")
    s.add_argument("text", nargs="+")
    s.add_argument("--by", default="session")
    s.set_defaults(fn=cmd_comment)

    s = sub.add_parser("dismiss")
    s.add_argument("id")
    s.add_argument("--by", default="session")
    s.set_defaults(fn=cmd_dismiss)

    s = sub.add_parser("apply", help="act on answered questions not yet applied (idempotent)")
    s.add_argument("id", nargs="?")
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("ping", help='send "N questions waiting in the app: <link>" through /text')
    s.set_defaults(fn=cmd_ping)

    s = sub.add_parser("reply", help="re-ask in the same thread (appends an ask turn, reopens the question)")
    s.add_argument("id")
    s.add_argument("--text", required=True, help=f"the new ask, ≤ {MAX_QUESTION} characters")
    s.add_argument("--kind", choices=KINDS, help="default: the current kind")
    s.add_argument("--options", help="comma-separated, for --kind choice")
    s.add_argument("--context", help="text shown under this ask")
    s.add_argument("--context-file", help="read the context from a file")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--command", help="one shell command for this ask, shown with a Copy button")
    g.add_argument("--command-file", help="read the command from a file")
    s.add_argument("--applies-to", help="JSON; default: the current applies_to")
    s.add_argument("--title", help="replace the thread's title")
    s.add_argument("--by", default="session")
    s.set_defaults(fn=cmd_reply)

    s = sub.add_parser("pending", help="answers/comments not yet delivered to the asking session")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--session", help="coordinator session id (default: CAREER_COORDINATOR_SESSION, else all)")
    g.add_argument("--all", action="store_true", help="every session")
    s.add_argument("--mark", action="store_true", help="stamp the listed events delivered")
    s.set_defaults(fn=cmd_pending)

    s = sub.add_parser("link", help="print the https link that opens the app at a question (or the inbox)")
    s.add_argument("id", nargs="?")
    s.set_defaults(fn=cmd_link)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (AskError, Refused) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
