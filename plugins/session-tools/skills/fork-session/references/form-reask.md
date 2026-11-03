# Re-ask an unanswered or mis-answered Claude form

Use `claude_fork.py <session> --reask-form [tool_use_id]`. Without an ID it
selects the last recorded AskUserQuestion; supply its tool-use ID if later
forms exist or the target lives on a specific abandoned arm. Locate it with
browse-chat-history's `cq grep`, `cq context <id> @N` and raw-record inspection.

A naive cut at AskUserQuestion leaves a dangling `tool_use`; Claude's API
requires a matching `tool_result`. The helper reproduces the interrupted-form
state in a new session:

1. Walk the parent chain through the assistant record containing the form.
2. Drop the old answer and later messages.
3. Append a user `tool_result` with `is_error: true` for every pending tool call,
   including parallel calls in that record. The rejection asks the agent to stop
   and wait for the user.
4. Append `[Request interrupted by user for tool use]`, chaining both appended
   records to the chosen leaf. Verify that no calls remain unresolved.

The new records inherit session metadata, then receive the fork's new session
ID and any requested target cwd. Original JSONL and sidecars remain untouched.
Resume using the printed command and prompt **"re-ask what you just asked"**;
the rejection preserves a valid transcript but does not itself guarantee an
automatic form popup before the next prompt.

For Codex, use `codex_fork.py --fork-before <turn-id>` and send the corrected
prompt in the new thread. Claude's AskUserQuestion splice does not apply there.
