# Contributing

Skills live in `plugins/<plugin>/skills/<skill>/`. Each has a `SKILL.md` and whatever it needs beside it, such as `scripts/`, `references/` and `tests/`.

Before opening a pull request:

1. Keep `SKILL.md` short. State the idea, and put detail in references.
2. Keep the frontmatter `description` to 1024 characters or fewer.
3. Run the skill's tests, and `claude plugin validate .` from the repository root. After changing a `plugin.json` or the marketplace, run `python3 tools/sync_codex.py`.
4. Writable state belongs in `~/.devdroid/<skill>/`, never inside the skill.
5. Put no personal data, hostnames or private paths in anything committed.
6. Code kept from another project carries its license and notice, and gets an entry in `NOTICES.md`.

The working rules for building here are in `plugins/dev-tools/skills/building-rules/SKILL.md`.
