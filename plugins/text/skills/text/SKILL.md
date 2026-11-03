---
name: text
description: Talk with the user and other people over WhatsApp, and let their messages reach the right Claude or Codex agent. Use when the user says /text, text me, WhatsApp a contact, notify me when done, change who answers a chat, add a chat command, set up WhatsApp for agents, or fix /text.
---

WhatsApp is a conversation with a return address. Put the agent’s readable name on a
message to the user so a swipe-reply reaches that agent through /ask. Answer a message
with the reply command it carries, so the answer stays beside the right message.

Read saved choices first. To speak to someone else, resolve one exact person or chat,
show the message, and follow the user’s saved send permission (including “ask every time”).
Ask once when there is no saved choice. Speak plainly with the visible AI prefix.
A private project group can keep the agent’s stamp. Never guess who a message is for.

A chat’s rule saves the user’s target choice: an existing agent or a fresh persistent
agent whose role, instructions and permissions you choose together. Recommend a fresh
agent for other people; explain its powers plainly. Confirm an existing full-access
target once on WhatsApp. Recommend draft when that target also holds private owner work.
The rule says what wakes the agent, who may ask, and what it may do.
When a chat includes other people, ask which answer mode to use, with a recommendation;
the user chooses. There is no default. The chat’s saved mode applies whoever speaks, including the owner.
Keep a chat’s saved behaviour until the user asks to change it.

A draft stays in the user’s own chat until their WhatsApp message says send. It can
be revised or discarded. A local command cannot stand in for that approval. Broader
access or authority needs the user’s confirmation too. Explain rules and introduce the
AI to new people only after confirmation; /help follows its saved allow, ask or deny choice.

A failure keeps the message and tells the user why. Only proven temporary transport
failures retry on their own; other messages wait until their cause is fixed.
“Fix /text” means run doctor, repair the cause in place, including customized code,
and check again. Inspect unclear acceptance first. Preserve the user’s changes.
After fixing the cause, record failure-resolve with the evidence that it was not delivered and send it again with failure-retry; if it was handled another way, close it with failure-discard and the reason. Never ask the user to resend or discard.

Setup is a short conversation, one question at a time with a recommendation: one number
or two (prefer two); with two, which account sends as the agent and which one the user reads (saved as that engine's
`ownerChat`; every group an agent creates includes it, so nothing lands in a group only the agent's number can see); where to reach the user; where devdroid's own notices and approvals go (recommend a
private group made with `group-create`, saved as `noticeChat`, so nothing lands in the user's own chat); which chats and wake words; whether and how
to speak to other people; which machines and accounts to link. On Linux, ask whether
the engine should keep running after logout and save the choice.
Agents talk to each other through /ask; WhatsApp is where the user sees and joins in.

The [operations reference](references/operations.md) gives the commands and setup.
The [rule reference](references/rules.md) defines the format. Install /ask alongside
/text; never make another copy of it.
