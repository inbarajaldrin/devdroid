"""Hourly wake checks; company scans catch up after sleep."""
import os
import subprocess
import sys
from datetime import datetime, timezone

from runtime import HOME, Refused, locked, now, read_json, write_json


def tick(root=HOME, run=subprocess.run, instant=None):
    instant = instant or datetime.now(timezone.utc)
    with locked(root / ".schedule", blocking=False):
        path = root / "data/schedule.json"
        state = read_json(path, {})
        settings = read_json(root / "settings.json", {})
        if settings.get("scheduled_scans") != "Enabled":
            return state
        failures = []
        for name, interval, command in (
            ("jobs", 86400, ["node", str(root / "tools/scan.mjs")]),
        ):
            if state.get(name, {}).get("failed_at"):
                interval = 3600  # Retry on the next scheduled wake, rather than losing a day.
            last = state.get(name, {}).get("attempted_at") or state.get(name, {}).get("completed_at")
            if last and (instant - datetime.fromisoformat(last)).total_seconds() < interval:
                continue
            try:
                result = run(command, cwd=root, env=dict(os.environ, CAREER_HOME=str(root), CAREER_PYTHON=sys.executable), check=False, timeout=300)
                failed = bool(result.returncode)
            except subprocess.TimeoutExpired:
                failed = True
            if failed:
                state[name] = dict(state.get(name, {}), failed_at=instant.isoformat(), attempted_at=instant.isoformat())
                write_json(path, state)
                failures.append(name)
                continue
            state[name] = {"completed_at": instant.isoformat(), "attempted_at": instant.isoformat()}
            write_json(path, state)
        if failures:
            raise Refused("SCAN_FAILED", "Scans failed: " + ", ".join(failures) + "; completion was not advanced.")
        return state


if __name__ == "__main__":
    tick()
