# `cq provenance` — trace agent activity on a file

Find which agent sessions created, edited, read or ran/referenced a file, with the
session's first real request explaining why. It reads raw transcripts directly;
no index refresh or model call is involved.

## Op-kind taxonomy

| Bucket | Recorded operation | Meaning |
|---|---|---|
| **Authored** | Claude/Cursor `Edit`, `Write`, `MultiEdit`, `NotebookEdit`, `Update` | A tool can create or modify file content. |
| **Authored** | Codex `apply_patch`, including patches inside `exec` calls | A patch names the file. |
| **Authored** | Shell writes: `>`/`>>`, `tee`, `cp`/`mv`/`install`/`rsync` destinations, `sed -i`/`perl -i`, Python `open(..., 'w')`/`.write_text` | The command can write the file. |
| **Inspected** | `Read`, `NotebookRead` | A file-read tool inspected content. |
| **Shell-touched** | Bash or Codex shell/code commands referencing the file without a recognized write | The command ran, read or otherwise referenced the path; inspect the drill to distinguish these. |
| **Mentioned** | Other tool inputs carrying the path, such as `Glob` | Metadata reference, hidden unless `--show-mentions` is set. |

Rows are session summaries. An author can also have reads and shell references;
the highest-priority bucket determines the kept row's section. Claude main-chain
operations rewound away are grouped under **On abandoned branches (rewound away)**.
Inline `isSidechain: true` work stays credited: it ran outside the main chain.
Codex/Cursor operations are all treated as kept.

## Matching and resolution

```bash
cq provenance tools/scripts/lab.sh
cq provenance lab.sh
```

A path fragment pins the file's directory context. Shell matching compares
resolved path operands at directory boundaries, never falling back to just the
basename. Assignments in the same command and leading `cd` commands are resolved;
relative paths start at the session cwd. For example, `cd vm-lab &&
./scripts/lab.sh up` and `L=~/tools/scripts/lab.sh; $L up` can reference the
same fragment. The same resolution is used for shell writes and shell-touched
classification. Quoted paths with spaces are supported.

A bare filename deliberately matches broadly across directories. The header says
`Path: lab.sh (filename-only match; any directory)`. A May session using a different
`lab.sh` therefore belongs in the bare query, but not in the directory-specific
query. Direct file-tool fields (`file_path`, `path`, `notebook_path`, `notebookPath`,
`filePath`, `filepath`) retain substring matching. Codex patches use resolved path
matching. The basename-only mmap prefilter selects candidate JSONLs; it does not
classify a match.

## Output shape — fresh path-fragment run

Example output (session IDs replaced with placeholders):

```bash
cq provenance tools/scripts/lab.sh
```

```text
Path: tools/scripts/lab.sh

Authored (Edit/Write/MultiEdit, Codex apply_patch, or a shell write — >, tee, cp/mv, sed -i):
  11111111-2222-3333-4444-555555555555  mac          claude-code  2026-10-28T22:19:27  (2 edits, 1 shell writes)
    "add retry handling to the upload step"

Shell-touched (Bash — created/read/referenced via subprocess):
  11111111-2222-3333-4444-555555555555  mac          codex     2026-10-29T23:56:24  (4 bash)
    "add a dry-run flag to lab.sh"
    command: sed -n '1,80p' tools/scripts/lab.sh
    cq context 11111111-2222-3333-4444-555555555555 @579

First authored: 2026-10-29T10:28:17 by 11111111-2222-3333-4444-555555555555 on mac
Last authored:  2026-10-29T23:44:33 by 11111111-2222-3333-4444-555555555555 on mac
```

The kept author is the October session. The Codex row gives the session request,
the first matching command (one line, at most 120 characters), and a ready
`cq context` drill for the command's zero-based physical JSONL line. Rows with no
recoverable real user request omit that intent line. Markdown task headings are
kept; injected AGENTS instructions and inter-session routing banners are skipped.

First/Last authored use actual kept authoring timestamps, including shell writes;
the timestamp column still shows the session start. If only abandoned authorship
exists, the footer says `First/Last authored: no kept-path authorship; only
abandoned-branch edits exist.`

## Worked example — broad filename versus the specific file

This second fresh local run intentionally uses the bare filename. It retains the
unrelated May session and the abandoned June conversation, demonstrating why the
path fragment is needed to trace the October script specifically.

