"""Saved human decisions. This module never grants access or changes system state."""
import fcntl
import json
import os
from pathlib import Path
import tempfile

FEATURES = {
    "mac_permission": ("Mac Screen Recording: you grant access in System Settings; recommend granting it to the invoking terminal before capture.", ("person_grants", "unavailable", "ask")),
    "linux_packages": ("Linux packages: you install X11 tools, ImageMagick and ffmpeg; recommend installing them before capture.", ("person_installs", "unavailable", "ask")),
    "private_display": ("Private Linux display: recommend an owned Xvfb/Xephyr display to keep capture away from your desktop.", ("prefer", "require", "shared", "ask")),
    "stage_manager": ("Stage Manager shrinks a background window: recommend bringing that app forward for about a second, capturing it full size and returning to your app; no setting changes.", ("bring-forward", "wait", "report", "ask")),
}


def cache_dir():
    return Path(os.environ.get("CAPTURE_WINDOW_HOME", Path.home() / ".devdroid" / "capture-window"))


def settings_path():
    return Path(os.environ.get("CAPTURE_WINDOW_SETTINGS", cache_dir() / "settings.json"))


def load_settings():
    path = settings_path()
    data = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(data, dict) or any(k not in FEATURES or v not in FEATURES[k][1] for k, v in data.items()):
        raise ValueError("Invalid saved setup answers; correct them with setup --set feature=choice.")
    return data


def save_answers(answers):
    for key, value in answers.items():
        if key not in FEATURES or value not in FEATURES[key][1]:
            raise ValueError("Unknown setup answer; use setup --show to see the choices.")
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = dict(load_settings(), **answers)
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as temp:
            staged = Path(temp.name)
            try:
                json.dump(data, temp)
                temp.flush()
                os.fsync(temp.fileno())
                os.replace(staged, path)
            finally:
                staged.unlink(missing_ok=True)
    return data


def questions(saved):
    return [dict(feature=key, question=line, choices=choices) for key, (line, choices) in FEATURES.items() if key not in saved]
