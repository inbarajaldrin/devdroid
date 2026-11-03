# devdroid

Everyday skills for Claude Code and Codex.

## Install

In Claude Code, add the marketplace once, then install the plugins you want. Each plugin brings the skills listed after it:

```text
/plugin marketplace add inbarajaldrin/devdroid
/plugin install session-tools@devdroid    # /ask /browse-chat-history /fork-session /handoff /continue-from-handoff
/plugin install text@devdroid             # /text
/plugin install sign-in@devdroid          # /sign-in
/plugin install career@devdroid           # /jobs
/plugin install docs@devdroid             # /apple-docs /windows-docs
/plugin install dev-tools@devdroid        # /difit /gitpass /timetravel /building-rules /live-fetch-skill-builder
/plugin install utils@devdroid            # /airdrop /capture-window
```

In Codex, the same plugins install the same way:

```bash
codex plugin marketplace add inbarajaldrin/devdroid
codex plugin add session-tools@devdroid
codex plugin add text@devdroid
codex plugin add sign-in@devdroid
codex plugin add career@devdroid
codex plugin add docs@devdroid
codex plugin add dev-tools@devdroid
codex plugin add utils@devdroid
```

## Plugins and their skills

**session-tools**

| Skill | What it does |
|---|---|
| `ask` | Message any Claude Code or Codex agent, across providers and your connected machines: dispatch work, review changes, get a second opinion |
| `browse-chat-history` | Search past sessions, and trace who wrote a file and why |
| `fork-session` | Branch a Claude Code or Codex session at any turn, leaving the original alone |
| `handoff` | Write a note so a fresh agent can pick up the work |
| `continue-from-handoff` | Resume from the latest handoff note |

**text**

| Skill | What it does |
|---|---|
| `text` | Talk with your agents over WhatsApp: message them from any chat, hear back when long work finishes, and add commands that run your own scripts. Choose for each chat who answers and who may wake them, set each agent's role and the permissions it holds for the work you hand it, and give an agent temporary access to answer or work with anyone in your contacts |

**sign-in**

| Skill | What it does |
|---|---|
| `sign-in` | Sign in to any website once to automate work there with your agent |

**career**

| Skill | What it does |
|---|---|
| `jobs` | A personal job search: scan the companies you choose, prepare one application at a time for your approval, and track it all in a local website and iPhone app |

**docs**

| Skill | What it does |
|---|---|
| `apple-docs` | Helps you set up and build apps for iPhone, iPad, Mac, Apple Watch, Apple TV and Vision Pro, working from Apple's current documentation |
| `windows-docs` | Helps you set up and build Windows apps, tools and installers, working from Microsoft's current documentation |

**dev-tools**

| Skill | What it does |
|---|---|
| `difit` | Show a change as a GitHub-style diff in your browser and collect your comments |
| `gitpass` | Catch stale claims and leftover process notes before a commit |
| `timetravel` | Take a detour mid-task, then hand back only the outcome |
| `building-rules` | The discipline for building and porting software with agents |
| `live-fetch-skill-builder` | Turn a website into a skill that reads it live |

**utils**

| Skill | What it does |
|---|---|
| `airdrop` | Send a file from a Mac to a nearby Apple device |
| `capture-window` | Screenshot or record an app window in the background |

## Credits

See [NOTICES.md](NOTICES.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