```bash
cq provenance lab.sh
```

```text
Path: lab.sh (filename-only match; any directory)

Authored (Edit/Write/MultiEdit, Codex apply_patch, or a shell write — >, tee, cp/mv, sed -i):
  11111111-2222-3333-4444-555555555555  mac          claude-code  2026-10-28T22:19:27  (2 edits, 1 shell writes)
    "add retry handling to the upload step"
  11111111-2222-3333-4444-555555555555  mac          codex     2026-10-29T23:56:24  (1 shell writes)
    "add a dry-run flag to lab.sh"

Shell-touched (Bash — created/read/referenced via subprocess):
  11111111-2222-3333-4444-555555555555  mac          claude-code  2026-05-14T05:42:23  (18 bash)
    "check why the streaming server keeps restarting"
    command: ssh workstation 'bash -s' <<'EOF' echo "=== Kill streaming server + client ===" docker rm -f app-stream 2>&1 | hea
    cq context 11111111-2222-3333-4444-555555555555 @1400

On abandoned branches (rewound away):
  11111111-2222-3333-4444-555555555555  mac          claude-code  2026-06-23T16:39:35  (2 bash)
    "check the lab script's network setup"
    command: ssh workstation 'echo "=== search project_env ==="; find ~ -maxdepth 5 -type d -iname "*project*" 2>/dev/null | head; f
    cq context 11111111-2222-3333-4444-555555555555 @95

First authored: 2026-10-29T10:28:17 by 11111111-2222-3333-4444-555555555555 on mac
Last authored:  2026-10-30T03:06:18 by 11111111-2222-3333-4444-555555555555 on mac
```

The shell-touched May row supplies its request, command and drill, rather than
looking like unexplained prior authorship. Abandoned shell rows have the same
command detail. “Abandoned” describes a conversation rewind, not an on-disk
rollback. One session can have both kept and abandoned rows. Counts are merged by
`(session_id, host, abandoned)`; the first command is retained separately for each
category. `--limit-reads` caps kept reader and shell-touched rows.

## JSON

```bash
cq provenance tools/scripts/lab.sh --json
```

Keys: `path`, `authored_by[]`, `inspected_by[]`,
`shell_touched_by[]`, `mentioned_by[]`. Both kept and abandoned rows live in their
corresponding buckets; every row has an `abandoned` boolean. Filter
`abandoned == false` to credit kept conversation work. Author rows retain
`first_authored_ts` and `last_authored_ts`.

Shell rows include `intent`, `first_command` (single line, at most 120 characters),
and `first_command_line` (zero-based physical line). A row with no shell operation has an empty command and
`null` command line. Remote drills also include `--host`.

## What to do next

- `cq context 11111111-2222-3333-4444-555555555555 @1400` opens the matching turn.
- `cq why 11111111-2222-3333-4444-555555555555 @2749` follows later revisions and evidence.
- `cq grep "tools/scripts/lab.sh"` lists raw literal hits, including text and tool output.

## Limits

Only file activity represented in agent transcripts is visible. Your manual edits,
changes caused by git checkouts/pulls, and changes from other applications are not
reconstructed. Tool inputs show intended operations; they do not prove a command
succeeded or that a write survived on disk. Shell parsing is a heuristic, not a
shell interpreter: dynamic paths assembled by a program or inherited variables
can be missed. File references inside command/code strings can also be counted.
Use `context` to read the evidence and `why` to follow how the reasoning changed.

## Cross-host and flags

```bash
cq provenance tools/scripts/lab.sh --host h1 --host h2
```

The remote host's cq walks its own providers and returns JSON rows. If it lacks
branch classification or shell command detail, cq falls back to fetching Claude
JSONLs and classifying them locally. The fallback is Claude-only; an up-to-date
remote cq is needed for its Codex/Cursor records. Fetching remote transcripts
uses the existing remote cache; the provenance classifier itself uses no index.

| Flag | Behavior |
|---|---|
| `--host TARGET` (repeatable) | Include a remote host's transcripts. |
| `--show-mentions` | Include the Mentioned section and mention-only abandoned rows. |
| `--limit-reads N` | Cap kept reader/shell-touched rows in human output (default 10). |
| `--json` | Emit structured bucket rows with branch and first-command detail. |

A full local scan uses a parallel mmap prefilter, then parses the matching files.
Cost depends on transcript size; it does not create or refresh the FTS index.
