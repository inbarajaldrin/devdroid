from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cq

FIXTURE = Path(__file__).parent / "fixtures" / "rewind.jsonl"


class ProvenanceTests(unittest.TestCase):
    def test_shell_path_fragments_resolve_without_basename_fallback(self):
        cases = [
            ('/home/x/scripts/lab.sh up', '/repo', True),
            ('cd /home/x && ./scripts/lab.sh up', '/repo', True),
            ('cd x && ./scripts/lab.sh up', '/home', True),
            ('L=~/x/scripts/lab.sh; $L up', '/repo', True),
            ('D=~/x; L=$D/scripts/lab.sh; "$L" up', '/repo', True),
            ('./scripts/lab.sh up', '/home/x', True),
            ('/other/lab.sh up', '/repo', False),
            ('cd /other && ./lab.sh up', '/repo', False),
            ('cat /home/x/scripts/lab.sh.bak', '/repo', False),
            ('cat /home/not-x/scripts/lab.sh', '/repo', False),
        ]
        for command, cwd, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(cq._shell_references(command, 'x/scripts/lab.sh', cwd), expected)
                self.assertTrue(cq._shell_references(command, 'lab.sh', cwd))

    def test_shell_writes_use_the_same_resolution(self):
        cases = [
            ('echo updated > /home/x/scripts/lab.sh', True),
            ('cd /home/x && echo updated > ./scripts/lab.sh', True),
            ('L=~/x/scripts/lab.sh; echo updated > "$L"', True),
            ('cd /home/x && cp old.sh ./scripts/lab.sh', True),
            ("cd /home/x && python3 -c \"open('scripts/lab.sh', 'w').write('hi')\"", True),
            ('echo updated > /other/lab.sh', False),
            ('cd /other && echo updated > ./lab.sh', False),
        ]
        for command, expected in cases:
            with self.subTest(command=command):
                self.assertEqual(cq._shell_writes(command, 'x/scripts/lab.sh', '/repo'), expected)
                self.assertTrue(cq._shell_writes(command, 'lab.sh', '/repo'))

    def test_shell_resolution_preserves_paths_with_spaces(self):
        command = 'cd "/home/my project" && cat "./scripts/lab.sh"'
        self.assertTrue(cq._shell_references(command, '/home/my project/scripts/lab.sh', '/repo'))
        command = 'cd "/home/my project" && cp old "./scripts/lab.sh"'
        self.assertTrue(cq._shell_writes(command, '/home/my project/scripts/lab.sh', '/repo'))

    def test_bare_filename_header_explains_broad_match(self):
        args = SimpleNamespace(path='dead_only.py', host=None, json=False,
                               limit_reads=10, show_mentions=False)
        with patch.object(cq, '_provenance_local_candidates', return_value=[
            (str(FIXTURE), 'test', 'claude-code')
        ]), contextlib.redirect_stdout(io.StringIO()) as output:
            cq.cmd_provenance(args)
        self.assertIn('Path: dead_only.py (filename-only match; any directory)', output.getvalue())

    def test_provenance_shell_path_gate_applies_to_claude_and_codex(self):
        for runtime in ('claude-code', 'codex'):
            rows = []
            commands = ['/other/lab.sh up', 'cd /home/x && ./scripts/lab.sh up',
                        'echo bad > /other/lab.sh', 'L=~/x/scripts/lab.sh; echo good > "$L"']
            for index, command in enumerate(commands):
                if runtime == 'codex':
                    row = {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'exec_command',
                           'arguments': json.dumps({'cmd': command})}}
                else:
                    row = {'type': 'assistant', 'message': {'content': [
                        {'type': 'tool_use', 'name': 'Bash', 'input': {'command': command}}]}}
                rows.append(dict(row, cwd='/repo', timestamp='2026-10-30T10:%02d:00Z' % index))
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'shell.jsonl'
                path.write_text('\n'.join(json.dumps(row) for row in rows))
                precise = cq._provenance_walk_jsonl(path, 'x/scripts/lab.sh', 'test', runtime)[0]
                broad = cq._provenance_walk_jsonl(path, 'lab.sh', 'test', runtime)[0]
            self.assertEqual((precise['shell'], precise['shell_writes']), (1, 1))
            self.assertEqual((broad['shell'], broad['shell_writes']), (2, 2))

    def test_shell_details_keep_physical_lines_and_branch_categories(self):
        prompt = 'Please inspect the lab script before changing anything.'
        commands = ['cat /repo/x/scripts/lab.sh', 'cd /repo/x && ./scripts/lab.sh check',
                    'cd /repo/x && ./scripts/lab.sh\nstatus']
        rows = [dict(uuid='root', parentUuid=None, type='user', sessionId='shell-detail', cwd='/repo',
                     message={'content': prompt}, timestamp='2026-10-30T10:00:00Z')]
        for index, command in enumerate(commands):
            rows.append(dict(uuid='cmd-'+str(index), parentUuid='cmd-1' if index == 2 else 'root',
                             type='assistant', timestamp='2026-10-30T10:0%d:00Z' % (index + 1),
                             message={'content': [{'type': 'tool_use', 'name': 'Bash', 'input': {'command': command}}]}))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shell.jsonl'
            # A malformed physical line must not shift the context drill address.
            path.write_text(json.dumps(rows[0])+'\nnot-json\n'+'\n'.join(json.dumps(r) for r in rows[1:]))
            records = cq._provenance_walk_jsonl(path, 'x/scripts/lab.sh', 'test', 'claude-code')
            kept, abandoned = records
            self.assertEqual((kept['shell'], abandoned['shell']), (2, 1))
            self.assertEqual((kept['first_command_line'], abandoned['first_command_line']), (3, 2))
            self.assertEqual(kept['first_command'], commands[1])
            self.assertEqual(abandoned['first_command'], commands[0])
            args = SimpleNamespace(path='x/scripts/lab.sh', host=None, json=False, limit_reads=10, show_mentions=False)
            with patch.object(cq, '_provenance_local_candidates', return_value=[
                (str(path), 'test', 'claude-code')
            ]), contextlib.redirect_stdout(io.StringIO()) as output:
                cq.cmd_provenance(args)
            text = output.getvalue()
            self.assertIn('Shell-touched', text)
            self.assertIn('On abandoned branches', text)
            self.assertEqual(text.count(prompt), 2)
            self.assertIn('cq context shell-detail @3', text)
            self.assertIn('cq context shell-detail @2', text)

    def test_codex_shell_details_are_compact_and_in_json(self):
        rows = [dict(type='session_meta', payload={'id': 'codex-detail', 'cwd': '/repo'}),
                dict(type='response_item', payload={'type': 'message', 'role': 'user',
                     'content': [{'text': 'Please inspect the lab script and explain it.'}]}),
                dict(type='response_item', timestamp='2026-10-30T10:00:00Z', payload={'type': 'function_call',
                     'name': 'exec_command', 'arguments': json.dumps({'cmd': 'cat x/scripts/lab.sh\n# '+('detail '*40)})})]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'codex.jsonl'
            path.write_text('\n'.join(json.dumps(r) for r in rows))
            args = SimpleNamespace(path='x/scripts/lab.sh', host=None, json=True, limit_reads=10, show_mentions=False)
            with patch.object(cq, '_provenance_local_candidates', return_value=[
                (str(path), 'test', 'codex')
            ]), contextlib.redirect_stdout(io.StringIO()) as output:
                cq.cmd_provenance(args)
        row = json.loads(output.getvalue())['shell_touched_by'][0]
        self.assertEqual(row['first_command_line'], 2)
        self.assertEqual(len(row['first_command']), 120)
        self.assertNotIn('\n', row['first_command'])
        self.assertEqual(row['intent'], 'Please inspect the lab script and explain it.')
        self.assertNotIn('_first_command_ts', row)

    def test_first_request_keeps_markdown_but_skips_injected_instructions(self):
        self.assertEqual(cq._provenance_intent('# AGENTS.md instructions\n\nInjected rules'), '')
        self.assertEqual(cq._provenance_intent('<skill>Injected skill instructions</skill>'), '')
        request = '# Task: inspect the lab script and explain its behavior.'
        self.assertEqual(cq._provenance_intent(request), request)
        wrapped = '━━━ INTER-SESSION MESSAGE ━━━\nFrom thread: sender\n━━━━━━━━━\n\n'+request
        self.assertEqual(cq._provenance_intent(wrapped), request)

    def test_merged_shell_rows_retain_the_earliest_command(self):
        row = cq._provenance_walk_jsonl(FIXTURE, 'src/widget.py', 'test', 'claude-code')[0]
        row.update(edits=0, reads=0, shell_writes=0, shell=1, first_command='cat later/src/widget.py',
                   first_command_line=7, _first_command_ts='2026-10-01T12:00:00Z')
        earlier = dict(row, first_command='cat earlier/src/widget.py', first_command_line=12,
                       _first_command_ts='2026-10-01T11:00:00Z')
        args = SimpleNamespace(path='src/widget.py', host=None, json=True, limit_reads=10, show_mentions=False)
        with patch.object(cq, '_provenance_local_candidates', return_value=[
            ('one.jsonl', 'test', 'claude-code'), ('two.jsonl', 'test', 'claude-code')
        ]), patch.object(cq, '_provenance_walk_jsonl', side_effect=[[row], [earlier]]), contextlib.redirect_stdout(io.StringIO()) as output:
            cq.cmd_provenance(args)
        result = json.loads(output.getvalue())['shell_touched_by'][0]
        self.assertEqual(result['shell'], 2)
        self.assertEqual(result['first_command'], earlier['first_command'])
        self.assertEqual(result['first_command_line'], 12)

    def test_counts_are_partitioned_and_author_times_use_edits(self):
        rows = cq._provenance_walk_jsonl(FIXTURE, "src/widget.py", "test", "claude-code")
        kept, dead = rows
        self.assertFalse(kept["abandoned"])
        self.assertTrue(dead["abandoned"])
        self.assertEqual((kept["edits"], dead["edits"]), (1, 1))
        self.assertEqual((dead["reads"], dead["mentions"], dead["shell_writes"]), (1, 1, 1))
        self.assertEqual(kept["first_authored_ts"], "2026-10-01T10:06:00")
        self.assertEqual(kept["last_authored_ts"], "2026-10-01T10:06:00")

    def test_dead_only_authorship_is_explicit_and_json_preserves_buckets(self):
        args = SimpleNamespace(path="dead_only.py", host=None, json=False,
                               limit_reads=10, show_mentions=False)
        candidates = [(str(FIXTURE), "test", "claude-code")]
        for as_json in (False, True):
            args.json = as_json
            output = io.StringIO()
            with patch.object(cq, "_provenance_local_candidates", return_value=candidates), contextlib.redirect_stdout(output):
                cq.cmd_provenance(args)
            if as_json:
                self.assertTrue(json.loads(output.getvalue())["authored_by"][0]["abandoned"])
            else:
                self.assertIn("On abandoned branches (rewound away)", output.getvalue())
                self.assertIn("only abandoned-branch edits exist", output.getvalue())
                self.assertNotIn("First authored:", output.getvalue())

    def test_cursor_and_codex_are_kept(self):
        for runtime in ("cursor", "codex"):
            rows = cq._provenance_walk_jsonl(FIXTURE, "src/widget.py", "test", runtime)
            self.assertEqual(len(rows), 1)
            self.assertFalse(rows[0]["abandoned"])
            self.assertEqual(rows[0]["edits"], 2)

    def test_inline_sidechain_edits_are_kept_even_off_main_chain(self):
        rows = cq._provenance_walk_jsonl(FIXTURE, "sidechain.py", "test", "claude-code")
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["abandoned"])
        self.assertEqual(rows[0]["edits"], 1)
        graph = cq._session_graph(FIXTURE)
        self.assertNotIn("sidechain-edit", graph["active"])
        self.assertEqual(graph["leaf"], "end")

    def test_older_remote_cq_falls_back_to_branch_aware_jsonl_walk(self):
        args = SimpleNamespace(path="dead_only.py", host=["test-host"], json=True,
                               limit_reads=10, show_mentions=False)
        legacy = {"authored_by": [{"session_id": "rewind-fixture"}]}
        output = io.StringIO()
        with patch.object(cq, "_provenance_local_candidates", return_value=[]), patch.object(
            cq, "_run_remote_cq", return_value=(0, json.dumps(legacy))
        ), patch.object(cq, "_provenance_remote_candidates", return_value=[
            (str(FIXTURE), "test-host", "claude")
        ]) as fallback, contextlib.redirect_stdout(output):
            cq.cmd_provenance(args)
        fallback.assert_called_once_with("test-host", "dead_only.py")
        row = json.loads(output.getvalue())["authored_by"][0]
        self.assertTrue(row["abandoned"])
        self.assertEqual(row["host"], "test-host")

    def test_branch_leaf_ignores_sidechains_and_traverses_metadata(self):
        rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
        rows += [dict(uuid="meta", parentUuid="end", type="system"),
                 dict(uuid="new-end", parentUuid="meta", type="assistant", message={"content": "done"}),
                 dict(uuid="side", parentUuid="dead-edit", type="assistant", isSidechain=True)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in rows))
            graph = cq._session_graph(path)
        self.assertEqual(graph["leaf"], "new-end")
        self.assertIn("meta", graph["active"])
        self.assertIn(4, graph["dead_lines"])
        self.assertNotIn(6, graph["dead_lines"])


if __name__ == "__main__":
    unittest.main()
