---
name: continue-from-handoff
description: Resume from the newest handoff in this project. Use when the user says /continue-from-handoff, "pick up from the handoff", or "continue where we left off".
argument-hint: "[handoff filename, or blank for the newest]"
---

Load the newest handoff and carry on from it. Handoffs live in `.scratchpad/handoff/` under the project root. Only files whose names start with `YYYYMMDD-HHMMSS` count, sorted byte by byte; a file the user names wins. Say which file you picked. If there is none, say so and stop; never invent context.

Read the whole file. Tell the user in two or three lines what was done, what is left, and the next action, then start it.
