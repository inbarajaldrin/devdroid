"""Recovery tests never connect to a daemon or execute Codex."""

import io
import json
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_ask import ask, codex


AUTH = {"message": "fixture auth failure", "codexErrorInfo": "unauthorized"}
REFRESH_FAILURE = (
    "Your access token could not be refreshed because you have since logged out "
    "or signed in to another account. Please sign in again."
)


class FakeClock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeClient:
    def __init__(self, daemon):
        self.daemon = daemon
        self.socket_path = daemon.socket_path
        self._daemon_identity = {"pid": daemon.generation, "startedAt": 10 if daemon.generation == 1 else 30}
        self.sock = SimpleNamespace(settimeout=Mock())
        self.pending = []
        self.closed = False

    def request(self, method, params):
        self.daemon.requests.append((self._daemon_identity["pid"], method, params))
        if method == "turn/start":
            generation = self._daemon_identity["pid"]
            self.daemon.submissions.append((generation, params.copy()))
            error = self.daemon.error if generation == 1 or self.daemon.second_failure else None
            if self.daemon.reject and error:
                raise codex.RequestError(method, error)
            turn_id = f"turn-{len(self.daemon.submissions)}"
            thread_id = params["threadId"]
            for event in self.daemon.progress:
                self.pending.append({"method": event, "params": {
                    "threadId": thread_id, "turnId": turn_id,
                    "item": {"type": "commandExecution"},
                }})
            if error and self.daemon.error_event:
                self.pending.append({"method": "error", "params": {
                    "threadId": thread_id, "turnId": turn_id, "error": error,
                }})
                error = {"message": error["message"], "codexErrorInfo": None}
            if not error:
                self.pending.append({"method": "item/completed", "params": {
                    "threadId": thread_id, "turnId": turn_id,
                    "item": {"type": "agentMessage", "text": "fixture answer"},
                }})
            self.pending.append({"method": "turn/completed", "params": {
                "threadId": thread_id, "turn": {"id": turn_id,
                "status": "failed" if error else "completed", "error": error,
                "items": self.daemon.completed_items},
            }})
            return {"turn": {"id": turn_id}}
        if method == "thread/loaded/list":
            ids = ["target", "busy-one", "busy-two"]
            offset = int(params.get("cursor") or 0)
            return {"data": ids[offset:offset + 1], "nextCursor": str(offset + 1) if offset < 2 else None}
        if method in {"thread/read", "thread/resume"}:
            thread_id = params["threadId"]
            return {"thread": {"id": thread_id, "status": {"type": "active" if thread_id in self.daemon.busy else "idle"}}}
        if method == "thread/list":
            return {"data": [{"id": "target", "status": {"type": "idle"}}]}
        if method == "thread/start":
            return {"thread": {"id": "target"}}
        raise AssertionError(f"unexpected fake request: {method}")

    def events(self):
        if self.daemon.barrier and self._daemon_identity["pid"] == 1:
            self.daemon.barrier.wait(timeout=5)
        while self.pending:
            yield self.pending.pop(0)

    def close(self):
        self.closed = True


