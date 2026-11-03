# Command contract

The entrance is `scripts/capture`, using Python 3 standard library. Mac capture uses
Swift and ScreenCaptureKit; Linux capture uses X11 tools. `check` is read-only and
reports dependencies, actual helper permission, display/session state, saved answers,
and unanswered setup questions. It never installs packages or requests permission.

```sh
scripts/capture setup --show
scripts/capture setup --set stage_manager=wait
scripts/capture list
scripts/capture shot 'literal title or app' -o still.png
scripts/capture step 'literal title or app' frames/
scripts/capture encode frames/ -o steps.mp4 --fps 6
scripts/capture record 'literal title or app' -o live.mp4 --seconds 5 --fps 30
scripts/capture --display :123 shot 'literal title or class' -o still.png
```

## Setup and saved answers

`setup` asks only unanswered questions in an interactive terminal, one feature per
plain line. Otherwise it returns those questions as JSON for the agent to ask in
chat. `setup --show` reads; `setup --set feature=choice` changes an answer without
re-asking others. Answers are saved atomically in
`~/.devdroid/capture-window/settings.json` and no capture verb repeats those questions.
The choices explain who makes the change; none authorizes an agent to grant access,
install packages, or change Stage Manager.

| Feature | Recommendation and choices |
| --- | --- |
| `mac_permission` | Recommend granting Screen Recording access to the invoking terminal yourself before capture: `person_grants`, `unavailable`, `ask`. |
| `linux_packages` | Recommend installing X11 tools, ImageMagick and ffmpeg yourself before capture: `person_installs`, `unavailable`, `ask`. |
| `private_display` | Recommend an owned private Xvfb/Xephyr display: `prefer`, `require`, `shared`, `ask`. `require` rejects captures without explicit `--display`. |
| `stage_manager` | Recommend bringing the shrunk app forward for about a second, capturing it full size and returning to the person's app: `bring-forward`, `wait`, `report`, `ask`. |

A saved `ask` remains saved. When that feature needs a decision, the receipt contains
`decision_required`, a question and choices. Supply `--decision feature=choice` for
this run; it does not replace the saved ruling. Captures never prompt on stdin.
Permission and package decisions are needed only when access or dependencies are missing.

Mac needs Xcode command line tools (`swiftc`); the native `codesign` verifier checks
cache integrity. Screen Recording is preflighted in the actual helper. A grant may
follow the invoking terminal; a rebuilt helper might need a different grant. Source
hashes select cached builds outside the skill, and invalid binaries are rebuilt.
Linux needs `xwininfo`, `xprop`, ImageMagick (`import`, `convert`), and ffmpeg for video.
`ffprobe` is used by live tests. Optional Xvfb/Xephyr keeps the app off the person's
desktop; set `LIBGL_ALWAYS_SOFTWARE=1` for its apps. No Python packages are required.

## Stage Manager stays on

Still error `-3811` and stream `contentScale < 1` are measured thumbnail signals.
Those pixels are refused even if a stream labels them complete. CG visibility and
active-display intersection support selection; SCK `isOnScreen` alone is never a
filter. Small dimensions alone do not mean a thumbnail.

With `bring-forward` (the default), the capture brings the app forward for about a
second, takes the full-size shot, returns to the app that was in front, and says so in
the receipt's warnings. With `stage_manager=wait`, the capture prints one line explaining the temporary
Control Centre switch, polls the preference read-only, retries after it is off,
and reminds the person to switch it back on at the end, including failure or timeout.
`--stage-timeout` defaults to 60 seconds. `report` returns the failed receipt without
waiting. The skill does not suggest turning Stage Manager off permanently.

There is one person-run wrapper, which the agent **must never execute**, including
through a PTY:

```sh
scripts/capture film-session -- scripts/capture record 'literal title or app' -o live.mp4 --seconds 5
```

