---
name: handoff
description: Write a handoff document so a fresh agent can continue this work. Use when the user says /handoff, "write a handoff", or is wrapping up a session.
argument-hint: "What will the next session be used for?"
---

Write a handoff so a fresh agent can continue without re-deriving anything. Save it in `.scratchpad/handoff/` under the project root (git root, else the current folder) as `<YYYYMMDD-HHMMSS>-<session-id>.md`, with the id from `scripts/session-id.sh`. Keep one handoff per session: if this session already has one, rewrite it. In a git repository, make sure `.scratchpad/` is gitignored.

Open with a plain `## Standing notes` section: copy the previous handoff's standing notes word for word and add any rule the user said must always carry. Never drop one, because it exists nowhere else. Then name the one task the next agent picks up first.

Sweep the whole session, not its last stretch. What gets lost is what the user decided: rulings and reversals, requests parked for later, and anything established that outlives the current task. Put those next, then what is done and what is left.

Make it resumable. The session id in the file name lets the next agent open this session with /browse-chat-history. List any agents you started that can still be resumed, and how to reach them. Say whether the working tree has uncommitted changes.

Point, don't copy: anything already in a spec, plan, commit or diff gets a path. Save scripts worth keeping that live only in a temporary folder into the project. Redact keys, passwords and personal data. End with the skills the next agent should use. If the user passed arguments, shape the handoff around what the next session is for.

Then raise what this session taught about skills. A skill that got in the way, or a multi-step workflow worked out by hand that no skill covers yet: tell the user what you saw and the fix or new skill you'd propose, and ask whether to do it now. Setting it up while the evidence is fresh spares every future session the same work.
