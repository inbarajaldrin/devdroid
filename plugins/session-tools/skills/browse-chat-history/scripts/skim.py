# Compact skim of a Claude Code JSONL range.
# Usage: python3 skim.py <session.jsonl> <start_line> <end_line> [width=400]
# Prints one line per event: [line] timestamp ROLE/TOOL/RESULT/QUEUE: text truncated to width. Pipe to grep.
from __future__ import annotations

import json, sys
path, start, end, width = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]) if len(sys.argv) > 4 else 400
with open(path) as f:
    for i, line in enumerate(f):
        if i < start: continue
        if i > end: break
        try: o = json.loads(line)
        except Exception: continue
        ts = o.get("timestamp", "")[:19]
        m = o.get("message") or {}
        role = o.get("type")
        content = m.get("content") if isinstance(m, dict) else None
        if o.get("type") == "queue-operation":
            print(f"[{i}] {ts} QUEUE {str(o.get('content',''))[:width]}"); continue
        if isinstance(content, str):
            print(f"[{i}] {ts} {role}: {content[:width]!r}"); continue
        if not isinstance(content, list): continue
        for b in content:
            t = b.get("type")
            if t == "text": print(f"[{i}] {ts} {role} TEXT: {b['text'][:width]!r}")
            elif t == "tool_use":
                inp = b.get("input", {})
                s = inp.get("command") or inp.get("message") or inp.get("file_path") or json.dumps(inp)
                print(f"[{i}] {ts} TOOL {b['name']}: {str(s)[:width]!r}")
            elif t == "tool_result":
                c = b.get("content")
                if isinstance(c, list): c = " ".join(x.get("text","") for x in c if isinstance(x, dict))
                print(f"[{i}] {ts} RESULT: {str(c)[:width]!r}")