class FakeDaemon:
    def __init__(self, socket_path):
        self.socket_path = socket_path
        self.generation = 1
        self.error = AUTH.copy()
        self.busy = []
        self.requests = []
        self.submissions = []
        self.progress = []
        self.completed_items = []
        self.error_event = False
        self.second_failure = False
        self.reject = False
        self.healthy = True
        self.restart_code = 0
        self.barrier = None
        self.clients = []

    def connect(self, *_args, **_kwargs):
        if self.generation > 1 and not self.healthy:
            raise ConnectionRefusedError("fixture unhealthy")
        client = FakeClient(self)
        self.clients.append(client)
        return client

    def command(self, command, **_kwargs):
        if command != ["fixture-codex", "app-server", "daemon", "restart"]:
            raise AssertionError(f"unexpected command: {command}")
        if not self.restart_code:
            self.generation += 1
        return SimpleNamespace(returncode=self.restart_code)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(directory)
        self.socket_path = self.root / "control.sock"
        self.daemon = FakeDaemon(self.socket_path)
        self.clock = FakeClock()
        self.stderr = io.StringIO()
        self.stack.enter_context(redirect_stderr(self.stderr))
        self.stack.enter_context(patch.object(codex, "DEFAULT_SOCKET", self.socket_path))
        self.stack.enter_context(patch.object(codex, "connect_app_server", side_effect=self.daemon.connect))
        self.stack.enter_context(patch.object(codex, "resolve_codex_binary", return_value="fixture-codex"))
        self.command = self.stack.enter_context(patch.object(codex.subprocess, "run", side_effect=self.daemon.command))
        self.mtime = self.stack.enter_context(patch.object(codex, "auth_file_mtime", return_value=20))
        self.stack.enter_context(patch.object(codex.time, "monotonic", side_effect=self.clock.monotonic))
        self.stack.enter_context(patch.object(codex.time, "sleep", side_effect=self.clock.sleep))
        # Catch accidental use of the real app-server transport, even on failure paths.
        self.stack.enter_context(patch.object(codex.socket, "socket", side_effect=AssertionError("real socket forbidden")))
        codex.OUTCOME.clear()

    def send(self, target="target", **kwargs):
        return codex.start_turn(self.daemon.connect(), target, "fixture `$literal`\nbody", 0, False, True,
                                {"model": "fixture-model"}, **kwargs)

    def test_stale_idle_recovers_once_with_identical_input_and_same_thread(self):
        turn, reply = self.send()
        self.assertEqual(reply, "fixture answer")
        self.assertEqual(self.command.call_count, 1)
        self.assertEqual([g for g, _ in self.daemon.submissions], [1, 2])
        self.assertEqual(self.daemon.submissions[0][1], self.daemon.submissions[1][1])
        self.assertEqual(codex.OUTCOME["turnId"], turn)
        self.assertEqual(codex.OUTCOME["code"], "COMPLETED")
        self.assertEqual(codex.OUTCOME["recovery"], {
            "kind": "stale-daemon-auth", "action": "restarted", "retried": True})
        self.assertEqual(self.stderr.getvalue().count("auth recovery:"), 1)
        self.assertIn("after fixing the cause", self.stderr.getvalue())
        resumes = [params for generation, method, params in self.daemon.requests
                   if generation == 2 and method == "thread/resume"]
        self.assertEqual(resumes, [{"threadId": "target", "excludeTurns": True}])

    def test_stale_busy_refuses_names_all_threads_and_exact_command(self):
        self.daemon.busy = ["busy-one", "busy-two"]
        with self.assertRaisesRegex(codex.ProtocolError, "stale; busy threads: busy-one, busy-two;.*codex app-server daemon restart"):
            self.send()
        self.command.assert_not_called()
        self.assertEqual(len(self.daemon.submissions), 1)
        self.assertEqual(codex.OUTCOME["code"], "AUTH_DAEMON_BUSY")
        self.assertEqual(codex.OUTCOME["recovery"]["busyThreads"], self.daemon.busy)

    def test_failed_target_itself_active_prevents_restart(self):
        self.daemon.busy = ["target"]
        with self.assertRaisesRegex(codex.ProtocolError, "busy threads: target"):
            self.send()
        self.command.assert_not_called()

    def test_fresh_daemon_requires_login_without_restart_or_retry(self):
        self.mtime.return_value = 10
        with self.assertRaisesRegex(codex.ProtocolError, "run `codex login`"):
            self.send()
        self.command.assert_not_called()
        self.assertEqual(len(self.daemon.submissions), 1)
        self.assertEqual(codex.OUTCOME["recovery"]["kind"], "login-required")

    def test_quota_and_429_do_not_even_inspect_login(self):
        for error in ({"message": "Unauthorized", "codexErrorInfo": "usageLimitExceeded"},
                      {"message": "Unauthorized", "codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 429}}},
                      {"message": "HTTP 429: quota exceeded"}):
            self.daemon.error = error
            with self.assertRaises(codex.TurnError):
                self.send()
        self.command.assert_not_called()
        self.mtime.assert_not_called()

    def test_exact_refresh_wording_recovers_without_login(self):
        self.daemon.error = {"message": REFRESH_FAILURE}
        self.assertEqual(self.send()[1], "fixture answer")
        self.assertEqual(self.command.call_count, 1)

    def test_error_notification_retains_structured_classification(self):
        self.daemon.error_event = True
        self.assertEqual(self.send()[1], "fixture answer")
        self.assertEqual(self.command.call_count, 1)

    def test_turn_start_explicit_auth_rejection_is_safe(self):
        self.daemon.reject = True
        self.assertEqual(self.send()[1], "fixture answer")
        self.assertEqual(self.command.call_count, 1)

    def test_two_concurrent_calls_restart_once_and_both_retry_same_threads(self):
        self.daemon.barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(self.send, name) for name in ("target-a", "target-b")]
            self.assertEqual([future.result(timeout=5)[1] for future in futures], ["fixture answer"] * 2)
        self.assertEqual(self.command.call_count, 1)
        self.assertEqual(len(self.daemon.submissions), 4)
        for target in ("target-a", "target-b"):
            submissions = [params for _, params in self.daemon.submissions if params["threadId"] == target]
            self.assertEqual(submissions[0], submissions[1])

    def test_restart_not_healthy_stops_without_resending(self):
        self.daemon.healthy = False
        with self.assertRaisesRegex(codex.ProtocolError, "did not become healthy"):
            self.send()
        self.assertEqual(self.command.call_count, 1)
        self.assertEqual(len(self.daemon.submissions), 1)
        self.assertFalse(codex.OUTCOME["recovery"]["retried"])
        self.assertEqual(codex.OUTCOME["recovery"]["status"], "failed")
        self.assertEqual(codex.OUTCOME["code"], "AUTH_RECOVERY_FAILED")
        self.assertGreaterEqual(self.clock.now, 8)

    def test_failed_restart_is_not_repeated_by_another_caller(self):
        self.daemon.restart_code = 17
        with self.assertRaisesRegex(codex.ProtocolError, "failed.*exit 17"):
            self.send()
        with self.assertRaisesRegex(codex.ProtocolError, "already attempted"):
            self.send()
        self.assertEqual(self.command.call_count, 1)
        self.assertEqual(len(self.daemon.submissions), 2)

    def test_second_auth_failure_after_restart_stops(self):
        self.daemon.second_failure = True
        with self.assertRaises(codex.TurnError):
            self.send()
        self.assertEqual(self.command.call_count, 1)
        self.assertEqual(len(self.daemon.submissions), 2)
        self.assertEqual(codex.OUTCOME["code"], "TURN_FAILED")
        self.assertTrue(codex.OUTCOME["recovery"]["retried"])

    def test_work_before_auth_prevents_restart_and_resend(self):
        for progress in ("item/started", "item/agentMessage/delta", "turn/plan/updated"):
            self.daemon.progress = [progress]
            with self.assertRaisesRegex(codex.ProtocolError, "refusing to resend"):
                self.send()
        self.command.assert_not_called()
        self.mtime.assert_not_called()

    def test_work_in_final_turn_prevents_resend(self):
        self.daemon.completed_items = [{"type": "commandExecution"}]
        with self.assertRaisesRegex(codex.ProtocolError, "refusing to resend"):
            self.send()
        self.command.assert_not_called()

    def test_stream_disconnected_auth_is_not_safe_to_retry(self):
        self.daemon.error = {"message": "fixture", "codexErrorInfo": {
            "responseStreamDisconnected": {"httpStatusCode": 401}}}
        with self.assertRaisesRegex(codex.ProtocolError, "refusing to resend"):
            self.send()
        self.command.assert_not_called()

    def test_target_becomes_active_after_restart_is_not_steered(self):
        original = self.daemon.command

        def restart(*args, **kwargs):
            result = original(*args, **kwargs)
            self.daemon.busy = ["target"]
            return result

        self.command.side_effect = restart
        with self.assertRaisesRegex(codex.ProtocolError, "no longer idle"):
            self.send()
        self.assertEqual(self.command.call_count, 1)
        self.assertEqual(len(self.daemon.submissions), 1)

    def test_missing_auth_metadata_requires_login(self):
        self.mtime.side_effect = FileNotFoundError()
        with self.assertRaisesRegex(codex.ProtocolError, "codex login"):
            self.send()
        self.command.assert_not_called()

    def test_unknown_activity_refuses_restart(self):
        with patch.object(codex, "busy_threads", side_effect=codex.ProtocolError("cannot establish daemon activity")):
            with self.assertRaisesRegex(codex.ProtocolError, "cannot establish"):
                self.send()
        self.command.assert_not_called()

    def test_no_wait_explicit_auth_rejection_still_recovers_once(self):
        self.daemon.reject = True
        turn, reply = codex.start_turn(self.daemon.connect(), "target", "fixture", 0, True, True)
        self.assertIsNone(reply)
        self.assertEqual(codex.OUTCOME["turnId"], turn)
        self.assertEqual(codex.OUTCOME["code"], "TURN_ACCEPTED")
        self.assertEqual(codex.OUTCOME["status"], "accepted")
        self.assertEqual(self.command.call_count, 1)

    def test_unknown_acceptance_never_resends(self):
        client = self.daemon.connect()
        client.request = Mock(side_effect=ConnectionResetError("Unauthorized"))
        with self.assertRaises(ConnectionResetError):
            codex.start_turn(client, "target", "fixture", 0, False, True)
        self.command.assert_not_called()
        self.mtime.assert_not_called()
        self.assertEqual(codex.OUTCOME["kind"], "unknown")

    def test_auth_failure_on_custom_socket_never_restarts_managed_daemon(self):
        client = self.daemon.connect()
        client.socket_path = self.root / "custom.sock"
        with self.assertRaisesRegex(codex.ProtocolError, "custom socket"):
            codex.start_turn(client, "target", "fixture", 0, False, True)
        self.command.assert_not_called()

    def test_changed_login_alone_does_not_trigger_recovery(self):
        self.daemon.error = None
        self.assertEqual(self.send()[1], "fixture answer")
        self.command.assert_not_called()
        self.mtime.assert_not_called()

    def test_helper_typed_result_and_new_thread_recovery_keep_thread_id(self):
        outcome = self.root / "outcome.json"
        metadata = self.root / "metadata.json"
        with patch("sys.argv", ["helper", "--start", "--raw", "--message", "fixture",
                                "--outcome", str(outcome), "--metadata", str(metadata)]), redirect_stdout(io.StringIO()):
            codex.main()
        record = json.loads(outcome.read_text())
        self.assertEqual(record["code"], "COMPLETED")
        self.assertEqual(record["threadId"], "target")
        self.assertEqual(record["turnId"], json.loads(metadata.read_text())["turnId"])
        self.assertEqual(record["recovery"]["action"], "restarted")
        self.assertEqual(sum(method == "thread/start" for _, method, _ in self.daemon.requests), 1)

    def test_second_auth_failure_is_written_as_result_not_successful_recovery(self):
        self.daemon.second_failure = True
        outcome = self.root / "outcome.json"
        with patch("sys.argv", ["helper", "--session", "target", "--raw", "--message", "fixture",
                                "--outcome", str(outcome)]):
            with self.assertRaises(codex.TurnError):
                codex.main()
        record = json.loads(outcome.read_text())
        self.assertEqual(record["code"], "TURN_FAILED")
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["recovery"]["action"], "restarted")


