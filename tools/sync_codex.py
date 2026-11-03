#!/usr/bin/env python3
"""Generate the Codex marketplace and plugin manifests from the Claude ones.

Codex installs a whole plugin folder, so each plugin's scripts, engine and tools come with its skills.
Run after changing .claude-plugin/marketplace.json or a plugin.json; --check exits 1 when a file is stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATEGORY = {"development": "Developer Tools", "documentation": "Developer Tools"}


def short(description: str) -> str:
    """The first sentence; past Codex's 100 characters, the part before a colon or the last whole clause."""
    first = description.split(". ")[0].split(" — ")[0].rstrip(".")
    if len(first) <= 100:
        return first
    if ":" in first[:100]:
        return first.split(":")[0]
    return first[:100].rsplit(", ", 1)[0]


def generated() -> dict[Path, str]:
    catalog = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    marketplace = {"name": catalog["name"], "interface": {"displayName": catalog["name"]}, "plugins": []}
    files = {}
    for entry in catalog["plugins"]:
        folder = ROOT / entry["source"]
        manifest = json.loads((folder / ".claude-plugin/plugin.json").read_text())
        manifest.pop("$schema", None)
        category = CATEGORY.get(entry.get("category"), "Productivity")
        manifest["skills"] = "./skills/"
        manifest["interface"] = {
            "displayName": entry["name"],
            "shortDescription": short(manifest["description"]),
            "developerName": manifest.get("author", {}).get("name", ""),
            "category": category,
        }
        files[folder / ".codex-plugin/plugin.json"] = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        marketplace["plugins"].append({
            "name": entry["name"],
            "source": {"source": "local", "path": entry["source"]},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"},
            "category": category,
        })
    files[ROOT / ".agents/plugins/marketplace.json"] = json.dumps(marketplace, indent=2, ensure_ascii=False) + "\n"
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if a generated file is missing or stale")
    stale = []
    args = parser.parse_args()
    for path, text in generated().items():
        if path.exists() and path.read_text() == text:
            continue
        stale.append(path.relative_to(ROOT))
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    for path in stale:
        print(("stale: " if args.check else "wrote: ") + str(path))
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    sys.exit(main())
