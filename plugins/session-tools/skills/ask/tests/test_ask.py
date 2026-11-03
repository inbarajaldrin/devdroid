import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


ask = module("ask_under_test", ROOT / "scripts/ask.py")
codex = module("codex_under_test", ROOT / "scripts/codex/send_to_codex_session.py")


class AskTests(unittest.TestCase):
    def setUp(self):
        # Run as if outside any agent session, whatever session launched the tests.
        inherited = {k: v for k, v in os.environ.items()
                     if not k.startswith(("CLAUDE_CODE_", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "ASK_"))}
        patcher = patch.dict(os.environ, inherited, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_exact_uuid_follows_parked_job_and_posts_raw_without_reply_header(self):
        old = {'name':'old','sessionId':'old-id','parkedJobId':'job'}
        live = {'name':'moved/name','sessionId':'new-id','jobId':'job','messagingSocketPath':'/new'}
        with patch('sys.argv',['ask.py','--exact','--session','old-id','--raw','--message','literal input']), \
             patch.object(ask,'native_roster',return_value=[old,live]), \
             patch.object(ask,'_local_session_files',return_value=[old,live]), \
             patch.object(ask,'native_self_identity',return_value=None), \
             patch.object(ask,'direct_socket_send',return_value=(True,'sent')) as send:
            ask.main()
            self.assertEqual(send.call_args.args,('/new','literal input'))

    def test_dead_uuid_follows_a_fork_of_its_transcript(self):
        old = {'name':'old','sessionId':'old-id','pid':10}
        live = {'name':'fork','sessionId':'new-id','pid':20,'messagingSocketPath':'/fork'}
        def alive(pid, _signal):
            if pid == 10: raise ProcessLookupError()
        with patch.object(ask,'_local_session_files',return_value=[old,live]), \
             patch.object(ask.os,'kill',side_effect=alive), \
             patch.object(ask.subprocess,'run',return_value=SimpleNamespace(stdout='20 claude --fork-session --resume /project/old-id.jsonl')):
            self.assertEqual(ask.session_continuation('old-id',[live])['sessionId'],'new-id')

    def test_inspect_reports_continuation_and_compaction_without_delivering(self):
        with tempfile.TemporaryDirectory() as directory:
            transcript = Path(directory)/'.claude/projects/project/new-id.jsonl'
            transcript.parent.mkdir(parents=True)
            transcript.write_text('{"type":"compact_boundary","timestamp":"2026-01-01T00:00:00Z"}\n')
            old={'name':'old','sessionId':'old-id','parkedJobId':'job'}
            new={'name':'new','sessionId':'new-id','jobId':'job'}
            from contextlib import redirect_stdout
            import io
            output=io.StringIO()
            with patch.dict(os.environ,{'HOME':directory}), \
                 patch('sys.argv',['ask.py','--exact','--inspect','--session','old-id']), \
                 patch.object(ask,'native_roster',return_value=[old,new]), \
                 patch.object(ask,'_local_session_files',return_value=[]), \
                 patch.object(ask,'direct_socket_send') as send, redirect_stdout(output):
                ask.main()
            result=json.loads(output.getvalue());self.assertEqual(result['sessionId'],'new-id');self.assertGreater(result['compactedAt'],0);send.assert_not_called()

    def test_exact_uuid_does_not_collapse_to_duplicate_name(self):
        sid = "11111111-1111-4111-8111-111111111111"
        rows = [{"name": "plan/47", "sessionId": sid, "messagingSocketPath": "/first"},
                {"name": "plan/47", "sessionId": "22222222-2222-4222-8222-222222222222", "messagingSocketPath": "/second"}]
        with patch("sys.argv", ["ask.py", "--session", sid, "--exact", "--raw", "--message", "hello"]), \
                patch.object(ask, "native_roster", return_value=rows), \
                patch.object(ask, "native_self_identity", return_value=None), \
                patch.object(ask, "direct_socket_send", return_value=(True, "sent")) as send:
            ask.main()
        self.assertEqual(send.call_args.args[0], "/first")

    def test_exact_missing_and_broken_socket_refuse_without_relay(self):
        sid = "11111111-1111-4111-8111-111111111111"
        for rows in [[], [{"name": "x", "sessionId": sid, "messagingSocketPath": "/broken"}]]:
            with patch("sys.argv", ["ask.py", "--session", sid, "--exact", "--raw", "--message", "hello"]), \
                    patch.object(ask, "native_roster", return_value=rows), \
                    patch.object(ask, "native_self_identity", return_value=None), \
                    patch.object(ask, "direct_socket_send", return_value=(False, "broken")), \
                    patch.object(ask, "native_relay_send") as relay:
                with self.assertRaises(SystemExit):
                    ask.main()
                relay.assert_not_called()

    def test_standing_name_never_matches_a_substring(self):
        with patch("sys.argv", ["ask.py", "--session", "jobs", "--ensure", "--raw", "--message", "hi"]), \
                patch.object(ask, "native_roster", return_value=[{"name": "jobs-backup", "sessionId": "other"}]), \
                patch.object(ask, "claude_worker") as worker, \
                patch.object(ask, "direct_socket_send") as send:
            ask.main()
            self.assertEqual(worker.call_args.args[0].session, "jobs")
            send.assert_not_called()

    def test_standing_name_uses_live_roster_uuid_not_stale_duplicate_file(self):
        with patch("sys.argv", ["ask.py", "--session", "jobs", "--ensure", "--raw", "--message", "hi"]), \
                patch.object(ask, "native_roster", return_value=[{"name": "jobs", "sessionId": "current"}]), \
                patch.object(ask, "native_self_identity", return_value=None), \
                patch.object(ask, "_local_session_files", return_value=[{"name": "jobs", "sessionId": "stale", "messagingSocketPath": "/wrong"}, {"name": "jobs", "sessionId": "current", "messagingSocketPath": "/right"}]), \
                patch.object(ask, "direct_socket_send", return_value=(True, "sent")) as send:
            ask.main()
            self.assertEqual(send.call_args.args[0], "/right")

    def test_remote_codex_reenters_ask_before_any_local_daemon_work(self):
        args = ["ask.py", "--codex", "--host", "box", "--session", "thread", "--auto", "--message", "`$X`\n$(x)"]
        reply = SimpleNamespace(returncode=0, stdout=json.dumps({"stdout": "", "stderr": "accepted", "metadata": None}), stderr="")
        with patch("sys.argv", args), patch.object(ask.subprocess, "run", return_value=reply) as run, \
                patch.object(ask, "ensure_codex_daemon") as daemon:
            with self.assertRaises(SystemExit) as done:
                ask.main()
            self.assertEqual(done.exception.code, 0)
            daemon.assert_not_called()
        invocation = run.call_args
        self.assertEqual(invocation.args[0][:1], ["ssh"])
        packet = json.loads(invocation.kwargs["input"])
        self.assertEqual(packet["body"], "`$X`\n$(x)")
        self.assertIn("--auto", packet["argv"])
        self.assertNotIn("--host", packet["argv"])
        self.assertNotIn("$(x)", invocation.args[0][-1])

    def test_remote_metadata_is_returned_to_local_path(self):
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "meta.json"
            reply = SimpleNamespace(returncode=0, stdout=json.dumps({"stdout": "", "stderr": "", "metadata": {"threadId": "new"}}), stderr="")
            with patch("sys.argv", ["ask.py", "--codex", "--host", "box", "--start", "--message", "hi", "--metadata", str(metadata)]), \
                    patch.object(ask.subprocess, "run", return_value=reply):
                with self.assertRaises(SystemExit):
                    ask.main()
            self.assertEqual(json.loads(metadata.read_text()), {"threadId": "new"})

    def test_claude_home_and_standing_use_stock_cli_through_ask(self):
        with tempfile.TemporaryDirectory() as directory:
            for start in [False, True]:
                args = SimpleNamespace(session=None if start else "plan/47", start=start, cwd=directory,
                                       no_wait=True, output=str(Path(directory) / "out"), metadata=str(Path(directory) / "meta"))
                with patch.object(ask.subprocess, "Popen", return_value=SimpleNamespace(pid=1234)) as popen:
                    ask.claude_worker(args, "hello")
                command = popen.call_args.args[0]
                self.assertIn("--session-id" if start else "--resume", command)
                self.assertEqual(json.loads(Path(args.metadata).read_text())["status"], "spawned")

    def test_codex_auto_steers_active_or_resumes_idle_once(self):
        for status in ["active", "idle", "notLoaded"]:
            client = SimpleNamespace(close=lambda: None)
            with patch("sys.argv", ["helper.py", "--session", "exact", "--message", "hi", "--auto", "--no-wait"]), \
                    patch.object(codex, "connect_app_server", return_value=client), \
                    patch.object(codex, "list_threads", return_value=[]), \
                    patch.object(codex, "resolve_thread_live", return_value={"id": "exact", "status": {"type": status}}), \
                    patch.object(codex, "steer_thread", return_value=("exact", "turn")) as steer, \
                    patch.object(codex, "send_message", return_value=("exact", "turn", None)) as send:
                codex.main()
                self.assertEqual(steer.call_count, int(status == "active"))
                self.assertEqual(send.call_count, int(status != "active"))

    def test_codex_timeout_default_and_explicit_override_are_preserved(self):
        for flags, timeout in [([], "0"), (["--timeout", "7"], "7")]:
            with patch("sys.argv", ["ask.py", "--codex", "--list", *flags]), \
                    patch.object(ask, "ensure_codex_daemon"), \
                    patch.object(ask.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
                with self.assertRaises(SystemExit) as done:
                    ask.main()
                self.assertEqual(done.exception.code, 0)
                command = run.call_args.args[0]
                self.assertEqual(command[command.index("--timeout") + 1], timeout)

    def test_claude_relay_default_is_180_and_failed_dispatch_never_retries(self):
        for flags, timeout in [([], 180), (["--timeout", "7"], 7)]:
            rows = [{"name": "peer", "sessionId": "peer-uuid"}]
            with patch("sys.argv", ["ask.py", "--session", "peer", "--message", "hi", *flags]), \
                    patch.object(ask, "native_roster", return_value=rows), \
                    patch.object(ask, "native_self_identity", return_value=None), \
                    patch.object(ask, "_local_session_files", return_value=[]), \
                    patch.object(ask, "native_relay_send", return_value=(False, "quota")) as relay:
                with self.assertRaisesRegex(SystemExit, "quota"):
                    ask.main()
                self.assertEqual(relay.call_count, 1)
                self.assertEqual(relay.call_args.kwargs["timeout"], timeout)

    def test_roster_failure_does_not_spawn_a_replacement(self):
        with patch("sys.argv", ["ask.py", "--session", "jobs", "--ensure", "--message", "hi"]), \
                patch.object(ask, "native_roster", return_value=None), \
                patch.object(ask, "claude_worker") as worker:
            with self.assertRaisesRegex(SystemExit, "could not read"):
                ask.main()
            worker.assert_not_called()

    def test_remote_failure_and_equals_options_do_not_retry_or_leak_local_paths(self):
        args = ["ask.py", "--codex", "--host=box", "--session=thread", "--raw", "--message=hi",
                "--metadata=/local/metadata.json", "--output=/local/output.txt"]
        reply = SimpleNamespace(returncode=1, stdout=json.dumps({"stdout": "", "stderr": "ERROR: quota", "metadata": None, "output": None}), stderr="")
        with patch("sys.argv", args), patch.object(ask.subprocess, "run", return_value=reply) as run:
            with self.assertRaises(SystemExit) as done:
                ask.main()
            self.assertEqual(done.exception.code, 1)
            self.assertEqual(run.call_count, 1)
        packet = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(packet["argv"], ["--codex", "--session=thread", "--raw"])
        self.assertEqual(packet["body"], "hi")
        self.assertTrue(packet["metadata"] and packet["output"])

    def test_remote_bootstrap_discovers_independent_ask_and_transfers_files(self):
        # Run the real bootstrap against a fake ask executable, never SSH or an agent.
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            fake = home / "independent ask.py"
            fake.write_text('''import json,sys
from pathlib import Path
a=sys.argv[1:]
value=Path(a[a.index("--prompt-file")+1]).read_text()
Path(a[a.index("--metadata")+1]).write_text(json.dumps({"threadId":"exact-thread","status":"accepted"}))
Path(a[a.index("--output")+1]).write_text("answer:"+value)
print(json.dumps({"prompt":value,"argv":a}))
''')
            env = {**os.environ, "HOME": directory, "TMPDIR": directory}
            env.pop("ASK_SCRIPT", None); env.pop("CLAUDE_PLUGIN_ROOT", None)
            packet = {"argv": ["--codex", "--session", "exact-thread", "--raw"], "body": "`$HOME` $(literal)\nnext", "metadata": True, "output": True}
            for location in ["override", "plugin", "user"]:
                current = dict(env)
                if location == "override":
                    current["ASK_SCRIPT"] = str(fake)
                else:
                    root = home / location
                    dependency = root / ("skills/ask/scripts/ask.py" if location == "plugin" else ".claude/skills/ask/scripts/ask.py")
                    dependency.parent.mkdir(parents=True, exist_ok=True)
                    dependency.write_text(fake.read_text())
                    if location == "plugin": current["CLAUDE_PLUGIN_ROOT"] = str(root)
                    else: current["HOME"] = str(root)
                result = subprocess.run([sys.executable, "-c", ask.REMOTE_BOOTSTRAP], input=json.dumps(packet), capture_output=True, text=True, env=current)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt = json.loads(result.stdout)
                self.assertEqual(receipt["metadata"]["threadId"], "exact-thread")
                self.assertEqual(receipt["output"], "answer:" + packet["body"])
                self.assertEqual(json.loads(receipt["stdout"])["prompt"], packet["body"])
                self.assertFalse(any(home.glob("ask-worker-*")))
            env["ASK_SCRIPT"] = str(home / "missing.py")
            result = subprocess.run([sys.executable, "-c", ask.REMOTE_BOOTSTRAP], input=json.dumps(packet), capture_output=True, text=True, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ERROR: remote /ask missing", result.stderr)

    def test_remote_bootstrap_finds_the_installed_session_tools_plugin_without_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            provider=root/'provider/skills/ask/scripts/ask.py'
            provider.parent.mkdir(parents=True)
            provider.write_text('print("registry dependency")\n')
            registry=root/'.claude/plugins/installed_plugins.json'
            registry.parent.mkdir(parents=True)
            registry.write_text(json.dumps({'plugins':{'session-tools@devdroid':[{'installPath':str(root/'provider')}]}}))
            env={**os.environ,'HOME':directory};env.pop('CLAUDE_PLUGIN_ROOT',None);env.pop('ASK_SCRIPT',None)
            request={'argv':['--list'],'body':None,'metadata':False,'output':False}
            result=subprocess.run([sys.executable,'-c',ask.REMOTE_BOOTSTRAP],input=json.dumps(request),text=True,capture_output=True,env=env)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['stdout'],'registry dependency\n')


if __name__ == "__main__":
    unittest.main()
