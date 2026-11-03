import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


class ObservedWorkerTests(unittest.TestCase):
    def setUp(self):
        inherited = {k: v for k, v in os.environ.items()
                     if not k.startswith(("CLAUDE_CODE_", "CODEX_", "ASK_"))}
        env = patch.dict(os.environ, inherited, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_real_cli_observes_slow_success_and_failure_without_accepting_spawn(self):
        script = Path(__file__).resolve().parents[1] / "scripts/ask.py"
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cli = root / "claude"
                cli.write_text("#!/usr/bin/env python3\nimport sys,time,json\nsys.stdin.read()\ntime.sleep(.5)\nprint(json.dumps({'type':'result','session_id':'actual-fixture-id'}))\nsys.exit(" + str(exit_code) + ")\n")
                cli.chmod(0o700)
                env = dict(os.environ, HOME=directory, PATH=directory + os.pathsep + os.environ["PATH"])
                receipt, typed, metadata = [root / name for name in ("receipt.json", "outcome.json", "meta.json")]
                result = subprocess.run(["python3", str(script), "--start", "--raw", "--no-wait",
                                         "--message", "fixture", "--output", str(root / "output"),
                                         "--worker-receipt", str(receipt), "--metadata", str(metadata),
                                         "--outcome", str(typed)], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(typed.read_text())["kind"], "started")
                self.assertEqual(json.loads(metadata.read_text())["workerReceipt"], str(receipt))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and json.loads(receipt.read_text())["kind"] == "started":
                    time.sleep(.02)
                final = json.loads(receipt.read_text())
                self.assertEqual(final["kind"], "accepted" if exit_code == 0 else "unknown")
                self.assertEqual(final["sessionId"], "actual-fixture-id")


    def test_stream_worker_receives_another_message_while_work_is_still_running(self):
        script = Path(__file__).resolve().parents[1] / "scripts/ask.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / "claude"
            cli.write_text("""#!/usr/bin/env python3
import sys,json,time,threading
from pathlib import Path
import os
work=Path(os.environ['HOME'])/'working'
work.write_text('yes')
for line in sys.stdin:
 frame=json.loads(line)
 if frame['message']['content']=='second':
  (Path(os.environ['HOME'])/'second-arrived').write_text(str(work.exists()))
 print(json.dumps(dict(frame,session_id='00000000-0000-4000-8000-000000000001')),flush=True)
 if frame['message']['content']=='close':break
""")
            cli.chmod(0o700)
            env = dict(os.environ, HOME=directory, PATH=directory + os.pathsep + os.environ["PATH"])
            receipt = root / "worker.json"
            def deliver(body, first=False):
                typed = root / (body + ".outcome")
                meta = root / (body + ".meta")
                args = (["--start", "--worker-stream", "--worker-receipt", str(receipt), "--output", str(root / "output")] if first
                        else ["--worker-input", str(receipt), "--worker-receipt", str(root / (body + ".receipt"))])
                done = subprocess.run(["python3", str(script), "--raw", "--no-wait", "--message", body,
                                       "--outcome", str(typed), "--metadata", str(meta), *args],
                                      env=env, capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)
                evidence = json.loads(typed.read_text())
                observed = Path(json.loads(meta.read_text())["workerReceipt"])
                deadline = time.monotonic() + 3
                while evidence["kind"] == "started" and time.monotonic() < deadline:
                    time.sleep(.02)
                    evidence = json.loads(observed.read_text())
                self.assertEqual(evidence["kind"], "accepted",
                                 {"receipt": evidence, "output": (root / "output").read_text()})
            try:
                deliver("first", True)
                deliver("second")
                self.assertEqual((root / "second-arrived").read_text(), "True")
            finally:
                if receipt.exists() and json.loads(receipt.read_text()).get("socket"):
                    deliver("close")
