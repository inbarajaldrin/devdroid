"""Audit distributable bytes. Optional private deny terms stay outside the plugin."""
import argparse
import json
import re
from pathlib import Path

SKIP = {"node_modules", "__pycache__", ".pytest_cache", ".venv", ".git", ".scratchpad"}
PATTERNS = {
    "unpublished_lineage": re.compile(rb"\b\w*ha" + rb"rness\w*\b", re.I),
    "personal_home": re.compile(rb"/(?:Users|home)/[A-Za-z][A-Za-z0-9_-]+/"),
    "mesh_address": re.compile(rb"\b100\.(?:6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.\d{1,3}\.\d{1,3}\b"),
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "cloud_key": re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
    "api_secret": re.compile(rb"\b(?:sk-proj-|ghp_)[A-Za-z0-9_-]{20,}"),
    "apple_team": re.compile(rb"(?:DEVELOPMENT_TEAM|APPLE_TEAM_ID)\s*[=:]\s*[A-Z0-9]{10}\b"),
}


def audit(root, terms=()):
    hits = []
    count = 0
    for path in sorted(Path(root).rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in SKIP for part in relative.parts):
            continue
        count += 1
        data = str(relative).encode() + b"\n" + path.read_bytes()
        for name, pattern in PATTERNS.items():
            if pattern.search(data):
                hits.append({"file": str(relative), "rule": name})
        for index, term in enumerate(terms):
            if str(term).encode().lower() in data.lower():
                hits.append({"file": str(relative), "rule": "private-deny-term", "term_index": index})
        if path.name in ("profile.yml", "api_token", "accounts.json", "settings.json", "targets.json", "companies.yml", "answers.json", "chat.json", "notifications.json", "events.json", "active.json", "schedule.json", "TRACKER.md") or path.suffix in (".p12", ".mobileprovision"):
            hits.append({"file": str(relative), "rule": "private-runtime-file"})
        if relative.parts[:2] in (("data", "postings"), ("data", "questions"), ("data", "applications")) and path.suffix != ".md":
            hits.append({"file": str(relative), "rule": "runtime-record"})
    return {"ok": not hits, "files": count, "private_terms": len(terms), "hits": hits}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--private-terms", type=Path)
    args = parser.parse_args()
    terms = json.loads(args.private_terms.read_text()) if args.private_terms else []
    result = audit(args.root, terms)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
