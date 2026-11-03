#!/usr/bin/env python3
"""index — query what the career agent already knows about postings.

One read-only entry point over data/postings/*.json, data/pipeline.md and data/scan-history.tsv,
so an agent (or the user) never has to grep. Markdown tables out; --json for machines.

  .venv/bin/python tools/index.py list [--status S] [--fit F] [--urgency U] [--company C] [--term T]
  .venv/bin/python tools/index.py search <words…>        # title/company/summary/description, AND of words
  .venv/bin/python tools/index.py show <id-or-url>        # one record, the parts that matter
  .venv/bin/python tools/index.py exists <url> [<url>…]   # seen anywhere? (records, pipeline, scan history)
  .venv/bin/python tools/index.py companies               # per-company counts by status/fit/urgency
  .venv/bin/python tools/index.py pending                 # pipeline.md rows with no record yet


Statuses: new enriched approved skipped tailored ready applied interview rejected offer closed variant
Fit: direct adjacent off-target.   Urgency: now week soon rolling none.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from runtime import HOME

WORKSPACE = HOME
POSTINGS = WORKSPACE / "data" / "postings"
PIPELINE = WORKSPACE / "data" / "pipeline.md"
HISTORY = WORKSPACE / "data" / "scan-history.tsv"
TRACKER = WORKSPACE / "data" / "applications" / "TRACKER.md"

URL_RE = re.compile(r"https?://[^\s|)>\]]+")


def norm_url(u: str) -> str:
    u = (u or "").strip().rstrip("/").lower()
    u = re.sub(r"[?#].*$", "", u) if "greenhouse" not in u and "gh_jid" not in u else u
    return u


def load_records() -> list[dict]:
    out = []
    for p in sorted(POSTINGS.glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception as e:  # noqa: BLE001
            print(f"skip {p.name}: {e}", file=sys.stderr)
            continue
        d["_file"] = p.name
        out.append(d)
    return out


def pipeline_rows() -> list[dict]:
    rows = []
    if not PIPELINE.exists():
        return rows
    for line in PIPELINE.read_text().splitlines():
        m = re.match(r"- \[( |x)\] (\S+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \| (\S+)(.*)$", line.strip())
        if not m:
            continue
        done, url, company, title, loc, date, rest = m.groups()
        rows.append({"url": url, "company": company.strip(), "title": title.strip(), "location": loc.strip(),
                     "date": date, "done": done == "x", "domain": "| domain" in rest,
                     "variant_of": (re.search(r"variant-of (\S+)", rest) or [None, None])[1] if "variant-of" in rest else None,
                     "source": "tier2" if "tier2" in rest else ("manual" if "manual" in rest else
                               "scan")})
    return rows


def history_urls() -> set[str]:
    s = set()
    if HISTORY.exists():
        for line in HISTORY.read_text().splitlines()[1:]:
            u = line.split("\t")[0]
            if u:
                s.add(norm_url(u))
    return s


def tracker_urls() -> set[str]:
    if not TRACKER.exists():
        return set()
    return {norm_url(u) for u in URL_RE.findall(TRACKER.read_text())}


# ── views ────────────────────────────────────────────────────────────

def g(d: dict, *path, default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur or cur[k] is None:
            return default
        cur = cur[k]
    return cur


def row_of(d: dict) -> dict:
    return {
        "id": d.get("id"), "kind": d.get("kind") or "job", "company": d.get("company"), "title": d.get("title"),
        "status": d.get("status"), "fit": g(d, "summary_ai", "fit", default="—"),
        "urgency": g(d, "urgency", "level", default="—"), "by": g(d, "urgency", "by", default="—"),
        "term": g(d, "posting", "term", default="—"), "location": d.get("location", "—"),
        "work_auth": (g(d, "posting", "work_authorization") or "—")[:60],
        "url": d.get("url"),
    }


def table(rows: list[dict], cols: list[str]) -> str:
    if not rows:
        return "(none)"
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols)
    body = "\n".join("| " + " | ".join(str(r.get(c, "—") if r.get(c) not in (None, "") else "—").replace("|", "/") for c in cols) + " |" for r in rows)
    return head + "\n" + body


URG_ORDER = {"now": 0, "week": 1, "soon": 2, "rolling": 3, "none": 4, "—": 5}
FIT_ORDER = {"direct": 0, "adjacent": 1, "off-target": 2, "—": 3}


def cmd_list(a):
    recs = load_records()
    rows = [row_of(d) for d in recs]
    if not a.all:
        rows = [r for r in rows if r["status"] != "variant"]
    for key, val in (("status", a.status), ("fit", a.fit), ("urgency", a.urgency)):
        if val:
            rows = [r for r in rows if r[key] == val]
    if a.company:
        rows = [r for r in rows if a.company.lower() in (r["company"] or "").lower()]
    if a.term:
        rows = [r for r in rows if a.term.lower() in (r["term"] or "").lower()]
    rows.sort(key=lambda r: (URG_ORDER.get(r["urgency"], 9), FIT_ORDER.get(r["fit"], 9), r["company"], r["title"]))
    emit(rows, ["company", "title", "status", "fit", "urgency", "by", "term", "location", "id"], a)


def cmd_search(a):
    words = [w.lower() for w in a.words]
    rows = []
    for d in load_records():
        hay = " ".join(str(x) for x in [d.get("company"), d.get("title"), g(d, "summary_ai", "text"),
                                        g(d, "posting", "description"), " ".join(g(d, "summary_ai", "flags", default=[]))]).lower()
        if all(w in hay for w in words):
            rows.append(row_of(d))
    rows.sort(key=lambda r: (FIT_ORDER.get(r["fit"], 9), URG_ORDER.get(r["urgency"], 9)))
    emit(rows, ["company", "title", "status", "fit", "urgency", "id"], a)


def cmd_show(a):
    key = a.key.strip()
    for d in load_records():
        if d.get("id") == key or norm_url(d.get("url", "")) == norm_url(key) or d["_file"] == key:
            if a.json:
                print(json.dumps(d, indent=2, ensure_ascii=False))
                return
            print(f"# {d.get('company')} — {d.get('title')}\n")
            print(f"- id: `{d.get('id')}`  status: **{d.get('status')}**  fit: **{g(d,'summary_ai','fit')}**  urgency: **{g(d,'urgency','level')}** ({g(d,'urgency','reason')})")
            print(f"- url: {d.get('url')}\n- location: {d.get('location')}  term: {g(d,'posting','term')}  posted: {g(d,'posting','posted_on')}  deadline: {g(d,'posting','deadline')}")
            print(f"- work authorization: {g(d,'posting','work_authorization')}")
            print(f"- ruling: {g(d,'ruling','decision')} {g(d,'ruling','reason') or ''}  materials: " + ", ".join(k for k, v in (d.get('materials') or {}).items() if v) )
            if d.get("variants"):
                print("- also posted as: " + ", ".join(v.get("label", v.get("id")) for v in d["variants"]))
            print(f"\n**{g(d,'summary_ai','label')}**\n\n{g(d,'summary_ai','text')}\n")
            print("Flags: " + "; ".join(g(d, "summary_ai", "flags", default=[])))
            print("\nHistory: " + " → ".join(h.get("event", "") for h in d.get("history", [])))
            return
    print(f"not found: {key}", file=sys.stderr)
    sys.exit(1)


def cmd_exists(a):
    recs = {norm_url(d.get("url", "")): d for d in load_records()}
    pipe = {norm_url(r["url"]): r for r in pipeline_rows()}
    hist = history_urls()
    trk = tracker_urls()
    rows = []
    for u in a.urls:
        n = norm_url(u)
        where = []
        if n in recs:
            where.append(f"record {recs[n]['id']} ({recs[n].get('status')})")
        if n in pipe:
            where.append("pipeline" + (" (processed)" if pipe[n]["done"] else " (pending)"))
        if n in hist:
            where.append("scan-history")
        if n in trk:
            where.append("TRACKER")
        rows.append({"url": u, "seen": "yes" if where else "no", "where": "; ".join(where) or "—"})
    emit(rows, ["url", "seen", "where"], a)
    sys.exit(0 if all(r["seen"] == "yes" for r in rows) else 1)


def cmd_companies(a):
    by = defaultdict(lambda: {"records": 0, "status": Counter(), "fit": Counter(), "urgency": Counter()})
    for d in load_records():
        if d.get("status") == "variant":
            continue
        c = by[d.get("company")]
        c["records"] += 1
        c["status"][d.get("status")] += 1
        c["fit"][g(d, "summary_ai", "fit", default="—")] += 1
        c["urgency"][g(d, "urgency", "level", default="—")] += 1
    rows = [{"company": k, "records": v["records"],
             "status": ", ".join(f"{s}:{n}" for s, n in v["status"].most_common()),
             "fit": ", ".join(f"{s}:{n}" for s, n in v["fit"].most_common()),
             "urgency": ", ".join(f"{s}:{n}" for s, n in v["urgency"].most_common())}
            for k, v in sorted(by.items(), key=lambda kv: -kv[1]["records"])]
    emit(rows, ["company", "records", "status", "fit", "urgency"], a)



def cmd_pending(a):
    have = {norm_url(d.get("url", "")) for d in load_records()}
    rows = [r for r in pipeline_rows() if not r["done"] and norm_url(r["url"]) not in have]
    emit(rows, ["company", "title", "location", "date", "source", "url"], a)


def emit(rows, cols, a):
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
    else:
        print(table(rows, cols))
        print(f"\n{len(rows)} row(s)")


def main():
    p = argparse.ArgumentParser(prog="index", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("--status"); s.add_argument("--fit"); s.add_argument("--urgency"); s.add_argument("--company"); s.add_argument("--term"); s.add_argument("--all", action="store_true", help="include variant records"); s.set_defaults(fn=cmd_list)
    s = sub.add_parser("search"); s.add_argument("words", nargs="+"); s.set_defaults(fn=cmd_search)
    s = sub.add_parser("show"); s.add_argument("key"); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("exists"); s.add_argument("urls", nargs="+"); s.set_defaults(fn=cmd_exists)
    s = sub.add_parser("companies"); s.set_defaults(fn=cmd_companies)
    s = sub.add_parser("pending"); s.set_defaults(fn=cmd_pending)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
