#!/usr/bin/env python3
"""Career's command entrance. Every application needs the person's approval before submit."""
import argparse
import json
import sys
from pathlib import Path

from runtime import HOME, Refused


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=HOME)
    commands = parser.add_subparsers(dest="action", required=True)
    command = commands.add_parser("batch", help="Curate Today without changing posting rulings")
    batches = command.add_subparsers(dest="operation", required=True)
    for operation in ("set", "add"):
        batch = batches.add_parser(operation)
        batch.add_argument("ids", nargs="*")
        batch.add_argument("--why-file", type=Path, required=True, help="JSON or TSV posting ids and one-line reasons")
        batch.add_argument("--note")
    batches.add_parser("show")
    batches.add_parser("clear")
    for action in ("fill", "apply-edits", "approve-submit", "submit", "confirm", "release", "gate", "review", "preview"):
        command = commands.add_parser(action)
        command.add_argument("id")
        if action == "approve-submit":
            command.add_argument("--digest", dest="fill_digest", required=True)
    command = commands.add_parser("pick")
    command.add_argument("id")
    command.add_argument("decision", choices=("approved", "skipped"))
    command.add_argument("--reason")
    command = commands.add_parser("box")
    command.add_argument("id")
    command.add_argument("name")
    command.add_argument("decision", choices=("approve", "edit"))
    command.add_argument("--value-file", type=Path)
    command = commands.add_parser("setup")
    command.add_argument("resume", help="UTF-8 resume material extracted by the agent")
    command = commands.add_parser("save", help="Save the person's chat answer; proposals stay in chat until ruled on")
    command.add_argument("--target", type=json.loads, required=True, help='For example: {"setting":"resume_pdf"}')
    command.add_argument("--value-file", type=Path, required=True)
    commands.add_parser("open", help="Open the running web app in this machine's browser, already paired")
    command = commands.add_parser("check", aliases=["doctor"], help="Report broken capabilities")
    command.add_argument("--offline", action="store_true", help="Do not contact sign-in or company feeds; report those as unverified")
    command = commands.add_parser("chat")
    command.add_argument("--text-file", type=Path, required=True)
    command.add_argument("--reply", action="store_true")
    command.add_argument("--resolve", type=int)
    command.add_argument("--posting-id")
    commands.add_parser("models",help="List the installed CLIs’ model choices without an agent turn")
    commands.add_parser("messages")
    commands.add_parser("events")
    commands.add_parser("tracker")
    command = commands.add_parser("resolve-uncertain")
    command.add_argument("id")
    command.add_argument("--reason", required=True)
    command = commands.add_parser("recipe")
    command.add_argument("id")
    command.add_argument("system")
    command.add_argument("--learning-file", type=Path)
    args = vars(parser.parse_args(argv))
    root = args.pop("home")
    action, pid = args.pop("action"), args.pop("id", None)
    if action == "models":
        from models import catalog
        print(json.dumps(catalog(),indent=2))
        return 0
    if action == "open":
        from serve import open_web
        print(json.dumps({"opened": open_web()}))
        return 0
    if action in ("check", "doctor"):
        from doctor import inspect
        result = inspect(root, **args)
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    from workflow import Workflow
    from questions import AskError
    workflow = Workflow(root)
    if action == "batch" and "why_file" in args:
        from batch import cli_items
        args["items"] = cli_items(args.pop("ids"), args.pop("why_file"))
    if "value_file" in args:
        path = args.pop("value_file")
        args["value"] = path.read_text() if path else None
    if "text_file" in args:
        args["text"] = args.pop("text_file").read_text()
    if "learning_file" in args:
        path = args.pop("learning_file")
        args["learning"] = json.loads(path.read_text()) if path else {}
    if action == "gate":
        missing = workflow.gate(pid)
        result = {"allowed": not missing, "missing": missing}
    elif action == "review":
        workflow.record(pid)
        result = workflow.review(pid)
    elif action == "preview":
        result = workflow.preview(pid)
    elif action == "events":
        result = workflow.deliveries()
    elif action == "messages":
        result = workflow.messages()
    elif action == "save":
        try:
            result = workflow.dispatch(action, **args)
        except AskError as error:
            raise Refused("BAD_DECISION", str(error)) from error
    else:
        if action in ("approve-submit", "box", "pick", "resolve-uncertain"):
            if not sys.stdin.isatty() or input("Type yes to record this personal decision: ") != "yes":
                raise Refused("PERSON_APPROVAL", "The person must decide in the app or type yes in a terminal.")
            args["by"] = "terminal-person"
        elif action == "chat":
            args["by"] = "agent" if args.get("reply") else "terminal-person"
        result = workflow.dispatch(action, pid, **args)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Refused as error:
        print(json.dumps({"ok": False, "code": error.code, "error": str(error)}), file=sys.stderr)
        sys.exit(1)
