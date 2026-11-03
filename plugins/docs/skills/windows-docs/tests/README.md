# Running the tests

From the skill directory, run:

```bash
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

Only Python 3's standard library, curl and public internet are required. The
checks GET every catalog URL and its readable representation, follow an actual
hub link to a leaf, and query Learn search before reading a returned API page.
Each GET prints status, MIME type, byte count and effective URL. Network errors,
non-200 responses, wrong representations and unreadable content fail; nothing
is skipped or saved as a documentation snapshot.

The checks also follow Win32 header links to function, structure, enum, callback
and COM interface references and inspect declarations or member documentation.
Release hubs must lead to actual published notes; version slugs are discovered,
not hardcoded. Other checks preserve an explicit PowerShell 5.1 view across the
representation query, verify the HTML and HTTP Accept-header alternatives, and
discover public GitHub source files from HTML metadata before reading them.
An intentionally missing URL must raise a fetch failure. A changed service
contract needs investigation; do not relax checks to accept shells or silently
skip failed pages.

The catalog uses one typed row per URL, and each Markdown row is checked in both
normal HTML and the reading representation. Tests run from any working
directory: supply the path to this `tests` directory as unittest's `-s` argument.
No Microsoft login, API key, third-party Python package, installed skill or
particular agent CLI is required.

## Checking the skill in an agent

HTTP tests do not prove skill selection or a particular tool's MIME support.
Make the skill available through the consuming agent's normal discovery
mechanism, then submit each `prompt` in
[trigger-cases.json](trigger-cases.json) in a separate fresh session without
naming the skill or requesting a fetch. Keep tool traces and score against
`activate` and `expect`. Selection requires an actual skill read or invocation;
positive cases also require live article content before the answer and
citations supporting its claims. A keyword match or fetch without skill
selection does not prove activation. Negative cases cover consumer support,
excluded Microsoft products and unrelated platforms.

Exercise that agent's fetch tool on the hub, a discovered API page, an explicit
older `view` and a discovered release note. If the tool rejects Markdown, record
the exact failure and use the documented curl fallback to read the same page.
Verify declarations or source prose in the body; a successful HEAD, title or
search snippet is insufficient. Record the date, agent/tool, selection results,
read results and any fallback in evidence outside the skill. Do not report this
manual behavior pass as automated or passed merely because the HTTP suite is
green.
