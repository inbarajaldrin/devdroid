#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Recompute `urgency` on every posting record from its own dates. Safe to run nightly.

    uv run urgency.py --dry-run              # print the table, write nothing
    uv run urgency.py                        # write `urgency` where it changed
    uv run urgency.py --today 2026-10-20     # pin "today" (default: today's UTC date)
    uv run urgency.py --dir ../data/postings # another postings directory

Inputs per record (read only): posting.posted_on, posting.deadline (ISO dates or null) and
posting.deadline_note. Output: the top-level `urgency` block. Nothing else in the file changes and
`history` is not touched, so nightly runs do not grow it. A record is rewritten only when level, by,
days_left or reason differ from what is stored, so two runs on the same day write nothing the second
time. Files keep their exact formatting (2-space JSON, UTF-8, trailing newline as found).

Levels (SCHEMA.md), with the two cases the table leaves open made explicit:
  now      deadline 0-7 days away, or a stated date has passed while deadline_note says the req is
           still live ("at least until", "req still live", "stated minimum")
  week     deadline 8-21 days away
  soon     deadline 22-45 days away, or no usable deadline and posted >= 30 days ago
  rolling  no usable deadline and posted < 30 days ago
  none     no deadline and no posted date
  * deadline more than 45 days away: not urgent by date, so the posted-age rule decides
    (soon / rolling); with no posted date it is `rolling`. `by`/`days_left` still carry the deadline.
  * deadline passed and nothing says the req is still live: `none`, reason "deadline <d> passed".
  * deadline_note says "likely stale" (a flagged stale posting): `none`, so it stays out of
    Act soon until the user rules; `by`/`days_left` still carry any deadline.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path

LEVELS = ("now", "week", "soon", "rolling", "none")
SOFT = re.compile(r"at least until|req still live|stated minimum", re.I)
STALE = re.compile(r"likely stale", re.I)
from runtime import HOME

DEFAULT_DIR = HOME / "data" / "postings"


def iso(s: str | None) -> dt.date | None:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def compute(posting: dict, today: dt.date) -> dict:
    posted, deadline = iso(posting.get("posted_on")), iso(posting.get("deadline"))
    note = posting.get("deadline_note") or ""
    by = days_left = None
    far = ""
    if STALE.search(note):
        if deadline:
            by, days_left = deadline.isoformat(), (deadline - today).days
        return dict(level="none", by=by, days_left=days_left, reason="likely stale; the person decides")
    if deadline:
        by, days_left = deadline.isoformat(), (deadline - today).days
        if days_left < 0:
            if SOFT.search(note):
                return dict(level="now", by=by, days_left=days_left, reason="stated date passed, req still live")
            return dict(level="none", by=by, days_left=days_left, reason=f"deadline {by} passed")
        if days_left <= 7:
            return dict(level="now", by=by, days_left=days_left, reason=f"deadline {by}")
        if days_left <= 21:
            return dict(level="week", by=by, days_left=days_left, reason=f"deadline {by}")
        if days_left <= 45:
            return dict(level="soon", by=by, days_left=days_left, reason=f"deadline {by}")
        far = f"; deadline {by} ({days_left} days)"
    if posted:
        age = (today - posted).days
        level = "soon" if age >= 30 else "rolling"
        return dict(level=level, by=by, days_left=days_left, reason=f"rolling since {posted.isoformat()} ({age} day{'' if age == 1 else 's'}){far}")
    if deadline:
        return dict(level="rolling", by=by, days_left=days_left, reason=f"posted date unknown{far}")
    return dict(level="none", by=None, days_left=None, reason="no deadline, no posted date known")


def with_urgency(rec: dict, urg: dict) -> dict:
    """Return rec with `urgency` set in place, or inserted after company_info (schema order)."""
    if "urgency" in rec:
        rec["urgency"] = urg
        return rec
    out, placed = {}, False
    anchor = "company_info" if "company_info" in rec else None
    for k, v in rec.items():
        if not placed and anchor is None and k in ("materials", "ruling", "history"):
            out["urgency"], placed = urg, True
        out[k] = v
        if k == anchor:
            out["urgency"], placed = urg, True
    if not placed:
        out["urgency"] = urg
    return out


def write_json(path: Path, rec: dict, trailing_nl: bool) -> None:
    text = json.dumps(rec, indent=2, ensure_ascii=False) + ("\n" if trailing_nl else "")
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--today", type=dt.date.fromisoformat, default=dt.datetime.now(dt.timezone.utc).date())
    ap.add_argument("--dry-run", action="store_true", help="print the table, write nothing")
    a = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, written = [], 0
    for path in sorted(a.dir.glob("*.json")):
        raw = path.read_text(encoding="utf-8")
        rec = json.loads(raw)
        new = compute(rec.get("posting") or {}, a.today)
        old = rec.get("urgency") or {}
        changed = any(old.get(k) != new[k] for k in ("level", "by", "days_left", "reason"))
        rows.append((LEVELS.index(new["level"]), new["by"] or "9999", rec.get("company", ""), rec.get("title", ""),
                     new, changed))
        if changed and not a.dry_run:
            write_json(path, with_urgency(rec, {**new, "computed_at": now}), raw.endswith("\n"))
            written += 1

    print(f"today {a.today.isoformat()} · {len(rows)} records · "
          + (f"{sum(r[5] for r in rows)} would change (dry run)" if a.dry_run else f"{written} written"))
    print()
    print("| level | by | days_left | company | title | reason | changed |")
    print("|---|---|---|---|---|---|---|")
    for _, _, company, title, u, changed in sorted(rows, key=lambda r: r[:3]):
        dl = "" if u["days_left"] is None else u["days_left"]
        print(f"| {u['level']} | {u['by'] or ''} | {dl} | {company} | {title.replace('|', '/')} | {u['reason']} | {'yes' if changed else ''} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
