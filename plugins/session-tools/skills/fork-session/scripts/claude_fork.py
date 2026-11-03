#!/usr/bin/env python3
"""Fork or repair a Claude transcript, preserving only the chosen parent chain.

Collision and broken-parent repairs follow the idea in
https://gist.github.com/tennox/90ef5c803ec4b64c9fbba0f71ca1ae2e.
The source transcript and sidecars are never opened for writing.
"""

import argparse
import copy
import json
import re
import shlex
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path


CLAUDE_DIR = Path.home() / ".claude"
BACKUP_ROOT = Path.home() / ".devdroid/fork-session/backups"
REASK_REJECT_MSG = (
    "The user doesn't want to proceed with this tool use. The tool use was rejected "
    "(eg. if it was a file edit, the new_string was NOT written to the file). STOP what "
    "you are doing and wait for the user to tell you how to proceed."
)


def read_records(path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def source_path(source, project_dir=None):
    if project_dir:
        path = Path(project_dir).expanduser() / (Path(source).stem + ".jsonl")
    elif source.endswith(".jsonl"):
        path = Path(source).expanduser().resolve()
    else:
        matches = list((CLAUDE_DIR / "projects").glob("*/" + source + ".jsonl"))
        if len(matches) != 1:
            raise ValueError("source must match exactly one transcript; use its full path")
        path = matches[0]
    if not path.is_file():
        raise ValueError("source transcript not found: {}".format(path))
    return path


def sidecars(path, session_id):
    yield "projects_subdir", path.parent / session_id
    for kind in ("session-env", "tasks", "file-history"):
        yield kind, CLAUDE_DIR / kind / session_id


def backup_original(path, label):
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^a-zA-Z0-9_-]", "-", label)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup = Path(tempfile.mkdtemp(prefix=stamp + "-" + safe_label + "-", dir=str(BACKUP_ROOT)))
    backup.chmod(0o700)
    shutil.copy2(str(path), str(backup / path.name))
    for kind, src in sidecars(path, path.stem):
        if src.exists():
            shutil.copytree(str(src), str(backup / "sidecars" / kind / path.stem))
    return backup


def content(record):
    message = record.get("message") or {}
    blocks = message.get("content") or []
    return [block for block in blocks if isinstance(block, dict)]


def find_form(records, tool_id=None):
    found = None
    for record in records:
        for block in content(record):
            if block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
                if tool_id is None or block.get("id") == tool_id:
                    found = (record["uuid"], block["id"])
    if found is None:
        raise ValueError("no matching AskUserQuestion form found")
    return found


def select_chain(records, cut_uuid=None, line=None):
    """Repair within the prefix, then walk backwards from the exact chosen UUID.

    Filtering by ancestry (rather than physical line order alone) excludes siblings
    from both active and abandoned arms. Never extend a cut into a later tool result.
    """
    if line is not None and not 1 <= line <= len(records):
        raise ValueError("--truncate-at must be between 1 and {}".format(len(records)))
    end = line or len(records)
    if cut_uuid:
        indices = [i for i, record in enumerate(records) if record.get("uuid") == cut_uuid]
        if len(indices) != 1:
            raise ValueError("message UUID must occur exactly once: " + cut_uuid)
        end = indices[0] + 1
    prefix = copy.deepcopy(records[:end])
    ids = {record["uuid"] for record in prefix if record.get("uuid")}
    collision_fixes = parent_fixes = 0
    previous = None
    for i, record in enumerate(prefix):
        if (record.get("type") == "file-history-snapshot" and record.get("messageId")
                and i + 1 < len(prefix) and record["messageId"] == prefix[i + 1].get("uuid")):
            record["messageId"] = None
            collision_fixes += 1
        parent = record.get("parentUuid")
        if parent and parent not in ids:
            if previous is None:
                raise ValueError("missing root parent: " + parent)
            record["parentUuid"] = previous
            parent_fixes += 1
        if record.get("uuid"):
            previous = record["uuid"]
    by_id = {record["uuid"]: record for record in prefix if record.get("uuid")}
    leaf = cut_uuid or previous
    if not leaf:
        raise ValueError("no message UUID at or before the selected boundary")
    keep = set()
    current = leaf
    while current:
        if current in keep:
            raise ValueError("cycle in parentUuid chain at " + current)
        if current not in by_id:
            raise ValueError("broken parentUuid chain at " + current)
        keep.add(current)
        current = by_id[current].get("parentUuid")
    selected = [record for record in prefix if record.get("uuid") in keep
                or (not record.get("uuid") and
                    (not record.get("parentUuid") or record.get("parentUuid") in keep))]
    return selected, leaf, {"collision_fixes": collision_fixes, "parent_fixes": parent_fixes,
                            "chain_steps": len(keep), "chain_reached_root": True}


def pending_tools(records):
    pending = {}
    for record in records:
        for block in content(record):
            if block.get("type") == "tool_use":
                pending[block["id"]] = block
            elif block.get("type") == "tool_result":
                tool_id = block.get("tool_use_id")
                if tool_id not in pending:
                    raise ValueError("orphan tool_result: " + str(tool_id))
                del pending[tool_id]
    return pending


