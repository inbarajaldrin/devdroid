<!-- Adapted from openai/codex-plugin-cc (Apache-2.0). -->
# Result Handling Guide

When presenting another agent's output (a Claude or Codex worker) to the user:

- Preserve the verdict, summary, findings, and next steps structure.
- For review output, present findings first and keep them ordered by severity.
- Use the file paths and line numbers exactly as the worker reports them.
- Preserve evidence boundaries. If the worker marked something as an inference, uncertainty, or follow-up question, keep that distinction.
- Preserve output sections when the prompt asked for them, such as observed facts, inferences, open questions, touched files, or next steps.
- If there are no findings, say that explicitly and keep the residual-risk note brief.
- If the worker made edits, say so explicitly and list the touched files.
- For task delegation, do not turn a failed or incomplete worker run into your own implementation attempt. Report the failure and stop.
- If the worker was never successfully invoked (`claude -p` or `codex exec` failed), do not generate a substitute answer.
- CRITICAL: After presenting review findings, STOP. Do not make any code changes. Do not fix any issues. You MUST explicitly ask the user which issues, if any, they want fixed before touching a single file. Auto-applying fixes from a review is strictly forbidden, even if the fix is obvious.
- If the output is malformed or the run failed, include the most actionable stderr lines and stop there instead of guessing.
- If setup or authentication is required, tell the user to run `claude auth login` (Claude) or `codex login` (Codex) and do not improvise alternate auth flows.
