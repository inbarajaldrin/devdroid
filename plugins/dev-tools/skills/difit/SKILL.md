---
name: difit
description: Show the person a change as a GitHub-style diff in their browser and wait for their review, using difit, a local diff viewer. Use when the user wants to review changes, look at what changed before a commit or push, check a branch or pull request visually, or give approval on a diff, and when you have finished a change and want their eyes on it rather than pasting a diff in the terminal. Also use to point them at specific lines with your own comments, and to collect the comments they leave.
---

A person judges a change better on a rendered diff than in a terminal, and their approval is the reason to open one. Show them exactly what is about to be committed, wait for them, and act on what they said.

difit needs Node 21 or newer. From inside the repository run `scripts/difit.sh up`, which shows everything uncommitted and includes new files. Name another target to narrow it: `up staged`, `up working`, `up HEAD~2`, `up feature main`, or `up -- --pr <url>`. Never run bare `difit`: it shows the last commit rather than the work in progress. `up` prints the files in the diff and the address. Check that the list matches what you meant to show before you give the address to the person.

There is no signal when a review is finished, so wait until the person says so. Then `scripts/difit.sh comments` prints what they wrote; nothing printed means they had nothing to change. After you edit, tell them to refresh the tab; a change of target needs another `up`.

To point them at particular lines, pass difit's comments at start: `up . -- --comment '{"type":"thread","filePath":"src/a.py","position":{"side":"new","line":42},"body":"Intended?"}'`. Use `"old"` for deleted lines. Never put a secret from the diff in a comment or a command.

When the review is over, read the comments, then run `scripts/difit.sh down`. It stops only this repository's viewer, and it discards the comments it has not printed. `up` starts one per repository, so several can run at once, and the address it prints is the one to use. For another computer, `up . -- --host 0.0.0.0` exposes the diff to everyone on that network, so ask first. Other flags: `npx --yes difit@5 --help`.
