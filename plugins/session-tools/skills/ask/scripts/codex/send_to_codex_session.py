#!/usr/bin/env python3
"""Message a live Codex thread through the managed app-server Unix socket."""

import argparse
import base64
import fcntl
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


DEFAULT_SOCKET = Path.home() / ".codex/app-server-control/app-server-control.sock"
CLIENT = {"name": "send_to_codex_session", "title": "Send to Codex Session", "version": "1.0.0"}
MANAGED_CODEX = Path.home() / ".codex/packages/standalone/current/bin/codex"
LOCAL_CODEX = Path.home() / ".local/bin/codex"
OUTCOME = {}
RESTART_COMMAND = "codex app-server daemon restart"


class ProtocolError(RuntimeError):
    pass


class RequestError(ProtocolError):
    def __init__(self, method, error):
        super().__init__(f"{method}: {error.get('message', 'request rejected')}")
        self.method, self.error = method, error


class TurnError(ProtocolError):
    def __init__(self, status, error, safe_to_retry):
        super().__init__(f"target turn ended with status {status}: {error.get('message', 'no error detail returned')}")
        self.error, self.safe_to_retry = error, safe_to_retry


def failure_kind(error):
    """Structured classifications win, including unknown future classifications."""
    if not isinstance(error, dict):
        return "other"
    structured = structured_failure(error)
    if structured is not None:
        return structured
    message = error.get("message", "")
    if re.search(r"\b(?:429|insufficient_quota|quota exceeded|rate limit exceeded)\b", message, re.I):
        return "quota"
    if message.strip() == (
        "Your access token could not be refreshed because you have since logged out "
        "or signed in to another account. Please sign in again."
    ) or re.match(r"^(?:Unauthorized\b|(?:HTTP(?: error)?|unexpected status(?: code)?)\s*:?\s*(?:401|403)\b)", message, re.I):
        return "auth"
    return "other"


def has_classification(error):
    return structured_failure(error) is not None


def structured_failure(error):
    """None means absent; 'other' is a present classification, so forbids prose fallback."""
    kinds = []
    statuses = []
    info = error.get("codexErrorInfo")
    if info is not None:
        kinds.append("auth" if info == "unauthorized" else "quota" if info in (
            "usageLimitExceeded", "rateLimitExceeded", "sessionBudgetExceeded") else "other")
        if isinstance(info, dict):
            statuses.extend(value["httpStatusCode"] for value in info.values()
                            if isinstance(value, dict) and value.get("httpStatusCode") is not None)
    statuses.extend(error[key] for key in ("httpStatusCode", "statusCode") if error.get(key) is not None)
    code = error.get("code")
    if code in (401, 403, 429):
        statuses.append(code)
    elif isinstance(code, str):
        kinds.append({"unauthorized": "auth", "invalid_api_key": "auth", "token_expired": "auth",
                      "insufficient_quota": "quota", "rate_limit_exceeded": "quota"}.get(code, "other"))
    kinds.extend("quota" if status == 429 else "auth" if status in (401, 403) else "other"
                 for status in statuses)
    for key in ("data", "error"):
        if isinstance(error.get(key), dict):
            nested = structured_failure(error[key])
            if nested is not None:
                kinds.append(nested)
    return "quota" if "quota" in kinds else "auth" if "auth" in kinds else "other" if kinds else None


def record_outcome(kind, code, **details):
    OUTCOME.update(version=1, kind=kind, code=code, **details)


