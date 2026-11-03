# One application and recovery

Use an ID from the actual inbox as `POSTING`. The person picks the role in the app or personally runs `pick` in an interactive terminal. From the workspace, with its venv Python:

```sh
python tools/career.py pick "$POSTING" approved
python tools/career.py fill "$POSTING"
python tools/career.py review "$POSTING"
python tools/career.py gate "$POSTING"
```

Fill reads reachable steps, drafts from real material and runs a reviewer before the person's first pass. Standard fields auto-approve; long custom replies (25 words by default) and flagged boxes wait for the person. Missing facts create one job thread. Chat answers use the save entrance in `answers.md`. Human-only actions happen in the browser. Show the exact URL, headings, values and submission terms before approval.

The resume uses `resume_pdf` from settings (or this application's resume in tailored mode). Optional uploads remain empty unless a readable local file is explicitly supplied in the profile's `standard_answers` under the exact upload label. For a cover letter, save its plain local path in `cover-path.txt`, then use the same chat-answer entrance:

```sh
python tools/career.py save --target '{"profile_key":"standard_answers.Cover letter"}' --value-file cover-path.txt
```

The filler writes the native file input, never Attach, Dropbox, Google Drive or Enter manually. The preview includes the uploaded filename and an owned copy of the verified bytes. A change to the attached file requires a matching new approval.

When the person edits a box, the app calls the same workflow as the CLI. A file holds their exact edit:

```sh
python tools/career.py box "$POSTING" 'Why this role?' edit --value-file reply.txt
python tools/career.py apply-edits "$POSTING"
python tools/career.py review "$POSTING"
```

The working agent reads back edits. A second full reviewer pass is unnecessary; newly reachable steps get their own first review. Once all boxes resolve, the person approves this application using `DISPLAYED_DIGEST` from the current review:

```sh
python tools/career.py approve-submit "$POSTING" --digest "$DISPLAYED_DIGEST"
python tools/career.py submit "$POSTING"
python tools/career.py tracker
```

Personal CLI decisions require the person to type yes; an agent must not impersonate them. The browser revalidates identity, every step/value, destination and control before submitting. Intent is durable before the click. Success needs positive confirmation and a PNG snapshot.

If sending is uncertain, inspect the same saved tab and run `confirm`; never repeat Submit. A vanished form proves nothing. Only the person can choose manual submission after verifying nothing was sent. If confirmation cannot be recovered, their `resolve-uncertain` decision frees the active slot while preserving the attempt marker. For an unattempted form use `release` before switching. Resuming `fill` recovers its step journal after a restart.