class ClassificationTests(unittest.TestCase):
    def test_structured_errors_override_wording(self):
        for error, expected in [
            ({"codexErrorInfo": "unauthorized", "message": "fixture"}, "auth"),
            ({"codexErrorInfo": "internalServerError", "message": "Unauthorized"}, "other"),
            ({"codexErrorInfo": "other", "message": REFRESH_FAILURE}, "other"),
            ({"codexErrorInfo": "usageLimitExceeded", "message": REFRESH_FAILURE}, "quota"),
            ({"data": {"codexErrorInfo": "rateLimitExceeded"}, "message": "Unauthorized"}, "quota"),
            ({"data": {"httpStatusCode": 403}, "message": "fixture"}, "auth"),
            ({"data": {"httpStatusCode": 429}, "message": "Unauthorized"}, "quota"),
            ({"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 401}}}, "auth"),
            ({"code": -32000, "message": REFRESH_FAILURE}, "auth"),
            ({"codexErrorInfo": "unauthorized", "httpStatusCode": 429}, "quota"),
            ({"codexErrorInfo": "usageLimitExceeded", "data": {"message": "Unauthorized"}}, "quota"),
            ({"data": {"error": {"code": "insufficient_quota"}}, "message": "Unauthorized"}, "quota"),
        ]:
            with self.subTest(error=error):
                self.assertEqual(codex.failure_kind(error), expected)

    def test_narrow_wording_fallback(self):
        for text, expected in [(REFRESH_FAILURE, "auth"), ("Unauthorized: fixture", "auth"),
                               ("HTTP 401: Unauthorized", "auth"), ("unexpected status 403 Forbidden", "auth"),
                               ("fixture mentions Unauthorized later", "other"),
                               ("thread issue-429-fix", "quota"),
                               ("please sign in again", "other")]:
            with self.subTest(text=text):
                self.assertEqual(codex.failure_kind({"message": text}), expected)

    def test_rpc_request_retains_error_data(self):
        client = object.__new__(codex.AppServer)
        client.next_id, client.pending = 1, []
        client._send_json = Mock()
        error = {"code": -32000, "message": "fixture", "data": {"codexErrorInfo": "unauthorized"}}
        client.receive = Mock(return_value={"id": 1, "error": error})
        with self.assertRaises(codex.RequestError) as failure:
            client.request("turn/start", {})
        self.assertIs(failure.exception.error, error)

    def test_auth_file_uses_stat_only(self):
        with patch.object(Path, "stat", return_value=SimpleNamespace(st_mtime=20)) as stat, \
             patch.object(Path, "open", side_effect=AssertionError("credential read forbidden")), \
             patch("builtins.open", side_effect=AssertionError("credential read forbidden")):
            self.assertEqual(codex.auth_file_mtime(), 20)
        stat.assert_called_once()

    def test_peer_identity_uses_process_start_not_socket_time(self):
        peer = Mock()
        peer.getsockopt.return_value = 123
        client = SimpleNamespace(sock=peer)
        with patch.object(codex.sys, "platform", "darwin"), \
             patch.object(codex.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="Mon Nov 02 10:00:00 2026\n")) as run:
            identity = codex.daemon_identity(client)
        self.assertEqual(identity["pid"], 123)
        self.assertGreater(identity["startedAt"], 0)
        self.assertEqual(run.call_args.args[0], ["ps", "-p", "123", "-o", "lstart="])

    def test_peer_identity_timeout_is_a_controlled_error(self):
        client = SimpleNamespace(sock=Mock(getsockopt=Mock(return_value=123)))
        with patch.object(codex.sys, "platform", "darwin"), \
             patch.object(codex.subprocess, "run", side_effect=subprocess.TimeoutExpired("ps", 5)):
            with self.assertRaisesRegex(codex.ProtocolError, "cannot establish"):
                codex.daemon_identity(client)

    def test_failed_handshake_closes_its_socket(self):
        sock = Mock()
        sock.connect.side_effect = ConnectionRefusedError("fixture")
        with patch.object(codex.socket, "socket", return_value=sock):
            with self.assertRaises(ConnectionRefusedError):
                codex.AppServer("fixture.sock", 1)
        sock.close.assert_called_once()

    def test_stale_outcome_cannot_turn_helper_crash_into_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "outcome.json"
            destination.write_text(json.dumps({"version": 1, "kind": "accepted", "code": "COMPLETED"}))
            with patch("sys.argv", ["ask", "--codex", "--raw", "--session", "target", "--message", "fixture",
                                    "--outcome", str(destination)]), \
                 patch.object(ask, "ensure_codex_daemon"), \
                 patch.object(ask.subprocess, "run", return_value=SimpleNamespace(returncode=1)):
                with self.assertRaises(SystemExit):
                    ask.main()
            self.assertEqual(json.loads(destination.read_text())["kind"], "unknown")

    def test_wrapper_preserves_helper_recovery_and_result(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "outcome.json"
            record = {"version": 1, "kind": "accepted", "code": "COMPLETED", "threadId": "target",
                      "recovery": {"kind": "stale-daemon-auth", "action": "restarted", "retried": True}}

            def helper(command):
                self.assertEqual(command[command.index("--outcome") + 1], str(destination))
                destination.write_text(json.dumps(record))
                return SimpleNamespace(returncode=0)

            with patch("sys.argv", ["ask", "--codex", "--raw", "--session", "target", "--message", "fixture",
                                    "--outcome", str(destination)]), \
                 patch.object(ask, "ensure_codex_daemon"), patch.object(ask.subprocess, "run", side_effect=helper):
                with self.assertRaises(SystemExit) as done:
                    ask.main()
            self.assertEqual(done.exception.code, 0)
            self.assertEqual(json.loads(destination.read_text()), record)


if __name__ == "__main__":
    unittest.main()
