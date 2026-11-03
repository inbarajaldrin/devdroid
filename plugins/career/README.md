# Career

The career plugin gives you **/jobs**, a personal job search that starts from your resume. It scans the career pages of companies you choose, prepares one application at a time for your review, and tracks everything in a local website and an iPhone app. Nothing is submitted until you approve it.

When a site needs an account, you sign in yourself through /sign-in. You can also get updates on WhatsApp through /text. Your resume, answers and records stay in your own folder, `~/.devdroid/career`.

## How it is built

The [jobs skill](skills/jobs/SKILL.md) routes requests to [examples](skills/jobs/examples/) and one [reference](skills/jobs/reference.md). The engine lives in `tools/`, and dependencies are declared in `dependencies.json`. `tools/career.py check` reports anything that is not working.

## Running the tests

From this directory:

```sh
uv run --with-requirements requirements-dev.txt python -m pytest tools -q
npm ci --prefix tools
node --test tools/*.test.mjs
```

Browser form tests also run when `CAREER_TEST_CHROME` and `CAREER_TEST_OPENCLI` point to a temporary headless browser and the stock OpenCLI parser; those tests block external network routing. No test needs a live account or phone. See [third-party notices](THIRD_PARTY_NOTICES.md).
