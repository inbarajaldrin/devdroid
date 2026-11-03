import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_ask import ask


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        inherited = {k: v for k, v in os.environ.items()
                     if not k.startswith(("CLAUDE_CODE_", "CODEX_THREAD_ID", "CODEX_SESSION_ID", "ASK_"))}
        env = patch.dict(os.environ, inherited, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_failed_socket_prose_does_not_decide_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            outcome = Path(directory) / 'outcome.json'
            row = {'name': 'issue-429-fix', 'sessionId': 'fixture-id', 'messagingSocketPath': '/fixture/socket'}
            with patch('sys.argv', ['ask.py', '--session', 'fixture-id', '--exact', '--raw', '--message', 'fixture', '--outcome', str(outcome)]), \
                 patch.object(ask, 'native_roster', return_value=[row]), \
                 patch.object(ask, 'native_self_identity', return_value=None), \
                 patch.object(ask, 'direct_socket_send', return_value=(False, 'quota Unauthorized 429 after a write')):
                with self.assertRaises(SystemExit):
                    ask.main()
            self.assertEqual(json.loads(outcome.read_text())['kind'], 'unknown')

    def test_missing_session_is_typed_before_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            outcome = Path(directory) / 'outcome.json'
            with patch('sys.argv', ['ask.py', '--session', 'fixture-id', '--exact', '--message', 'fixture', '--outcome', str(outcome)]), \
                 patch.object(ask, 'native_roster', return_value=[]), \
                 patch.object(ask, 'session_continuation', return_value=None):
                with self.assertRaises(SystemExit):
                    ask.main()
            record = json.loads(outcome.read_text())
            self.assertEqual(record['version'], 1)
            self.assertEqual(record['kind'], 'not-delivered')

    def test_real_failing_claude_start_is_not_accepted(self):
        import subprocess, sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'claude'
            binary.write_text('#!/bin/sh\necho "fixture worker failed" >&2\nexit 17\n')
            binary.chmod(0o700)
            env = {**os.environ, 'HOME': directory, 'PATH': directory + os.pathsep + os.environ['PATH']}
            record = root / 'outcome.json'
            result = subprocess.run([sys.executable, str(ask.SKILL_DIR/'scripts/ask.py'),
                                     '--start', '--message', 'fixture', '--no-wait',
                                     '--startup-timeout', '1', '--output', str(root/'worker.log'),
                                     '--outcome', str(record)], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            typed = json.loads(record.read_text())
            self.assertNotEqual(typed['kind'], 'accepted')
            self.assertEqual(typed['code'], 'WORKER_EXITED')
            self.assertIn('fixture worker failed', result.stderr)

    def test_queue_never_steers_an_active_codex_turn(self):
        from test_ask import codex
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory)/'metadata.json'
            with patch('sys.argv', ['helper', '--session', 'fixture', '--message', 'fixture',
                                   '--queue', '--metadata', str(metadata)]), \
                 patch.object(codex, 'connect_app_server', return_value=SimpleNamespace(close=lambda:None)), \
                 patch.object(codex, 'list_threads', return_value=[]), \
                 patch.object(codex, 'resolve_thread_live', return_value={'id':'fixture','status':{'type':'active'}}), \
                 patch.object(codex, 'steer_thread') as steer, patch.object(codex, 'send_message') as send:
                with self.assertRaises(SystemExit) as stopped:
                    codex.main()
                self.assertEqual(stopped.exception.code, 75)
                self.assertEqual(json.loads(metadata.read_text())['status'], 'queued')
                steer.assert_not_called()
                send.assert_not_called()
