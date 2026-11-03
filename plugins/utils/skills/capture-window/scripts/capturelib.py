"""Window capture orchestration. No command here changes window state."""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import signal
from settings import FEATURES, cache_dir, load_settings, questions, save_answers

USAGE, PERMISSION, NO_MATCH, AMBIGUOUS, BLANK, CAPTURE, DISPLAY = range(2, 9)


class Failure(Exception):
    def __init__(self, code, message, details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def run(args, env=None, timeout=30, binary=False, cwd=None):
    try:
        p = subprocess.run(args, env=env, capture_output=True, text=not binary, timeout=timeout, cwd=cwd, **({} if binary else dict(errors="replace")))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Failure(CAPTURE, str(e)) from e
    if p.returncode:
        message = p.stderr.decode(errors="replace") if binary else p.stderr
        raise Failure(CAPTURE, message.strip() or f"{args[0]} exited {p.returncode}")
    return p.stdout


def receipt(verb, **fields):
    result = dict(tool="capture-window", verb=verb, window_title=None, app=None, id=None,
                  pixel_size=None, front_app_before=None, front_app_after=None,
                  frame_spread=None, output_path=None, warnings=[])
    result.update(fields)
    return result


def select(windows, match, exact=False):
    key = match.casefold()
    matches = [w for w in windows if key in w["title"].casefold() or key in w["app"].casefold()]
    if not matches:
        raise Failure(NO_MATCH, "No window matches the literal title or app/class. Use list.")
    if exact and len(matches) > 1:
        raise Failure(AMBIGUOUS, f"{len(matches)} windows match; --exact requires one.")
    matches.sort(key=lambda w: (not w.get("capturable", True), -w["width"] * w["height"], str(w["id"])))
    return matches[0], len(matches)


def parse_geometry(text):
    """xwininfo's Absolute fields are root coordinates, including WM reparenting."""
    def number(label):
        m = re.search(r"^\s*" + re.escape(label) + r":\s*(-?\d+)", text, re.M)
        if not m:
            raise Failure(CAPTURE, f"xwininfo missing {label}")
        return int(m.group(1))
    return dict(x=number("Absolute upper-left X"), y=number("Absolute upper-left Y"),
                width=number("Width"), height=number("Height"))


def tree_ids(text):
    return list(dict.fromkeys(re.findall(r'^\s*(0x[\da-fA-F]+)\s+"', text, re.M)))


def root_stacking(text, target):
    """Root siblings bottom to top, mapping a reparented client to its WM frame."""
    rows = [(len(m[1]), m[2]) for m in re.finditer(r"^([ \t]+)(0x[\da-fA-F]+)\s", text, re.M)]
    if not rows:
        raise Failure(CAPTURE, "Cannot establish X11 stacking order")
    depth = min(d for d, _ in rows)
    ancestor = None
    siblings = []
    target_ancestor = None
    for d, wid in rows:
        if d == depth:
            ancestor = wid
            siblings.append(wid)
        if int(wid, 16) == int(target, 16):
            target_ancestor = ancestor
    if target_ancestor is None:
        raise Failure(CAPTURE, "Cannot establish target stacking order; use a private display.")
    return list(reversed(siblings)), target_ancestor


def parse_properties(text):
    title = ""
    app = ""
    for line in text.splitlines():
        values = re.findall(r'"((?:\\.|[^"\\])*)"', line)
        values = [v.replace(r'\"', '"').replace(r'\\', '\\') for v in values]
        if line.startswith("_NET_WM_NAME") and values:
            title = values[0]
        elif line.startswith("WM_NAME") and values and not title:
            title = values[0]
        elif line.startswith("WM_CLASS"):
            app = " / ".join(values)
    return title, app


def even_size(width, height):
    # Pad rather than crop the last row/column.
    return (width + width % 2, height + height % 2)


def frame_stats(raw):
    if not raw:
        raise Failure(CAPTURE, "No pixels returned")
    mean = sum(raw) / len(raw)
    return math.sqrt(sum((v - mean) ** 2 for v in raw) / len(raw))


def uniform_pixels(raw):
    return not raw or max(raw) - min(raw) <= 2


def sustained_blank(flags, fps):
    if flags and all(flags):
        return True
    longest = current = 0
    for blank in flags:
        current = current + 1 if blank else 0
        longest = max(longest, current)
    return longest >= fps


def geometry_spoiled(frame, content):
    return min(content[0] / frame[0], content[1] / frame[1]) < 0.85


def thumbnail_signal(error_code=None, content_scale=None):
    return error_code == -3811 or (content_scale is not None and content_scale < 1)


def recording_late(elapsed, requested, fps):
    return elapsed > requested + max(0.2, 2 / fps)


def mapped_window(info):
    return bool(re.search(r"^\s*Map State:\s*IsViewable\s*$", info, re.M))


def drawable_window(info):
    return not re.search(r"^\s*Class:\s*InputOnly\s*$", info, re.M) and mapped_window(info)


def normalize_id(value):
    return str(value) if value is not None else None


def normalize_front(value):
    if value is None or isinstance(value, dict):
        return value
    return dict(app=value, id=None)


def desktop_recording_warning(result):
    result["occlusion_guarantee"] = "polled only"
    result["warnings"].append("Desktop recording can include an occluder appearing between checks; use an owned private display for isolation from the person's desktop.")


def checkpoint_identity(sidecars, result):
    result["identity_verified"] = False
    result["front_app_source"] = "checkpoint receipts, when present"
    identities = {(p.get("app"), str(p.get("id")), p.get("window_title"), str(p.get("process_identity"))) for p in sidecars}
    if len(identities) == 1:
        for key in ("window_title", "app", "id", "front_app_before"):
            result[key] = sidecars[0].get(key)
        result["front_app_after"] = sidecars[-1].get("front_app_after")
        result["warnings"].append("Checkpoint identity is observed metadata; window ids may be reused between captures, so continuity is unverified.")
    else:
        result["warnings"].append("Mixed or missing checkpoint identities: this movie may join different windows; inspect the sequence.")


def setup(args):
    changes = {}
    for answer in args.set:
        key, sep, value = answer.partition("=")
        if not sep:
            raise Failure(USAGE, "Use --set feature=choice")
        changes[key] = value
    saved = save_answers(changes) if changes else load_settings()
    if not args.show and not args.set and sys.stdin.isatty():
        for item in questions(saved):
            print(item["question"] + " Choices: " + ", ".join(item["choices"]), file=sys.stderr)
            value = input().strip()
            saved = save_answers({item["feature"]: value})
    return dict(settings=saved, unanswered=questions(saved), choices={k: v[1] for k, v in FEATURES.items()})


def ruling(feature, args, required=False):
    overrides = dict(item.split("=", 1) for item in getattr(args, "decision", []))
    saved = load_settings()
    value = overrides.get(feature, saved.get(feature))
    if value == "ask" or (value is None and required):
        raise Failure(USAGE, "A human decision is needed; use setup to save a ruling or --decision feature=choice for this run.",
                      dict(decision_required=feature, question=FEATURES[feature][0], choices=FEATURES[feature][1]))
    if value is not None and value not in FEATURES[feature][1]:
        raise Failure(USAGE, "Invalid per-run decision")
    return value


def bring_forward(action, backend, result):
    """Bring the shrunk app forward for the shot, then return to the app that was in front."""
    before = backend.front() or {}
    pid = int(str(result.get("process_identity") or "0:").split(":")[0] or 0)
    if not pid:
        raise Failure(BLANK, "Stage Manager shrank the window and its app could not be identified.")
    was_hidden = backend.bring_forward(pid)
    result["brought_forward"] = True
    try:
        deadline = time.monotonic() + 6
        while True:
            try:
                return action()
            except Failure as retry:
                if not retry.details.get("stage_manager_spoiled") or time.monotonic() >= deadline:
                    raise
                time.sleep(0.25)  # Stage Manager animates the window back to full size.
    finally:
        if before.get("id"):
            backend.bring_forward(int(before["id"]))
        if was_hidden:
            backend.hide(pid)  # It was hidden with ⌘H before the shot; leave it that way.
        result["warnings"].append(f"Brought {result.get('app') or 'the app'} forward for about a second for a full-size shot, then returned to {before.get('app') or 'your app'}. No setting was changed.")


def with_stage_policy(action, backend, args, result):
    try:
        return action()
    except Failure as error:
        if not error.details.get("stage_manager_spoiled"):
            raise
        choice = ruling("stage_manager", args) or "bring-forward"
        result["stage_manager_ruling"] = choice
        if choice == "report":
            raise
        if choice == "bring-forward" and hasattr(backend, "bring_forward"):
            return bring_forward(action, backend, result)
        print("For this run, switch Stage Manager off in Control Centre → Stage Manager, or in your own terminal use: capture film-session -- <command>; capture resumes automatically when it is off.", file=sys.stderr)
        result["stage_manager_waited"] = True
        deadline = time.monotonic() + getattr(args, "stage_timeout", 60)
        try:
            while backend.check().get("stage_manager_enabled") is not False:
                if time.monotonic() >= deadline:
                    raise Failure(BLANK, "Timed out waiting for Stage Manager to be switched off.", error.details)
                time.sleep(0.5)
            while True:
                try:
                    return action()
                except Failure as retry:
                    if not retry.details.get("stage_manager_spoiled") or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.25)  # The compositor may settle after the preference changes.
        finally:
            message = "Reminder: switch Stage Manager back on in Control Centre when you are ready."
            print(message, file=sys.stderr)
            result["warnings"].append(message)


