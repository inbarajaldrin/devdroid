---
name: gitpass
description: Catch information drift and process residue in code — stale or now-false claims, any wording that only makes sense if you know a prior or now-erased state, and anything written for the wrong reader. The code should read as timeless truth, with git history holding how it got there. Use when the user says "gitpass". Defaults to the diff between the last commit and uncommitted work; scans the whole codebase only when asked.
---

Review for three things, in order. By default look only at the diff between the last commit and the current uncommitted work (`git diff HEAD`); scan the whole codebase only when asked. Before a release, review the whole unpublished range (for example `origin/main..dev`), not only uncommitted work.

1. Information drift — first. Find anything this change makes false: a comment, docstring, name, value, or doc that no longer matches what the code now does. Reconcile each to the current truth.

2. Process residue. The code, comments, and commit message should be timeless statements of what is true now; git history is the record of how you got there. Process residue is any sentence that only makes sense if you already know a prior or now-erased state. Flag each and rewrite it to stand on its own — as if the current understanding was always held.

3. Audience. Each place has its own reader. Code, comments, docs and test data serve the project's users: they carry no internal identifiers (job or ticket ids, hostnames, private addresses, reviewer or session labels, other projects' names), and test data uses neutral names and documentation-reserved addresses. The working branch's history may keep the internal record. A release branch gets one curated commit per release, its message written for users. Never rewrite a branch whose commits other records cite; curate on the release branch instead.

Release media: an image or video in a README renders inline only from a GitHub `user-attachments` URL, never from a committed file. `scripts/upload_readme_media.py OWNER/REPO <file>` uploads one the way `gh --attach` does and prints that URL; it is idempotent (a receipt beside the file), since an upload cannot be undone.

Report findings one at a time, each with its fix; apply on confirmation.
