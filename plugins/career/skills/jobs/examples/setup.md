# Resume-led setup

Read the supplied resume locally, propose a profile and target roles/companies, and save what the person agrees to in chat. Unknown facts stay null. The YAML and JSON examples are shapes, never the person's answers.

From the installed plugin, prepare an owned workspace (use the person's registered marketplace name):

```sh
python3 tools/setup.py --workspace "$HOME/.devdroid/career" --marketplace devdroid
export CAREER_HOME="$HOME/.devdroid/career"
sh "$CAREER_HOME/scripts/setup.sh"
cd "$CAREER_HOME"
```

Retain the original resume there. Extract UTF-8 text as `resume.txt` and retain the chosen upload as `resume.pdf`. Ask once for the drafter and reviewer, separately: each is an agent plus model. Run `tools/career.py models` to offer installed CLI choices. Recommend Claude Opus for drafting and Claude Sonnet for reviewing; either role may use either CLI. Save each ruled JSON object with `career.py save --target '{"setting":"drafter"}' --value-file drafter.json` and the equivalent `reviewer` target. These choices do not inherit the coordinator’s provider.

Ask once whether the person wants WhatsApp notifications. Save `Enabled` or `Disabled` with target `{"setting":"notifications"}`. Yes creates a dedicated career group and adds the owner’s other configured WhatsApp accounts from /text. No, unknown, or an absent /text sends nothing; app chat, Questions and Today remain available. The answer is changeable in settings or chat.

If using the proposal worker:

```sh
.venv/bin/python tools/career.py setup "$CAREER_HOME/resume.txt"
```

It returns profile and targets JSON for chat. It creates no Questions and does not replace existing data. After the person rules, save the final JSON profile as `profile-decision.json`, targets as `targets-decision.json`, and the plain PDF path as `pdf-decision.txt`, then:

```sh
.venv/bin/python tools/career.py save --target '{"setup_object":"profile"}' --value-file profile-decision.json
.venv/bin/python tools/career.py save --target '{"setup_object":"targets"}' --value-file targets-decision.json
.venv/bin/python tools/career.py save --target '{"setting":"resume_pdf"}' --value-file pdf-decision.txt
.venv/bin/python tools/career.py check --offline
```

This records the person's chat answer without a second prompt. For deliberate later replacement use `{"setup_object":"profile","replace":true}`; optional `base_digest` protects against concurrent changes. Individual facts use targets such as `{"profile_key":"contact.email"}`. Questions supports the same targets. Do not rerun setup to erase corrections.

Save the current /ask coordinator as `{agent, session}` in settings. Save the account and general proof site already enrolled through /sign-in as `account` and `bridge_site`. Enable `live_browser_writes` when the person asks to fill forms; each application still needs approval. Defaults support company scans and application assistance; WhatsApp stays off until chosen. Do not ask about other features up front. Dependency failures remain visible until fixed.

Configure Apple signing, paired phone and HTTPS origin using the reference. Run `check` with live checks authorized to verify sign-in and feeds. Services in `.private/services/` remain inactive until activated. No example company enters the inbox automatically.
