"""Review regressions: fake tools and settings, no personal windows or settings."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import hashlib
import unittest
from unittest.mock import Mock, patch

from test_unit import c, temporary_directory


class Review(unittest.TestCase):
    def test_B2_setup_saved_answers(self):
        with temporary_directory() as folder:
            env = dict(os.environ, CAPTURE_WINDOW_SETTINGS=str(Path(folder) / "settings.json"))
            cli = Path(c.__file__).with_name("capture")
            p = subprocess.run([str(cli), "setup", "--set", "stage_manager=wait", "--show"], env=env, text=True, capture_output=True)
            self.assertEqual(p.returncode, 0, p.stdout)
            self.assertEqual(json.loads(p.stdout)["settings"]["stage_manager"], "wait")
            self.assertEqual(json.loads((Path(folder) / "settings.json").read_text())["stage_manager"], "wait")

    def test_B1_stage_wait_resumes_and_reminds(self):
        backend = Mock()
        backend.check.side_effect = [dict(stage_manager_enabled=True), dict(stage_manager_enabled=False)]
        action = Mock(side_effect=[c.Failure(c.BLANK, "Shrunk", dict(stage_manager_spoiled=True)), dict(pixel_size=[20, 20])])
        args = c.parser().parse_args(["shot", "fixture"])
        result = c.receipt("shot")
        with patch.object(c, "load_settings", return_value=dict(stage_manager="wait"), create=True), patch.object(c.time, "sleep"), contextlib.redirect_stderr(io.StringIO()) as output:
            value = c.with_stage_policy(action, backend, args, result)
        self.assertEqual(value["pixel_size"], [20, 20])
        self.assertIn("Control Centre", output.getvalue())
        self.assertIn("back on", output.getvalue())
        self.assertTrue(result["stage_manager_waited"])

    def test_B1_report_never_polls_or_asks(self):
        backend = Mock()
        action = Mock(side_effect=c.Failure(c.BLANK, "Thumbnail", dict(stage_manager_spoiled=True)))
        args = c.parser().parse_args(["shot", "fixture"])
        with patch.object(c, "load_settings", return_value=dict(stage_manager="report")), patch("builtins.input", side_effect=AssertionError("Must not re-ask")):
            with self.assertRaises(c.Failure) as caught:
                c.with_stage_policy(action, backend, args, c.receipt("shot"))
        self.assertEqual(caught.exception.code, c.BLANK)
        backend.check.assert_not_called()

    def test_B1_wait_timeout_reminds_and_preserves_failure(self):
        backend = Mock()
        backend.check.return_value = dict(stage_manager_enabled=True)
        action = Mock(side_effect=c.Failure(c.BLANK, "Thumbnail", dict(stage_manager_spoiled=True)))
        args = c.parser().parse_args(["shot", "fixture", "--stage-timeout", "1"])
        result = c.receipt("shot")
        with patch.object(c, "load_settings", return_value=dict(stage_manager="wait")), patch.object(c.time, "monotonic", side_effect=[0, 2]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(c.Failure) as caught:
                c.with_stage_policy(action, backend, args, result)
        self.assertEqual(caught.exception.code, c.BLANK)
        self.assertIn("back on", result["warnings"][-1])

    def test_B2_ask_is_saved_and_uses_one_run_answer(self):
        args = c.parser().parse_args(["shot", "fixture"])
        with patch.object(c, "load_settings", return_value=dict(stage_manager="ask")):
            with self.assertRaises(c.Failure) as caught:
                c.ruling("stage_manager", args, required=True)
            self.assertEqual(caught.exception.details["decision_required"], "stage_manager")
            args.decision = ["stage_manager=report"]
            self.assertEqual(c.ruling("stage_manager", args, required=True), "report")

    def test_film_session_restores_original_false_on_child_failure(self):
        calls = []
        def setting(*args):
            calls.append(args)
            return "0\n"
        result = c.receipt("film-session")
        with patch.object(c.sys.stdin, "isatty", return_value=True), patch.object(c.sys.stdout, "isatty", return_value=True), patch.object(c.subprocess, "run", return_value=Mock(returncode=9)):
            with self.assertRaises(c.Failure):
                c.film_session(["owned-command"], settings_command=setting, result=result)
        self.assertTrue(result["stage_manager_restored"])
        self.assertFalse(result["stage_manager_after"])
        self.assertEqual(calls[-1][-1], "false")

    def test_B3_visible_match_wins(self):
        hidden = dict(id=1, title="Fixture", app="App", width=100, height=100, capturable=False)
        visible = dict(id=2, title="Fixture", app="App", width=20, height=20, capturable=True)
        self.assertEqual(c.select([hidden, visible], "fixture")[0], visible)

    def test_B4_geometry_ratio_rejects_thumbnail_not_small_window(self):
        self.assertTrue(c.geometry_spoiled([400, 300], [100, 75]))
        self.assertFalse(c.geometry_spoiled([40, 30], [40, 30]))

    def test_B4_measured_thumbnail_signals(self):
        self.assertTrue(c.thumbnail_signal(error_code=-3811))
        self.assertTrue(c.thumbnail_signal(content_scale=0.1378))
        self.assertTrue(c.thumbnail_signal(content_scale=0.99))
        self.assertFalse(c.thumbnail_signal(content_scale=1.0))
        self.assertFalse(c.thumbnail_signal(error_code=-1, content_scale=1.0))

    def test_film_session_requires_real_terminals(self):
        command = Mock()
        with patch.object(c.sys.stdin, "isatty", return_value=False), patch.object(c.sys.stdout, "isatty", return_value=True):
            with self.assertRaises(c.Failure):
                c.film_session(["owned-command"], settings_command=command)
        command.assert_not_called()

    def test_film_session_restores_setting_after_interruption(self):
        calls = []
        def setting(*args):
            calls.append(args)
            return "1\n"
        with patch.object(c.sys.stdin, "isatty", return_value=True), patch.object(c.sys.stdout, "isatty", return_value=True), patch.object(c.subprocess, "run", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                c.film_session(["owned-command"], settings_command=setting)
        self.assertEqual(calls[-1], ("write", "com.apple.WindowManager", "GloballyEnabled", "-bool", "true"))

    def test_H1_sparse_dark_pixels_are_not_blank(self):
        pixels = bytes([0] * 999_999 + [64])
        self.assertFalse(c.uniform_pixels(pixels))
        self.assertTrue(c.uniform_pixels(bytes([10] * 1000)))

    def test_H1_short_dark_gap_is_not_sustained_blank(self):
        self.assertFalse(c.sustained_blank([False] * 10 + [True] * 2 + [False] * 10, 10))
        self.assertTrue(c.sustained_blank([True] * 20, 10))

    def test_H2_wayland_check_and_missing_window(self):
        with patch.dict(os.environ, WAYLAND_DISPLAY="wayland-test", XDG_SESSION_TYPE="wayland"):
            backend = c.Linux(":123")
            with patch.object(backend, "ready"):
                result = backend.check()
        self.assertTrue(result["wayland_session"])
        self.assertFalse(result["native_wayland_capture"])

    def test_H3_every_desktop_recording_warns_about_transients(self):
        data = c.receipt("record")
        c.desktop_recording_warning(data)
        self.assertTrue(any("private display" in w and "between" in w for w in data["warnings"]))
        self.assertEqual(data["occlusion_guarantee"], "polled only")

    def test_H4_unrelated_front_change_is_allowed(self):
        from test_live import assert_background
        data = dict(front_app_before=dict(app="One", id="1"), front_app_after=dict(app="Two", id="2"))
        assert_background(self, data, {"owned-fixture", "capture-helper"})
        with self.assertRaises(AssertionError):
            assert_background(self, dict(data, front_app_after=dict(app="owned-fixture", id="3")), {"owned-fixture"})

    def test_M1_real_shot_no_display(self):
        cli = Path(c.__file__).with_name("capture")
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        with patch.object(c.platform, "system", return_value="Linux"), patch.dict(os.environ, env, clear=True), patch.object(c.sys, "argv", [str(cli), "shot", "fixture"]), contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as caught:
                c.main()
        self.assertEqual(caught.exception.code, c.DISPLAY, output.getvalue())

    def test_M2_closed_window_has_no_match_code(self):
        backend = c.Linux(":123")
        with patch.object(backend, "command", side_effect=c.Failure(c.CAPTURE, "X Error: Bad Drawable")):
            with self.assertRaises(c.Failure) as caught:
                backend.assert_visible(dict(id="0x21"))
        self.assertEqual(caught.exception.code, c.NO_MATCH)

    def test_M3_late_recording_fails(self):
        self.assertTrue(c.recording_late(4, 2, 60))
        self.assertFalse(c.recording_late(2.01, 2, 30))

    def test_M4_offscreen_advice_does_not_point_to_step(self):
        backend = c.Linux(":123")
        with patch.object(backend, "command", return_value="Map State: IsUnMapped"):
            with self.assertRaises(c.Failure) as caught:
                backend.assert_visible(dict(id="0x21"))
        self.assertNotIn("step", str(caught.exception))

    def test_M5_mixed_checkpoint_identity_warns(self):
        result = c.receipt("encode")
        c.checkpoint_identity([dict(app="App", id="1", window_title="first"), dict(app="App", id="1", window_title="second")], result)
        self.assertTrue(result["warnings"])
        self.assertIsNone(result["window_title"])
        self.assertFalse(result["identity_verified"])

    def test_M6_default_paths_do_not_collide(self):
        args = [c.parser().parse_args(["shot", "fixture"]) for _ in range(2)]
        self.assertNotEqual(args[0].output, args[1].output)

    def test_M7_input_only_is_not_occlusion(self):
        self.assertFalse(c.drawable_window("Class: InputOnly\nMap State: IsViewable"))
        self.assertTrue(c.drawable_window("Class: InputOutput\nMap State: IsViewable"))

    def test_L3_title_cannot_override_map_state(self):
        self.assertFalse(c.mapped_window('Window id: 0x21 "IsViewable"\n Map State: IsUnMapped'))

    def test_L6_receipt_identity_types(self):
        self.assertEqual(c.normalize_id(42), "42")
        self.assertEqual(c.normalize_front("Fixture"), dict(app="Fixture", id=None))

    def test_L4_corrupt_cached_helper_rebuilds(self):
        source = Path(c.__file__).with_name("mac.swift")
        digest = hashlib.sha256(source.read_bytes() + c.platform.machine().encode()).hexdigest()[:16]
        with temporary_directory() as folder:
            root = Path(folder)
            target = root / ("capture-" + digest)
            target.write_bytes(b"broken")
            target.chmod(0o644)
            def compile(args, **kw):
                staged = Path(args[-1])
                staged.write_bytes(b"\xcf\xfa\xed\xfe" + bytes(2048))
                staged.chmod(0o755)
                return ""
            with patch.object(c, "cache_dir", return_value=root), patch.object(c, "run", side_effect=compile), patch.object(c.shutil, "which", return_value="swiftc"):
                repaired = c.mac_helper()
            self.assertTrue(os.access(repaired, os.X_OK))
            self.assertGreater(repaired.stat().st_size, 1024)


if __name__ == "__main__":
    unittest.main()
