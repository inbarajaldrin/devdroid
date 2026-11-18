# Google Workspace setup with Devdroid

This guide explains the Google API connection used by the sign-in plugin and the
setup lessons from a Calendar and Gmail integration. Each person uses their own
Google Cloud project and Desktop OAuth client. Devdroid keeps the OAuth client and
token cache in a private, account-specific directory; they do not belong in a
repository or support request.

## Explain the design

The integration has four parts:

1. **Google Cloud project:** the person owns the project and enables only the APIs
   they need.
2. **OAuth consent configuration:** the app name, support contact, audience and
   test users describe who can consent. Testing is a valid development state.
3. **Desktop OAuth client:** Devdroid imports the person's downloaded client file,
   checks that it belongs to the recorded project, and stores it with private file
   permissions.
4. **Scoped API connection:** each service adds exact OAuth scopes. The person
   signs in and approves Google's consent page; Devdroid verifies the resulting
   account and granted scopes before recording the connection.

This is technical access to a Google API, not blanket permission for an agent to
send mail, edit events or perform other actions. Google's authorization is separate
from Devdroid's service choices and action permissions.

## Set up a project

Use the commands from the sign-in plugin directory, or pass the plugin script's
full path:

```sh
node scripts/sign-in.mjs setup
node scripts/sign-in.mjs google opt-in ACCOUNT
node scripts/sign-in.mjs open ACCOUNT --site google
```

Sign in to the intended Google account in the opened browser. Then configure the
person-owned project and Google Auth Platform in that project:

1. Enable the needed APIs, such as Calendar API and Gmail API.
2. Configure app branding, a support email and developer contact information.
3. For a personal development app, choose **External** and **Testing**, then add
   the intended Google account as a test user.
4. Create a **Desktop app** OAuth client and download its JSON privately.
5. Import that client and explicitly confirm its audience:

```sh
node scripts/sign-in.mjs google project ACCOUNT --project YOUR_PROJECT_ID \
  --client-file /private/path/client-secret.json --audience testing
```

The person chooses the account and approves OAuth consent in Google's window.
Devdroid does not accept consent or download the client file for them. The
downloaded JSON contains a client secret: keep it private and never commit it.

## Connect Calendar and Gmail

Add the smallest scope the task needs. These examples retain existing scopes when
you add another one:

```sh
node scripts/sign-in.mjs google connect ACCOUNT \
  --service calendar --api calendar-json.googleapis.com \
  --scope https://www.googleapis.com/auth/calendar.events

node scripts/sign-in.mjs google connect ACCOUNT \
  --service gmail --api gmail.googleapis.com \
  --scope https://www.googleapis.com/auth/gmail.readonly
```

To authorize Gmail draft and send operations as well, add
`https://www.googleapis.com/auth/gmail.compose` to the Gmail service. Google will
show a new consent screen for the expanded scope:

```sh
node scripts/sign-in.mjs google connect ACCOUNT \
  --service gmail --api gmail.googleapis.com \
  --scope https://www.googleapis.com/auth/gmail.compose
```

`gmail.readonly` permits reading messages and settings. `gmail.compose` permits
managing drafts and sending messages. `google read` remains read-only and accepts
only API `get` and `list` methods. Gmail writing uses the separate command below,
which calls the pinned Google Workspace CLI Gmail helper.

Create a private UTF-8 body file with mode `0600`, then save a Gmail draft:

```sh
node scripts/sign-in.mjs google email ACCOUNT --action draft \
  --to recipient@example.com --subject 'Subject' --body-file /private/tmp/message.txt
```

To send, show the recipient, subject and complete body to the user in chat first.
Only after a current, explicit request to send that message, run:

```sh
node scripts/sign-in.mjs google email ACCOUNT --action send \
  --to recipient@example.com --subject 'Subject' --body-file /private/tmp/message.txt \
  --confirm-send
```

The command accepts one recipient and a private regular body file up to 1 MiB.
Use `--action draft` when asked to draft; never send a draft request. If the command
cannot confirm the outcome, check Gmail Sent or Drafts before retrying. Gmail API
draft creation and sending require the `gmail.compose` scope ([Gmail API draft guide](https://developers.google.com/workspace/gmail/api/guides/drafts), [send method](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages.send)).

### Schedule a message

The Gmail API workflow above supports saving a draft or sending immediately; it
does not provide a scheduled-send action. To send later, save the message as a
draft and schedule it in Gmail using the **Schedule send** control. Gmail sends
using the timezone selected in its interface, and the message should appear in
the **Scheduled** folder. If Gmail's interface is unavailable, keep the message
as a draft and do not claim it is scheduled. Avoid a local timer: it depends on
the computer and network being available at the requested time and is not a
Gmail scheduled message.

Verify the connection and perform a harmless read:

```sh
node scripts/sign-in.mjs google check ACCOUNT --service calendar
node scripts/sign-in.mjs google check ACCOUNT --service gmail
node scripts/sign-in.mjs google read ACCOUNT --service gmail -- \
  users drafts list --params '{"userId":"me","maxResults":1}'
```

Testing authorizations for scopes beyond basic identity can expire after seven
days. Reconnect when Google requires it, or complete Google's production and
verification requirements before publishing for broader use.

## When consent appears blank

The managed Chrome for Testing browser may start offscreen. The sign-in plugin
opens the Google page in a foreground tab, moves its window onto the screen, and
activates Chrome on macOS when showing the human handoff.

If OAuth times out, Devdroid records the result as unresolved and refuses to retry
silently. The person reviews the browser state first. If the page was blank or the
attempt clearly failed, clear the local block in the person's terminal, then start
a fresh connection:

```sh
node scripts/sign-in.mjs google change ACCOUNT --block clear
node scripts/sign-in.mjs google connect ACCOUNT \
  --service gmail --api gmail.googleapis.com \
  --scope https://www.googleapis.com/auth/gmail.readonly
```

If a Google Console setup handoff is still open, inspect and finish that handoff
before starting another Console step. Do not infer that a silent timeout means
consent succeeded.

## Keep setup data private

Never commit OAuth client JSON, token caches, authorization URLs, email addresses,
project identifiers from a person's account, or screenshots of a private consent
page. Use synthetic values in tests and examples. The account metadata stores
nonsecret service and scope facts; the secret client and tokens remain in the
private Devdroid Google account directory.
