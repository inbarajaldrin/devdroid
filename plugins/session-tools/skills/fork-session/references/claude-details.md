# Claude storage and repair details

The source can be a full JSONL path or an exact session UUID found under
`~/.claude/projects/*/<uuid>.jsonl`. `--project-dir` overrides source lookup.
The script derives paths from the source and the user's home, independent of
the invoking agent or working directory. No dependency manager is needed.

For `--target-cwd`, create the directory first. The explicit override resolves to
the physical directory (including `/var` → `/private/var` on macOS), as Claude's
new-project lookup does. Saved source cwd is still read verbatim without an override.
Its project key replaces characters
other than ASCII letters, digits and hyphens with `-`, matching ordinary Claude
project paths. Use a short temporary cwd for verification; unusual very long
paths may use a Claude-version-specific project key and need inspection.

## Selection and repairs

Both UUID and line boundaries select one parent chain, rather than copying all
messages physically preceding the cut. An abandoned arm can contain earlier
sibling arms too; walking ancestry prevents these from contaminating the fork.
Non-UUID housekeeping records within the prefix are retained unless they point
to a parent outside the chosen chain. The script does not append housekeeping
with a later UUID after the selected message or silently finish a tool call.

Two repairs run in memory within the selected prefix before walking ancestry:

- Null a file-history-snapshot `messageId` that collides with the next UUID.
- Repoint a missing parent UUID to the preceding UUID. Existing parent pointers,
  including branch divergences, are preserved. A missing first parent or a cycle
  is refused. An unknown corruption pattern may require further inspection;
  repair success does not establish that all original context was recovered.

Collision/broken-parent logic originated in the
[tennox gist](https://gist.github.com/tennox/90ef5c803ec4b64c9fbba0f71ca1ae2e).

## Backup and output

Every run uses a unique backup directory, even for repeated calls within one
second with the same label. It contains the exact source JSONL and available
sidecars under `sidecars/<kind>/<source-id>/`, where kind is `projects_subdir`,
`session-env`, `tasks`, or `file-history`.
If backup fails, no destination is written. A failed destination write or sidecar
copy removes only artifacts created by that attempt; the backup is retained.

The new JSONL uses a fresh session ID and mode 0600. Message UUIDs stay stable so
parent links remain valid. Available sidecars are copied as opaque directories;
their contents are not a historical filesystem snapshot at the cut. In particular,
forking does not undo edits or replay old task actions. Backups use mode 0700
directories and contain the original private transcript.

`checks` reports repair counts, selected chain size, root reachability, output
line count and leaf UUID. The resume command shell-quotes the verbatim cwd.
If the leaf is not an assistant message, Claude may inject "Continue from where
you left off" / "No response requested" on the first resume.