def splice_reask(records, leaf, pending):
    schema = {"isSidechain": False, "userType": "external"}
    for record in reversed(records):
        if record.get("sessionId"):
            for key in ("sessionId", "cwd", "version", "gitBranch", "userType", "isSidechain"):
                if record.get(key) is not None:
                    schema[key] = record[key]
            break
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result_id, interrupt_id = str(uuid.uuid4()), str(uuid.uuid4())
    records.append(dict(schema, type="user", parentUuid=leaf, uuid=result_id, timestamp=timestamp,
                        message={"role": "user", "content": [
                            {"type": "tool_result", "tool_use_id": tool_id, "is_error": True,
                             "content": REASK_REJECT_MSG} for tool_id in pending]}))
    records.append(dict(schema, type="user", parentUuid=result_id, uuid=interrupt_id,
                        timestamp=timestamp, message={"role": "user", "content": [
                            {"type": "text", "text": "[Request interrupted by user for tool use]"}]}))
    return interrupt_id


def fork_session(path, cut_uuid=None, line=None, reask_form=None, target_cwd=None, label="fork"):
    records = read_records(path)
    form = find_form(records, None if reask_form == "__LAST__" else reask_form) if reask_form else None
    if form:
        cut_uuid = form[0]
    selected, leaf, stats = select_chain(records, cut_uuid, line)
    pending = pending_tools(selected)
    if form:
        leaf = splice_reask(selected, leaf, pending)
        pending = pending_tools(selected)
    if pending:
        raise ValueError("cut leaves unresolved tool_use IDs {}; select a completed reply or --reask-form".
                         format(", ".join(pending)))
    # Encoding a project directory is lossy; read cwd verbatim from the transcript.
    cwd = target_cwd or next((r["cwd"] for r in selected if r.get("cwd")), None)
    if not cwd or not Path(cwd).is_absolute():
        raise ValueError("no absolute transcript cwd; provide --target-cwd")
    if target_cwd and not Path(target_cwd).is_dir():
        raise ValueError("--target-cwd must name an existing directory")
    if target_cwd:
        # Claude keys new projects by physical cwd (e.g. /var -> /private/var on
        # macOS). Keep source transcript cwd verbatim when no override is given.
        cwd = str(Path(target_cwd).resolve())
    project = (CLAUDE_DIR / "projects" / re.sub(r"[^a-zA-Z0-9-]", "-", cwd)
               if target_cwd else path.parent)
    new_id = str(uuid.uuid4())
    target = project / (new_id + ".jsonl")
    for record in selected:
        if "sessionId" in record:
            record["sessionId"] = new_id
        if target_cwd and "cwd" in record:
            record["cwd"] = cwd
    # Complete backup BEFORE creating any destination transcript or sidecar.
    backup = backup_original(path, label)
    project.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        with target.open("x", encoding="utf-8") as stream:
            created.append(target)
            target.chmod(0o600)
            for record in selected:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        for (kind, src), (_, dst) in zip(sidecars(path, path.stem), sidecars(target, new_id)):
            if src.exists():
                if dst.exists():
                    raise FileExistsError(str(dst))
                created.append(dst)
                shutil.copytree(str(src), str(dst))
    except Exception:
        for item in reversed(created):
            if item.is_dir():
                shutil.rmtree(str(item))
            elif item.exists():
                item.unlink()
        raise
    stats.update(final_lines=len(selected), leaf_uuid=leaf)
    if form:
        stats["chain_steps"] += 2
    return {"id": new_id, "path": str(target), "backup": str(backup), "cwd": cwd,
            "stats": stats, "resume": "cd {} && claude --resume {}".format(shlex.quote(cwd), new_id)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("src", help="Source session UUID or transcript .jsonl path")
    boundary = parser.add_mutually_exclusive_group()
    boundary.add_argument("--cut-at-uuid", help="Keep this message and its ancestors (preferred)")
    boundary.add_argument("--truncate-at", type=int, help="Use the leaf at or before this 1-based source line")
    boundary.add_argument("--reask-form", nargs="?", const="__LAST__", metavar="TOOL_USE_ID",
                          help="Fork at the last or specified AskUserQuestion, with a balanced rejection")
    parser.add_argument("--project-dir", help="Override source project storage directory")
    parser.add_argument("--target-cwd", help="Fork into this existing absolute directory instead of the saved cwd")
    parser.add_argument("--label", default="fork", help="Backup directory label")
    args = parser.parse_args()
    result = fork_session(source_path(args.src, args.project_dir), args.cut_at_uuid,
                          args.truncate_at, args.reask_form, args.target_cwd, args.label)
    print("backup:   " + result["backup"])
    print("new uuid: " + result["id"])
    print("jsonl:    " + result["path"])
    print("checks:   " + json.dumps(result["stats"]))
    print("resume command:\n" + result["resume"])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        sys.exit(1)
