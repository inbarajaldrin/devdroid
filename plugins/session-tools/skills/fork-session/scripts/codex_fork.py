#!/usr/bin/env python3
"""Fork a saved Codex thread with the official app-server; never write rollouts.

Talks to the app-server over a small standard-library WebSocket client.
No other installed skill is required.
"""

import argparse
import base64
import hashlib
import json
import os
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path


CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
SESSIONS = CODEX_HOME / "sessions"


class AppServer:
    """JSON-RPC over the daemon's Unix WebSocket, using only the stdlib."""

    def __init__(self, socket_path=None, timeout=20):
        self.timeout = timeout
        self.next_id = 0
        self.buffer = b""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect(str(socket_path or CODEX_HOME / "app-server-control/app-server-control.sock"))
            self.handshake()
            self.request("initialize", {"clientInfo": {"name": "fork_session", "version": "1.0"},
                                        "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.sock.close()
            raise

    def handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
                           "Connection: Upgrade\r\nSec-WebSocket-Key: " + key +
                           "\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("daemon closed during WebSocket handshake")
            self.buffer += chunk
            if len(self.buffer) > 65536:
                raise RuntimeError("oversized WebSocket handshake")
        header, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        headers = dict(line.split(b":", 1) for line in header.split(b"\r\n")[1:] if b":" in line)
        headers = {name.lower(): value.strip() for name, value in headers.items()}
        if b" 101 " not in header.split(b"\r\n", 1)[0] or headers.get(b"sec-websocket-accept") != accept:
            raise RuntimeError("invalid daemon WebSocket handshake")

    def exact(self, size):
        while len(self.buffer) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("daemon closed the WebSocket")
            self.buffer += chunk
        result, self.buffer = self.buffer[:size], self.buffer[size:]
        return result

    def frame(self, opcode, payload=b""):
        mask = os.urandom(4)
        size = len(payload)
        if size < 126:
            header = bytes([0x80 | opcode, 0x80 | size])
        elif size < 65536:
            header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", size)
        else:
            header = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack("!Q", size)
        self.sock.sendall(header + mask + bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload)))

    def receive(self, deadline):
        fragments = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("app-server request timed out")
            self.sock.settimeout(remaining)
            first, second = self.exact(2)
            opcode, size = first & 0x0F, second & 0x7F
            if size == 126:
                size = struct.unpack("!H", self.exact(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.exact(8))[0]
            mask = self.exact(4) if second & 0x80 else None
            payload = self.exact(size)
            if mask:
                payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
            if opcode == 0x8:
                raise RuntimeError("daemon closed the WebSocket")
            if opcode == 0x9:
                self.frame(0xA, payload)
            elif opcode in (0x1, 0x0):
                fragments.append(payload)
                if first & 0x80:
                    return json.loads(b"".join(fragments))

    def send(self, message):
        self.frame(0x1, json.dumps(message, separators=(",", ":")).encode())

    def request(self, method, params):
        self.next_id += 1
        request_id = self.next_id
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            message = self.receive(deadline)
            if message.get("id") != request_id:
                if "method" in message and "id" in message:
                    raise RuntimeError("unexpected interactive app-server request: " + message["method"])
                continue
            if "error" in message:
                raise RuntimeError("{}: {}".format(method, message["error"]))
            return message.get("result") or {}

    def close(self):
        try:
            self.frame(0x8)
        except OSError:
            pass
        self.sock.close()


def connect_app_server(socket_path=None):
    try:
        return AppServer(socket_path)
    except (OSError, RuntimeError, ValueError) as first_error:
        print("app-server unavailable; starting Codex daemon: {}".format(first_error), file=sys.stderr)
    result = subprocess.run(["codex", "app-server", "daemon", "start"],
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError("daemon start failed: " + (result.stderr or result.stdout).strip())
    deadline = time.monotonic() + 8
    while True:
        try:
            return AppServer(socket_path)
        except (OSError, RuntimeError, ValueError) as error:
            if time.monotonic() >= deadline:
                raise RuntimeError("daemon socket still unreachable: {}".format(error)) from error
            time.sleep(0.1)


def resolve_thread(client, query):
    # Exact IDs bypass list pagination and also work for archived threads.
    import uuid
    try:
        exact_id = str(uuid.UUID(query))
    except ValueError:
        exact_id = None
    if exact_id:
        return client.request("thread/read", {"threadId": exact_id, "includeTurns": False})["thread"]
    threads = []
    cursor = None
    while True:
        params = {"limit": 100, "archived": False}
        if cursor:
            params["cursor"] = cursor
        result = client.request("thread/list", params)
        threads.extend(result.get("data") or [])
        cursor = result.get("nextCursor")
        if not cursor:
            break
    matches = [thread for thread in threads if thread["id"].startswith(query)
               or (thread.get("name") or "").casefold() == query.casefold()]
    if len(matches) != 1:
        raise RuntimeError("expected one thread for {!r}, found {}; use a full UUID".format(query, len(matches)))
    return matches[0]


def rollout_path(thread):
    path = thread.get("path")
    if path and Path(path).is_file():
        return Path(path)
    matches = list(SESSIONS.rglob("*-{}.jsonl".format(thread["id"])))
    if len(matches) != 1:
        raise RuntimeError("expected one source rollout, found {}".format(len(matches)))
    return matches[0]


def read_turns(path):
    turns = {}
    current = None
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError("invalid JSON at {}:{}: {}".format(path, line_number, error)) from error
            payload = record.get("payload") or {}
            if record.get("type") == "turn_context":
                current = payload.get("turn_id")
                if current and current not in turns:
                    turns[current] = {"id": current, "timestamp": record.get("timestamp", ""),
                                      "status": "inProgress", "texts": []}
            elif record.get("type") == "response_item" and payload.get("type") == "message":
                metadata = payload.get("internal_chat_message_metadata_passthrough") or {}
                turn_id = metadata.get("turn_id") or current
                if turn_id in turns:
                    text = " ".join(part.get("text", "") for part in payload.get("content") or []
                                    if isinstance(part, dict) and part.get("text"))
                    if text:
                        turns[turn_id]["texts"].append((payload.get("role", "message"), text))
            elif record.get("type") == "event_msg":
                turn_id = payload.get("turn_id") or current
                if turn_id in turns and payload.get("type") in {"task_complete", "turn_completed", "turn_aborted"}:
                    turns[turn_id]["status"] = "interrupted" if payload["type"] == "turn_aborted" else "completed"
    return list(turns.values())


def print_matches(turns, needle):
    found = False
    for number, turn in enumerate(turns, 1):
        matches = [(role, text) for role, text in turn["texts"] if needle.casefold() in text.casefold()]
        if matches:
            found = True
            role, text = matches[0]
            print("{}  {}  {:11}  {}  {}: {}".format(number, turn["id"], turn["status"],
                  turn["timestamp"], role, " ".join(text.split())[:240]))
    if not found:
        raise RuntimeError("no persisted turn contains {!r}".format(needle))


def fork_thread(client, source, turns, through=None, before=None, name=None):
    selected = through or before
    if selected:
        known = {turn["id"]: turn for turn in turns}
        if selected not in known:
            raise RuntimeError("unknown turn: " + selected)
        if through and known[selected]["status"] != "completed":
            raise RuntimeError("--fork-through requires a completed turn: " + selected)
    params = {"threadId": source["id"], "excludeTurns": True}
    if through:
        params["lastTurnId"] = through
    if before:
        params["beforeTurnId"] = before
    result = client.request("thread/fork", params)
    fork = result.get("thread") or {}
    fork_id = fork.get("id")
    if not fork_id or fork_id == source["id"]:
        raise RuntimeError("thread/fork did not return a NEW thread id")
    # Report the new ID before optional naming, so a naming failure is recoverable.
    print("new thread:     " + fork_id, flush=True)
    if name:
        client.request("thread/name/set", {"threadId": fork_id, "name": name})
    return fork


def copy_clipboard(text):
    for command in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(command[0]):
            try:
                subprocess.run(command, input=text, text=True, check=True, timeout=3)
                return True
            except (OSError, subprocess.SubprocessError):
                pass
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--session", required=True, help="Source thread UUID, unique prefix or exact name")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--find-text", help="Search saved turns without forking")
    mode.add_argument("--fork-through", metavar="TURN_ID", help="Keep this completed turn, inclusive")
    mode.add_argument("--fork-before", metavar="TURN_ID", help="Exclude this turn and later turns")
    parser.add_argument("--name", help="Name the new fork")
    parser.add_argument("--socket", help="Daemon control socket override")
    parser.add_argument("--no-clipboard", action="store_true")
    args = parser.parse_args()
    client = connect_app_server(args.socket)
    try:
        source = resolve_thread(client, args.session)
        path = rollout_path(source)
        turns = read_turns(path)
        if args.find_text:
            print_matches(turns, args.find_text)
            return
        fork = fork_thread(client, source, turns, args.fork_through, args.fork_before, args.name)
        cwd = fork.get("cwd") or source.get("cwd") or os.getcwd()
        endpoint = "unix://" + str(Path(args.socket).expanduser()) if args.socket else "unix://"
        command = "cd {} && codex --remote {} resume {}".format(shlex.quote(cwd), shlex.quote(endpoint), fork["id"])
        print("source thread:  " + source["id"])
        print("source rollout: " + str(path))
        print("fork boundary:  " + ("through " + args.fork_through if args.fork_through else
                                    "before " + args.fork_before if args.fork_before else "at leaf"))
        print("resume command:\n" + command)
        if not args.no_clipboard:
            print("copied to clipboard" if copy_clipboard(command) else "clipboard unavailable")
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("ERROR: {}".format(error), file=sys.stderr)
        sys.exit(1)
