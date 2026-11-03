<!-- Reference: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/overview and
     https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
     (Anthropic's prompting guidance, summarised in devdroid's own words). -->
# Claude Prompting Guide

Use this guide when composing a brief for a Claude worker (`claude -p`, `ask.py --start`) or a message
to a running Claude Code session. For GPT and Codex, see `gpt-prompting-guide.md`.

Brief Claude like a capable colleague who has just joined the project and knows none of its history. If a
person with no context would be confused by the brief, Claude will be too.

## Core Rules

- State the goal and what done looks like. Claude follows instructions closely, so ask for exactly
  what you want: "change this function" gets changes, "can you suggest changes" gets suggestions.
- Give the reason behind a constraint, not only the constraint. "The output is read aloud, so avoid
  symbols" generalises better than "no symbols".
- Say what to do rather than what not to do, especially for format: "answer in flowing paragraphs"
  works better than "no markdown". The brief's own style pulls the answer's style toward it.
- Put context first and the question last when the brief carries long material such as logs,
  documents or diffs.
- Use numbered steps only when order or completeness matters; otherwise describe the goal.

## Structure

- Separate the parts of a long brief with consistent XML tags, for example `<context>`,
  `<instructions>`, `<constraints>` and `<output_format>`. Nest them when the material is nested
  (`<documents>` holding `<document>` entries).
- Examples steer format and tone more reliably than description. Give a few relevant, varied ones
  inside `<example>` tags so Claude can tell them apart from instructions.
- A single sentence of role ("You are reviewing this change as a security engineer") focuses tone
  and judgement. With `claude -p`, `--append-system-prompt` adds it without replacing Claude Code's
  own system prompt.

## Agentic work

- Say how far the worker may go. Claude acts on clear requests; for anything hard to reverse
  (deleting files or branches, force-pushing, posting to other people) say whether it must ask first.
- Keep scope tight when it matters: ask for the smallest change that meets the goal, with no extra
  files, abstractions or refactors that were not requested, and ask it to remove its own scratch files.
- Ask for a general solution, not one that only makes the tests pass, and to report a wrong or
  impossible test instead of working around it.
- Require grounding: read the files before making claims about them, and label an inference as one.
- For long tasks, ask the worker to keep its state in files (a progress note, a test list, git
  commits) so a fresh context can pick up where it left off.
- Claude Code compacts context on its own; tell a long-running worker so, and that it should not
  stop early to save context.
- State what the report must contain (the result, its evidence, anything left undone), because
  recent Claude models keep summaries short unless asked.

## Effort and thinking

Current Claude models decide how much to think on their own. Raise or lower `--effort` (or `--model`)
for the job instead of writing "think hard" into the brief. When a worker over-explores, tell it to
choose an approach and commit to it, revisiting only when new evidence contradicts it.

## Checklist

1. Goal, done-criteria and why it matters.
2. Context: what is already known, measured or ruled out, with paths.
3. Constraints: where it may write, what must not happen, what needs a question first.
4. Output: the exact shape of the report.
5. Read it once as a newcomer and remove anything ambiguous or redundant.
