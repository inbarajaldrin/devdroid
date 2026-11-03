import importlib.util
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/claude_fork.py"
SPEC = importlib.util.spec_from_file_location("claude_fork", SCRIPT)
claude = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(claude)


class ClaudeForkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.claude_dir = self.root / ".claude"
        self.state = self.root / ".devdroid/fork-session/backups"
        self.cwd = str(self.root / "project with spaces-and-'quotes-$dollar")
        Path(self.cwd).mkdir()
        self.project = self.claude_dir / "projects/encoded-lossy-name"
        self.project.mkdir(parents=True)
        self.source = self.project / "original.jsonl"
        self.records = [self.message("root", None, "user"), self.message("answer", "root"),
                        self.message("dead-user", "answer", "user"), self.message("dead-answer", "dead-user"),
                        self.message("active-user", "answer", "user"), self.message("active-answer", "active-user")]
        self.save()
        self.patches = [patch.object(claude, "CLAUDE_DIR", self.claude_dir),
                        patch.object(claude, "BACKUP_ROOT", self.state)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def message(self, uid, parent, role="assistant", blocks=None):
        return {"type": role, "uuid": uid, "parentUuid": parent, "sessionId": "original",
                "cwd": self.cwd, "message": {"role": role, "content": blocks or [{"type": "text", "text": uid}]}}

    def save(self):
        self.source.write_text("".join(json.dumps(r) + "\n" for r in self.records), encoding="utf-8")

    def fork(self, **kwargs):
        original = self.source.read_bytes()
        result = claude.fork_session(self.source, **kwargs)
        self.assertEqual(original, self.source.read_bytes(), "source changed")
        self.assertNotEqual("original", result["id"])
        self.assertEqual(original, (Path(result["backup"]) / self.source.name).read_bytes())
        output = claude.read_records(Path(result["path"]))
        self.assertTrue(all(r["sessionId"] == result["id"] for r in output if "sessionId" in r))
        self.assertTrue(result["stats"]["chain_reached_root"])
        return result, output

    def test_midpoint_by_uuid(self):
        result, records = self.fork(cut_uuid="answer")
        self.assertEqual([r["uuid"] for r in records], ["root", "answer"])
        self.assertEqual(result["stats"]["leaf_uuid"], "answer")

    def test_midpoint_by_line(self):
        _, records = self.fork(line=2)
        self.assertEqual([r["uuid"] for r in records], ["root", "answer"])

    def test_abandoned_arm_by_uuid(self):
        _, records = self.fork(cut_uuid="dead-answer")
        self.assertEqual([r["uuid"] for r in records], ["root", "answer", "dead-user", "dead-answer"])

    def test_leaf_excludes_abandoned_arm(self):
        _, records = self.fork()
        self.assertEqual([r["uuid"] for r in records], ["root", "answer", "active-user", "active-answer"])

    def test_backup_namespaces_all_sidecars_and_verbatim_cwd(self):
        for kind, sidecar in claude.sidecars(self.source, "original"):
            sidecar.mkdir(parents=True)
            (sidecar / "state.txt").write_text(kind)
        result, _ = self.fork(cut_uuid="answer")
        self.assertEqual(result["cwd"], self.cwd)
        self.assertEqual(shlex.split(result["resume"]), ["cd", self.cwd, "&&", "claude", "--resume", result["id"]])
        for kind, sidecar in claude.sidecars(Path(result["path"]), result["id"]):
            self.assertEqual((sidecar / "state.txt").read_text(), kind)
            self.assertEqual((Path(result["backup"]) / "sidecars" / kind / "original/state.txt").read_text(), kind)
        second, _ = self.fork(cut_uuid="answer")
        self.assertNotEqual(result["backup"], second["backup"])

    def test_new_cwd_storage_and_records(self):
        target_cwd = self.root / "new working-dir"
        target_cwd.mkdir()
        result, records = self.fork(cut_uuid="answer", target_cwd=str(target_cwd))
        self.assertEqual(Path(result["path"]).parent.parent, self.claude_dir / "projects")
        self.assertNotEqual(Path(result["path"]).parent, self.project)
        self.assertTrue(all(r["cwd"] == str(target_cwd.resolve()) for r in records))

    def test_target_cwd_symlink_uses_physical_project(self):
        real_cwd = self.root / "physical"
        real_cwd.mkdir()
        link = self.root / "alias"
        link.symlink_to(real_cwd, target_is_directory=True)
        result, _ = self.fork(cut_uuid="answer", target_cwd=str(link))
        self.assertEqual(result["cwd"], str(real_cwd.resolve()))
        self.assertNotIn("alias", Path(result["path"]).parent.name)

    def test_backup_failure_creates_no_fork(self):
        before = list(self.project.iterdir())
        with patch.object(claude, "backup_original", side_effect=OSError("backup unavailable")):
            with self.assertRaises(OSError):
                claude.fork_session(self.source, cut_uuid="answer")
        self.assertEqual(before, list(self.project.iterdir()))

    def test_repair_collision_and_missing_parent(self):
        self.records.insert(1, {"type": "file-history-snapshot", "messageId": "answer"})
        self.records[2]["parentUuid"] = "missing"
        self.save()
        result, records = self.fork(cut_uuid="answer")
        self.assertEqual(records[1]["messageId"], None)
        self.assertEqual(records[2]["parentUuid"], "root")
        self.assertEqual(result["stats"]["parent_fixes"], 1)
        self.assertEqual(result["stats"]["collision_fixes"], 1)

    def test_rejects_unbalanced_tool_cut_before_writing(self):
        self.records[1]["message"]["content"] = [{"type": "tool_use", "id": "call-1", "name": "Read"}]
        self.save()
        with self.assertRaisesRegex(ValueError, "unresolved"):
            claude.fork_session(self.source, cut_uuid="answer")
        self.assertFalse(self.state.exists())

    def test_reask_form_balances_all_parallel_tools(self):
        self.records[1]["message"]["content"] = [
            {"type": "tool_use", "id": "form-1", "name": "AskUserQuestion"},
            {"type": "tool_use", "id": "parallel-1", "name": "Read"}]
        self.save()
        result, records = self.fork(reask_form="form-1")
        self.assertEqual(claude.pending_tools(records), {})
        self.assertEqual(records[-2]["parentUuid"], "answer")
        self.assertEqual(len(records[-2]["message"]["content"]), 2)
        self.assertEqual(result["stats"]["leaf_uuid"], records[-1]["uuid"])
        self.assertTrue(all(b["is_error"] for b in records[-2]["message"]["content"]))

    def test_cycles_and_bad_boundaries_fail(self):
        self.records[0]["parentUuid"] = "answer"
        self.save()
        with self.assertRaisesRegex(ValueError, "cycle"):
            claude.fork_session(self.source, cut_uuid="answer")
        for kwargs in ({"cut_uuid": "unknown"}, {"line": 0}, {"line": 99}):
            with self.assertRaises(ValueError):
                claude.fork_session(self.source, **kwargs)
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
