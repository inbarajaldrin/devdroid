from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cq
import why

FIXTURE = Path(__file__).parent / "fixtures" / "rewind.jsonl"
DOCK_FIXTURE = FIXTURE.with_name("dock-topic.jsonl")
CLI = Path(cq.__file__)


class WhyTests(unittest.TestCase):
    def setUp(self):
        self.session = cq._resolve_jsonl_path(str(FIXTURE))

    def test_concrete_handles_and_token_boundaries(self):
        handles = why.extract_handles(
            'ValueError: missing widget\nsrc/widget.py `load_widget` "WidgetFactory" '
            'def validate_widget(x): class Widget: run pytest',
            ['python3 -m unittest tests.test_widget'])
        for token in ('ValueError', 'src/widget.py', 'load_widget', 'WidgetFactory',
                      'validate_widget', 'Widget', 'pytest', 'python3 -m unittest tests.test_widget'):
            self.assertIn(token, handles)
        self.assertFalse(why.mentions('OtherValueError', ['ValueError']))
        self.assertTrue(why.mentions('raise ValueError()', ['ValueError']))
        self.assertNotIn('command', why.extract_handles('{"command": "pytest"}'))
        self.assertIn('/repo/my folder/widget.py', why.extract_handles('"/repo/my folder/widget.py"'))

    def test_kept_revisions_and_final_evidence_survive_limit(self):
        result = why.resolve(self.session, '@1', [], 2, cq)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['revisions']), 2)
        self.assertEqual(result['last_statement']['line'], 12)
        self.assertEqual(result['evidence']['last_edit']['line'], 6)
        self.assertEqual(result['evidence']['last_passing_run']['kind'], 'fail-to-pass')
        self.assertEqual(result['evidence']['last_commit']['line'], 10)
        all_rows = why.resolve(self.session, '@1', [], 100, cq)['revisions']
        self.assertFalse({3, 4} & {r['line'] for r in all_rows})
        self.assertIn('user-correction', {r['kind'] for r in all_rows})
        self.assertIn('assistant-revision', {r['kind'] for r in all_rows})

    def test_addresses_and_explicit_terms(self):
        for address in ('1', '1-2', '@1', 'msg[1]', 'line:1'):
            result = why.resolve(self.session, address, ['schema', 'load_widget'], 20, cq)
            self.assertEqual(result['anchor']['line'], 1)
            self.assertEqual(result['handles'], ['schema', 'load_widget'])
        with self.assertRaises(ValueError):
            why.resolve(self.session, '@999', [], 20, cq)
        with self.assertRaises(ValueError):
            why.resolve(self.session, '999', [], 20, cq)

    def test_no_revision_and_dead_anchor_are_explicit(self):
        self.assertIn('no later revision found on the kept path',
                      why.resolve(self.session, '@14', [], 20, cq)['note'])
        result = why.resolve(self.session, '@4', [], 20, cq)
        self.assertTrue(result['anchor']['abandoned'])
        self.assertIn('Anchor is on an abandoned branch', result['note'])

    def test_actual_cli_alias_json_and_invalid_limit(self):
        command = [sys.executable, str(CLI), 'resolve', '--jsonl', str(FIXTURE), '--turn', '@1']
        run = subprocess.run(command + ['--json'], text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['last_statement']['line'], 12)
        run = subprocess.run(command + ['--limit', '0'], text=True, capture_output=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn('--limit must be at least 1', run.stderr)

    def test_statuses_require_evidence_of_success(self):
        for text, error, expected in [('done', False, None), ('FAILED', False, 'fail'),
                                      ('OK', True, 'fail'), ('0 passed, 1 failed', False, 'fail'),
                                      ('12 passed', False, 'pass'),
                                      ('Process exited with code 0', False, 'pass'),
                                      ('Process exited with code 2', False, 'fail')]:
            self.assertEqual(why.result_status({'text': text, 'is_error': error}), expected)

    def test_tool_line_anchors_containing_turn_claim_and_ranks_prose(self):
        session = cq._resolve_jsonl_path(str(DOCK_FIXTURE))
        result = why.resolve(session, '@2', [], 20, cq)
        self.assertEqual(result['anchor']['line'], 1)
        self.assertEqual(result['anchor']['turn_start_line'], 0)
        self.assertIn('elapsed time', result['anchor']['snippet'])
        self.assertIn('Dock', result['handles'])
        self.assertIn('reveal', result['handles'])
        self.assertFalse(set(result['handles']) & {'int', 'round', 'sum', 'Image', '/tmp/w0.png', '/dev/null'})
        self.assertTrue(any('only arms' in row['snippet'] for row in result['revisions'][:5]))

    def test_long_context_turn_uses_statement_nearest_the_tool_line(self):
        rows = [json.loads(line) for line in DOCK_FIXTURE.read_text().splitlines()]
        earlier = dict(rows[1], uuid='earlier', parentUuid=rows[0]['uuid'],
                       message={'content': 'The weather forecast looks clear.'})
        rows[1]['parentUuid'] = 'earlier'
        rows.insert(1, earlier)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'long-turn.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in rows))
            result = why.resolve(cq._resolve_jsonl_path(str(path)), '@3', [], 20, cq)
        self.assertEqual(result['anchor']['line'], 2)
        self.assertEqual(result['anchor']['turn'], 1)
        self.assertIn('elapsed time', result['anchor']['snippet'])

    def test_injections_and_tool_results_are_never_human_corrections(self):
        session = cq._resolve_jsonl_path(str(DOCK_FIXTURE))
        result = why.resolve(session, '@2', [], 100, cq)
        self.assertFalse(any(row['kind'] == 'user-correction' for row in result['revisions']))
        self.assertFalse(any('task-notification' in row['snippet'] or 'INTER-SESSION' in row['snippet']
                             for row in result['revisions']))
        self.assertTrue(any(row['role'] == 'tool' and row['line'] == 3 for row in result['revisions']))

    def test_gap_stops_before_unrelated_turns_and_later_topic_return(self):
        session = cq._resolve_jsonl_path(str(DOCK_FIXTURE))
        result = why.resolve(session, '@2', [], 100, cq)
        self.assertEqual(result['stopped']['gap_turns'], 3)
        self.assertEqual(result['stopped']['line'], 16)
        self.assertTrue(all(row['line'] < 16 for row in result['revisions']))
        self.assertEqual(result['last_statement']['line'], 9)

    def test_temp_writes_are_not_evidence_but_project_writes_are(self):
        session = cq._resolve_jsonl_path(str(DOCK_FIXTURE))
        result = why.resolve(session, '@2', [], 100, cq)
        self.assertEqual(result['evidence']['last_edit']['line'], 7)
        self.assertFalse(any(row['kind'] == 'file-write' and row['line'] in (2, 5)
                             for row in result['revisions']))
        self.assertFalse(why.project_file('/tmp/picture.png', '/project'))
        self.assertFalse(why.project_file('/project-sibling/lab.sh', '/project'))
        self.assertTrue(why.project_file('src/lab.sh', '/project'))
        self.assertTrue(why.project_file('/project/dev/lab.sh', '/project'))

    def test_shell_write_after_cd_outside_project_is_not_project_evidence(self):
        rows = [json.loads(line) for line in DOCK_FIXTURE.read_text().splitlines()]
        rows[7]['message']['content'] = [{'type': 'tool_use', 'name': 'Bash', 'id': 'fix',
                                       'input': {'command': 'cd /other && echo Dock > src/lab.sh'}}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'outside-project.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in rows))
            result = why.resolve(cq._resolve_jsonl_path(str(path)), '@2', [], 100, cq)
        self.assertIsNone(result['evidence']['last_edit'])

    def test_common_terms_and_code_noise_are_not_ranked_handles(self):
        anchor = dict(text='Dock reveal common after restart.', kind='text', role='assistant',
                      abandoned=False, injected=False)
        events = [dict(anchor, text='common setup conversation '+str(i)) for i in range(30)] + [anchor]
        handles = why.ranked_handles(anchor, '', [], events)
        self.assertNotIn('common', handles)
        self.assertIn('Dock', handles)
        for noise in ('int', 'round', 'Image.open', 'json', 'try', 'RGB', '3', 'x',
                      '/3', '/dev/null', '/tmp/log.txt', '/private/tmp/output.txt', 'mktemp -d', '$S/file.py'):
            self.assertFalse(why.useful_handle(noise), noise)
        self.assertTrue(why.useful_handle('ValueError'))
        handles = why.extract_handles('ROOT_PATH=/project; TMP_FILE=$(mktemp); echo $ROOT_PATH')
        self.assertNotIn('ROOT_PATH', handles)
        self.assertNotIn('TMP_FILE', handles)

    def test_revision_phrase_requires_a_topic_handle(self):
        session = cq._resolve_jsonl_path(str(DOCK_FIXTURE))
        # A custom handle absent from every message must not match "turns out".
        result = why.resolve(session, '@2', ['AbsentTopicToken'], 100, cq)
        self.assertFalse(any(row['kind'] in ('user-correction', 'assistant-revision')
                             for row in result['revisions']))
        self.assertIn('no later revision found on the kept path', result['note'])

    def test_irrelevant_passing_command_is_not_resolution_evidence(self):
        rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
        rows[1]['message']['content'][1]['input']['command'] = 'pytest irrelevant_test.py'
        rows[2]['message']['content'][0]['content'] = 'FAILED'
        rows[8]['message']['content'][0]['input']['command'] = 'pytest irrelevant_test.py'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.jsonl'
            path.write_text('\n'.join(json.dumps(r) for r in rows))
            session = cq._resolve_jsonl_path(str(path))
            result = why.resolve(session, '@5', ['load_widget'], 20, cq)
        self.assertIsNone(result['evidence']['last_passing_run'])

    def test_relative_shell_writes_resolve_against_session_cwd(self):
        rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
        rows[6]['message']['content'][1] = {
            'type': 'tool_use', 'id': 'fix', 'name': 'Bash',
            'input': {'command': 'echo fixed > src/widget.py'}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.jsonl'
            path.write_text('\n'.join(json.dumps(r) for r in rows))
            session = cq._resolve_jsonl_path(str(path))
            result = why.resolve(session, '@1', ['/repo/src/widget.py'], 20, cq)
        self.assertEqual(result['evidence']['last_edit']['line'], 6)

    def test_follow_sessions_requires_two_handles_later_same_cwd(self):
        rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
        with tempfile.TemporaryDirectory() as directory:
            candidates = []
            for name, date, cwd, text in [
                ('later', '2026-10-03', '/repo', 'src/widget.py ValueError'),
                ('earlier', '2026-09-01', '/repo', 'src/widget.py ValueError'),
                ('elsewhere', '2026-10-03', '/other', 'src/widget.py ValueError'),
                ('one', '2026-10-03', '/repo', 'ValueError')]:
                path = Path(directory) / (name + '.jsonl')
                entry = dict(rows[0], uuid=name, parentUuid=None, cwd=cwd,
                             timestamp=date + 'T00:00:00Z', message={'content': text})
                path.write_text(json.dumps(entry) + '\n')
                candidates.append(dict(self.session, path=path, session_id=name))
            with patch.object(cq, 'discover_sessions', return_value=candidates):
                found = why.follow_sessions(self.session, ['src/widget.py', 'ValueError'], cq, 20)
        self.assertEqual([r['session'] for r in found], ['later'])

    def test_codex_and_cursor_normalization(self):
        codex = [
            {'type': 'session_meta', 'payload': {'id': 'codex', 'cwd': '/repo'}},
            {'type': 'event_msg', 'payload': {'type': 'task_started'}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Fix ValueError'}]}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'ValueError is a cache problem.'}]}},
            {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Turns out ValueError is a validation problem.'}]}},
        ]
        cursor = [
            {'role': 'user', 'message': {'role': 'user', 'content': 'Fix ValueError'}},
            {'role': 'assistant', 'message': {'role': 'assistant', 'content': 'ValueError is a cache problem.'}},
            {'role': 'user', 'message': {'role': 'user', 'content': 'Actually the schema is wrong.'}},
            {'role': 'assistant', 'message': {'role': 'assistant', 'content': 'Turns out ValueError is a validation problem.'}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            for name, rows in [('codex', codex), ('cursor', cursor)]:
                path = Path(directory) / (name + '.jsonl')
                for index, entry in enumerate(rows):
                    entry['timestamp'] = '2026-10-01T10:%02d:00Z' % index
                path.write_text('\n'.join(json.dumps(e) for e in rows))
                session = cq._resolve_jsonl_path(str(path))
                result = why.resolve(session, '1', ['ValueError'], 20, cq)
                self.assertIn('validation problem', result['last_statement']['snippet'])
                self.assertFalse(result['anchor']['abandoned'])


if __name__ == '__main__':
    unittest.main()
