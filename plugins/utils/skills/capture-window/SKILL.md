---
name: capture-window
description: Take a screenshot or recording of an app window on a Mac or Linux (X11) without interrupting what the person is doing. Use for “screenshot this app”, “show me the window”, “record the GUI”, “film this”, “what does the app look like now”, and whenever an agent needs to inspect its own UI work.
---

The person's screen is theirs. Never move, resize, minimize or focus their windows, and
never change their settings. The one exception is the brief Stage Manager bring-forward
below, which returns them to the app they were using. An unavailable picture is an
honest failure, not a reason to disturb them.

Prefer the app's own export, then a background still, then checkpoint stills encoded
as a sequence, then live video. An owned private Linux display isolates the GUI from
the person's desktop. App-specific capture recipes belong with the app.

One entrance: `scripts/capture`. Read [the command contract](references/commands.md)
for setup, saved rulings, receipts, failures, and the limits of each backend.
Use `setup --show` to obtain unanswered questions; ask each plainly with its
recommendation and save the person's choices with `setup --set feature=choice`.
Never re-ask a saved ruling. A saved `ask` requests a decision only for the current run.

Stage Manager stays on for everyday use. When it has shrunk the window, bring that
app forward for about a second, take the full-size shot and return to the app that was
in front; say so in the receipt. Never change the setting. A saved wait or report ruling
overrides this. Only the person may run `film-session -- <command>` in their own real terminal;
it disables Stage Manager for that command and restores the earlier value in `finally`.
The agent never runs that verb, requests a permission prompt, or writes the setting.
Inspect both receipt and picture: still pixels alone cannot prove app freshness.
