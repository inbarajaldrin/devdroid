# Record a chat answer once

Read `POSTING` from the actual inbox. Put the person's exact response in `fact.txt` in their workspace. Record a country answer for the job; never infer it from their home address:

```sh
python tools/career.py save --target "{\"job_country\":true,\"posting_id\":\"$POSTING\"}" --value-file fact.txt
```

For a missing field, `fact.txt` contains only their answer. The target includes the verbatim label and, for authorization, the confirmed country:

```sh
python tools/career.py save --target "{\"form_question\":\"Are you authorized to work in this country?\",\"posting_id\":\"$POSTING\",\"country\":\"US\"}" --value-file fact.txt
python tools/career.py fill "$POSTING"
```

Chat and Questions use the same validation and effect. Drafts remain proposals until the person accepts them; never save an agent's inferred personal fact. Passwords, codes and legal actions cannot be stored as form values. The person acts directly and confirms completion only.

When the engine already asked in Questions, use that thread's ID as `QUESTION` to resolve it from chat. Each missing fact has its own plain question. `fact.txt` contains the person’s answer, with multiple selected options separated by semicolons in displayed order. Never ask the person for JSON. For a human-action stop it contains only `Confirmed`, after the person acted in the browser:

```sh
python tools/career.py save --target "{\"question_id\":\"$QUESTION\"}" --value-file fact.txt
python tools/career.py fill "$POSTING"
```

Answer keys include normalized label and truth scope. Contact applies to the person, authorization per country, role-specific facts per canonical application URL. Exact same-polarity aliases are declared; sponsorship and authorization are different questions. Checkbox state never becomes permission.

`GET /answers` lists values. Questions in both clients edits them through `POST /answers/ID {value, by}`. The literal `Ask me every time` suspends reuse for that scope. The coordinating agent may relay that same ruling through `Workflow.change_answer(ID, value, by="chat")`. Changed answers block stale uses until reapplied. An approved essay supplies writing evidence, not reusable assertions about another role.
