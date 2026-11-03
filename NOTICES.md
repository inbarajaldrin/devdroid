# Upstream Notices

devdroid incorporates code, text and ideas from the following open-source projects. Each is listed with its source, license, and scope of use in this repository.

## mattpocock/skills

**Source:** https://github.com/mattpocock/skills (`skills/productivity/handoff`)
**Copyright:** Copyright (c) 2026 Matt Pocock
**License:** MIT
**Scope:** Adapted. devdroid's `handoff` started from the upstream skill's text (write a handoff a fresh agent can
continue from; suggested skills; reference artifacts instead of copying them; redact secrets; tailor to arguments)
and changes where it is saved and what it must capture.
**Used in:**
- `plugins/session-tools/skills/handoff/`
- `plugins/dev-tools/skills/building-rules/`: ideas only, reworded, from `skills/engineering/codebase-design` (deep modules: the interface is everything a caller must know, depth, seams, and the interface as the test surface). No text copied.
- `plugins/session-tools/skills/continue-from-handoff/` (devdroid's own companion that reads handoffs back)

---

## Dimillian/Skills

**Source:** https://github.com/Dimillian/Skills (`macos-spm-app-packaging/assets/templates`)
**Copyright:** Copyright (c) 2026 Thomas Ricouard
**License:** MIT
**Scope:** Copied. Four scripts for packaging, running, signing and notarizing a Mac app built with SwiftPM, each
carrying a reference header; the MIT license text sits beside them in `scripts/LICENSE`. `setup_dev_signing.sh` is
modified to write a PKCS#12 file macOS can import under OpenSSL 3.
**Used in:**
- `plugins/docs/skills/apple-docs/scripts/`

---

## openai/codex-plugin-cc

**Source:** https://github.com/openai/codex-plugin-cc
**Copyright:** Copyright OpenAI
**License:** Apache-2.0
**Scope:** Copied and adapted. The prompt blocks and anti-patterns and the review output schema are copied
unchanged. The prompting guide, prompt recipes, result-handling guide and adversarial-review prompt are adapted from
its `gpt-5-4-prompting` and `codex-result-handling` skills and `prompts/adversarial-review.md`; each adapted file says
so in its first line.
**Used in:**
- `plugins/session-tools/skills/ask/references/prompting/` (all files except `claude-prompting-guide.md`, which is devdroid's own)
- `plugins/session-tools/skills/ask/prompts/adversarial-review.md`
- `plugins/session-tools/skills/ask/schemas/review-output.schema.json`

---

## ponytail

**Source:** https://github.com/DietrichGebert/ponytail
**Copyright:** Copyright (c) 2026 DietrichGebert
**License:** MIT
**Scope:** Ideas only, reworded. Its ladder for the smallest complete diff (whether it needs to exist, what already does it, then the smallest change) shaped one paragraph. No text or code copied.
**Used in:**
- `plugins/dev-tools/skills/building-rules/`

---

## tennox gist: fix-cc-session-for-gist

**Source:** https://gist.github.com/tennox/90ef5c803ec4b64c9fbba0f71ca1ae2e
**License:** none stated
**Scope:** Ideas only. Its two transcript repairs (clear a file-history snapshot `messageId` that collides with the
next message, and repoint a missing parent to the preceding message) are reimplemented in Python. No code copied.
**Used in:**
- `plugins/session-tools/skills/fork-session/scripts/claude_fork.py`

---

## tty-airdrop and airdrop-cli

**Source:** https://github.com/su-mt/tty-airdrop and https://github.com/vldmrkl/airdrop-cli
**License:** MIT (both)
**Scope:** Ideas only. Both open the AirDrop picker through `NSSharingService(named: .sendViaAirDrop)` with a
sharing-service delegate; devdroid's helper uses the same approach, written independently. No code copied.
**Used in:**
- `plugins/utils/skills/airdrop/scripts/airdrop_open.swift`

---

## Notices kept inside plugins

Some plugins carry their own notices beside the code they cover:

- `plugins/sign-in/NOTICE`: OpenCLI 2.0.2 (Apache-2.0), adapted authentication identity verification.
- `plugins/career/THIRD_PARTY_NOTICES.md`: career-ops, ai-job-search and Feather icons (all MIT).

---

## How to add a new upstream

When a plugin imports, copies, or adapts code from an upstream project:

1. Preserve the upstream copyright header in the derived file.
2. Add an entry to this file with source, license, and scope.
3. Verify the upstream's license is compatible with Apache-2.0. MIT, BSD, Apache-2.0, and similar permissive licenses are compatible. GPL/AGPL are NOT compatible and must be either refused or flagged in a separate "Copyleft dependencies" section.
4. For conceptual influence (no code copied), list the project with its source and license; derived files need no copyright header.
