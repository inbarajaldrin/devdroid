<!-- Adapted from openai/codex-plugin-cc (Apache-2.0). -->
# GPT Prompting Guide

Use this guide when composing prompts for GPT via `codex exec`.

Prompt GPT like an operator, not a collaborator. Keep prompts compact and block-structured with XML tags. State the task, the output contract, the follow-through defaults, and the small set of extra constraints that matter.

## Core Rules

- Prefer one clear task per `codex exec` run. Split unrelated asks into separate runs.
- Tell GPT what done looks like. Do not assume it will infer the desired end state.
- Add explicit grounding and verification rules for any task where unsupported guesses would hurt quality.
- Prefer better prompt contracts over raising reasoning or adding long natural-language explanations.
- Use XML tags consistently so the prompt has stable internal structure.

## Default Prompt Recipe

- `<task>`: the concrete job and the relevant repository or failure context.
- `<structured_output_contract>` or `<compact_output_contract>`: exact shape, ordering, and brevity requirements.
- `<default_follow_through_policy>`: what GPT should do by default instead of asking routine questions.
- `<verification_loop>` or `<completeness_contract>`: required for debugging, implementation, or risky fixes.
- `<grounding_rules>` or `<citation_rules>`: required for review, research, or anything that could drift into unsupported claims.

## When to Add Blocks

- Coding or debugging: add `completeness_contract`, `verification_loop`, and `missing_context_gating`.
- Review or adversarial review: add `grounding_rules`, `structured_output_contract`, and `dig_deeper_nudge`.
- Research or recommendation tasks: add `research_mode` and `citation_rules`.
- Write-capable tasks: add `action_safety` so GPT stays narrow and avoids unrelated refactors.

## How to Choose Prompt Shape

- Use `codex review` when the job is reviewing local git changes (built-in reviewer).
- Use `codex exec --output-schema <skill>/schemas/review-output.schema.json` with
  `<skill>/prompts/adversarial-review.md` for adversarial/challenge reviews with structured JSON output.
- Use `codex exec` (or a steerable app-server thread via `ask.py --codex --start`) when the task is
  diagnosis, planning, research, or implementation and you need to control the prompt directly.

## Working Rules

- Prefer explicit prompt contracts over vague nudges.
- Use stable XML tag names that match the block names from `references/prompting/prompt-blocks.md`.
- Do not raise reasoning or complexity first. Tighten the prompt and verification rules before escalating.
- Ask GPT for brief, outcome-based progress updates only when the task is long-running or tool-heavy.
- Keep claims anchored to observed evidence. If something is a hypothesis, say so.

## Prompt Assembly Checklist

1. Define the exact task and scope in `<task>`.
2. Choose the smallest output contract that still makes the answer easy to use.
3. Decide whether GPT should keep going by default or stop for missing high-risk details.
4. Add verification, grounding, and safety tags only where the task needs them.
5. Remove redundant instructions before sending the prompt.

## Reference Files

- Reusable blocks: `references/prompting/prompt-blocks.md`
- End-to-end templates: `references/prompting/prompt-recipes.md`
- Common failure modes to avoid: `references/prompting/prompt-antipatterns.md`
