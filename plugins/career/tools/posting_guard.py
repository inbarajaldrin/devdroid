#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
# Reference: https://github.com/MadsLorentzen/ai-job-search/blob/d621ebe63549dffb291edea7f7621cd00e67ddb1/tools/posting_guard.py
#   (MIT). The guard itself is vendored unmodified at tools/vendor/ai_job_search/tools/posting_guard.py; this file is
#   the career agent wrapper: same CLI for one text, plus a pass over data/postings/*.json that writes `posting.guard`.
"""posting_guard — prompt-injection guard for fetched posting text (ai-job-search's integrity gate, vendored).

Strips hidden HTML and invisible characters and flags what looks aimed at an agent rather than a reader:
instruction-shaped lines ("ignore previous …", "you are an AI assistant", "run the following command"),
zero-width characters inside words, bidi overrides, Latin/Cyrillic look-alike words. It advises; it never blocks.

    uv run posting_guard.py --in FILE [--html|--text] [--json]     # one text (the upstream CLI, unchanged)
    uv run posting_guard.py records [--write] [--id ID …]          # every record's posting.description

Enrichment contract (SCHEMA.md): enrichers run the guard on the fetched text before writing summary_ai and
store the result in `posting.guard` = {checked_at, verdict, flags[], removed{…}}. `flags` holds only the
targeting signals; an empty list means clean. From code: `from posting_guard import check; check(text)`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from runtime import HOME  # noqa: E402  the person's workspace, never the installed plugin

POSTINGS = Path(os.environ.get("WORKSPACE_POSTINGS_DIR") or HOME / "data" / "postings")
_spec = importlib.util.spec_from_file_location(
    "ai_job_search_posting_guard", HERE / "vendor" / "ai_job_search" / "tools" / "posting_guard.py")
upstream = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(upstream)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def check(text: str, mode: str = "auto") -> dict:
    """The `posting.guard` value for one text: verdict, the targeting flags (empty = clean), what was removed."""
    _, f = upstream.guard(text or "", mode)
    flags = ["External text asks the agent to disregard earlier instructions"] if re.search(r"\bdisregard\s+(?:the\s+)?(?:earlier|previous|prior)\s+(?:guidance|instructions)\b", text, re.I) else []
    flags += [f"instruction-like line: {line}" for line in f["instruction_like"]]
    if f["invisible_flagged"]:
        flags.append(f"invisible characters inside words or bidi overrides: {f['invisible_flagged']}")
    flags += [f"mixed-script word: {w}" for w in f["homoglyph_runs"]]
    return {"checked_at": now_iso(), "verdict": f["verdict"].removeprefix("posting_guard: "), "flags": flags,
            "removed": {"hidden_blocks": len(f["hidden_blocks"]), "hidden_urls": f["hidden_urls"],
                        "invisible_chars": f["invisible_chars"]}}


def write_guard(path: Path, guard: dict) -> None:
    """Merge `posting.guard` into the record (re-read right before writing; other keys untouched)."""
    rec = json.loads(path.read_text())
    rec.setdefault("posting", {})["guard"] = guard
    rec.setdefault("history", []).append(
        {"at": guard["checked_at"], "event": f"posting_guard: {guard['verdict']}"})
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def cell(s) -> str:
    return str(s if s not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")[:110]


def cmd_records(args) -> int:
    paths = sorted(POSTINGS.glob("*.json"))
    if args.id:
        paths = [p for p in paths if p.stem in set(args.id)]
    print("| record | verdict | flags |")
    print("|---|---|---|")
    n_flagged = n_written = 0
    for p in paths:
        rec = json.loads(p.read_text())
        desc = (rec.get("posting") or {}).get("description")
        if not desc:
            print(f"| {p.stem} | no posting.description — skipped | — |")
            continue
        g = check(desc)
        n_flagged += bool(g["flags"])
        if g["flags"] or args.all:
            print(f"| {p.stem} | {cell(g['verdict'])} | {cell('; '.join(g['flags']))} |")
        if args.write:
            write_guard(p, g)
            n_written += 1
    print(f"\n{len(paths)} records, {n_flagged} flagged" + (f", posting.guard written to {n_written}" if args.write else
                                                           " (dry run — add --write to store posting.guard)"))
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != "records":
        return upstream.main(argv)                 # one text: the upstream CLI
    ap = argparse.ArgumentParser(prog="posting_guard.py records")
    ap.add_argument("--write", action="store_true", help="merge posting.guard into each record")
    ap.add_argument("--id", action="append", help="only this record id (repeatable)")
    ap.add_argument("--all", action="store_true", help="list clean records too")
    return cmd_records(ap.parse_args(argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
