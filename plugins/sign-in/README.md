# sign-in

Sign in to any website once to automate work there with your agent.

Each login has its own browser profile. Open the site, sign in by hand, and let
your agent continue. The plugin records the identity the page exposes without
asking you to confirm it again. A second identity on the same website gets a
second login and profile.

From this plugin's folder, replace the example origin with your website:

```sh
node scripts/sign-in.mjs setup
node scripts/sign-in.mjs add-site --site example --origin https://www.example.org --private-machine
node scripts/sign-in.mjs open example
# Sign in by hand in the window that opens.
node scripts/sign-in.mjs drive example -- browser --session example-read open https://www.example.org/
node scripts/sign-in.mjs drive example -- browser --session example-read extract
```

Your agent gets a short notice when you finish signing in, or when sign-in fails
or is cancelled, so it can carry on. Browsers start when needed and close after
two idle minutes; `overview` shows which logins are running and why.

Any website works by its address. Your agent checks that it is still signed in as
the same account before it reads a page, and stops at a sign-in page, a challenge
or a different account. Your agent reads pages in the background and asks you
before changing anything on a website.

Google Workspace access (Gmail, Drive, Calendar through Google's API) is another
feature, with its own consent and scope choices. Setup asks what you want and
remembers approvals, refusals and ask-every-time preferences.
The [skill](skills/sign-in/SKILL.md), [command reference](skills/sign-in/references/operations.md)
and [Google Workspace guide](skills/sign-in/references/google.md) explain these choices.

## Technical details

Requires Node 22+ on macOS arm64/x64 or Linux x64. Keep logins on a private
machine; remote access uses SSH. Some native sign-in dialogs require a laptop.
The browser profile and account data default to `~/.devdroid/browser`.

The verified installation pins OpenCLI 2.0.2, Chrome for Testing 149.0.7827.22
and the official extension 1.0.26. gws 0.22.5 installs the first time
you use Google Workspace access.
Setup verifies downloads and runtime checksums, preserves account data, and
does not start a browser or daemon. License information is in [LICENSE](LICENSE)
and [NOTICE](NOTICE).

```sh
node scripts/sign-in.mjs --help
node scripts/test.mjs
node scripts/mutation-check.mjs
node scripts/runtime-check.mjs
```

Sign-in notices go through the installed session-tools `/ask` script (`ASK_SCRIPT`
sets its path); `open LOGIN --notify NAME_OR_ID` picks another recipient, and
`sign-in-status LOGIN` shows whether the notice was delivered. A generic login's
identity check uses the site's origin, visible account labels and recognizable
sign-in states, and is labelled lower assurance; named identity checks read more
specific account facts. `doctor` checks the installation without opening a browser.

Tests use isolated files and fixtures; no signed-in browser is required.
