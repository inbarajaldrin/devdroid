# text

Talk with your agents over WhatsApp: message them from any chat, hear back when long work
finishes, and add commands that run your own scripts. Choose for each chat who answers, who
may wake them and whether replies wait for your OK; set each agent's role and the permissions
it holds for the work you hand it; and give an agent temporary access to answer or work with
anyone in your contacts.

/text reaches your agents through `/ask`, so install session-tools as well. The
[rules](skills/text/references/rules.md) and [operations](skills/text/references/operations.md)
references describe chat rules, commands and setup.

If a message cannot be delivered, you keep the original and get the reason privately.
Brief network drops retry on their own; once anything else is fixed, your agent sends the
message again, and you can also reply `resend`.

## Technical details

Requires Node 22+ and Python 3.10+. The runtime source and its dependency lock are in
`engine/`; setup installs dependencies and keeps account state in the runtime folder,
`~/.devdroid/text`. `/ask` is found through `ASK_SCRIPT`, a sibling ask skill,
`CLAUDE_PLUGIN_ROOT`, user skills, then the session-tools plugin registry or enclosing
marketplace catalog; setup records the path it finds.

`setup.cjs doctor` checks the installed source, dependencies, the engine and its macOS
launchd or Linux systemd user service without changing anything. `setup.cjs repair` keeps
your customized source while repairing dependencies and staged service files; it reports
held failures to you and starts no service.

## Running the tests

The tests use no network, WhatsApp account or phone. Prepare the pinned Baileys test
dependency once (this step needs the network):

```sh
mkdir -p .scratchpad/sdk
cp text/engine/baileys/package*.json .scratchpad/sdk/
npm ci --prefix .scratchpad/sdk --ignore-scripts --no-audit --no-fund
node --test text/tests/*.test.cjs
```

`TEXT_BAILEYS_PACKAGE` can point to an already prepared Baileys package instead. `/ask`
tests live beside that skill:
`python3 -m unittest discover -s <session-tools>/skills/ask/tests -p 'test_*.py'`.

## License

Apache-2.0, see [LICENSE](LICENSE). Baileys is an npm dependency under its MIT license:
[WhiskeySockets/Baileys](https://github.com/WhiskeySockets/Baileys). Other dependencies carry
their own licenses in their distributions.
