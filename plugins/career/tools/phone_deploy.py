#!/usr/bin/env python3
"""Install the newest published build when the configured phone is reachable and unlocked."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ota
from integrations import Text
from runtime import HOME, Refused, locked, read_json, write_json

DEVICE = None
STATE_NAME = "deploy_state.json"
RETRY_MINUTES = 30
QUERY_TIMEOUT, INSTALL_TIMEOUT = 25, 180


def as_build(v) -> int | None:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def devicectl(args: list[str], timeout: int = QUERY_TIMEOUT) -> tuple[dict | None, str]:
    """Run `xcrun devicectl … --json-output`; (the JSON's `result`, "") or (None, why)."""
    with tempfile.TemporaryDirectory(prefix="phone-deploy") as d:
        out = Path(d) / "out.json"
        cmd = ["xcrun", "devicectl", *args, "--quiet", "--timeout", str(timeout), "--json-output", str(out)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, str(e)
        try:
            doc = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        if r.returncode != 0 or (doc.get("info") or {}).get("outcome") != "success":
            return None, ((r.stderr or r.stdout).strip()[:300] or f"exit {r.returncode}")
        return doc.get("result") or {}, ""


def reachable(device: str | None = None) -> bool:
    """The Mac can talk to it: listing only reads CoreDevice's own table, so this is instant and never wakes the phone."""
    if not device:
        return False
    res, _ = devicectl(["list", "devices"])
    for d in (res or {}).get("devices") or []:
        if d.get("identifier") == device:
            return (d.get("connectionProperties") or {}).get("tunnelState") in ("connected", "disconnected")
    return False


def unlocked(device: str = DEVICE) -> bool:
    res, _ = devicectl(["device", "info", "lockState", "--device", device])
    return res is not None and res.get("passcodeRequired") is False


def installed_build(bundle_id: str, device: str = DEVICE) -> tuple[bool, int | None]:
    """(asked ok, its build); the app missing from the phone reads (True, None)."""
    res, _ = devicectl(["device", "info", "apps", "--device", device, "--bundle-id", bundle_id])
    if res is None:
        return False, None
    apps = [a for a in res.get("apps") or [] if a.get("bundleIdentifier") == bundle_id]
    return True, as_build(apps[0].get("bundleVersion")) if apps else None


def unpack(ipa: Path, dest: Path) -> Path | None:
    """The .app inside the .ipa: devicectl installs an app bundle. ditto keeps the modes and links its signature needs."""
    r = subprocess.run(["/usr/bin/ditto", "-x", "-k", str(ipa), str(dest)], capture_output=True, text=True, check=False)
    apps = sorted((dest / "Payload").glob("*.app"))
    return apps[0] if r.returncode == 0 and apps else None


def install(ipa: Path, device: str = DEVICE) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory(prefix="phone-deploy-app") as d:
        app = unpack(ipa, Path(d))
        if app is None:
            return False, f"no .app inside {ipa.name}"
        res, why = devicectl(["device", "install", "app", "--device", device, str(app)], timeout=INSTALL_TIMEOUT)
    return res is not None, why


def read_state(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def in_backoff(state: dict, build: int, now: datetime) -> bool:
    """A failed install of this very build is left alone for RETRY_MINUTES."""
    if state.get("failed_build") != build:
        return False
    try:
        at = datetime.fromisoformat(str(state.get("failed_at")).replace("Z", "+00:00"))
    except ValueError:
        return False
    if at.tzinfo is None:
        return False
    return now - at < timedelta(minutes=RETRY_MINUTES)


@dataclass
class Outcome:
    action: str                    # idle | installed | failed | would-install
    why: str


def run_once(ota_dir: Path = ota.OTA_DIR, device: str | None = None, dry_run: bool = False,
             now: datetime | None = None, *, text=None, settings=None) -> Outcome:
    """One pass. Order goes from cheapest to dearest, so an absent phone costs one local table read."""
    now = now or datetime.now(timezone.utc)
    inf = ota.info(ota_dir)
    build = as_build((inf or {}).get("build"))
    if inf is None or build is None:
        return Outcome("idle", "nothing published")
    settings = settings if settings is not None else read_json(HOME / "settings.json", {})
    device = device or settings.get("phone_device")
    if not device:
        return Outcome("failed", "Save phone_device in workspace settings.json")
    if not reachable(device):
        return Outcome("idle", "phone not reachable")
    if not unlocked(device):
        return Outcome("idle", "phone locked")
    asked, have = installed_build(str(inf.get("bundle_id")), device)
    if not asked:
        return Outcome("idle", "phone stopped answering")
    if have is not None and have >= build:
        return Outcome("idle", f"phone already has build {have}")
    state_path = ota_dir / STATE_NAME
    state = read_state(state_path)
    if in_backoff(state, build, now):
        return Outcome("idle", f"build {build} failed recently; waiting")
    if dry_run:
        return Outcome("would-install", f"build {have} -> {build}")
    ok, why = install(ota_dir / ota.IPA_NAME, device)
    stamp = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if not ok:
        first = state.get("failed_build") != build
        write_json(state_path, {"failed_build": build, "failed_at": stamp})
        return Outcome("failed", f"build {build}: {why}" + ("" if first else " (again)"))
    write_json(state_path, {"installed_build": build, "installed_at": stamp})
    notice = {"message": f"App updated to build {build}", "state": "pending", "at":stamp}
    write_json(ota_dir / "notification.json", notice)
    try:
        if settings.get("notifications") != "Enabled":
            notice["state"] = "disabled"
            write_json(ota_dir / "notification.json",notice)
            return Outcome("installed",f"build {build}")
        notice["receipt"] = (text or Text()).send(notice["message"], group=Text.group_id((settings.get("notification_group") or {}).get("id")))
        notice["state"] = "sent"
    except Refused as error:
        notice["error"] = str(error)
    write_json(ota_dir / "notification.json", notice)
    return Outcome("installed", f"build {build}" + ("" if notice["state"] == "sent" else " (notification pending; inspect data/ota/notification.json)"))


def readiness(root=HOME, settings=None, *, run=subprocess.run):
    """Local probes only: never contacts a phone or deploys a build."""
    root = Path(root)
    settings = settings if settings is not None else read_json(root / "settings.json", {})
    rows = []
    for name in ("xcodegen", "xcrun"):
        import shutil
        rows.append(dict(name=name, ok=shutil.which(name) is not None, detail=f"Install {name} on the Mac build host"))
    # Device and signing inputs are private workspace settings, not shipped identities.
    for name in ("apple_team_id", "bundle_id_prefix", "phone_device"):
        rows.append(dict(name=name, ok=bool(settings.get(name)), detail=f"Save {name} in workspace settings.json"))
    try:
        result = run(["xcodebuild", "-version"], capture_output=True, text=True, timeout=10)
        ok = result.returncode == 0 and "Xcode" in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    rows.append(dict(name="Xcode", ok=ok, detail="Select an installed Xcode with the iOS SDK"))
    try:
        result = run(["security", "find-identity", "-v", "-p", "codesigning"], capture_output=True, text=True, timeout=10)
        ok = result.returncode == 0 and bool(settings.get("apple_team_id")) and settings["apple_team_id"] in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    rows.append(dict(name="signing identity", ok=ok, detail="Configure this Apple team in Xcode; register and pair the phone yourself"))
    inf = ota.info(root / "data/ota")
    rows.append(dict(name="published app", ok=bool(inf) and inf.get("bundle_id") == str(settings.get("bundle_id_prefix", "")) + ".careerapp",
                     detail="Run app/ota.sh in your workspace after signing setup; no build or deployment runs during check"))
    try:
        ota.base_url("readiness", settings.get("ota_base") or ota.FUNNEL_BASE)
        ok = True
    except Refused:
        ok = False
    rows.append(dict(name="install HTTPS", ok=ok, detail="Save ota_base as the reachable HTTPS origin serving /app/install/"))
    return [dict(check=row.pop("name"), **row) for row in rows]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Install the newest published build on the iPhone when it is reachable.")
    ap.add_argument("--once", action="store_true", help="one pass that says what it decided (launchd omits it: silent when idle)")
    ap.add_argument("--dry-run", action="store_true", help="decide, install nothing")
    args = ap.parse_args(argv)
    with locked(HOME / ".phone-deploy", blocking=False):
        out = run_once(dry_run=args.dry_run)
    if args.once or out.action != "idle":
        when = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        print(f"[{when}] {out.action}: {out.why}", flush=True)
    return 1 if out.action == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