def film_session(command, settings_command=None, result=None):
    """The sole setting writer. The agent must never invoke this real-terminal path."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise Failure(USAGE, "film-session is person-run only: stdin and stdout must both be real terminals.")
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise Failure(USAGE, "film-session needs -- followed by your command")
    setting = settings_command or (lambda *args: run(["defaults", *args]))
    result = result if result is not None else receipt("film-session")
    lock_path = cache_dir() / "film-session.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = setting("read", "com.apple.WindowManager", "GloballyEnabled").strip().lower()
        if previous not in ("0", "1", "true", "false"):
            raise Failure(CAPTURE, "Cannot determine the earlier Stage Manager value; nothing was changed.")
        enabled = previous in ("1", "true")
        result.update(stage_manager_before=enabled, stage_manager_restored=False)
        try:
            setting("write", "com.apple.WindowManager", "GloballyEnabled", "-bool", "false")
            child = subprocess.run(command, check=False)
            result["command_exit_code"] = child.returncode
        finally:
            blocked = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
            try:
                setting("write", "com.apple.WindowManager", "GloballyEnabled", "-bool", "true" if enabled else "false")
                result.update(stage_manager_restored=True, stage_manager_after=enabled)
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, blocked)
    if result["command_exit_code"]:
        raise Failure(CAPTURE, "Your film-session command failed; the earlier Stage Manager value was restored.")
    return result


def mac_helper():
    source = Path(__file__).with_name("mac.swift")
    digest = hashlib.sha256(source.read_bytes() + platform.machine().encode()).hexdigest()[:16]
    cache = cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / ("capture-" + digest)
    def valid(path):
        try:
            with path.open("rb") as data:
                sane = os.access(path, os.X_OK) and path.stat().st_size > 1024 and data.read(4) in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe")
            if sane:
                run(["codesign", "--verify", "--strict", str(path)])
            return sane
        except (OSError, Failure):
            return False
    if not valid(target):
        if not shutil.which("swiftc"):
            raise Failure(CAPTURE, "Missing swiftc; install Xcode command line tools before capture.")
        with tempfile.TemporaryDirectory(dir=cache) as temp:
            staged = Path(temp) / "capture"
            run(["swiftc", "-parse-as-library", "-O", "-module-cache-path", str(cache / "modules"),
                 str(source), "-o", str(staged)], timeout=120)
            os.replace(staged, target)
    with (cache / "builds.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        builds = sorted((p for p in cache.glob("capture-*") if re.fullmatch(r"capture-[0-9a-f]{16}", p.name)), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in builds[4:]:
            if old != target:
                old.unlink(missing_ok=True)
    return target


class Mac:
    def call(self, *args, timeout=30):
        result = json.loads(run([str(mac_helper()), *map(str, args)], timeout=timeout))
        if result.get("error"):
            raise Failure(result["exit_code"], result["error"], result)
        return result

    def check(self):
        if not shutil.which("swiftc"):
            return dict(backend="ScreenCaptureKit", tools=dict(swiftc=False),
                        shot_available=False, record_available=False, encode_available=False,
                        setup="Install Xcode command line tools before first use.")
        return self.call("check")

    def windows(self):
        return self.call("list")["windows"]

    def front(self):
        return normalize_front(self.call("front")["front"])

    def bring_forward(self, pid):
        # LaunchServices brings the app's windows into the current stage; no Accessibility or setting needed.
        app = self.call("bundle", pid)
        subprocess.run(["open", app["bundle"]], check=True, timeout=15)
        return bool(app.get("hidden"))

    def hide(self, pid):
        self.call("hide", pid)

    def shot(self, window, output):
        return self.call("shot", window["id"], output)

    def encode(self, frames, output, fps):
        cache = cache_dir()
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as temp:
            manifest = Path(temp) / "frames.json"
            manifest.write_text(json.dumps([str(p) for p in frames]))
            return self.call("encode", manifest, output, fps, timeout=max(60, len(frames) * 3))

    def record(self, window, output, seconds, fps):
        return self.call("record", window["id"], output, seconds, fps, timeout=seconds + 45)


class Linux:
    def __init__(self, display):
        self.env = dict(os.environ)
        self.display = display or self.env.get("DISPLAY")
        if self.display:
            self.env["DISPLAY"] = self.display

    def command(self, *args, **kw):
        return run(list(args), env=self.env, **kw)

    def ready(self):
        if not self.display:
            raise Failure(DISPLAY, "No X11 DISPLAY. Native Wayland windows are unsupported; use a private X11 display.")
        if not shutil.which("xwininfo") or not shutil.which("xprop"):
            raise Failure(CAPTURE, "Missing xwininfo/xprop; run check for setup.")
        try:
            self.command("xwininfo", "-root")
        except Failure as e:
            raise Failure(DISPLAY, "Cannot open the selected X11 display.") from e

    def check(self):
        names = ("xwininfo", "xprop", "import", "convert", "ffmpeg", "ffprobe", "Xvfb")
        tools = {name: bool(shutil.which(name)) for name in names}
        try:
            self.ready()
            available = True
        except Failure:
            available = False
        wayland = bool(self.env.get("WAYLAND_DISPLAY") or self.env.get("XDG_SESSION_TYPE", "").lower() == "wayland")
        return dict(backend="X11", display_available=available, tools=tools,
                    shot_available=available and all(tools[n] for n in ("xwininfo", "xprop", "import", "convert")),
                    record_available=available and all(tools[n] for n in ("xwininfo", "xprop", "ffmpeg")),
                    encode_available=all(tools[n] for n in ("convert", "ffmpeg")),
                    wayland_session=wayland, native_wayland_capture=False,
                    setup="X11 tools + ImageMagick for stills; ffmpeg for video; optional Xvfb for a private display. Native Wayland windows are invisible to this backend.")

    def assert_visible(self, window):
        try:
            return self._assert_visible(window)
        except Failure as error:
            if error.code != CAPTURE:
                raise
            # A WM frame/related window can disappear between tree/property reads.
            # Check the selected client before attributing that race to capture.
            try:
                self.command("xwininfo", "-id", window["id"])
            except Failure as current:
                if re.search(r"Bad (?:Drawable|Window)|BadDrawable|BadWindow|No such window|No window with|invalid window", str(current), re.I):
                    raise Failure(NO_MATCH, "The selected window closed during capture.") from current
                raise error
            if re.search(r"Bad (?:Drawable|Window)|BadDrawable|BadWindow|stacking order", str(error), re.I):
                return self._assert_visible(window)
            raise

    def _assert_visible(self, window):
        try:
            info = self.command("xwininfo", "-id", window["id"])
        except Failure as error:
            if re.search(r"Bad (?:Drawable|Window)|BadDrawable|BadWindow|No such window|No window with|invalid window", str(error), re.I):
                raise Failure(NO_MATCH, "The selected window closed during capture.") from error
            raise
        if not mapped_window(info):
            raise Failure(BLANK, "Window is unmapped/minimized. It must be visible on the selected display before capture can work.")
        geo = parse_geometry(info)
        root = parse_geometry(self.command("xwininfo", "-root"))
        if geo["x"] < 0 or geo["y"] < 0 or geo["x"] + geo["width"] > root["width"] or geo["y"] + geo["height"] > root["height"]:
            raise Failure(BLANK, "Window extends outside the display; no state change attempted.")
        # Include override-redirect menus/popups missing from EWMH's client list.
        stacking, ancestor = root_stacking(self.command("xwininfo", "-root", "-tree"), window["id"])
        normalized = [int(wid, 16) for wid in stacking]
        target = int(ancestor, 16)
        for wid in stacking[normalized.index(target) + 1:]:
            try:
                other_info = self.command("xwininfo", "-id", wid)
            except Failure:
                continue
            if not drawable_window(other_info):
                continue
            other = parse_geometry(other_info)
            if (geo["x"] < other["x"] + other["width"] and other["x"] < geo["x"] + geo["width"]
                    and geo["y"] < other["y"] + other["height"] and other["y"] < geo["y"] + geo["height"]):
                if self.related(window["id"], ancestor, wid):
                    continue
                raise Failure(BLANK, "Another window covers the target; use an owned private display or wait until the covering window is gone.")
        return geo

    def related(self, target, frame, other):
        # Resolve WM frames to clients, then require positive same-process evidence
        # or a transient relationship. Matching classes alone is insufficient.
        clients = re.findall(r"^\s+(0x[\da-fA-F]+)\s", self.command("xwininfo", "-id", other, "-tree"), re.M)
        owner = self.command("xprop", "-id", target, "_NET_WM_PID", "WM_CLIENT_LEADER")
        for candidate in [other, *clients]:
            props = self.command("xprop", "-id", candidate, "_NET_WM_PID", "WM_CLIENT_LEADER", "WM_TRANSIENT_FOR")
            def value(text, key):
                found = re.search(r"^" + key + r"[^=#\n]*(?:=|#)\s*(0x[\da-fA-F]+|\d+)", text, re.M)
                return found[1] if found else None
            pid = value(owner, "_NET_WM_PID")
            if pid and pid == value(props, "_NET_WM_PID"):
                return True
            leader = value(owner, "WM_CLIENT_LEADER")
            if leader and leader != "0x0" and leader == value(props, "WM_CLIENT_LEADER"):
                return True
            transient = value(props, "WM_TRANSIENT_FOR")
            if transient and int(transient, 0) == int(target, 0):
                return True
        return False

    def windows(self):
        self.ready()
        prop = self.command("xprop", "-root", "_NET_CLIENT_LIST")
        ids = re.findall(r"0x[\da-fA-F]+", prop)
        if not ids:
            ids = tree_ids(self.command("xwininfo", "-root", "-tree"))
        windows = []
        for wid in dict.fromkeys(ids):
            try:
                info = self.command("xwininfo", "-id", wid)
                title, app = parse_properties(self.command("xprop", "-id", wid, "_NET_WM_NAME", "WM_NAME", "WM_CLASS"))
                geo = parse_geometry(info)
                if (title or app) and geo["width"] > 1 and geo["height"] > 1:
                    windows.append(dict(id=wid, title=title, app=app, mapped=mapped_window(info),
                                        process_identity=self.command("xprop", "-id", wid, "_NET_WM_PID").strip(), **geo))
            except Failure:
                continue  # A window may close during enumeration.
        for window in windows:
            try:
                self.assert_visible(window)
                window.update(capturable=True, capture_state="available")
            except Failure as error:
                window.update(capturable=False, capture_state=str(error))
        return windows

    def front(self):
        prop = self.command("xprop", "-root", "_NET_ACTIVE_WINDOW")
        match = re.search(r"0x[\da-fA-F]+", prop)
        if not match or int(match[0], 16) == 0:
            return None
        try:
            title, app = parse_properties(self.command("xprop", "-id", match[0], "WM_CLASS"))
            return dict(id=match[0], app=app)
        except Failure:
            return None

    def shot(self, window, output):
        geo = self.assert_visible(window)
        self.command("import", "-silent", "-window", "root", "-crop", f'{geo["width"]}x{geo["height"]}+{geo["x"]}+{geo["y"]}', "+repage", "frame.png", cwd=output.parent)
        if output.name != "frame.png":
            os.replace(output.parent / "frame.png", output)
        after = self.assert_visible(window)
        if any(after[k] != window[k] for k in ("x", "y", "width", "height")):
            raise Failure(CAPTURE, "Window geometry changed during capture; wait for stable bounds and retry.")
        dimensions = self.command("convert", output.name, "-format", "%w %h", "info:", cwd=output.parent).split()
        raw = self.command("convert", output.name, "-colorspace", "Gray", "-depth", "8", "gray:-", binary=True, cwd=output.parent)
        spread = frame_stats(raw)
        if uniform_pixels(raw):
            raise Failure(BLANK, "Uniform pixels across the full frame; wait for content or use the app's own export.",
                          dict(pixel_size=list(map(int, dimensions)), frame_spread=spread))
        return dict(pixel_size=list(map(int, dimensions)), frame_spread=spread, backend="X11 checked client rectangle",
                    warnings=["X11 drawable capture; known occlusion is rejected. Inspect the image; a private display is preferred."])

    def record(self, window, output, seconds, fps):
        geo = self.assert_visible(window)
        proof = output.with_suffix(".framemd5")
        command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "x11grab",
                   "-draw_mouse", "0", "-framerate", str(fps), "-video_size", f'{geo["width"]}x{geo["height"]}',
                   "-i", f'{self.display}+{geo["x"]},{geo["y"]}', "-t", str(seconds),
                   "-filter_complex_threads", "1", "-filter_complex",
                   "[0:v]split=2[v][p];[v]pad=ceil(iw/2)*2:ceil(ih/2)*2[video];[p]signalstats,metadata=print:file=quality.txt:direct=1,scale=64:64,format=gray[proof]",
                   "-map", "[video]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-movflags", "+faststart", str(output), "-map", "[proof]", "-t", str(seconds), "-f", "framemd5", "-flush_packets", "1", str(proof)]
        with tempfile.TemporaryFile(dir=output.parent) as errors:
            try:
                proc = subprocess.Popen(command, env=self.env, stdout=subprocess.DEVNULL, stderr=errors, cwd=output.parent)
            except OSError as e:
                raise Failure(CAPTURE, str(e)) from e
            try:
                started = time.monotonic()
                deadline = started + seconds + 15
                while proc.poll() is None:
                    if self.assert_visible(window) != geo:
                        raise Failure(CAPTURE, "Window moved or changed size during recording; use step.")
                    if time.monotonic() > deadline:
                        raise Failure(CAPTURE, "Recorder timed out; use step.")
                    if time.monotonic() - started >= 3 and proof.exists():
                        seen = [line.rsplit(",", 1)[-1].strip() for line in proof.read_text().splitlines() if line and not line.startswith("#")]
                        quality = output.parent / "quality.txt"
                        if len(seen) >= fps * 2 and len(set(seen)) == 1 and quality.exists():
                            text = quality.read_text()
                            lows = re.findall(r"lavfi.signalstats.YMIN=([\d.]+)", text)
                            highs = re.findall(r"lavfi.signalstats.YMAX=([\d.]+)", text)
                            if any(float(hi) - float(lo) > 2 for lo, hi in zip(lows, highs)):
                                raise Failure(CAPTURE, "No changing source frames in the first three seconds; use step for static content.")
                    time.sleep(0.1)
                if proc.returncode:
                    errors.seek(0)
                    raise Failure(CAPTURE, errors.read().decode(errors="replace").strip())
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait()
        # Validate change before lossy encoding: codec noise must not certify motion.
        checksums = [line.rsplit(",", 1)[-1].strip() for line in proof.read_text().splitlines()
                     if line and not line.startswith("#")]
        # Decode and validate the actual output, not just a recorder's exit status.
        samples = output.with_suffix(".gray")
        self.command("ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(output), "-vf", "scale=64:64,format=gray",
                     "-f", "rawvideo", str(samples), timeout=seconds + 30)
        minimum, count, hashes = float("inf"), 0, set()
        with samples.open("rb") as data:
            for frame in iter(lambda: data.read(4096), b""):
                if len(frame) != 4096:
                    raise Failure(CAPTURE, "Incomplete decoded video frame; use step.")
                minimum = min(minimum, frame_stats(frame))
                hashes.add(hashlib.sha256(frame).digest())
                count += 1
        if count == 0:
            raise Failure(CAPTURE, "No decoded video frames; use step.")
        quality = output.parent / "quality.txt"
        if quality.exists():
            text = quality.read_text()
            lows = list(map(float, re.findall(r"lavfi.signalstats.YMIN=([\d.]+)", text)))
            highs = list(map(float, re.findall(r"lavfi.signalstats.YMAX=([\d.]+)", text)))
            blanks = [hi - lo <= 2 for lo, hi in zip(lows, highs)]
        else:
            raise Failure(CAPTURE, "Missing source pixel quality evidence")
        if sustained_blank(blanks, fps):
            raise Failure(BLANK, "Recording has sustained uniformly blank source frames; wait for actual content or use the app's own export.")
        if len(set(checksums)) < 2:
            raise Failure(CAPTURE, "No changing source frames: static or unavailable window. Use step.")
        distinct = len(hashes)
        if distinct < 2:
            raise Failure(CAPTURE, "No changing video frames: static or unavailable window. Use step.")
        return dict(pixel_size=list(even_size(geo["width"], geo["height"])), frame_spread=minimum,
                    blank_frames=sum(blanks),
                    distinct_frames=distinct, source_distinct_frames=len(set(checksums)), frame_count=count, fps=fps, duration_seconds=count / fps,
                    capture_geometry=geo, backend="ffmpeg X11 absolute screen region",
                    warnings=["X11 screen-region video includes occluders. Stacking/geometry are polled; sub-poll transients cannot be ruled out. Prefer a private display."])

    def encode(self, frames, output, fps):
        dimensions = []
        spreads = []
        for frame in frames:
            dimensions.append(self.command("convert", str(frame), "-format", "%w %h", "info:").strip())
            raw = self.command("convert", str(frame), "-colorspace", "Gray", "-depth", "8", "gray:-", binary=True)
            spreads.append(frame_stats(raw))
            if uniform_pixels(raw):
                raise Failure(BLANK, "Sequence contains a full-frame uniform image")
        if len(set(dimensions)) != 1:
            raise Failure(CAPTURE, "Sequence dimensions changed; use separate sequences.")
        width, height = map(int, dimensions[0].split())
        with tempfile.TemporaryDirectory(dir=output.parent) as temp:
            # A private numbered sequence avoids ffmpeg concat quoting and filename injection.
            for i, frame in enumerate(frames):
                os.symlink(frame, Path(temp) / f"{i:06d}.png")
            self.command("ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                         "-framerate", str(fps), "-i", "%06d.png",
                         "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                         "-movflags", "+faststart", str(output), cwd=temp, timeout=max(60, len(frames) * 3))
        return dict(pixel_size=list(even_size(width, height)), frame_spread=min(spreads),
                    frame_count=len(frames), fps=fps, duration_seconds=len(frames) / fps, backend="ffmpeg checkpoint sequence")


def positive_int(value):
    number = int(value)
    if not 1 <= number <= 120:
        raise argparse.ArgumentTypeError("fps must be between 1 and 120")
    return number


def duration(value):
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 3600:
        raise argparse.ArgumentTypeError("seconds must be greater than zero and at most 3600")
    return number


def sequence_frames(folder):
    frames = sorted(folder.glob("frame-*.png"))
    if not frames or any(p.name != f"frame-{i:06d}.png" for i, p in enumerate(frames, 1)):
        raise Failure(CAPTURE, "Expected a contiguous sequence frame-000001.png onward, made with step.")
    return frames


def capture_one(backend, args, result, output):
    if isinstance(backend, Linux):
        try:
            backend.ready()
        except Failure as error:
            if error.code == CAPTURE:
                ruling("linux_packages", args)
            raise
        if args.verb == "record":
            desktop_recording_warning(result)
        choice = ruling("private_display", args)
        result["private_display_ruling"] = choice
        if choice == "require" and not args.display:
            raise Failure(DISPLAY, "Saved ruling requires an explicitly selected owned private display (--display).")
        if choice == "prefer" and not args.display:
            result["warnings"].append("Saved preference recommends an owned private display; select it explicitly with --display for isolation.")
    result["front_app_before"] = backend.front()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output.parent) as temp:
            staged = Path(temp) / ("movie.mp4" if args.verb == "record" else "frame.png")
            def action():
                staged.unlink(missing_ok=True)
                windows = backend.windows()
                try:
                    window, count = select(windows, args.match, args.exact)
                except Failure as error:
                    if isinstance(backend, Linux) and (backend.env.get("WAYLAND_DISPLAY") or backend.env.get("XDG_SESSION_TYPE") == "wayland"):
                        error.args = (str(error) + " Native Wayland windows are invisible; use an X11 app on an owned private display.",)
                    raise
                matching = [w for w in windows if args.match.casefold() in w["title"].casefold() or args.match.casefold() in w["app"].casefold()]
                skipped = sum(not w.get("capturable", True) for w in matching)
                result.update(window_title=window["title"], app=window["app"], id=normalize_id(window["id"]),
                              process_identity=window.get("process_identity"), match_count=count, skipped_uncapturable=skipped,
                              selection="capturable first, then largest area" if count > 1 else "unique", output_path=str(output))
                if args.verb == "record":
                    return backend.record(window, staged, args.seconds, args.fps)
                return backend.shot(window, staged)
            try:
                details = with_stage_policy(action, backend, args, result)
            except Failure as error:
                if error.code == PERMISSION:
                    ruling("mac_permission", args)
                raise
            warnings = list(result["warnings"])
            result.update(details)
            result["warnings"] = list(dict.fromkeys(warnings + details.get("warnings", [])))
            os.replace(staged, output)
    finally:
        try:
            result["front_app_after"] = backend.front()
        except Failure:
            result["front_app_after"] = None
            result["warnings"].append("Could not read foreground app after capture")


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps(receipt(self.prog.split()[-1], ok=False, exit_code=USAGE, error=message)))
        raise SystemExit(USAGE)


def parser():
    p = ArgumentParser(description="Capture windows without changing focus or window state.")
    p.add_argument("--display", help="Linux X11 display, including a private display owned by your run")
    subs = p.add_subparsers(dest="verb", required=True)
    for verb in ("list", "check", "shot", "step", "encode", "record", "setup", "film-session"):
        sub = subs.add_parser(verb)
        sub.add_argument("--display", default=argparse.SUPPRESS)
        if verb in ("shot", "step", "record"):
            sub.add_argument("match")
            sub.add_argument("--exact", action="store_true")
            sub.add_argument("--stage-timeout", type=duration, default=60)
            sub.add_argument("--decision", action="append", default=[], help="One-run feature=choice when the saved ruling is ask")
        if verb == "shot":
            sub.add_argument("-o", "--output", default="capture-" + uuid.uuid4().hex + ".png")
        if verb in ("step", "encode"):
            sub.add_argument("directory", type=Path)
        if verb == "encode":
            sub.add_argument("-o", "--output", required=True)
            sub.add_argument("--fps", type=positive_int, default=6)
        if verb == "record":
            sub.add_argument("-o", "--output", required=True)
            sub.add_argument("--seconds", type=duration, required=True)
            sub.add_argument("--fps", type=positive_int, default=30)
        if verb == "setup":
            sub.add_argument("--show", action="store_true")
            sub.add_argument("--set", action="append", default=[])
        if verb == "film-session":
            sub.add_argument("command", nargs=argparse.REMAINDER)
    return p


def main():
    args = parser().parse_args()
    result = receipt(args.verb)
    try:
        system = platform.system()
        if args.verb == "setup":
            result.update(setup(args), ok=True, exit_code=0)
            print(json.dumps(result))
            return
        if args.verb == "film-session":
            if system != "Darwin":
                raise Failure(USAGE, "film-session manages the Mac Stage Manager setting.")
            film_session(args.command, result=result)
            result.update(ok=True, exit_code=0)
            print(json.dumps(result))
            return
        if system == "Darwin":
            if args.display:
                raise Failure(USAGE, "--display is for Linux X11.")
            backend = Mac()
        elif system == "Linux":
            backend = Linux(args.display)
        else:
            raise Failure(DISPLAY, "This display is unsupported.")
        if args.verb == "check":
            result.update(backend.check())
            result["settings"] = load_settings()
            result["setup_unanswered"] = questions(result["settings"])
        elif args.verb == "list":
            result["windows"] = backend.windows()
        elif args.verb in ("shot", "record"):
            capture_one(backend, args, result, Path(args.output).expanduser().resolve())
        elif args.verb == "step":
            folder = args.directory.expanduser().resolve()
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / ".capture-lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                existing = list(folder.glob("frame-*.png"))
                if existing:
                    sequence_frames(folder)
                output = folder / f"frame-{len(existing) + 1:06d}.png"
                capture_one(backend, args, result, output)
                try:
                    output.with_suffix(".json").write_text(json.dumps(dict(result, ok=True, exit_code=0)))
                except OSError:
                    output.unlink()
                    raise
        elif args.verb == "encode":
            frames = sequence_frames(args.directory.expanduser().resolve())
            output = Path(args.output).expanduser().resolve()
            result["output_path"] = str(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            sidecars = [json.loads(p.with_suffix(".json").read_text()) for p in frames if p.with_suffix(".json").exists()]
            if any(not isinstance(p, dict) for p in sidecars):
                raise Failure(CAPTURE, "Invalid checkpoint receipt")
            checkpoint_identity(sidecars, result)
            with tempfile.TemporaryDirectory(dir=output.parent) as temp:
                staged = Path(temp) / "movie.mp4"
                result.update(backend.encode(frames, staged, args.fps))
                os.replace(staged, output)
        result.update(ok=True, exit_code=0)
    except Failure as e:
        result.update(e.details)
        result.update(ok=False, exit_code=e.code, error=str(e))
    except (OSError, ValueError, KeyError, TypeError) as e:
        result.update(ok=False, exit_code=CAPTURE, error=str(e))
    except KeyboardInterrupt:
        result.update(ok=False, exit_code=CAPTURE, error="Capture interrupted; owned recorder stopped and staged output removed.")
    for key in ("front_app_before", "front_app_after"):
        result[key] = normalize_front(result[key])
    result["id"] = normalize_id(result["id"])
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(result["exit_code"])
