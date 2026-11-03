# Running the tests

The automated checks need Python 3, curl and public internet access. They use
only paths relative to this skill: no Xcode, Apple login, API key, third-party
Python package, installed personal skill, or author-machine path. They do not
run the signing or packaging scripts or save documentation snapshots.

From the skill directory:

```bash
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

From another directory, supply the checkout-relative or absolute path to `tests`
as `-s`. Network errors, non-200 responses, unexpected representations and
unreadable content fail the checks rather than being silently skipped.

## What the automated checks cover

The five automated checks cover:

1. The documented Markdown rule returns substantive text for API docs, HIG,
   release notes and Safari, and an observed link from each hub leads to a
   readable leaf. This catches a working hub with a broken navigation rule.
2. Every URL in `catalog.md` resolves live, including both canonical pages and
   their reading representations. The technology directory yields a framework
   that can itself be fetched. A catalog URL outside the typed rows fails.
3. The separate DocC JSON feeds expose content and resolvable topic references.
4. App Review contains actual numbered rules, and the unversioned videos hub
   leads to a WWDC page with transcript markup and substantive text.
5. The skill points to the existing local guides and scripts.

Each GET prints final status, MIME type, byte count and effective URL. Inspect
failures as possible Apple changes; do not “fix” them by relaxing content checks
to accept an HTML shell or hardcoding whichever release was newest at build time.

## Checking the skill in an agent

The HTTP suite cannot prove that a model selects a skill or that its web tool
accepts a MIME type. Repeat this pass in the agent that will use the skill.

Make this skill available through that agent's normal skill discovery mechanism.
In separate fresh sessions, submit only each `prompt` in
[trigger-cases.json](trigger-cases.json), without naming the skill or telling the
agent to fetch. Keep the tool traces. Score each case using `activate` and
`expect`: selection means an actual skill invocation/read, not merely mentioning
its name. For positive cases, require a live fetch before the answer and
citations that support its claims; guesses from memory fail. The consumer case
checks that activation is not based on an Apple product name alone.

Then explicitly exercise the retrieval rule with that agent's own web tool on a
Markdown page, HIG, a release-note page, App Review and a discovered WWDC session.
If the web tool refuses `text/markdown` or JSON, record the error and complete the
same read with the documented curl fallback. A successful HEAD, curl-only URL
check, search snippet, navigation shell or transcript-tab label does not pass
this behavior check. Check source prose or API declarations in the actual body.

Record the agent/tool names, date, activation outcomes, fetch outcomes and any
fallback used. Do not report model activation as automated or passed merely
because a description contains a prompt's keywords. These fixtures can be run
with any skill-capable agent; they require no particular commercial CLI or test
account belonging to the author.
