"""A persistent Claude JSON input stream, acknowledged only by replayed user frames."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import uuid
from observe_worker import write


def run(request):
    os.umask(0o077)
    directory = tempfile.mkdtemp(prefix="ask-worker-")
    address = str(Path(directory) / "input.sock")
    root = Path(request["receipt"])
    command = request["argv"]
    command[command.index("--output-format") + 1] = "stream-json"
    command += ["--input-format", "stream-json", "--verbose", "--replay-user-messages"]
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(address)
    server.listen(8)
    server.settimeout(.2)
    record = {"version": 1, "kind": "started", "code": "PROCESS_STARTED", "status": "spawned",
              "sessionId": request["sessionId"], "socket": address, "observerPid": os.getpid()}
    pending = {}
    lock = threading.RLock()
    output = open(request["output"], "w")
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, cwd=request["cwd"], text=True)
    except OSError as error:
        write(root, dict(record, kind="not-delivered", code="WORKER_SPAWN_FAILED", detail=str(error)))
        server.close()
        output.close()
        shutil.rmtree(directory)
        return
    record["pid"] = process.pid

    def submit(body, file):
        with lock:
            identity = str(uuid.uuid4())
            pending[identity] = file
            write(file, record)
            frame = {"type": "user", "uuid": identity, "session_id": record["sessionId"],
                     "message": {"role": "user", "content": body}, "parent_tool_use_id": None}
            process.stdin.write(json.dumps(frame) + "\n")
            process.stdin.flush()

    def read_output():
        for line in process.stdout:
            output.write(line)
            output.flush()
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            with lock:
                if frame.get("type") == "user" and frame.get("uuid") in pending:
                    file = pending.pop(frame["uuid"])
                    if frame.get("session_id"):
                        record["sessionId"] = frame["session_id"]
                    write(file, dict(record, kind="accepted", code="ENQUEUED", status="ready"))

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        submit(request["body"], root)
        while process.poll() is None:
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            with connection:
                connection.settimeout(5)
                try:
                    packet = json.loads(connection.makefile("rb").readline(1024 * 1024))
                    submit(packet["body"], Path(packet["receipt"]))
                    connection.sendall(b'{"submitted":true}\n')
                except (OSError, ValueError, KeyError) as error:
                    connection.sendall(json.dumps({"error": str(error)}).encode() + b"\n")
        reader.join(timeout=2)
    finally:
        with lock:
            for file in pending.values():
                write(file, dict(record, kind="unknown", code="WORKER_EXITED", status="failed",
                                 exitCode=process.poll()))
            current = json.loads(root.read_text())
            write(root, dict(current, closed=True, exitCode=process.poll()))
        server.close()
        output.close()
        shutil.rmtree(directory)


def deliver(receipt, body, file):
    record = json.loads(Path(receipt).read_text())
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(record["socket"])
        connection.sendall(json.dumps({"body": body, "receipt": file}).encode() + b"\n")
        response = json.loads(connection.makefile("rb").readline(65536))
        if not response.get("submitted"):
            raise RuntimeError(response.get("error", "Worker supplied no receipt"))
    return json.loads(Path(file).read_text())
