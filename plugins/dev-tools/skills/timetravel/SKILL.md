---
name: timetravel
description: Take a detour without filling the main thread with it, then fold back only what the detour produced. Use when the user types "/timetravel <side task>" mid-work, and when they later say "time travel" to return. Works in Claude Code, Codex and Cursor.
---

A detour should cost the main thread one message, not its whole transcript. The conversation forks; the disk does not.

On `/timetravel <task>`: reply in one line and start the task. No plan and no recap, because this message is the one the user will rewind over.

On "time travel": write the message the main thread will receive in place of the `/timetravel` message. Write it for an agent that never saw the detour. Say what was done and what it produced: absolute paths, commits, and anything left uncommitted. Include what the user said along the way, what is unfinished, and what we were doing before. Keep it under 400 words. Never mention the detour itself.

Hand over evidence, not your reading of it. When subagents or long research produced the result, point to the files instead of summarizing them. Say what actually reached this session and what exists only on disk. First save anything worth keeping that lives only in a temporary folder or a subagent transcript into the project, because after the fork nothing points to it.

Show the message in a code block and copy it to the clipboard of the machine the user types on. Over SSH there is no such clipboard, so say it must be copied by hand. Then ask the user to rewind to the `/timetravel` message and replace it with this one; in a runtime without rewind, paste it as the first message of a fresh session.

If `/timetravel` is typed again mid-detour, emit the current catch-up first, then start the new detour.
