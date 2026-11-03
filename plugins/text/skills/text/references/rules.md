# Chat rules, version 1

A bundle is {"version":1,"rules":[...]}, with at most one rule per exact chat JID.
A chat containing other people must have an explicit answerMode chosen by the user.
private:true is only for a group containing the user and their agents.

~~~json
{
  "version": 1,
  "rules": [{
    "chat": "project@g.us",
    "label": "Jobs",
    "target": {"kind":"live","agent":"claude","host":"mac","id":"11111111-1111-4111-8111-111111111111"},
    "named": {"plan47":{"kind":"standing","agent":"claude","host":"box","id":"plan/47"}},
    "triggers": {"keywords":["@ai"],"mention":true,"every":false},
    "allow": ["contact@s.whatsapp.net","contact@lid"],
    "answerMode": "draft",
    "style": "plain",
    "commands": {
      "/usage": {"type":"script","host":"box","argv":["/usr/bin/uptime"]},
      "/review": {"type":"instruction","host":"mac","text":"Review the current changes."}
    },
    "expiresAt": "2099-01-01T00:00:00Z"
  }]
}
~~~

Targets always have host and agent (claude or codex). live needs an exact UUID;
standing resumes an exact Claude name/UUID (Codex always needs a full thread UUID).
home uses an absolute cwd instead of id, plus homeKey, rolePrompt and permissions;
these are chosen with the owner, with no built-in role. The created agent is retained.
Codex permissions save sandbox and approvalPolicy; Claude saves permissionMode and
allowedTools. For example, choose read-only/on-request or default with an explicit
tool list only after explaining what that lets the agent do. Do not invent a choice. /ask follows moved Claude sessions to their current
continuation. Names can contain slashes. A failed lookup never starts a substitute
for a live session. Named agents use @name.

Swipe-replies win over commands, /help and all ordinary triggers. A genuine owner's
reply to a UUID-addressed stamp remains addressed by its UUID/name/host.
New stamps use the same readable name-first, UUID-last format, with @host.
Standing/home return addresses are also retained behind each sent message ID.
Plain messages keep their exact return address behind their ID. Unknown peer quotes
cannot grant access to arbitrary sessions or give an unruled outsider a default mode.
Phone/LID aliases are learned from WhatsApp and checked for both incoming and outgoing
messages. An unresolved address cannot evade a protected contact's chosen mode.

Without a rule, only the owner's swipe-reply routes, and any answer to a shared chat
is a private held draft. A swipe never changes the saved chat mode. Otherwise a keyword at the start,
a WhatsApp mention of the linked account, @name or every:true can wake the target.
ownerEvery:true wakes the target on every owner message in that chat, without admitting other people's chatter.
ownerContext:true lets the chat's agent read the owner's own messages there as context, without answering
them unless the owner wakes it. Setup asks this once for each chat with other people.
No match means no guessed destination. A hint appears at most once a minute, only
when an owner's unmatched message quotes an apparent agent frame. Diagnostics go to
the owner's own chat so ordinary conversation never causes an unconfirmed shared post.

Only the owner may wake an agent unless allow lists other person JIDs. Commands have
their own allow lists, defaulting to owner only. List both phone and LID when needed;
they identify one person, not two invitations. Expiry disables other people's access,
never the owner's messages or stamped replies.

answerMode is required for a shared chat, including a chat where no outsider is
currently allowed. Ask the user to choose answer, draft, listen or tell-me. A private
group can omit it. The saved chat mode applies to owner requests too; draft stays held, listen answers only privately when asked, and tell-me only notifies. A confirmed answer
rule grants standing permission to reply to matching messages; it does not authorize
introductions or setup notes to third parties. Shared help and proactive sends follow the saved permissions.

- answer: deliver through /ask; the agent's response goes to the original message.
- draft: hold every response to other people, including direct send --confirmed.
  Show the draft privately. Only a genuine owner WhatsApp message in ownerChat saying
  send <draft-id>, or send while quoting that draft, releases it. Revision creates a
  new approval ID; the old one cannot be released. Drafts can be discarded.
- listen: deliver context through /ask, with no response until the owner says
  reply <delivery-id> in ownerChat. The answer stays in ownerChat. Local calls cannot
  simulate that request. Notices are limited to one per chat per minute; the listening
  JSON command lists retained messages and their reply handles.
- tell-me: notify only the owner; no agent, no script and no outward response.

Bare send never guesses among drafts or machines. Confirmations come only from the
in-process WhatsApp adapter or an authenticated relay of that actual event. The local
JSON entrance rejects fabricated inbound messages, approval flags and unsigned relays.
This is a guardrail for agents using the interface; it is not protection from a shell
user replacing the runtime or editing its state.

style is stamped or plain. Plain always gets the AI prefix; project-group notifications
honor the rule's chosen stamped style. Script commands use absolute argv without a
shell; arguments are appended as one literal argument. A script may set `timeoutSeconds` (1 to 600, default 60) and `"image":"first-line"`: its first output line names an image file to attach and the rest is the caption. Instruction commands expand
their text and go through /ask on the target host. /help is reserved and uses no agent.
Shared help omits phone numbers, participant IDs, session IDs, paths and hosts; it is
proposed privately until the owner saves allow, ask (each post), or deny. The choice is not re-asked.

A bundle may also contain `permissions`, keyed by exact chat, for example:

~~~json
{"version":1,"rules":[],"permissions":{"project@g.us":{"help":"allow","send":"ask"}}}
~~~

Each help/send choice is allow, ask or deny. Missing choices are asked once; ask is
itself a saved choice. A rules-set diff includes permission changes. Widening authority
requires one genuine owner confirmation per change, and never releases a draft.
Confirmed rules apply on available machines and retain/report offline peers for later
synchronization. Conflicts refuse rather than overwrite; prepared inputs stay durable.
No rule save sends introductions or notes. announce creates a separate proposal.

`setup convert` imports an earlier configuration into this format.

The setup conversation offers an existing agent or a fresh long-running one. Recommend
the fresh choice for other people; save the conversation’s actual role words and
permission choices. An existing full-access target is a widening, confirmed once. If it
also does owner work, recommend draft to let the owner check for private context.