def resolve_codex_binary():
    """Resolve Codex without relying on interactive shell startup files."""
    candidates = [
        os.environ.get("ASK_GPT_CODEX_BIN"),
        str(LOCAL_CODEX),
        str(MANAGED_CODEX),
        shutil.which("codex"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return os.path.realpath(candidate)
    raise ProtocolError(
        "codex executable not found; install the managed standalone Codex build or "
        "set ASK_GPT_CODEX_BIN"
    )


class AppServer:
    def __init__(self, socket_path, timeout):
        self.socket_path = Path(socket_path)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.settimeout(None if timeout <= 0 else timeout)
            self.sock.connect(str(socket_path))
            self.buffer = b""
            self.pending = []
            self.next_id = 1
            self._handshake()
            result = self.request(
                "initialize",
                {"clientInfo": CLIENT, "capabilities": {"experimentalApi": True}},
            )
            if not result.get("userAgent"):
                raise ProtocolError("app-server initialize returned no userAgent")
            self.notify("initialized", {})
        except Exception:
            self.sock.close()
            raise

    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            "GET / HTTP/1.1\r\n"
            "Host: localhost\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ProtocolError("app-server closed during WebSocket handshake")
            self.buffer += chunk
        header, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        if b" 101 " not in header.split(b"\r\n", 1)[0]:
            raise ProtocolError(f"WebSocket handshake failed: {header.decode(errors='replace')}")

    def _exact(self, size):
        while len(self.buffer) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ProtocolError("app-server closed the WebSocket")
            self.buffer += chunk
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data

    def _send_frame(self, opcode, payload=b""):
        mask = os.urandom(4)
        size = len(payload)
        if size < 126:
            header = bytes([0x80 | opcode, 0x80 | size])
        elif size < 65536:
            header = bytes([0x80 | opcode, 0x80 | 126]) + struct.pack("!H", size)
        else:
            header = bytes([0x80 | opcode, 0x80 | 127]) + struct.pack("!Q", size)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def _frame(self):
        first, second = self._exact(2)
        final = bool(first & 0x80)
        opcode = first & 0x0F
        size = second & 0x7F
        if size == 126:
            size = struct.unpack("!H", self._exact(2))[0]
        elif size == 127:
            size = struct.unpack("!Q", self._exact(8))[0]
        mask = self._exact(4) if second & 0x80 else None
        payload = self._exact(size)
        if mask:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return final, opcode, payload

    def receive(self):
        fragments = []
        while True:
            final, opcode, payload = self._frame()
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0x8:
                raise ProtocolError(f"app-server closed: {payload!r}")
            if opcode in (0x1, 0x0):
                fragments.append(payload)
                if final:
                    return json.loads(b"".join(fragments))

    def _send_json(self, value):
        self._send_frame(0x1, json.dumps(value, separators=(",", ":")).encode())

    def notify(self, method, params):
        self._send_json({"method": method, "params": params})

    def request(self, method, params):
        request_id = self.next_id
        self.next_id += 1
        self._send_json({"method": method, "id": request_id, "params": params})
        while True:
            message = self.receive()
            if message.get("id") == request_id:
                if "error" in message:
                    error = message["error"]
                    raise RequestError(method, error)
                return message.get("result") or {}
            self.pending.append(message)

    def events(self):
        while True:
            if self.pending:
                yield self.pending.pop(0)
            else:
                yield self.receive()

    def close(self):
        try:
            self._send_frame(0x8)
        except OSError:
            pass
        self.sock.close()


def status_type(thread):
    status = thread.get("status") or {}
    return status.get("type", "unknown") if isinstance(status, dict) else str(status)


def thread_label(thread):
    return thread.get("name") or thread.get("preview") or "(untitled)"


def list_threads(client, limit):
    result = client.request("thread/list", {"limit": limit, "archived": False})
    return result.get("data") or []


def print_threads(threads):
    if not threads:
        print("(no matching Codex threads)")
        return
    for thread in threads:
        print(
            f"{thread.get('id',''):36}  {status_type(thread):10}  "
            f"cwd={thread.get('cwd','')[:36]:36}  {thread_label(thread)[:70]}"
        )


def matches(thread, query):
    needle = query.casefold()
    values = [thread.get("id"), thread.get("name"), thread.get("preview"), thread.get("cwd")]
    return any(needle in (value or "").casefold() for value in values)


def resolve_thread(threads, query):
    exact_id = [thread for thread in threads if thread.get("id") == query]
    if exact_id:
        return exact_id[0]
    id_prefix = [thread for thread in threads if (thread.get("id") or "").startswith(query)]
    if len(id_prefix) == 1:
        return id_prefix[0]
    exact_name = [thread for thread in threads if (thread.get("name") or "").casefold() == query.casefold()]
    if len(exact_name) == 1:
        return exact_name[0]
    candidates = [thread for thread in threads if matches(thread, query)]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ProtocolError(f"no Codex thread matches {query!r}; use --list")
    details = "\n".join(f"  {item.get('id')}  {status_type(item):10}  {thread_label(item)}" for item in candidates[:10])
    raise ProtocolError(f"{query!r} matches {len(candidates)} threads; use a full ID:\n{details}")


def resolve_thread_live(client, threads, query):
    """Resolve from the list, with a direct-ID fallback for a just-created thread.

    A headless `--start --no-wait` response can become visible through
    `thread/read` a fraction before it appears in a fresh `thread/list` result.
    This makes an immediate follow-up `--steer` deterministic.
    """
    try:
        return resolve_thread(threads, query)
    except ProtocolError:
        if len(query) != 36 or query.count("-") != 4:
            raise
        result = client.request("thread/read", {"threadId": query, "includeTurns": False})
        thread = result.get("thread") or {}
        if thread.get("id") != query:
            raise
        return thread


def resolve_thread_exact(client, threads, query):
    """Resolve only a complete thread ID, never another thread's name or preview."""
    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", query or ""):
        raise ProtocolError("an exact target needs a complete thread UUID")
    found = [thread for thread in threads if thread.get("id") == query]
    if len(found) == 1:
        return found[0]
    thread = client.request("thread/read", {"threadId": query, "includeTurns": False}).get("thread") or {}
    if thread.get("id") != query:
        raise ProtocolError("exact Codex thread was not found")
    return thread


def envelope(message, sender=None):
    sender = sender or os.environ.get("CODEX_THREAD_ID")
    if not sender:
        return message
    return (
        "━━━ INTER-AGENT MESSAGE via /ask ━━━\n"
        f"From thread: {sender}\n"
        "If it asks for something, include the answer in your final answer for this turn. "
        "It does not change the work you were given.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        + message
    )


def write_json(path, value):
    if not path:
        return
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def writable_roots(args):
    return [str(Path(item).expanduser().resolve()) for item in args.add_dir]


def sandbox_policy(args):
    if args.sandbox == "read-only":
        return {"type": "readOnly"}
    if args.sandbox == "workspace-write":
        return {"type": "workspaceWrite", "writableRoots": writable_roots(args)}
    if args.sandbox == "danger-full-access":
        return {"type": "dangerFullAccess"}
    return None


def turn_overrides(args):
    overrides = {}
    for key, value in (
        ("approvalPolicy", args.approval_policy),
        ("cwd", args.cwd),
        ("model", args.model),
        ("effort", args.effort),
    ):
        if value is not None:
            overrides[key] = value
    policy = sandbox_policy(args)
    if policy is not None:
        overrides["sandboxPolicy"] = policy
    return overrides


def emit_reply(reply, output_path):
    if output_path:
        Path(output_path).write_text(reply, encoding="utf-8")
    print(reply)


def wait_for_turn(client, thread_id, turn_id, timeout):
    deadline = None if timeout <= 0 else time.monotonic() + timeout
    messages = []
    errors = []
    work_seen = False
    while deadline is None or time.monotonic() < deadline:
        client.sock.settimeout(None if deadline is None else max(0.1, deadline - time.monotonic()))
        event = next(client.events())
        method = event.get("method", "")
        params = event.get("params") or {}
        if params.get("threadId") != thread_id:
            continue
        if event.get("id") is not None and method:
            raise ProtocolError(f"target requested interactive input through {method}; handle it in the target TUI")
        if params.get("turnId") == turn_id:
            if method.startswith("item/"):
                item = params.get("item") or {}
                if method not in {"item/started", "item/completed"} or item.get("type") != "userMessage":
                    work_seen = True
            elif method in {"turn/plan/updated", "thread/tokenUsage/updated"}:
                work_seen = True
        if method == "item/completed" and params.get("turnId") == turn_id:
            item = params.get("item") or {}
            if item.get("type") == "agentMessage" and item.get("text"):
                messages.append(item["text"])
        if method == "error" and params.get("turnId") == turn_id:
            error = params.get("error") or {}
            if params.get("willRetry"):
                work_seen = True
            errors.append(error)
        if method == "turn/completed":
            completed = params.get("turn") or {}
            if completed.get("id") == turn_id:
                status = completed.get("status")
                if status != "completed":
                    turn_error = completed.get("error") or (errors[-1] if errors else {})
                    # A final message may omit the structured info carried by error.
                    if not has_classification(turn_error) and errors and has_classification(errors[-1]):
                        turn_error = {**errors[-1], **{key: value for key, value in turn_error.items() if value is not None}}
                    work_seen |= any(item.get("type") != "userMessage" for item in completed.get("items", []))
                    info = turn_error.get("codexErrorInfo")
                    if isinstance(info, dict) and "responseStreamDisconnected" in info:
                        work_seen = True  # the stream may have accepted work before disconnecting
                    safe = status == "failed" and not work_seen
                    raise TurnError(status, turn_error, safe)
                return messages[-1] if messages else ""
    raise ProtocolError(f"timed out after {timeout}s waiting for target reply")


def start_turn(client, thread_id, message, timeout, no_wait, raw, overrides=None, sender=None,
               resume_params=None):
    params = {
        "threadId": thread_id,
        "input": [{"type": "text", "text": message if raw else envelope(message, sender)}],
        "clientUserMessageId": str(uuid.uuid4()),
    }
    params.update(overrides or {})
    try:
        with daemon_lock(client.socket_path):
            result = submit_turn(client, params)
        return finish_turn(client, result, thread_id, timeout, no_wait)
    except (RequestError, TurnError) as error:
        if failure_kind(error.error) != "auth":
            raise
        safe = (isinstance(error, RequestError) and error.method == "turn/start") or (
            isinstance(error, TurnError) and error.safe_to_retry
        )
        if not safe:
            record_outcome(OUTCOME.get("kind", "unknown"), "AUTH_RETRY_UNSAFE")
            raise ProtocolError("auth failure after work may have started; refusing to resend") from error
        recovered = recover_auth(client, timeout, params, resume_params)
        try:
            # Deliberately outside the catch above: a second failure never recovers.
            return finish_turn(recovered[0], recovered[1], thread_id, timeout, no_wait)
        finally:
            recovered[0].close()


def submit_turn(client, params):
    # Keep the failed generation identifiable even if another /ask repairs it
    # before this caller reaches recovery. Failure to inspect never blocks sending.
    try:
        daemon_identity(client)
    except (OSError, ProtocolError):
        pass
    record_outcome("unknown", "ACCEPTANCE_UNKNOWN", threadId=params["threadId"], turnId=None, status="unknown")
    try:
        result = client.request("turn/start", params)
    except RequestError:
        record_outcome("not-delivered", "REQUEST_REJECTED", status="rejected")
        raise
    turn = result.get("turn") or {}
    turn_id = turn.get("id")
    if not turn_id:
        raise ProtocolError("turn/start returned no turn id")
    record_outcome("accepted", "TURN_ACCEPTED", turnId=turn_id, status="accepted")
    return result


def finish_turn(client, result, thread_id, timeout, no_wait):
    turn_id = result["turn"]["id"]
    print(f"[ask codex] target={thread_id} turn={turn_id}", file=sys.stderr)
    turn = result["turn"]
    if turn.get("status") == "failed":
        record_outcome("accepted", "TURN_FAILED", status="failed")
        info = (turn.get("error") or {}).get("codexErrorInfo")
        disconnected = isinstance(info, dict) and "responseStreamDisconnected" in info
        raise TurnError("failed", turn.get("error") or {},
                        not disconnected and not any(item.get("type") != "userMessage" for item in turn.get("items", [])))
    if no_wait:
        return turn_id, None
    try:
        reply = wait_for_turn(client, thread_id, turn_id, timeout)
    except TurnError:
        record_outcome("accepted", "TURN_FAILED", status="failed")
        raise
    record_outcome("accepted", "COMPLETED", status="completed")
    return turn_id, reply


def create_thread(client, args, message):
    params = {}
    for key, value in (
        ("cwd", args.cwd or os.getcwd()),
        ("model", args.model),
        ("sandbox", args.sandbox),
        ("approvalPolicy", args.approval_policy),
    ):
        if value is not None:
            params[key] = value
    if args.add_dir:
        params["config"] = {
            "sandbox_workspace_write": {"writable_roots": writable_roots(args)}
        }
    result = client.request("thread/start", params)
    thread = result.get("thread") or {}
    thread_id = thread.get("id")
    if not thread_id:
        raise ProtocolError("thread/start returned no thread id")
    # Persist the id SYNCHRONOUSLY at launch, before the (potentially long)
    # turn wait — dispatchers that plan to --steer need the handle immediately,
    # and the completion-time write below never arrives for a killed turn.
    write_json(args.metadata, {"threadId": thread_id, "turnId": None, "status": "started"})
    overrides = turn_overrides(args)
    turn_id, reply = start_turn(
        client, thread_id, message, args.timeout, args.no_wait, args.raw, overrides, args.from_session,
        {**params, "threadId": thread_id, "excludeTurns": True}
    )
    return thread_id, turn_id, reply


def send_message(client, thread, args, message, timeout, no_wait, raw, sender=None):
    current_status = status_type(thread)
    if current_status not in {"notLoaded", "idle", "active"}:
        raise ProtocolError(f"target status is {current_status}; expected notLoaded, idle, or active")
    if current_status == "active":
        raise ProtocolError("target has an active turn; use --steer to add input to it")

    thread_id = thread["id"]
    # Same synchronous-at-launch id persistence as create_thread — a resumed
    # dispatch must be steerable before its turn completes.
    write_json(args.metadata, {"threadId": thread_id, "turnId": None, "status": "started"})
    # A notLoaded thread is a saved transcript that is not currently attached to
    # the daemon. Loading it headlessly is exactly what this transport is for;
    # refusing it forces callers onto codex exec and permanently strands the
    # thread outside the managed path.
    resume_params = {"threadId": thread_id, "excludeTurns": True}
    for key, value in (
        ("approvalPolicy", args.approval_policy),
        ("cwd", args.cwd),
        ("sandbox", args.sandbox),
        ("model", args.model),
    ):
        if value is not None:
            resume_params[key] = value
    if args.add_dir:
        resume_params["config"] = {
            "sandbox_workspace_write": {"writable_roots": writable_roots(args)}
        }
    client.request("thread/resume", resume_params)
    return (thread_id,) + start_turn(
        client,
        thread_id,
        message,
        timeout,
        no_wait,
        raw,
        turn_overrides(args),
        sender,
        resume_params,
    )


class TurnEnded(ProtocolError):
    pass


def steer_thread(client, thread, message, raw, sender=None):
    current_status = status_type(thread)
    if current_status != "active":
        raise ProtocolError(f"target status is {current_status}; --steer requires an active turn")
    thread_id = thread["id"]
    # Full-history thread/read can take minutes for a long conversation with
    # images. The paginated endpoint gets the newest turn without hydrating
    # every earlier item, so mid-turn messages remain prompt even there.
    result = client.request(
        "thread/turns/list",
        {"threadId": thread_id, "limit": 1, "sortDirection": "desc", "itemsView": "notLoaded"},
    )
    active_turn = next(
        (turn for turn in result.get("data") or [] if turn.get("status") == "inProgress"),
        None,
    )
    if not active_turn or not active_turn.get("id"):
        record_outcome("not-delivered", "TURN_ENDED", threadId=thread_id)
        raise TurnEnded("the selected turn ended before input submission")
    turn_id = active_turn["id"]
    with daemon_lock(client.socket_path):
        record_outcome("unknown", "ACCEPTANCE_UNKNOWN", threadId=thread_id)
        try:
            client.request("turn/steer", {
                "threadId": thread_id,
                "expectedTurnId": turn_id,
                "input": [{"type": "text", "text": message if raw else envelope(message, sender)}],
                "clientUserMessageId": str(uuid.uuid4()),
            })
        except RequestError:
            record_outcome("not-delivered", "REQUEST_REJECTED", threadId=thread_id)
            raise
    record_outcome("accepted", "STEERED", threadId=thread_id, turnId=turn_id)
    print(f"[ask codex] steered target={thread_id} turn={turn_id}", file=sys.stderr)
    return thread_id, turn_id


def deliver_midturn(client, target, args, message):
    """Steer without interrupting; only an explicit pre-admission refusal can fall back."""
    if status_type(target) != "active":
        return send_message(client, target, args, message, args.timeout, args.no_wait, args.raw, args.from_session)
    try:
        thread_id, turn_id = steer_thread(client, target, message, args.raw, args.from_session)
        return thread_id, turn_id, None
    except (TurnEnded, RequestError) as error:
        if isinstance(error, RequestError) and (error.method != "turn/steer" or error.error.get("code") != -32600 or structured_failure(error.error) in ("auth", "quota")):
            raise
        current = resolve_thread_exact(client, [], target["id"])
        if status_type(current) != "idle":
            raise
        record_outcome("not-delivered", "TURN_ENDED", threadId=target["id"])
        result = send_message(client, current, args, message, args.timeout, args.no_wait, args.raw, args.from_session)
        OUTCOME["fallback"] = "TURN_ENDED"
        return result


@contextmanager
def daemon_lock(socket_path):
    """Also serialize turn admission, so /ask cannot start work during recovery."""
    path = Path(socket_path).parent / "ask-auth-recovery.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield stream
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def daemon_identity(client):
    """Identify the socket's actual peer, never a guessed process or socket mtime."""
    cached = getattr(client, "_daemon_identity", None)
    if cached is not None:
        return cached
    if sys.platform == "darwin":
        # SOL_LOCAL / LOCAL_PEERPID from sys/un.h.
        pid = client.sock.getsockopt(0, 2)
    else:
        pid = struct.unpack("3i", client.sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0]
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="],
                                capture_output=True, text=True, timeout=5,
                                env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProtocolError("cannot establish managed daemon start time; refusing auth recovery") from error
    try:
        started = datetime.strptime(result.stdout.strip(), "%a %b %d %H:%M:%S %Y").timestamp()
    except ValueError as error:
        raise ProtocolError("cannot establish managed daemon start time; refusing auth recovery") from error
    if result.returncode:
        raise ProtocolError("cannot establish managed daemon start time; refusing auth recovery")
    # ps has one-second precision. Use its upper bound rather than declaring a
    # same-second login stale without evidence.
    client._daemon_identity = {"pid": pid, "startedAt": started + 1}
    return client._daemon_identity


def auth_file_mtime():
    # Metadata only: never open the credential file, even for diagnostics.
    return (Path.home() / ".codex" / "auth.json").stat().st_mtime


def busy_threads(client):
    """Loaded includes ephemeral threads; the ordinary history list does not."""
    busy, cursor, seen = [], None, set()
    while True:
        page = client.request("thread/loaded/list", {"limit": 100, "cursor": cursor})
        if not isinstance(page.get("data"), list):
            raise ProtocolError("cannot establish daemon activity; refusing auth recovery")
        for thread_id in page["data"]:
            thread = client.request("thread/read", {"threadId": thread_id, "includeTurns": False}).get("thread") or {}
            state = status_type(thread)
            if thread.get("id") != thread_id or state not in {"idle", "notLoaded", "systemError", "active"}:
                raise ProtocolError("cannot establish daemon activity; refusing auth recovery")
            if state == "active":
                busy.append(thread_id)
        cursor = page.get("nextCursor")
        if not cursor:
            return busy
        if cursor in seen:
            raise ProtocolError("cannot finish daemon activity scan; refusing auth recovery")
        seen.add(cursor)


def restart_daemon():
    try:
        result = subprocess.run([resolve_codex_binary(), "app-server", "daemon", "restart"],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProtocolError(f"{RESTART_COMMAND} failed; no message resent") from error
    if result.returncode:
        # The command's output is not needed to classify recovery or delivery.
        raise ProtocolError(f"{RESTART_COMMAND} failed (exit {result.returncode}); no message resent")


def wait_for_health(socket_path):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            client = connect_app_server(socket_path, 1, repair=False)
        except (OSError, json.JSONDecodeError, ProtocolError):
            time.sleep(0.1)
            continue
        return client  # successful WebSocket and initialize handshake
    raise ProtocolError("managed app-server did not become healthy after auth restart; no message resent")


def recover_auth(failed_client, timeout, params, resume_params):
    recovery = {"kind": "stale-daemon-auth", "action": "none", "retried": False}
    OUTCOME["recovery"] = recovery
    if Path(failed_client.socket_path) != DEFAULT_SOCKET:
        raise ProtocolError("auth failure on a custom socket; refusing to restart a different daemon")
    failed_identity = daemon_identity(failed_client)
    try:
        login_time = auth_file_mtime()
    except OSError as error:
        record_outcome(OUTCOME.get("kind", "unknown"), "AUTH_LOGIN_REQUIRED")
        raise ProtocolError("Codex login is unavailable; run `codex login` before sending again") from error
    if login_time <= failed_identity["startedAt"]:
        recovery["kind"] = "login-required"
        record_outcome(OUTCOME.get("kind", "unknown"), "AUTH_LOGIN_REQUIRED")
        raise ProtocolError("Codex auth failed and the login is not newer than the daemon; run `codex login`")

    with daemon_lock(failed_client.socket_path) as lock:
        current = connect_app_server(failed_client.socket_path, 2, repair=False)
        try:
            identity = daemon_identity(current)
            if identity == failed_identity:
                busy = busy_threads(current)
                if busy:
                    recovery["busyThreads"] = busy
                    record_outcome(OUTCOME.get("kind", "unknown"), "AUTH_DAEMON_BUSY")
                    raise ProtocolError(f"Codex daemon is stale; busy threads: {', '.join(busy)}; "
                                        f"once they finish run `{RESTART_COMMAND}`")
                # Persist the attempted generation BEFORE running the command.
                # A failed restart must not make concurrent callers restart again.
                lock.seek(0)
                previous = lock.read()
                if previous and json.loads(previous).get("attempted") == identity:
                    raise ProtocolError("auth restart already attempted for this daemon; no message resent")
                lock.seek(0)
                lock.truncate()
                json.dump({"attempted": identity}, lock)
                lock.flush()
                recovery["action"] = "restart-attempted"
                current.close()
                current = None
                try:
                    restart_daemon()
                    current = wait_for_health(failed_client.socket_path)
                except ProtocolError:
                    recovery["status"] = "failed"
                    record_outcome(OUTCOME.get("kind", "unknown"), "AUTH_RECOVERY_FAILED")
                    raise
                if daemon_identity(current) == failed_identity:
                    raise ProtocolError("auth restart did not replace the stale daemon; no message resent")
                recovery["action"] = "restarted"
            else:
                # A concurrent caller (or the user) already replaced our peer.
                recovery["action"] = "already-restarted"
            if auth_file_mtime() > daemon_identity(current)["startedAt"]:
                raise ProtocolError("login changed during recovery; no message resent")
            # Restart unloads all threads. Resume the SAME id, preserving settings.
            current.sock.settimeout(None if timeout <= 0 else timeout)
            resumed = current.request("thread/resume", resume_params or {"threadId": params["threadId"], "excludeTurns": True})
            if status_type(resumed.get("thread") or {}) != "idle":
                record_outcome(OUTCOME.get("kind", "unknown"), "AUTH_RETRY_UNSAFE")
                raise ProtocolError("target is no longer idle after auth recovery; refusing to resend")
            print("[ask codex] auth recovery: login was newer than the daemon; "
                  f"{recovery['action']}; retrying the same thread once after fixing the cause", file=sys.stderr)
            recovery["retried"] = True
            result = submit_turn(current, params)
            recovered, current = current, None
            return recovered, result
        finally:
            if current is not None:
                current.close()


def start_daemon():
    codex = resolve_codex_binary()
    try:
        result = subprocess.run(
            [codex, "app-server", "daemon", "start"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProtocolError(f"could not start managed app-server: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ProtocolError(f"could not start managed app-server: {detail}")


def connect_app_server(socket_path, timeout, repair=True):
    """Connect, repairing a missing or stale managed socket once.

    Socket existence is not a health check: after an idle daemon exits, macOS
    and Linux can retain the Unix socket while connect(2) returns ECONNREFUSED.
    The official daemon start command safely replaces that stale socket under
    its own startup lock, so never unlink it ourselves.
    """
    first_error = None
    try:
        return AppServer(socket_path, timeout)
    except (OSError, socket.timeout, json.JSONDecodeError, ProtocolError) as error:
        first_error = error
        if not repair:
            raise

    start_daemon()
    deadline = time.monotonic() + 8
    last_error = first_error
    while time.monotonic() < deadline:
        try:
            return AppServer(socket_path, timeout)
        except (OSError, socket.timeout, json.JSONDecodeError, ProtocolError) as error:
            last_error = error
            time.sleep(0.1)
    raise ProtocolError(f"managed app-server did not become healthy after restart: {last_error}")


def doctor(socket_path, timeout):
    codex = resolve_codex_binary()
    socket_exists = Path(socket_path).exists()
    repaired = False
    try:
        client = connect_app_server(socket_path, timeout, repair=False)
    except (OSError, socket.timeout, json.JSONDecodeError, ProtocolError):
        client = connect_app_server(socket_path, timeout, repair=True)
        repaired = True
    try:
        result = client.request("thread/list", {"limit": 1, "archived": False})
        print(json.dumps({
            "status": "healthy",
            "codex": codex,
            "socket": str(socket_path),
            "socketExisted": socket_exists,
            "repaired": repaired,
            "protocol": "initialized",
            "threadList": isinstance(result.get("data", []), list),
        }, separators=(",", ":")))
    finally:
        client.close()


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List saved and live Codex threads")
    parser.add_argument("--find", help="List threads matching an ID, name, preview, or cwd substring")
    parser.add_argument("--doctor", action="store_true", help="Repair and verify the managed app-server transport")
    parser.add_argument("--start", action="store_true", help="Create a headless app-server thread and start its first turn")
    parser.add_argument("--steer", action="store_true", help="Add input to the target's currently active turn")
    parser.add_argument("--auto", action="store_true", help="Select steer/start from the target state; no retries")
    parser.add_argument("--mid-turn", action="store_true", help="Deliver to an exact target mid-turn without interrupting; handle a completed-turn race")
    parser.add_argument("--exact", action="store_true", help="Require a complete thread UUID")
    parser.add_argument("--queue", action="store_true", help="Return a queued receipt without submitting while the target is active; caller retains the input")
    parser.add_argument("--session", help="Target thread ID, ID prefix, name, or unique substring")
    parser.add_argument("--message", help="Message to deliver")
    parser.add_argument("--prompt-file", help="Read the message from a UTF-8 file")
    parser.add_argument("--no-wait", action="store_true", help="Return after turn/start is accepted")
    parser.add_argument("--raw", action="store_true", help="Do not add the inter-session envelope")
    parser.add_argument("--from-session", help="Sender thread/session id for the inter-session envelope")
    parser.add_argument("--timeout", type=float, default=180, help="Reply timeout in seconds")
    parser.add_argument("--limit", type=int, default=200, help="Maximum threads to inspect")
    parser.add_argument("--socket", default=str(DEFAULT_SOCKET), help="Managed app-server Unix socket")
    parser.add_argument("--cwd", default=None, help="Working directory override for a new or resumed thread")
    parser.add_argument("--model", help="Model override for a new or resumed thread")
    parser.add_argument("--sandbox", choices=("read-only", "workspace-write", "danger-full-access"), help="Sandbox override for a new or resumed thread")
    parser.add_argument("--add-dir", action="append", default=[], help="Additional writable root for workspace-write; repeatable")
    parser.add_argument("--approval-policy", choices=("untrusted", "on-request", "never"), help="Approval-policy override for a new or resumed thread")
    parser.add_argument("--effort", choices=("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"), help="Reasoning-effort override; must be supported by the selected model")
    parser.add_argument("--output", help="Also write the final reply to this file")
    parser.add_argument("--metadata", help="Write thread/turn identifiers as JSON")
    parser.add_argument("--outcome", help="Write versioned delivery evidence and auth recovery details")
    args = parser.parse_args()
    if args.mid_turn and not args.exact:
        parser.error("--mid-turn requires --exact")
    if args.mid_turn and (args.queue or args.steer or args.auto):
        parser.error("--mid-turn cannot be combined with --queue, --steer or --auto")
    OUTCOME["file"] = args.outcome
    if args.queue and (args.steer or args.auto):
        parser.error("--queue cannot steer; drop --auto and --steer")

    if args.message is not None and args.prompt_file:
        parser.error("choose only one of --message or --prompt-file")
    message = args.message
    if args.prompt_file:
        message = Path(args.prompt_file).read_text(encoding="utf-8")
    if args.start and args.steer:
        parser.error("choose only one of --start or --steer")
    if args.start and message is None:
        parser.error("--start requires --message or --prompt-file")
    if args.steer and not (args.session and message is not None):
        parser.error("--steer requires --session and --message or --prompt-file")
    if args.add_dir and args.sandbox != "workspace-write":
        parser.error("--add-dir requires --sandbox workspace-write")
    if args.steer and (args.sandbox or args.add_dir or args.cwd or args.approval_policy):
        parser.error("execution settings cannot be changed during --steer; start a new turn")
    if not (args.doctor or args.list or args.find or args.start or (args.session and message is not None)):
        parser.error("use --doctor, --list, --find QUERY, --start with input, or --session TARGET with input")

    if args.doctor:
        doctor(args.socket, args.timeout)
        return

    client = connect_app_server(args.socket, args.timeout)
    try:
        threads = list_threads(client, args.limit)
        if args.find:
            print_threads([thread for thread in threads if matches(thread, args.find)])
        elif args.list:
            print_threads(threads)
        elif args.start:
            thread_id, turn_id, reply = create_thread(client, args, message)
            write_json(args.metadata, {"threadId": thread_id, "turnId": turn_id, "status": "accepted" if args.no_wait else "completed"})
            if reply is not None:
                emit_reply(reply, args.output)
        else:
            target = resolve_thread_exact(client, threads, args.session) if args.exact else resolve_thread_live(client, threads, args.session)
            if args.mid_turn:
                thread_id, turn_id, reply = deliver_midturn(client, target, args, message)
                write_json(args.metadata, {"threadId":thread_id,"turnId":turn_id,"status":"accepted" if args.no_wait else "completed"})
                if reply is not None:
                    emit_reply(reply, args.output)
                return
            if args.queue and status_type(target) == "active":
                record_outcome("not-delivered", "TARGET_BUSY", threadId=target["id"])
                write_json(args.metadata, {"threadId": target["id"], "status": "queued", "code": "TARGET_BUSY"})
                raise SystemExit(75)
            if args.steer or (args.auto and status_type(target) == "active"):
                thread_id, turn_id = steer_thread(client, target, message, args.raw, args.from_session)
                write_json(args.metadata, {"threadId": thread_id, "turnId": turn_id, "status": "steered"})
            else:
                thread_id, turn_id, reply = send_message(client, target, args, message, args.timeout, args.no_wait, args.raw, args.from_session)
                write_json(args.metadata, {"threadId": thread_id, "turnId": turn_id, "status": "accepted" if args.no_wait else "completed"})
                if reply is not None:
                    emit_reply(reply, args.output)
    finally:
        client.close()


def main():
    OUTCOME.clear()
    record_outcome("not-delivered", "REQUEST_REJECTED")
    try:
        return _main()
    finally:
        filename = OUTCOME.pop("file", None)
        if filename:
            write_json(filename, OUTCOME)


if __name__ == "__main__":
    try:
        main()
    except (OSError, socket.timeout, json.JSONDecodeError, ProtocolError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