Both stdin and stdout must be real terminals. The wrapper serializes sessions,
reads the earlier preference, disables Stage Manager for the person's command, and
restores that exact value in `finally`, including command failure and interruption.
Only this verb writes the setting. Its JSON receipt reports the earlier value,
command exit code, and restoration status. Every other verb is read-only for it.
The wait message offers `capture film-session -- <command>` as the alternative to
the temporary Control Centre switch. Actual restore failure is reported as failure.

## Matching and outputs

Title and app/class matching is literal and case-insensitive, including punctuation.
Capturable matches rank first, then largest area, then stable id. The receipt reports
all matches and how many unavailable ones were skipped. `--exact` requires exactly
one matching row, including unavailable matches; it does not change substring syntax.
`list` includes `capturable` and `capture_state`; those are metadata checks, with
pixel availability still validated during capture. Closed windows fail distinctly.

Without `-o`, `shot` chooses a unique `capture-<random>.png`. `step` serializes writers
and saves consecutive `frame-000001.png` files with receipt sidecars. `encode` requires
a contiguous sequence with consistent dimensions, pads odd dimensions by one row or
column, and defaults to 6 fps. It warns about mixed/missing identities and about window
id reuse; it never claims provenance is verified. This is checkpoint pacing, not real time.

`record` requires a positive `--seconds` up to 3600 and defaults to 30 fps; allowed
rates are 1–120. It requires meaningful source-pixel change as well as decoded frame
change. Static content fails early in long runs and points to `step`. A brief dark
frame is allowed; uniformly blank output or a sustained blank interval is refused.
Full-resolution variation preserves sparse dark content; sampled spread is diagnostic.
Mac repeats valid frames across idle gaps and refuses thumbnails, unavailable windows,
and a writer that cannot keep real time. Receipts include capture elapsed time.
No output replaces an existing file until its capture/encoding has succeeded.

## Linux display limits

`--display` works before or after the verb and selects an existing X11 display; it
does not create one or prove ownership. Leave login displays alone. Wayland session
variables are reported explicitly: only X11/Xwayland windows are visible here;
native Wayland capture is unsupported. A missing native window is not a missing app.

X11 captures the absolute client rectangle, including related popups within that
rectangle; popup portions outside it are clipped. InputOnly windows are ignored.
Unrelated visual overlap is refused. Live desktop recording polls stacking and
geometry, so a brief occluder appearing between checks can be filmed. **Every Linux
recording receipt warns about this and recommends an owned private display** for
isolation from the person's desktop. `occlusion_guarantee` is always `polled only`.
The tool makes no stronger claim for an arbitrary display argument.

## JSON and exit codes

Each capture prints one JSON object: `tool`, `verb`, `window_title`, `app`, `id`
(string), `pixel_size`, `front_app_before`/`after` (`{app,id}` or null), `frame_spread`,
`output_path`, `warnings`, `ok`, and `exit_code`. Unknown values are null. Foreground
changes made elsewhere remain in the receipt. Apart from the Stage Manager
bring-forward, the tool never changes or restores focus.
Mac recordings also sample foreground identities during capture. Keep receipts
private: actual titles and foreground app names belong to the run, not public evidence.

| Exit | Meaning |
| --- | --- |
| 0 | Success |
| 2 | Invalid arguments, missing human decision, or refused person-only wrapper |
| 3 | Mac Screen Recording grant absent; no prompt requested |
| 4 | No match, or the selected window closed |
| 5 | Ambiguous `--exact` match |
| 6 | Unavailable, thumbnail, or uniformly blank pixels |
| 7 | Capture, dependency, static live content, timing, encoding, or interrupted run |
| 8 | Unsupported/unavailable X11 display or a required private-display selection missing |

`CAPTURE_WINDOW_HOME` and `CAPTURE_WINDOW_SETTINGS` provide isolated test locations;
normal use needs neither. [Tests](../tests/README.md) describe verified coverage and
the remaining person-run live checks. A still alone cannot prove arbitrary freshness.
