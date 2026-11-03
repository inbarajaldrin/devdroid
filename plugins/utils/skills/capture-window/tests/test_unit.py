import importlib.util
from pathlib import Path
import unittest
import json
import subprocess
import tempfile
import sys
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
spec = importlib.util.spec_from_file_location("capturelib", Path(__file__).parents[1] / "scripts" / "capturelib.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
TEMP_ROOT = c.cache_dir() / "tests" / "unit"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def temporary_directory():
    return tempfile.TemporaryDirectory(dir=TEMP_ROOT)


class Unit(unittest.TestCase):
    def test_geometry_with_window_manager(self):
        output = """Absolute upper-left X: 311
 Absolute upper-left Y: 207
 Relative upper-left X: 1
 Relative upper-left Y: 20
 Width: 423
 Height: 267
"""
        self.assertEqual(c.parse_geometry(output), dict(x=311, y=207, width=423, height=267))

    def test_negative_geometry(self):
        self.assertEqual(c.parse_geometry("Absolute upper-left X: -10\nAbsolute upper-left Y: -20\nWidth: 3\nHeight: 7")["x"], -10)

    def test_invalid_geometry(self):
        with self.assertRaises(c.Failure):
            c.parse_geometry("Relative upper-left X: 1")

    def test_unicode_properties(self):
        self.assertEqual(c.parse_properties('WM_NAME(STRING) = "old"\n_NET_WM_NAME(UTF8_STRING) = "Café [1]"\nWM_CLASS(STRING) = "test", "TestApp"'), ("Café [1]", "test / TestApp"))

    def test_tree_ignores_root_and_frames(self):
        self.assertEqual(c.tree_ids('Window id: 0x50 (root)\n 0x23 (has no name): () 4x4\n  0x24 "Fixture": ("fixture" "XTerm") 423x267+1+20 +311+207'), ["0x24"])

    def test_literal_matching_largest_and_exact(self):
        windows = [dict(id=1, title="Editor [1]", app="Edit", width=10, height=10),
                   dict(id=2, title="Editor [1]", app="Edit", width=20, height=20)]
        self.assertEqual(c.select(windows, "EDITOR [1]"), (windows[1], 2))
        with self.assertRaises(c.Failure) as caught:
            c.select(windows, "editor", exact=True)
        self.assertEqual(caught.exception.code, c.AMBIGUOUS)
        with self.assertRaises(c.Failure) as caught:
            c.select(windows, ".*")
        self.assertEqual(caught.exception.code, c.NO_MATCH)

    def test_class_matching(self):
        window = dict(id=1, title="changing title", app="StableClass", width=4, height=4)
        self.assertEqual(c.select([window], "stableclass")[0], window)

    def test_even_size_pads(self):
        self.assertEqual(c.even_size(423, 267), (424, 268))
        self.assertEqual(c.even_size(424, 268), (424, 268))

    def test_blank_spread(self):
        self.assertEqual(c.frame_stats(bytes([128] * 4096)), 0)
        self.assertGreater(c.frame_stats(bytes([0, 255] * 2048)), 100)

    def test_receipt_contract(self):
        data = c.receipt("shot")
        self.assertTrue({"tool", "window_title", "app", "id", "pixel_size", "front_app_before", "front_app_after", "frame_spread", "output_path"} <= data.keys())

    def test_exit_contract(self):
        self.assertEqual([c.PERMISSION, c.NO_MATCH, c.AMBIGUOUS, c.BLANK, c.CAPTURE, c.DISPLAY], [3, 4, 5, 6, 7, 8])

    def test_display_absent(self):
        with patch.dict(c.os.environ, {}, clear=True):
            with self.assertRaises(c.Failure) as caught:
                c.Linux(None).ready()
        self.assertEqual(caught.exception.code, c.DISPLAY)

    def test_root_stack_includes_popup_and_reparented_client(self):
        tree = '''Root window id: 0x1
     0x30 "popup": () 40x40+311+207
     0x20 (has no name): () 425x290+310+185
        0x21 "Fixture": ("fixture" "App") 423x267+1+22
     0x10 "bottom": () 100x100+0+0
'''
        self.assertEqual(c.root_stacking(tree, "0x21"), (["0x10", "0x20", "0x30"], "0x20"))

    def test_root_stack_absent_target_fails(self):
        with self.assertRaises(c.Failure):
            c.root_stacking('  0x10 "other": ()', "0x21")

    def test_mac_permission_failure_preserves_stage_diagnostic(self):
        with patch.object(c, "mac_helper", return_value=Path("helper")), patch.object(c, "run", return_value=json.dumps(dict(error="No grant", exit_code=3, stage_manager_enabled=True))):
            with self.assertRaises(c.Failure) as caught:
                c.Mac().windows()
        self.assertEqual(caught.exception.code, c.PERMISSION)
        self.assertTrue(caught.exception.details["stage_manager_enabled"])

    def test_missing_linux_tool(self):
        with patch.object(c.shutil, "which", return_value=None):
            with self.assertRaises(c.Failure) as caught:
                c.Linux(":123").ready()
        self.assertEqual(caught.exception.code, c.CAPTURE)

    def test_sequence_gap_rejected(self):
        with temporary_directory() as temp:
            (Path(temp) / "frame-000002.png").touch()
            with self.assertRaises(c.Failure):
                c.sequence_frames(Path(temp))

    def test_failure_preserves_existing_output(self):
        class Backend:
            def front(self):
                return "same app"

            def windows(self):
                return [dict(id=1, title="Fixture", app="App", width=20, height=20)]

            def shot(self, window, output):
                output.write_bytes(b"invalid pixels")
                raise c.Failure(c.BLANK, "Blank")

        args = c.parser().parse_args(["shot", "Fixture"])
        data = c.receipt("shot")
        with temporary_directory() as temp:
            path = Path(temp) / "existing.png"
            path.write_bytes(b"keep existing")
            with self.assertRaises(c.Failure):
                c.capture_one(Backend(), args, data, path)
            self.assertEqual(path.read_bytes(), b"keep existing")
            self.assertEqual(sorted(p.name for p in Path(temp).iterdir()), ["existing.png"])
        self.assertEqual(data["front_app_before"], data["front_app_after"])

    def test_argument_errors_emit_one_json_receipt(self):
        cli = Path(__file__).parents[1] / "scripts" / "capture"
        for args in (["record", "fixture", "--seconds", "nan", "-o", "out.mp4"],
                     ["encode", "frames", "-o", "out.mp4", "--fps", "0"],
                     ["shot", "fixture", "--activate"]):
            p = subprocess.run([str(cli), *args], capture_output=True, text=True)
            self.assertEqual(p.returncode, c.USAGE)
            self.assertEqual(json.loads(p.stdout)["exit_code"], c.USAGE)
            self.assertEqual(p.stderr, "")

    def test_corrupt_sidecar_preserves_existing_movie(self):
        with temporary_directory() as temp:
            folder = Path(temp)
            (folder / "frame-000001.png").touch()
            (folder / "frame-000001.json").write_text("{broken")
            movie = folder / "existing.mp4"
            movie.write_bytes(b"keep existing")
            cli = Path(__file__).parents[1] / "scripts" / "capture"
            p = subprocess.run([str(cli), "encode", str(folder), "-o", str(movie)], capture_output=True, text=True)
            self.assertEqual(p.returncode, c.CAPTURE)
            self.assertFalse(json.loads(p.stdout)["ok"])
            self.assertEqual(movie.read_bytes(), b"keep existing")

    def test_linux_blank_receipt_has_measured_pixels(self):
        backend = c.Linux(":123")
        geo = dict(x=311, y=207, width=423, height=267)
        window = dict(id="0x21", **geo)
        with patch.object(backend, "assert_visible", return_value=geo), patch.object(backend, "command", side_effect=["", "423 267", bytes([128] * 4096)]):
            with self.assertRaises(c.Failure) as caught:
                backend.shot(window, Path("frame.png"))
        self.assertEqual(caught.exception.code, c.BLANK)
        self.assertEqual(caught.exception.details["frame_spread"], 0)
        self.assertEqual(caught.exception.details["pixel_size"], [423, 267])

    def test_linux_record_classifies_blank_before_static(self):
        backend = c.Linux(":123")
        geo = dict(x=311, y=207, width=423, height=267)
        proc = Mock(returncode=0)
        proc.poll.return_value = 0
        for pixels, expected in ((bytes([128] * 4096), c.BLANK), (bytes([0, 255] * 2048), c.CAPTURE)):
            with temporary_directory() as temp:
                output = Path(temp) / "movie.mp4"
                output.with_suffix(".framemd5").write_text("# frame checksum\n0, 0, 0, 1, 4096, same\n0, 1, 1, 1, 4096, same\n")

                def decode(*args, **kwargs):
                    Path(args[-1]).write_bytes(pixels * 2)
                    blank = max(pixels) - min(pixels) <= 2
                    (output.parent / "quality.txt").write_text("lavfi.signalstats.YMIN=0\nlavfi.signalstats.YMAX=" + ("0" if blank else "255") + "\n")
                    return ""

                with patch.object(backend, "assert_visible", return_value=geo), patch.object(backend, "command", side_effect=decode), patch.object(c.subprocess, "Popen", return_value=proc) as spawn:
                    with self.assertRaises(c.Failure) as caught:
                        backend.record(dict(id="0x21", **geo), output, 1, 2)
                self.assertEqual(caught.exception.code, expected)
                if expected == c.BLANK:
                    self.assertNotIn("step", str(caught.exception))
                command = spawn.call_args[0][0]
                self.assertEqual(command[command.index("-i") + 1], ":123+311,207")

    def test_window_closing_during_related_frame_query_is_no_match(self):
        backend = c.Linux(":123")
        def info(x, y, w, h):
            return f"Absolute upper-left X: {x}\nAbsolute upper-left Y: {y}\nWidth: {w}\nHeight: {h}\nClass: InputOutput\nMap State: IsViewable\n"
        tree = '  0x31 "other frame": ()\n  0x20 "target frame": ()\n    0x21 "target": ()\n'
        replies = [info(311, 207, 423, 267), info(0, 0, 1200, 800), tree, info(320, 210, 50, 50),
                   c.Failure(c.CAPTURE, "X Error: 9: Bad Drawable: 0x31"), c.Failure(c.CAPTURE, "X Error: 3: Bad Window: 0x21")]
        with patch.object(backend, "command", side_effect=replies):
            with self.assertRaises(c.Failure) as caught:
                backend.assert_visible(dict(id="0x21"))
        self.assertEqual(caught.exception.code, c.NO_MATCH)


if __name__ == "__main__":
    unittest.main()


class BringForward(unittest.TestCase):
    def test_brings_the_app_forward_captures_and_returns(self):
        calls, attempts = [], []
        class Backend:
            def front(self): return {"app": "Editor", "id": "11"}
            def bring_forward(self, pid): calls.append(pid)
        def action():
            attempts.append(1)
            if len(attempts) == 1:
                raise c.Failure(c.BLANK, "shrunk", {"stage_manager_spoiled": True})
            return {"pixel_size": [1800, 1200]}
        result = {"process_identity": "42:0", "app": "Slicer", "warnings": []}
        args = type("Args", (), {"decision": ["stage_manager=bring-forward"]})()
        self.assertEqual(c.with_stage_policy(action, Backend(), args, result), {"pixel_size": [1800, 1200]})
        self.assertEqual(calls, [42, 11])
        self.assertTrue(result["brought_forward"])
        self.assertIn("No setting was changed", result["warnings"][-1])

    def test_an_app_hidden_before_the_shot_is_hidden_again(self):
        calls = []
        class Backend:
            def front(self): return {"app": "Editor", "id": "11"}
            def bring_forward(self, pid):
                calls.append(("forward", pid))
                return pid == 42
            def hide(self, pid): calls.append(("hide", pid))
        result = {"process_identity": "42:0", "app": "Slicer", "warnings": []}
        args = type("Args", (), {"decision": ["stage_manager=bring-forward"]})()
        attempts = []
        def action():
            attempts.append(1)
            if len(attempts) == 1:
                raise c.Failure(c.BLANK, "hidden", {"stage_manager_spoiled": True})
            return {"pixel_size": [1800, 1200]}
        c.with_stage_policy(action, Backend(), args, result)
        self.assertEqual(calls, [("forward", 42), ("forward", 11), ("hide", 42)])

