# Running the tests

From the directory containing the skill:

```sh
python3 -B -m unittest discover -s capture-window/tests -v
```

Use `-B` so Python does not cache test bytecode beside the relocated skill. All
fixture files, images, movies, setup answers and temporary builds use cache-owned
test directories. The public entrance disables library bytecode writes itself.
`CAPTURE_WINDOW_HOME` and `CAPTURE_WINDOW_SETTINGS` isolate test state. On a remote
Linux run, set the home override to a directory inside the private run folder;
pass the SSH destination through your own environment variable, never this skill.

Unit tests fake parsing, matching, geometry, settings, terminal checks, failures,
quality signals, provenance and receipts. Native policy tests use generated pixels
and metadata. `film-session` tests always inject a fake settings command and fake
terminal checks; never invoke the real wrapper as an agent, even to test refusal.

Mac live tests compile an owned AppKit fixture with activation prohibited. They
capture only that fixture, including sparse dark content and brief dark gaps;
closure, hiding, minimization and resizing affect owned fixture windows only. They
check that neither the fixture nor helper becomes front; unrelated activity is
allowed and remains in receipts. Three concurrent first builds and cache repair
run in an isolated cache. A missing grant fails without a permission prompt.

Linux live tests need Xvfb, Openbox, xterm, glxgears, X11 utilities, ImageMagick,
ffmpeg and ffprobe. They create a private display with `-displayfd` and software
rendering. Owned Xlib overlays use ctypes and the system Xlib, not Python packages.
Coverage includes absolute nonzero geometry, sparse dark content, dark-gap movies,
closure, unrelated occlusion, InputOnly/related overlays, percent paths, concurrent
steps, odd dimensions, static early exit, and SIGTERM child/staging cleanup.

Only an absent platform or private display is skipped, with a reason. Other missing
prerequisites fail. Use `gtimeout` on Mac and `timeout` on Linux for an outer guard.

The remaining live checks belong to the person at the Mac:

1. With an owned throwaway target already thumbnailed and the saved `wait` ruling,
   temporarily switch Stage Manager off in Control Centre for the run. Confirm
   capture resumes by itself and reminds you to restore it; switch it back on.
   Do not ask an agent to force another real shrink or operate that control.
2. In your own real terminal run `scripts/capture film-session -- <command>`.
   Check the earlier value and restoration fields, the actual setting afterward,
   and command success, failure and interruption. Agents must never execute this
   real wrapper, including through a PTY; automated tests use only fake commands.
