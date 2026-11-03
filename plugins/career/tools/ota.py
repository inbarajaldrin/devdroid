#!/usr/bin/env python3
"""Publish workspace-owned iPhone builds and token-scoped install pages."""

from __future__ import annotations

import argparse
import html
import json
import os
import secrets
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from runtime import HOME, Refused, read_json, write_json
from urllib.parse import urlsplit

OTA_DIR = HOME / "data" / "ota"
TOKEN_PATH = HOME / ".private" / "ota_token"
FUNNEL_BASE = os.environ.get("CAREER_FUNNEL_BASE", "")
PREFIX = "/app/install"
IPA_NAME = "Career.ipa"
TITLE = "Career"


def load_or_create_token(path: Path = TOKEN_PATH) -> str:
    try:
        t = path.read_text().strip()
        if t:
            return t
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(24)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t + "\n")
    return t


def info(ota_dir: Path = OTA_DIR) -> dict | None:
    try:
        d = json.loads((ota_dir / "info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and (ota_dir / IPA_NAME).is_file() else None


def base_url(token: str, base: str = FUNNEL_BASE) -> str:
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise Refused("OTA_HTTPS", "Set ota_base to a reachable HTTPS origin in settings.json.")
    return f"{base.rstrip('/')}{PREFIX}/{token}/"


def manifest(inf: dict, token: str, base: str = FUNNEL_BASE) -> bytes:
    url = base_url(token, base)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>items</key>
  <array>
    <dict>
      <key>assets</key>
      <array>
        <dict>
          <key>kind</key><string>software-package</string>
          <key>url</key><string>{escape(url + IPA_NAME)}</string>
        </dict>
      </array>
      <key>metadata</key>
      <dict>
        <key>bundle-identifier</key><string>{escape(str(inf.get("bundle_id")))}</string>
        <key>bundle-version</key><string>{escape(str(inf.get("version")))}</string>
        <key>kind</key><string>software</string>
        <key>title</key><string>{escape(TITLE)}</string>
      </dict>
    </dict>
  </array>
</dict>
</plist>
""".encode("utf-8")


def install_link(token: str, base: str = FUNNEL_BASE) -> str:
    return "itms-services://?action=download-manifest&url=" + base_url(token, base) + "manifest.plist"


def page(inf: dict | None, token: str, base: str = FUNNEL_BASE) -> bytes:
    if not inf:
        body = "<h1>Career</h1><p>No build published yet. Your agent runs <code>app/ota.sh</code>.</p>"
    else:
        when = html.escape(str(inf.get("built_at") or ""))
        body = f"""<h1>Career</h1>
<p class="v">Version {html.escape(str(inf.get("version")))} (build {html.escape(str(inf.get("build")))}), built {when}</p>
<p><a class="btn" href="{html.escape(install_link(token, base))}">Install</a></p>
<ol>
  <li>Open this page in <b>Safari</b> on the iPhone and tap Install, then Install again in the prompt.</li>
  <li>The app installs over the one already there; it keeps its pairing.</li>
  <li>If iOS says it cannot install: Settings, Privacy &amp; Security, Developer Mode must stay on (it already is,
      since cable installs work).</li>
</ol>"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer"><title>Install Career</title>
<style>body{{font:16px/1.5 -apple-system,system-ui,sans-serif;margin:0;padding:32px 20px;max-width:560px;margin:auto;
color:#1d1d1f;background:#f2f2f7}}h1{{font-size:24px;margin:0 0 4px}}.v{{color:#6e6e73;margin:0 0 24px}}
.btn{{display:inline-block;background:#0a66d8;color:#fff;text-decoration:none;font-weight:600;padding:12px 28px;
border-radius:12px;font-size:18px}}ol{{color:#3a3a3c;padding-left:20px}}li{{margin:8px 0}}
@media (prefers-color-scheme:dark){{body{{background:#000;color:#f5f5f7}}ol{{color:#c7c7cc}}}}</style>
</head><body>{body}</body></html>""".encode("utf-8")


def publish(ipa: Path, version: str, build: str, bundle_id: str, ota_dir: Path = OTA_DIR) -> dict:
    if not all(isinstance(v, str) and v.strip() for v in (version, build, bundle_id)):
        raise ValueError("version, build and bundle_id are required")
    ota_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = ota_dir / (IPA_NAME + ".tmp")
    shutil.copyfile(ipa, tmp)
    os.replace(tmp, ota_dir / IPA_NAME)
    inf = {"version": version, "build": build, "bundle_id": bundle_id,
           "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
           "size": (ota_dir / IPA_NAME).stat().st_size}
    write_json(ota_dir / "info.json", inf)
    return inf


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Over-the-air installs of the Career iPhone app.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("publish")
    s.add_argument("ipa", type=Path)
    s.add_argument("--version", required=True)
    s.add_argument("--build", required=True)
    s.add_argument("--bundle-id", required=True)
    sub.add_parser("url")
    args = ap.parse_args(argv)
    token = load_or_create_token()
    if args.cmd == "publish":
        inf = publish(args.ipa, args.version, args.build, args.bundle_id)
        print(f"published {inf['version']} ({inf['build']}), {inf['size'] // 1024} KB")
    print(base_url(token, read_json(HOME / "settings.json", {}).get("ota_base") or FUNNEL_BASE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
