---
name: jobs
description: "Run a personal job search: set up from a resume, scan company postings, prepare and review applications, track outcomes, or customize the local website and iPhone app."
---

Build the person's search in their own workspace. Read their resume, propose a profile and target companies in chat, save their corrections once, and start with the core. Find the matching row below, copy its example to the workspace and adapt it when asked. Do not interview them about a feature list. Chat answers count; Questions is another place to answer. Saved answers, including **Ask me every time**, stay changeable.

## Fixed rules

- Keep personal material, accounts, settings, answers and records in `~/.devdroid/career` (`CAREER_HOME` may select another owned folder), never in this plugin. Examples are fictional; recipes store structure.
- The person approves **each application before Submit**, against the displayed fill digest. Edits revoke approval. Never impersonate the person or infer approval from a preference. The posting guard prevents double submissions across URLs and variants; recover an uncertain attempt in the same tab, never retry it.
- Work on **one form at a time**, checkpoint each step, read back fields and review newly reachable steps before the person's pass. Preserve recovery across restarts. Reuse answers by what makes them true: person, country, employer or application. Unknown facts stay unknown.
- Stop at passwords, verification or one-time codes, CAPTCHA, consent, terms, signatures and account creation. A labelled checkbox group sharing a name is a multi-select answer, unless its meaning is a legal action. Standalone checkboxes remain human-only. The person performs legal and sign-in actions directly; never type or tick them, even if supplied in chat or Questions. Create account is never Submit.
- When a site needs an account, open it with **/sign-in** so the person signs in there by hand, one login per site; then work on that login's context with stock OpenCLI. Career owns form writes. Never attach a different login's context, ask for a password, or connect the person's email account.
- After each scan, run `tools/posting_guard.py records --write` on new postings, then the coordinator writes each one's `summary_ai` (text, fit, fit_reasons, flags) and `company_info` (what_they_do, recent, sources) from the guarded description and public sources, labelled as AI-written. The scanner already saves the description and dates. Never ask the person for these. The coordinator also curates Today: pick an ordered review batch and a one-line why for each posting with `career.py batch set --why-file FILE` (JSON or TSV); use `batch add`, `show` or `clear` to maintain it. Never hand-edit batch storage or treat selection as approval.
- Ask once for separate `drafter` and `reviewer` choices, each `{agent, model}`, regardless of the coordinator. Offer `career.py models` choices; recommend Claude Opus for drafting and Sonnet for review. Save chat answers; never choose for the person.
- WhatsApp updates: ask yes/no once. Yes creates a career-only **/text** group and adds the owner accounts read from /text. Send only there, never `me`, the owner chat or /text’s notice group. Off or absent /text sends nothing. Keep failures visible in the app. Agent work uses **/ask**; queued does not mean read.
- The core is the company scanner, local server, website, iPhone app and deploy-when-away; keep all of it working. Configure Apple signing and the paired phone in the workspace. Stage services inactive; activate what the person asks to run. Add other features only when asked.

| The person asks for | What it is | See example |
|---|---|---|
| “Set up from this resume” | Profile proposal, chat rulings and saved settings | [setup.md](examples/setup.md), [profile.yml](examples/profile.yml), [settings.json](examples/settings.json) |
| “Find openings at these companies” | Public company feeds and editable filters | [companies.yml](examples/companies.yml) |
| “Apply to this role” | Pick, fill, review, approve, submit and recover | [application.md](examples/application.md) |
| “Remember this / ask each time” | Scoped facts shared by chat and Questions | [answers.md](examples/answers.md) |
| “Write custom replies” | Evidence-based drafting and editable writing rules | [writing.md](examples/writing.md) |
| “Handle screening questions” | Match structure to facts, never preset personal values | [screening.py](examples/screening.py), [screening_rules.yaml](examples/screening_rules.yaml) |
| “Use another lead source” | Classify public sources, then use company sites | [platforms.yml](examples/platforms.yml) |
| “Change my website” | Complete editable website using the local API | [web/index.html](examples/web/index.html) |
| “Change my iPhone app / update it while I'm away” | SwiftUI app, signed build publication and unlocked-phone watcher | [app/project.yml](examples/app/project.yml), [app/ota.sh](examples/app/ota.sh) |

Run `python tools/career.py check` from the workspace to find broken capabilities. Use `--offline` when sign-in and feed checks are not authorized; these remain explicitly unverified. See [reference.md](reference.md) for setup, site knowledge, contracts, deployment and validation. Use /apple-docs for Apple changes. Resolve dependencies through `../../dependencies.json`; never edit /sign-in's installed files.
