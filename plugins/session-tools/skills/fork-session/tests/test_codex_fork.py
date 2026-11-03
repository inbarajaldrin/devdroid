import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/codex_fork.py"
SPEC = importlib.util.spec_from_file_location("codex_fork", SCRIPT)
codex = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(codex)


class CodexForkTests(unittest.TestCase):
    def test_exact_inclusive_and_exclusive_rpc(self):
        for through, before, expected in (("turn-1", None, "lastTurnId"), (None, "turn-1", "beforeTurnId")):
            client = Mock()
            client.request.return_value = {"thread": {"id": "new-thread"}}
            with contextlib.redirect_stdout(io.StringIO()):
                codex.fork_thread(client, {"id": "source"}, [{"id": "turn-1", "status": "completed"}],
                                  through, before, "fork-session-test")
            self.assertEqual(client.request.call_args_list[0].args,
                             ("thread/fork", {"threadId": "source", "excludeTurns": True, expected: "turn-1"}))
            self.assertEqual(client.request.call_args_list[1].args,
                             ("thread/name/set", {"threadId": "new-thread", "name": "fork-session-test"}))

    def test_unknown_or_unfinished_turn_never_forks(self):
        for selected, status in (("unknown", "completed"), ("t", "inProgress"), ("t", "interrupted")):
            client = Mock()
            with self.assertRaises(RuntimeError):
                codex.fork_thread(client, {"id": "source"}, [{"id": "t", "status": status}], through=selected)
            client.request.assert_not_called()

    def test_connect_starts_official_daemon_once_and_retries(self):
        healthy = Mock()
        with patch.object(codex, "AppServer", side_effect=[OSError("stale socket"), healthy]) as connect:
            with patch.object(codex.subprocess, "run", return_value=Mock(returncode=0)) as run:
                self.assertIs(codex.connect_app_server("/tmp/test.sock"), healthy)
        self.assertEqual(connect.call_count, 2)
        self.assertEqual(run.call_args.args[0], ["codex", "app-server", "daemon", "start"])

    def test_healthy_connect_does_not_start_daemon(self):
        with patch.object(codex, "AppServer") as connect, patch.object(codex.subprocess, "run") as run:
            self.assertIs(codex.connect_app_server(), connect.return_value)
            run.assert_not_called()

    def test_failed_start_is_reported(self):
        with patch.object(codex, "AppServer", side_effect=OSError("missing")):
            with patch.object(codex.subprocess, "run", return_value=Mock(returncode=1, stderr="start failed")):
                with self.assertRaisesRegex(RuntimeError, "start failed"):
                    codex.connect_app_server()

    def test_find_text_is_read_only_and_reports_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            records = [
                {"type": "turn_context", "payload": {"turn_id": "t"}},
                {"type": "response_item", "payload": {"type": "message", "role": "user",
                    "content": [{"text": "Checkpoint alpha"}]}},
                {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "t"}}]
            path.write_text("".join(json.dumps(r) + "\n" for r in records))
            original = path.read_bytes()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                codex.print_matches(codex.read_turns(path), "ALPHA")
            self.assertIn("t  completed", output.getvalue())
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
