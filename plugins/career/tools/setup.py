"""Prepare an owned workspace and inactive service templates. Never activates services."""
import argparse
import json
import os
import plistlib
import shlex
import shutil
import sys
from pathlib import Path

from runtime import CODE, write_json, write_text


def prepare(root, platform=sys.platform):
    root = Path(root).expanduser().resolve()
    if root == CODE or CODE in root.parents or root in CODE.parents:
        raise ValueError("Choose your own workspace outside the installed plugin.")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The person owns these sources and can iterate on both clients with their agent.
    for name in ("tools", "scripts", "skills"):
        if not (root / name).exists():
            shutil.copytree(CODE / name, root / name,
                ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', 'node_modules', '.venv'))
    for name in ("app", "web"):
        if not (root / name).exists():
            shutil.copytree(CODE / "skills/jobs/examples" / name, root / name)
    for name in ("requirements.txt", "requirements-dev.txt", "dependencies.json", "LICENSE", "THIRD_PARTY_NOTICES.md", "SETUP-FOR-AGENTS.md"):
        if not (root / name).exists():
            shutil.copy2(CODE / name, root / name)
    for name in ("postings", "questions", "applications"):
        (root / "data" / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / ".private").mkdir(exist_ok=True, mode=0o700)
    if not (root / "settings.json").exists():
        write_json(root / "settings.json", {"drafter": None, "reviewer": None, "review_min_words": 25,
            "resume_mode": "one resume", "live_browser_writes": "Disabled",
            "scheduled_scans": "Enabled", "application_assistance": "Enabled",
            "notifications": None, "recipes": "Enabled", "remote_access": "Local only"})
    from models import roles
    from runtime import read_json
    current = read_json(root / 'settings.json',{})
    if 'agent' in current:
        write_json(root / 'settings.json',roles(current))
    if not (root / "companies.yml").exists():
        write_text(root / "companies.yml", "companies: []\ntitle_filter: {}\nlocation_filter: {}\n")
    if not (root / "data/pipeline.md").exists():
        write_text(root / "data/pipeline.md", "# Pipeline\n\n## Pending\n\n## Processed\n")
    if not (root / ".gitignore").exists():
        write_text(root / ".gitignore", ".private/\nsettings.json\nprofile.yml\nresume*\ndata/\nrecipes/\n.setup/\n.application/\n.schedule/\n.events/\n.answers/\n.notifications/\n.venv/\nnode_modules/\n")
    services = root / ".private/services"
    services.mkdir(parents=True, exist_ok=True)
    python = str(root / ".venv/bin/python")
    env = {"CAREER_HOME": str(root), "CAREER_PYTHON": python,
           "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
    for name in ("SIGN_IN_SCRIPT", "TEXT_SCRIPT", "ASK_SCRIPT", "APPLE_DOCS_SKILL", "CAREER_APP_LINK", "CAREER_FUNNEL_BASE"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    if platform == "darwin":
        for name, command, interval in (
            ("server", [python, str(root / "tools/serve.py"), "--bind", "127.0.0.1", "--link-bind", "off"], None),
            ("scan", [python, str(root / "tools/schedule.py")], 3600),
            ("phone-deploy", [python, str(root / "tools/phone_deploy.py")], 180),
        ):
            content = {"Label": "dev.career." + name, "ProgramArguments": command,
                       "WorkingDirectory": str(root), "EnvironmentVariables": env, "RunAtLoad": True,
                       "StandardErrorPath": str(root / ".private" / (name + ".log")),
                       "StandardOutPath": str(root / ".private" / (name + ".log"))}
            if interval:
                if name == "scan":
                    content["StartCalendarInterval"] = {"Minute": 0}
                else:
                    content["StartInterval"] = interval
            else:
                content["KeepAlive"] = True
            target = services / ("dev.career." + name + ".plist")
            if not target.exists():
                with target.open("xb") as stream:
                    plistlib.dump(content, stream)
    elif platform.startswith("linux"):
        def unit_arg(value):
            if any(char in value for char in '\n\r\0'):
                raise ValueError("Service paths must be single-line strings.")
            return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
        env_lines = "\n".join("Environment=" + unit_arg(k + "=" + v) for k, v in env.items())
        for name, argv, kind in (
            ("server", [python, str(root / "tools/serve.py"), "--bind", "127.0.0.1", "--link-bind", "off"], "simple"),
            ("scan", [python, str(root / "tools/schedule.py")], "oneshot"),
        ):
            target = services / ("career-" + name + ".service")
            if target.exists():
                continue
            write_text(target,
                f"[Unit]\nDescription=Career {name}\n[Service]\nType={kind}\n{env_lines}\n"
                f"WorkingDirectory={unit_arg(str(root))}\nExecStart={' '.join(map(unit_arg, argv))}\n"
                + ("Restart=on-failure\n[Install]\nWantedBy=default.target\n" if kind == "simple" else ""))
        if not (services / "career-scan.timer").exists():
            write_text(services / "career-scan.timer", "[Unit]\nDescription=Career scan catch-up\n[Timer]\n"
                   "OnCalendar=hourly\nPersistent=true\nUnit=career-scan.service\n[Install]\nWantedBy=timers.target\n")
    else:
        raise ValueError("Use a Mac or Linux workspace.")
    return {"workspace": str(root), "services": str(services), "activated": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--marketplace", help="Registered catalog containing the required plugins")
    args = parser.parse_args()
    from dependencies import install
    install(marketplace=args.marketplace)
    print(json.dumps(prepare(args.workspace), indent=2))
