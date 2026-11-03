"""Live tests use only windows and displays owned by this test run."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from settings import cache_dir

ROOT = Path(__file__).parents[1]
CLI = ROOT / "scripts" / "capture"
TEMP_ROOT = cache_dir() / "tests" / "live"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def setUpModule():
    global TEST_SETTINGS, OLD_SETTINGS
    TEST_SETTINGS = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
    path = Path(TEST_SETTINGS.name) / "settings.json"
    path.write_text(json.dumps(dict(mac_permission="person_grants", linux_packages="person_installs", private_display="prefer", stage_manager="report")))
    OLD_SETTINGS = os.environ.get("CAPTURE_WINDOW_SETTINGS")
    os.environ["CAPTURE_WINDOW_SETTINGS"] = str(path)


def tearDownModule():
    if OLD_SETTINGS is None:
        os.environ.pop("CAPTURE_WINDOW_SETTINGS", None)
    else:
        os.environ["CAPTURE_WINDOW_SETTINGS"] = OLD_SETTINGS
    TEST_SETTINGS.cleanup()


def assert_background(test, receipt, forbidden_apps, forbidden_ids=()):
    samples = [receipt.get("front_app_before"), receipt.get("front_app_after"), *receipt.get("foreground_observed", [])]
    for sample in samples:
        if sample is None:
            continue
        app = sample.get("app") if isinstance(sample, dict) else sample
        wid = str(sample.get("id")) if isinstance(sample, dict) else None
        test.assertNotIn(app, forbidden_apps)
        test.assertNotIn(wid, set(map(str, forbidden_ids)))
        test.assertFalse((app or "").startswith("capture-helper-"))


def invoke(*args, **kwargs):
    p = subprocess.run([str(CLI), *map(str, args)], capture_output=True, text=True, timeout=150, **kwargs)
    data = json.loads(p.stdout)
    assert data["exit_code"] == p.returncode, (p.returncode, data)
    return data


def verify_movie(test, path, dimensions, minimum_frames):
    # macOS verifies with AVFoundation in the helper; Linux additionally decodes with ffmpeg.
    if platform.system() == "Linux":
        p = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height,nb_read_frames", "-of", "json", str(path)],
                           check=True, capture_output=True, text=True)
        stream = json.loads(p.stdout)["streams"][0]
        test.assertEqual([stream["width"], stream["height"]], dimensions)
        test.assertGreaterEqual(int(stream["nb_read_frames"]), minimum_frames)
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], check=True, capture_output=True)
    test.assertGreater(path.stat().st_size, 500)


def steps_and_encode(test, title, folder, env=None):
    seq = folder / "steps"
    for i in range(1, 4):
        data = invoke("step", title, seq, "--exact", env=env)
        test.assertTrue(data["ok"], data)
        test.assertEqual(Path(data["output_path"]).name, f"frame-{i:06d}.png")
    movie = folder / "steps.mp4"
    data = invoke("encode", seq, "-o", movie, "--fps", "4", env=env)
    test.assertTrue(data["ok"], data)
    test.assertEqual(data["frame_count"], 3)
    test.assertEqual(data["duration_seconds"], 0.75)
    test.assertTrue(all(v % 2 == 0 for v in data["pixel_size"]))
    verify_movie(test, movie, data["pixel_size"], 3)


def concurrent_steps(test, title, folder, env=None):
    seq = folder / "concurrent"
    processes = [subprocess.Popen([str(CLI), "step", title, str(seq), "--exact"],
                                 env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        receipts = []
        for p in processes:
            out, err = p.communicate(timeout=60)
            test.assertEqual(p.returncode, 0, (out, err))
            receipts.append(json.loads(out))
        test.assertEqual({Path(p["output_path"]).name for p in receipts}, {"frame-000001.png", "frame-000002.png"})
        for path in seq.glob("frame-*.json"):
            test.assertTrue(json.loads(path.read_text())["ok"])
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate(); p.communicate(timeout=10)


class LiveMac(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if platform.system() != "Darwin":
            raise unittest.SkipTest("Mac platform absent")
        cls.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        cls.check = invoke("check")
        if not cls.check["screen_recording_grant"]:
            # A present Mac without permission is a failed prerequisite, not a skipped live test.
            raise AssertionError("Mac grant absent; no permission prompt requested")
        binary = cls.folder / "fixture"
        cls.binary = binary
        subprocess.run(["swiftc", "-module-cache-path", str(cls.folder / "modules"), str(ROOT / "tests" / "window.swift"), "-o", str(binary)], check=True, capture_output=True)
        cls.title = "capture-fixture-" + uuid.uuid4().hex
        cls.proc = subprocess.Popen([str(binary), cls.title], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        cls.addClassCleanup(cls.stop)
        cls.wid = int(cls.proc.stdout.readline())
        assert cls.proc.stdout.readline().strip() == "ready"
        for _ in range(40):
            if any(w["title"] == cls.title for w in invoke("list")["windows"]):
                break
            time.sleep(0.1)
        else:
            raise AssertionError("Owned fixture did not appear")

    @classmethod
    def stop(cls):
        cls.proc.terminate()
        cls.proc.communicate(timeout=10)

    def test_shot(self):
        result = invoke("shot", self.title, "--exact", "-o", self.folder / "still.png")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["id"], str(self.wid))
        assert_background(self, result, {"fixture", "capture-helper"}, [self.proc.pid, result.get("helper_process_id")])
        self.assertGreater(result["frame_spread"], 1)
        self.assertGreater(result["pixel_size"][0], 400)
        self.assertTrue((self.folder / "still.png").is_file())

    def test_steps_and_encode(self):
        steps_and_encode(self, self.title, self.folder)

    def test_concurrent_steps(self):
        concurrent_steps(self, self.title, self.folder)

    def test_record_moving_background_window(self):
        movie = self.folder / "live.mp4"
        result = invoke("record", self.title, "--exact", "-o", movie, "--seconds", "2", "--fps", "15")
        self.assertTrue(result["ok"], result)
        assert_background(self, result, {"fixture", "capture-helper"}, [self.proc.pid, result.get("helper_process_id")])
        self.assertGreaterEqual(result["distinct_frames"], 2)
        self.assertGreaterEqual(result["complete_frames"], 2)
        self.assertTrue(result["meaningful_source_change"])
        self.assertLess(abs(result["capture_elapsed_seconds"] - result["duration_seconds"]), 0.25)
        verify_movie(self, movie, result["pixel_size"], 25)
        # Retain only nonpersonal metrics in stdout when running this test directly.
        print("Mac moving metrics:", json.dumps({k: result[k] for k in ("frame_count", "complete_frames", "distinct_frames", "repeated_frames", "pixel_size", "stream_status_counts", "decoded_frames", "decoded_distinct_frames")}))

    def test_native_policy(self):
        binary = self.folder / "policy"
        subprocess.run(["swiftc", "-D", "CAPTURE_WINDOW_TESTING", "-parse-as-library", "-module-cache-path", str(cache_dir() / "modules"),
                        str(ROOT / "scripts" / "mac.swift"), str(ROOT / "tests" / "policy.swift"), "-o", str(binary)], check=True, capture_output=True)
        subprocess.run([str(binary)], check=True, capture_output=True)

    def test_concurrent_build_and_cache_repair(self):
        cache = self.folder / "isolated-cache"
        env = dict(os.environ, CAPTURE_WINDOW_HOME=str(cache))
        processes = [subprocess.Popen([str(CLI), "check"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
        try:
            for proc in processes:
                out, err = proc.communicate(timeout=140)
                self.assertEqual(proc.returncode, 0, (out, err))
                self.assertTrue(json.loads(out)["screen_recording_grant"])
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.terminate(); proc.communicate(timeout=10)
        binaries = list(cache.glob("capture-*"))
        self.assertEqual(len(binaries), 1)
        helper = binaries[0]
        helper.chmod(0o644)
        self.assertTrue(invoke("check", env=env)["ok"])
        helper.write_bytes(helper.read_bytes()[:4096])
        self.assertTrue(invoke("check", env=env)["ok"])
        self.assertTrue(os.access(helper, os.X_OK))
        self.assertEqual({p.name for p in cache.iterdir() if p.is_dir()}, {"modules"})

    def fixture_failure(self, flag, code):
        title = "capture-case-" + uuid.uuid4().hex
        proc = subprocess.Popen([str(self.binary), title, flag], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            int(proc.stdout.readline())
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            movie = self.folder / (flag[2:] + ".mp4")
            data = invoke("record", title, "--exact", "-o", movie, "--seconds", "2", "--fps", "10")
            self.assertFalse(data["ok"], data)
            self.assertEqual(data["exit_code"], code, data)
            if flag == "--static":
                self.assertIn("step", data["error"])
            else:
                self.assertNotIn("step", data["error"])
            self.assertFalse(movie.exists())
            assert_background(self, data, {"fixture", "capture-helper"}, [proc.pid, data.get("helper_process_id")])
            print("Mac", flag, "metrics:", json.dumps({k: data[k] for k in ("exit_code", "error", "stream_status_counts", "complete_frames") if k in data}))
        finally:
            proc.terminate(); proc.communicate(timeout=10)

    def test_static_record_fails(self):
        self.fixture_failure("--static", 7)

    def test_hidden_record_fails(self):
        self.fixture_failure("--hide", 6)

    def test_shrunk_record_fails(self):
        self.fixture_failure("--shrink", 6)

    def test_closed_record_is_no_match(self):
        self.fixture_failure("--close", 4)

    def test_dark_sparse_still_and_encode(self):
        title = "capture-dark-" + uuid.uuid4().hex
        proc = subprocess.Popen([str(self.binary), title, "--dark", "--static"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            int(proc.stdout.readline())
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            seq = self.folder / "dark-sequence"
            data = invoke("step", title, seq, "--exact")
            self.assertTrue(data["ok"], data)
            movie = self.folder / "dark.mp4"
            data = invoke("encode", seq, "-o", movie)
            self.assertTrue(data["ok"], data)
            assert_background(self, data, {"fixture", "capture-helper"}, [proc.pid])
        finally:
            proc.terminate(); proc.communicate(timeout=10)

    def test_visible_match_beats_larger_hidden_match(self):
        title = "capture-choice-" + uuid.uuid4().hex
        proc = subprocess.Popen([str(self.binary), title, "--hidden-large"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            visible_id = str(int(proc.stdout.readline()))
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            for _ in range(40):
                listed = [w for w in invoke("list")["windows"] if title in w["title"]]
                if len(listed) >= 2 and any(not w["capturable"] for w in listed):
                    break
                time.sleep(0.1)
            self.assertGreaterEqual(len(listed), 2)
            self.assertTrue(any(not w["capturable"] for w in listed))
            data = invoke("shot", title, "-o", self.folder / "chosen.png")
            self.assertTrue(data["ok"], data)
            self.assertEqual(data["id"], visible_id)
            self.assertGreaterEqual(data["skipped_uncapturable"], 1)
        finally:
            proc.terminate(); proc.communicate(timeout=10)

    def test_record_tolerates_short_dark_gap(self):
        title = "capture-fade-" + uuid.uuid4().hex
        proc = subprocess.Popen([str(self.binary), title, "--blink-dark"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            int(proc.stdout.readline())
            self.assertEqual(proc.stdout.readline().strip(), "ready")
            data = invoke("record", title, "--exact", "-o", self.folder / "fade.mp4", "--seconds", "2", "--fps", "15")
            self.assertTrue(data["ok"], data)
            assert_background(self, data, {"fixture", "capture-helper"}, [proc.pid, data.get("helper_process_id")])
        finally:
            proc.terminate(); proc.communicate(timeout=10)


class LiveLinux(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if platform.system() != "Linux":
            raise unittest.SkipTest("Linux platform absent")
        if not shutil.which("Xvfb"):
            raise unittest.SkipTest("Private X11 display absent: Xvfb unavailable")
        cls.temp = tempfile.TemporaryDirectory(dir=TEMP_ROOT)
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        cls.processes = []
        cls.addClassCleanup(cls.stop)
        xvfb = cls.start(["Xvfb", "-displayfd", "1", "-screen", "0", "1024x768x24", "-nolisten", "tcp"])
        display = ":" + xvfb.stdout.readline().strip()
        if display == ":":
            raise AssertionError("Owned Xvfb failed to start")
        cls.env = dict(os.environ, DISPLAY=display, LIBGL_ALWAYS_SOFTWARE="1")
        cls.start(["openbox"], env=cls.env)
        for _ in range(40):
            p = subprocess.run(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"], env=cls.env, text=True, capture_output=True)
            if "0x" in p.stdout:
                break
            time.sleep(0.1)
        else:
            raise AssertionError("Owned window manager did not start")
        cls.title = "capture-fixture-" + uuid.uuid4().hex
        cls.start(["xterm", "-title", cls.title, "-geometry", "52x14+311+207", "-bg", "navy", "-fg", "white",
                   "-e", "sh", "-c", "printf 'Owned capture fixture\\n'; exec sleep 1800"], env=cls.env)
        for _ in range(40):
            windows = invoke("list", env=cls.env)["windows"]
            found = [w for w in windows if w["title"] == cls.title]
            if found and found[0]["x"] > 300 and found[0]["y"] > 200:
                cls.window = found[0]
                break
            time.sleep(0.1)
        else:
            raise AssertionError("Owned X11 fixture did not appear")

    @classmethod
    def start(cls, args, **kwargs):
        p = subprocess.Popen(args, cwd=cls.folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs)
        cls.processes.append(p)
        return p

    @classmethod
    def stop(cls):
        for p in reversed(cls.processes):
            if p.poll() is None:
                p.terminate()
            try:
                p.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()
                p.communicate(timeout=5)

    def test_shot_geometry(self):
        self.assertGreater(self.window["x"], 300)
        self.assertGreater(self.window["y"], 200)
        result = invoke("shot", self.title, "--exact", "-o", self.folder / "still.png", env=self.env)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["pixel_size"], [self.window["width"], self.window["height"]])
        assert_background(self, result, {"capture-helper"})
        self.assertGreater(result["frame_spread"], 1)

    def test_steps_and_encode(self):
        steps_and_encode(self, self.title, self.folder, self.env)

    def test_concurrent_steps(self):
        concurrent_steps(self, self.title, self.folder, self.env)

    def test_record_absolute_geometry(self):
        self.start(["glxgears", "-geometry", "201x151+71+53"], env=self.env)
        for _ in range(40):
            windows = invoke("list", env=self.env)["windows"]
            found = [w for w in windows if "glxgears" in (w["title"] + w["app"]).lower()]
            if found and found[0]["x"] > 60 and found[0]["y"] > 70:
                gears = found[0]
                break
            time.sleep(0.1)
        else:
            self.fail("Owned GLX fixture did not appear")
        # Create an owned, nonoverlapping foreground window; the animation stays behind it.
        title = "front-fixture-" + uuid.uuid4().hex
        self.start(["xterm", "-title", title, "-geometry", "20x5+780+560", "-e", "sleep", "1800"], env=self.env)
        for _ in range(40):
            if any(w["title"] == title for w in invoke("list", env=self.env)["windows"]):
                break
            time.sleep(0.1)
        movie = self.folder / "live.mp4"
        data = invoke("record", "glxgears", "--exact", "-o", movie, "--seconds", "2", "--fps", "15", env=self.env)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["capture_geometry"], {k: gears[k] for k in ("x", "y", "width", "height")})
        self.assertGreater(data["capture_geometry"]["x"], 60)
        assert_background(self, data, {"capture-helper"}, [gears["id"]])
        self.assertGreater(data["distinct_frames"], 1)
        self.assertEqual(data["occlusion_guarantee"], "polled only")
        self.assertTrue(any("between" in w and "private display" in w for w in data["warnings"]))
        verify_movie(self, movie, data["pixel_size"], 25)

    def test_static_record_fails(self):
        movie = self.folder / "static.mp4"
        result = invoke("record", self.title, "--exact", "-o", movie, "--seconds", "1", "--fps", "10", env=self.env)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["exit_code"], 7, result)
        self.assertIn("step", result["error"])
        self.assertFalse(movie.exists())

    def owned_window(self, title, command, geometry="100x30+5+5", color="#777777"):
        proc = self.start(["xterm", "-title", title, "-geometry", geometry, "-bg", "#000000", "-fg", color, "-cr", "#000000", "+bc", "-e", *command], env=self.env)
        for _ in range(60):
            found = [w for w in invoke("list", env=self.env)["windows"] if w["title"] == title and w["mapped"] and w["x"] > 0]
            if found:
                return proc, found[0]
            time.sleep(0.1)
        self.fail("Owned fixture did not appear")

    def test_dark_sparse_still(self):
        title = "sparse-" + uuid.uuid4().hex
        proc, window = self.owned_window(title, ["sh", "-c", "printf '\033[?25lx'; exec sleep 1800"], geometry="150x35+5+5", color="#404040")
        try:
            data = invoke("shot", title, "--exact", "-o", self.folder / "sparse.png", env=self.env)
            self.assertTrue(data["ok"], data)
            self.assertLess(data["frame_spread"], 1)
        finally:
            proc.terminate(); proc.communicate(timeout=10)

    def test_record_tolerates_short_dark_gap(self):
        title = "fade-" + uuid.uuid4().hex
        code = "import sys,time; start=time.monotonic(); i=0\nwhile True:\n t=(time.monotonic()-start)%2; sys.stdout.write('\\033[?25l\\033[2J\\033[H'+('' if .6<t<.8 else ('frame '+str(i)+'\\n')*8)); sys.stdout.flush(); i+=1; time.sleep(.05)"
        proc, window = self.owned_window(title, ["python3", "-c", code])
        try:
            data = invoke("record", title, "--exact", "-o", self.folder / "fade.mp4", "--seconds", "2", "--fps", "15", env=self.env)
            self.assertTrue(data["ok"], data)
            self.assertGreater(data["blank_frames"], 0)
        finally:
            proc.terminate(); proc.communicate(timeout=10)

    def test_input_only_overlay_is_ignored(self):
        proc = self.start(["python3", str(ROOT / "tests" / "x11_fixture.py"), "input", "330", "250"], env=self.env)
        try:
            wid = int(proc.stdout.readline())
            info = subprocess.check_output(["xwininfo", "-id", str(wid)], env=self.env, text=True)
            self.assertIn("Class: InputOnly", info)
            self.assertIn("Map State: IsViewable", info)
            data = invoke("shot", self.title, "--exact", "-o", self.folder / "input-only.png", env=self.env)
            self.assertTrue(data["ok"], data)
        finally:
            proc.terminate(); proc.communicate(timeout=10)

    def test_closed_record_is_no_match(self):
        title = "closing-" + uuid.uuid4().hex
        code = "import sys,time;sys.stdout.write('owned content\\n');sys.stdout.flush();time.sleep(1.5)"
        proc, window = self.owned_window(title, ["python3", "-c", code])
        data = invoke("record", title, "--exact", "-o", self.folder / "closed.mp4", "--seconds", "3", "--fps", "10", env=self.env)
        self.assertEqual(data["exit_code"], 4, data)
        self.assertFalse((self.folder / "closed.mp4").exists())

    def test_related_popup_is_included_in_client_rectangle(self):
        proc = self.start(["python3", str(ROOT / "tests" / "x11_fixture.py"), "popup", "330", "250", self.window["id"]], env=self.env)
        try:
            int(proc.stdout.readline())
            data = invoke("shot", self.title, "--exact", "-o", self.folder / "popup.png", env=self.env)
            self.assertTrue(data["ok"], data)
        finally:
            proc.terminate(); proc.communicate(timeout=10)

    def test_percent_output_folder(self):
        folder = self.folder / "pc%dt%"
        folder.mkdir()
        path = folder / "still.png"
        data = invoke("shot", self.title, "--exact", "-o", path, env=self.env)
        self.assertTrue(data["ok"], data)
        seq = folder / "steps"
        data = invoke("step", self.title, seq, "--exact", env=self.env)
        self.assertTrue(data["ok"], data)
        data = invoke("encode", seq, "-o", folder / "movie.mp4", env=self.env)
        self.assertTrue(data["ok"], data)

    def test_long_static_fails_early(self):
        start = time.monotonic()
        data = invoke("record", self.title, "--exact", "-o", self.folder / "long-static.mp4", "--seconds", "20", "--fps", "10", env=self.env)
        self.assertEqual(data["exit_code"], 7, data)
        self.assertLess(time.monotonic() - start, 7)

    def test_sigterm_reaps_recorder_and_staging(self):
        path = self.folder / "interrupted.mp4"
        before = set(self.folder.iterdir())
        proc = subprocess.Popen([str(CLI), "record", self.title, "--exact", "-o", str(path), "--seconds", "30"],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        child_pid = None
        try:
            for _ in range(80):
                for stat in Path("/proc").glob("[0-9]*/stat"):
                    try:
                        parts = stat.read_text().rsplit(")", 1)[1].split()
                        if int(parts[1]) == proc.pid and b"ffmpeg" in stat.with_name("cmdline").read_bytes():
                            child_pid = int(stat.parent.name)
                    except (OSError, ValueError, IndexError):
                        pass
                if child_pid:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(child_pid, "Owned ffmpeg did not start")
            proc.terminate()
            out, err = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 7, (out, err))
            self.assertFalse(Path(f"/proc/{child_pid}").exists())
            self.assertEqual(set(self.folder.iterdir()), before)
            self.assertFalse(path.exists())
        finally:
            if proc.poll() is None:
                proc.terminate(); proc.communicate(timeout=10)
            if child_pid:
                try:
                    if str(self.folder).encode() in Path(f"/proc/{child_pid}/cmdline").read_bytes():
                        os.kill(child_pid, 15)
                except OSError:
                    pass

    def test_occluded_shot_fails_without_focus_change(self):
        title = "occluder-" + uuid.uuid4().hex
        proc = self.start(["xterm", "-title", title, "-geometry", "20x5+320+240", "-e", "sleep", "1800"], env=self.env)
        try:
            for _ in range(40):
                found = [w for w in invoke("list", env=self.env)["windows"] if w["title"] == title and w["x"] > 300]
                if found:
                    break
                time.sleep(0.1)
            else:
                self.fail("Owned occluder did not appear")
            path = self.folder / "occluded.png"
            data = invoke("shot", self.title, "--exact", "-o", path, env=self.env)
            self.assertFalse(data["ok"], data)
            self.assertEqual(data["exit_code"], 6, data)
            assert_background(self, data, {"capture-helper"})
            self.assertFalse(path.exists())
        finally:
            proc.terminate(); proc.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
