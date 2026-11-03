"""Check working capabilities. No interview receipts; never installs or deploys anything."""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from integrations import script
from runtime import CODE, HOME, Refused, read_json


def read_yaml(path):
    if importlib.util.find_spec("yaml") is None:
        raise ValueError("Install requirements.txt to read YAML workspace files.")
    import yaml
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        raise ValueError(str(error)) from error


def inspect(root=HOME, *, offline=False, run=None):
    root = Path(root).expanduser().resolve()
    run = run or subprocess.run
    rows = []
    def check(name, ok, detail, **extra):
        rows.append(dict(check=name, ok=bool(ok), detail=detail, **extra))
    def command(argv, **kwargs):
        return run(argv, capture_output=True, text=True, timeout=kwargs.pop("timeout",30), **kwargs)
    try:
        settings = read_json(root / "settings.json", {})
        if not isinstance(settings, dict):
            raise ValueError("settings.json must be an object")
    except (OSError, ValueError) as error:
        check("settings", False, str(error))
        settings = {}
    installed = (CODE / ".claude-plugin/plugin.json").is_file()
    check("workspace", root.is_dir() and not (root / ".claude-plugin/plugin.json").is_file()
          and not (installed and CODE in root.parents),
          "Use an owned workspace outside the installed plugin: " + str(root))
    check("python", sys.version_info >= (3, 11), "Python 3.11+")
    for name in ("yaml", "qrcode", "PIL"):
        check("python:" + name, importlib.util.find_spec(name) is not None, "Install requirements.txt")
    try:
        version = command(["node", "--version"]).stdout.strip()
        node_ok = int(version.lstrip("v").split(".")[0]) >= 22
    except (OSError, ValueError, subprocess.TimeoutExpired):
        node_ok = False
    check("node", node_ok, "Node 22+; npm ci --prefix tools")
    for env, relative, fallback in (
        ("SIGN_IN_SCRIPT", "scripts/sign-in.mjs", ".devdroid/browser/runtime/scripts/sign-in.mjs"),
        ("ASK_SCRIPT", "skills/ask/scripts/ask.py", ".claude/skills/ask/scripts/ask.py"),
    ):
        try:
            path = script(env, relative, fallback)
            check(env, True, path)
        except Refused as error:
            check(env, False, str(error))
    if settings.get('notifications') != 'Enabled':
        check('WhatsApp notifications', True, 'Optional; off. Everything remains in the app.')
    else:
        try:
            from integrations import Text
            group = Text.group_id((settings.get('notification_group') or {}).get('id'))
            entry = script('TEXT_SCRIPT','scripts/text.cjs','.devdroid/text/runtime/scripts/text.cjs')
            if offline:
                check('WhatsApp group',False,'Group reachability is unverified offline',unverified=True)
            else:
                Text(runner=lambda argv,**kwargs: command(argv,**kwargs).stdout).check_group(group)
                check('WhatsApp group',True,'Dedicated career group is reachable and contains the owner accounts')
        except (Refused,OSError,ValueError,subprocess.TimeoutExpired) as error:
            check('WhatsApp group',False,str(error))
    from models import roles, choice, available
    role_settings = roles(settings)
    catalogs = {}
    for role in ('drafter','reviewer'):
        try:
            selected = choice(role_settings.get(role))
            provider = selected['agent']
            if not shutil.which(provider):
                raise Refused('AGENT_MISSING','Install '+provider)
            if offline and provider == 'codex':
                check(role,False,'Codex model availability is unverified offline',unverified=True)
                continue
            if provider not in catalogs: catalogs[provider] = available(provider,run=run)
            check(role,selected['model'] in [item['model'] for item in catalogs[provider]],
                  provider+' / '+selected['model']+' must be listed by the installed CLI')
        except (Refused,OSError,subprocess.TimeoutExpired) as error:
            check(role,False,str(error))
    check("coordinator", (settings.get("coordinator") or {}).get("session"),
          "Save the coordinating /ask session in settings.json")
    for name in ("resume", "resume_pdf"):
        ok = bool(settings.get(name)) and Path(settings[name]).expanduser().is_file()
        if name == "resume_pdf" and settings.get("resume_mode") == "tailored per application":
            ok = True
        check(name, ok, "Save a readable local file path; tailored mode selects a PDF per application")
    try:
        profile = read_yaml(root / "profile.yml")
        check("profile", isinstance(profile, dict) and bool(profile), "Save the profile agreed in chat")
    except (OSError, ValueError) as error:
        check("profile", False, str(error))
    try:
        if offline:
            check("stock OpenCLI", False, "Unverified offline; run career check with /sign-in installed", unverified=True)
            check("sign-in context", False, "Unverified offline; no browser was contacted", unverified=True)
        else:
            entry = script("SIGN_IN_SCRIPT", "scripts/sign-in.mjs", ".devdroid/browser/runtime/scripts/sign-in.mjs")
            result = command(["node", str(CODE / "scripts/browser-runtime.mjs"), entry, "--local-only"])
            proof = json.loads(result.stdout)
            check("stock OpenCLI", result.returncode == 0 and proof.get("ok") is True and proof.get("data", {}).get("version") == "2.0.2",
                  "Exact stock binary pinned by /sign-in, checked without starting a daemon")
            account, site = settings.get("account"), settings.get("bridge_site")
            if not account or not site:
                raise ValueError("Save account and bridge_site from /sign-in")
            result = command(["node", entry, "check", account, "--site", site])
            envelope = json.loads(result.stdout)
            proof = envelope.get("data", {})
            ok = (result.returncode == 0 and envelope.get("ok") is True and
                  proof.get("account") == account and proof.get("site") == site and
                  proof.get("status") == "signed-in" and isinstance(proof.get("contextId"), str) and bool(proof["contextId"].strip()))
            check("sign-in context", ok, "Must be signed in on the chosen account/site with a verified context")
    except (Refused, OSError, ValueError, subprocess.TimeoutExpired) as error:
        check("sign-in context", False, str(error))
    try:
        targets = read_yaml(root / "companies.yml")
        if not isinstance(targets, dict) or not isinstance(targets.get("companies"), list):
            raise ValueError("companies.yml needs a companies list")
        if not any(isinstance(c, dict) and c.get("enabled") is not False for c in targets["companies"]):
            raise ValueError("No target companies saved yet")
        if offline:
            check("scanner runs", False, "Unverified offline; no public feed requests made", unverified=True)
        else:
            result = command(["node", str(CODE / "tools/scan.mjs"), "--dry-run"],
                             cwd=root, env=dict(os.environ, CAREER_HOME=str(root)))
            check("scanner runs", result.returncode == 0, result.stdout.strip() or result.stderr.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        check("scanner runs", False, str(error))
    from phone_deploy import readiness
    for row in readiness(root, settings, run=run):
        check(row["check"], row["ok"], row["detail"])
    return {"ok": all(row["ok"] for row in rows), "checks": rows,
            "live_verified": not offline and any(row["check"] == "sign-in context" and row["ok"] for row in rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    result = inspect(offline=args.offline)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
