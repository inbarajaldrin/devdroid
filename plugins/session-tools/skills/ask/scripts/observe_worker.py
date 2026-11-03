"""Observe a background worker without turning process creation into acceptance."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def write(file, value):
    temporary = file.with_suffix(file.suffix + ".tmp")
    temporary.write_text(json.dumps(value))
    os.replace(temporary, file)


def main():
    request = json.load(sys.stdin)
    if request.get("stream"):
        from stream_worker import run
        return run(request)
    receipt = Path(request["receipt"])
    record = {"version": 1, "kind": "started", "code": "PROCESS_STARTED",
              "sessionId": request["sessionId"], "status": "spawned", "observerPid": os.getpid()}
    with open(request["output"], "w") as output:
        try:
            process = subprocess.Popen(request["argv"], stdin=subprocess.PIPE,
                                       stdout=output, stderr=output, cwd=request["cwd"])
        except OSError as error:
            record.update(kind="not-delivered", code="WORKER_SPAWN_FAILED",
                          status="failed", detail=str(error))
            write(receipt, record)
            return
        record["pid"] = process.pid
        write(receipt, record)
        process.communicate(request["body"].encode())
    record.update(kind="accepted" if process.returncode == 0 else "unknown",
                  code="COMPLETED" if process.returncode == 0 else "WORKER_EXITED",
                  status="completed" if process.returncode == 0 else "failed",
                  exitCode=process.returncode)
    try:
        result = json.loads(Path(request["output"]).read_text())
        if isinstance(result, dict) and isinstance(result.get("session_id"), str):
            record["sessionId"] = result["session_id"]
        if isinstance(result, dict) and result.get("is_error") is True:
            record.update(kind="unknown", code="WORKER_REPORTED_ERROR", status="failed")
    except (ValueError, OSError):
        pass
    write(receipt, record)


if __name__ == "__main__":
    main()
