"""Private workspace paths and atomic local persistence."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

CODE = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("CAREER_HOME", Path.home() / ".devdroid/career")).expanduser().resolve()
PERSON_BY = ("phone", "web", "terminal-person")

# Validation for saved settings, shared by chat and Questions; no setup interview.
SETTING_OPTIONS = {
    "resume_mode": ("one resume", "tailored per application"),
    "review_min_words": None,
    "account": None, "bridge_site": None, "coordinator": None,
    "resume": None, "resume_pdf": None, "drafter": None, "reviewer": None,
    "scheduled_scans": ("Enabled", "Disabled"),
    "notifications": ("Enabled", "Disabled"),
    "recipes": ("Enabled", "Disabled"),
    "application_assistance": ("Enabled", "Disabled"),
    "live_browser_writes": ("Enabled", "Disabled"),
    "remote_access": ("Local only", "Private mesh", "Public HTTPS"),
    "apple_team_id": None, "bundle_id_prefix": None, "phone_device": None,
    "ota_base": None,
}

class Refused(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".career-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def write_json(path, data):
    write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def slug(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,200}", value):
        raise Refused("BAD_ID", "Use a posting or question id from the inbox.")
    return value


@contextlib.contextmanager
def locked(root, *, blocking=True):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".workflow.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as error:
            raise Refused("BUSY", "An application operation is running; retry after it finishes.") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def company_url(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise Refused("COMPANY_SITE_REQUIRED", "Choose an HTTPS application on a company career site.")
    return host
