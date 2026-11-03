"""The application state machine shared by the CLI, HTTP clients and fake tests."""
from __future__ import annotations

import copy
import datetime
import hashlib
import io
import json
import re
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import yaml
from PIL import Image

from integrations import Agents, OpenCLIBrowser, Text
from field_policy import personal_control
from answer_fields import profile_node, profile_value, phone_country, degree_answer, draft_supported, office_question, motivation_question, mixed_evidence, DRAFT_CONTRACT, free_text, instruction_only
from runtime import company_url, HOME, PERSON_BY, Refused, locked, now, read_json, slug, write_json, write_text


def confirmed_material(value):
    if isinstance(value, dict):
        if value.get("confirm"):
            return None
        return {key: confirmed_material(node) for key, node in value.items()}
    if isinstance(value, list):
        return [confirmed_material(node) for node in value]
    return value


def date_scalar(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    raise TypeError("Only date scalars may be converted implicitly.")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=date_scalar).encode()).hexdigest()


def normalize(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().rstrip("* ").casefold()




# Explicit label names only. Ambiguous or custom questions go to the drafter.
STANDARD = {
    "first name": "name.first_name", "last name": "name.last_name",
    "full name": "name.full_name", "name": "name.full_name",
    "email": "contact.email", "email address": "contact.email", "phone": "contact.phone",
    "phone number": "contact.phone", "city": "location.city", "country": "location.country",
    "phone country": "contact.phone_country",
    "website": "contact.website", "linkedin": "contact.linkedin", "github": "contact.github",
    "linkedin profile":"contact.linkedin", "linkedin profile url":"contact.linkedin",
    "personal website or portfolio":"contact.website", "portfolio":"contact.website",
}
ACCOUNT_CONTROL = re.compile(r"\b(?:create|register|sign\s*up|enroll)\b|\b(?:consent|terms|agree|accept|acknowledge|certify)\b", re.I)
AUTH_FACT = re.compile(
    r"\b(?:authori[sz]ed|eligible|permitted|legally able)\s+to\s+work\b|\bright\s+to\s+work\b|"
    r"\b(?:authorization to work|work eligibility|employment authorization|immigration status|green card)\b|\bwork\s+(?:authorization|authorisation|permit)\b|\bsponsor(?:ship|ed)?\b|\bvisa\b|\bcitizen(?:ship)?\b|\bnationality\b|\bpermanent\s+resident\b|\b(?:us|u\.s\.)\s+citizen\b", re.I)
PERSON_FACT = re.compile(
    AUTH_FACT.pattern + r"|\bethnic(?:ity)?\b|\bhispanic\b|\blatino\b|\bgender\b|\bpronouns\b|"
    r"\b(?:security clearance|criminal record|sexual orientation|date of birth|over 18|18 years of age)\b|\bdisabilit(?:y|ies)\b|\bveteran\b|\bsalary\b|\bcompensation\b|\bconvict(?:ed|ion)\b|"
    r"\bat\s+least\s+18\b|\b18\s+(?:years\s+)?(?:or\s+)?older\b|\b(?:your|identify\s+your)\s+race\b(?!\s+condition)|"
    r"^\s*race(?:\s*[/&]\s*ethnicity)?\s*[*?]?$", re.I)


# Complete labels with the same polarity; unknown wording keeps its own meaning key.
ROLE_FACT = re.compile(r"\b(?:salary|compensation)\b|\b(?:this|the|our)\s+(?:role|position|company|team|job|organization)\b", re.I)
FACT_MEANINGS = {
    "do you need sponsorship?":"requires sponsorship",
    "do you require sponsorship?":"requires sponsorship",
    "require sponsorship?":"requires sponsorship",
    "will you now or in the future require sponsorship for employment visa status?":"requires sponsorship",
    "will you now or in the future require visa sponsorship?":"requires sponsorship",
    "do you now or will you in the future require sponsorship?":"requires sponsorship",
    "do you need visa sponsorship?":"requires sponsorship",
    "are you legally authorized to work in this country?":"authorized to work",
    "do you have the right to work?":"authorized to work",
    "are you eligible to work?":"authorized to work",
    "are you permitted to work here?":"authorized to work",
}


class Workflow:
    def __init__(self, root=HOME, *, browser=None, agents=None, text=None):
        self.root = Path(root).resolve()
        self.settings = read_json(self.root / "settings.json", {})
        self.browser = browser or OpenCLIBrowser(self.settings, root=self.root)
        self.agents = agents or Agents(self.settings,root=self.root)
        self.text = text or Text()

    def record(self, pid):
        rec = read_json(self.root / "data/postings" / (slug(pid) + ".json"))
        if not isinstance(rec, dict) or rec.get("id") != pid:
            raise Refused("NOT_FOUND", "No such posting.")
        intent = read_json(self.adir(pid) / "submit-intent.json", {})
        if intent.get("attempted_at"):
            rec["submission"] = {**rec.get("submission", {}), **intent}
            if rec.get("status") not in ("applied","under_review","interview","offer","rejected","closed"):
                rec["status"] = "submission_unconfirmed"
        return rec

    def save(self, rec):
        with locked(self.root / ".records"):
            path = self.root / "data/postings" / (slug(rec["id"]) + ".json")
            latest = read_json(path, {})
            intent = read_json(self.adir(rec["id"]) / "submit-intent.json", {})
            if latest.get("submission", {}).get("attempted_at") or intent.get("attempted_at"):
                rec["submission"] = {**latest.get("submission", {}), **rec.get("submission", {}), **intent}
                if rec.get("status") not in ("applied", "under_review", "interview", "offer", "rejected", "closed"):
                    rec["status"] = latest.get("status") if latest.get("status") == "applied" else "submission_unconfirmed"
            write_json(path, rec)
        self.tracker()

    def tracker(self):
        rows = []
        for path in sorted((self.root / "data/postings").glob("*.json")):
            rec = read_json(path)
            if rec.get("kind", "job") != "job" or rec.get("status") in ("new", "enriched", "skipped", "variant"):
                continue
            cell = lambda value: re.sub(r"\s+", " ", str(value or "")).replace("|", "/")
            rows.append("| " + " | ".join(cell(value) for value in (
                rec.get("status"), rec.get("company"), rec.get("title"), rec.get("track"),
                (rec.get("submission") or {}).get("confirmed_at"), rec.get("url"))) + " |")
        path = self.root / "data/applications/TRACKER.md"
        old = path.read_text() if path.exists() else ""
        notes = old[old.index("\n## Notes"):] if "\n## Notes" in old else "\n## Notes\n"
        write_text(path, "# Applications\n\n| Status | Company | Role | Track | Applied on | Link |\n"
                   "|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n" + notes)

    def adir(self, pid):
        return self.root / "data/applications" / slug(pid)

    def fill(self, pid):
        return read_json(self.adir(pid) / "form_fill.json")

    def review(self, pid):
        return read_json(self.adir(pid) / "review.json", {"boxes": []})

    def preview(self, pid):
        from presentation import preview
        return preview(self, pid)

    def batch(self, operation='show', items=None, note=None):
        from batch import change
        return change(self, operation, items, note)

    def event(self, rec, event):
        rec.setdefault("history", []).append({"at": now(), "event": event})

    def invalidate(self, rec):
        for key in ("submit_approved_at", "submit_approved_by", "submit_digest"):
            rec.setdefault("ruling", {}).pop(key, None)

    def receipt(self, message, *, kind="notice"):
        """Durable outbox; uncertain publication is visible and never automatically replayed."""
        with locked(self.root / ".notifications"):
            path = self.root / "data/notifications.json"
            rows = read_json(path, [])
            row = {"n": len(rows) + 1, "message": message, "at": now(), "state": "attempting", "kind":kind}
            rows.append(row)
            write_json(path, rows)
            notification_choice = read_json(self.root / "settings.json", {}).get("notifications")
            if notification_choice != "Enabled":
                row["state"] = "disabled" if notification_choice == "Disabled" else "awaiting setup choice"
                write_json(path, rows)
                return row
            try:
                row.update(state="sent", receipt=self.text.send(message, group=Text.group_id((read_json(self.root / "settings.json", {}).get("notification_group") or {}).get("id"))))
            except Refused as error:
                row.update(state="unconfirmed", code=error.code, error=str(error))
            write_json(path, rows)
            return row

    def gate(self, pid):
        self.settings = read_json(self.root / "settings.json", {})
        rec, fill, review = self.record(pid), self.fill(pid), self.review(pid)
        missing = []
        duplicate = self.duplicate_application(rec)
        if duplicate:
            missing.append("application already attempted under record " + duplicate)
        if rec.get("kind", "job") != "job":
            missing.append("only job records can be submitted as applications")
        if rec.get("status") != "ready" or (rec.get("ruling") or {}).get("decision") != "approved":
            missing.append("pick and fill this application first")
        if rec.get("submission", {}).get("attempted_at"):
            missing.append("a submit was already attempted; inspect its confirmation, never repeat it")
        if fill and fill.get("complete") is False:
            missing.append("complete every application step before submission")
        if not fill or fill.get("posting_id") != pid or not fill.get("rows"):
            missing.append("no verified fill")
        elif review.get("fill_digest") != digest(fill):
            missing.append("review does not cover this fill")
        else:
            if fill.get("material_digest") != self.material_digest(rec):
                missing.append("profile or resume material changed; release and rebuild the fill")
            if not self.country(rec) and any(AUTH_FACT.search(row["name"]) and row.get("value") for row in fill["rows"]):
                missing.append("confirm the job country before using an authorization answer")
            if fill.get("country") != self.country(rec):
                missing.append("the job country changed; resume and review authorization again")
            if fill.get("account") != self.settings.get("account") or fill.get("site") != rec.get("site") or fill.get("proof_site", rec.get("site")) != self.proof_site(rec):
                missing.append("application account or employer site changed; rebuild the fill")
            if fill.get("review_min_words") != self.threshold():
                missing.append("review threshold changed; rebuild the fill")
            if not review.get("agent_reviewed_at"):
                missing.append("the agent reviewer has not reviewed the filled form")
            memory = read_json(self.root / "data/answers.json", {})
            for row in fill["rows"]:
                saved = memory.get(self.cache_key(row, rec, personal=True)) or memory.get(self.cache_key(row, rec))
                if row.get("source") == "reused" or (saved and saved.get("edited_at") and saved.get("value") != "Ask me every time"):
                    if not saved or saved.get("value") != row["value"]:
                        missing.append("stored answer changed; resume this application: " + row["name"])
            if any(not row.get("verified") for row in fill["rows"]):
                missing.append("some fields have not been read back")
            for box in review.get("boxes", []):
                if box.get("state") == "approved" and box.get("approved_by") not in (*PERSON_BY, "auto"):
                    missing.append("this box has no personal approval: " + box["label"])
                if box.get("state") != "approved" or box.get("needs_apply"):
                    missing.append(f"review or apply edit: {box['label']}")
                if any(f.get("severity") == "block" for f in box.get("flags", [])):
                    missing.append(f"blocked box: {box['label']}")
            for path in (self.root / "data/questions").glob("*.json"):
                question = read_json(path)
                target = question.get("applies_to") or {}
                if (question.get("posting_id") or target.get("posting_id")) != pid or not (target.get("form_question") or target.get("form_facts")):
                    continue
                if question.get("status") == "dismissed":
                    continue
                value = (question.get("context") if question.get("kind") == "confirm" and question.get("answer") == "Confirmed"
                         else question.get("answer"))
                if target.get("form_facts"):
                    try:
                        values = json.loads(value or "{}")
                    except (ValueError, TypeError):
                        values = {}
                    if target.get("country") != self.country(rec) and isinstance(values, dict):
                        values = {name: answer for name, answer in values.items() if not AUTH_FACT.search(name)}
                        if not values:
                            continue
                    actual = {row["name"]: row["value"] for row in fill["rows"]}
                    for name in list(values):
                        row = next((row for row in fill["rows"] if row["name"] == name), None)
                        newer = self.newer_saved_answer(row, rec, question, memory) if row else None
                        if newer is not None:
                            values[name] = newer
                    if (question.get("status") != "answered" or
                            question.get("needs_reading") or not values or not isinstance(values, dict) or
                            any(actual.get(name) != answer for name, answer in values.items())):
                        missing.append("read and apply the Questions batch correction")
                    continue
                if AUTH_FACT.search(target["form_question"]) and target.get("country") != self.country(rec):
                    continue
                row = next((row for row in fill["rows"] if normalize(row["name"]) == normalize(target["form_question"])), None)
                newer = self.newer_saved_answer(row, rec, question, memory) if row else None
                if newer is not None:
                    value = newer
                if question.get("status") != "answered" or question.get("needs_reading") or not row or row["value"] != value:
                    missing.append("read and apply the Questions correction: " + target["form_question"])
        if any(m.get("posting_id") == pid and m.get("from") == "user" and not m.get("resolved_at")
               for m in read_json(self.root / "data/chat.json", [])):
            missing.append("resolve the application comment in chat")
        return missing

    def newer_saved_answer(self, row, rec, question, memory):
        saved = memory.get(self.cache_key(row, rec, personal=True))
        if not saved or saved.get("value") == "Ask me every time":
            return None
        try:
            edited = datetime.datetime.fromisoformat(saved.get("edited_at", "").replace("Z", "+00:00"))
            answered = datetime.datetime.fromisoformat(question.get("answered_at", "").replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        return saved["value"] if edited >= answered else None

    def dispatch(self, action, pid=None, **body):
        if action == "batch":
            with locked(self.root, blocking=False):
                return self.batch(**body)
        if action == "setup":
            with locked(self.root / ".setup"):
                return self.setup(**body)
        if action == "save":
            return self.save_decision(**body)
        if action == "chat":
            with locked(self.root, blocking=False):
                message = self.chat(**body)
            if message["from"] == "user":
                self.deliver_chat(message)
            self.receipt(("You: " if message["from"] == "user" else "Your agent: ")+message["text"],kind="chat")
            return message
        application_action = action in ("fill", "apply-edits", "submit", "confirm", "release", "recipe")
        lock_root = self.root / ".application" if application_action else self.root
        with locked(lock_root, blocking=False):
            self.settings = read_json(self.root / "settings.json", {})
            if action == "tracker":
                self.tracker()
                return {"ok": True}
            handlers = {"pick": self.pick, "fill": self.start, "box": self.box,
                        "apply-edits": self.apply_edits, "approve-submit": self.approve,
                        "submit": self.submit, "confirm": self.confirm, "release": self.release,
                        "recipe": self.recipe, "resolve-uncertain": self.resolve_uncertain}
            if action not in handlers:
                raise Refused("BAD_ACTION", "Unknown career action.")
            rec = self.record(pid)
            if action in ("pick", "box", "approve-submit", "resolve-uncertain"):
                with locked(self.root / ".application", blocking=False):
                    result = handlers[action](rec, **body)
            else:
                try:
                    result = handlers[action](rec, **body)
                except Refused as error:
                    if error.code in ("HUMAN_SIGN_IN", "IDENTITY_OR_PAGE", "SENSITIVE_FIELD", "HUMAN_ACCOUNT", "FIELD_CONTRACT", "UPLOAD_REQUIRED", "UNTRUSTED_POSTING", "FORM_CHANGED", "READBACK_MISMATCH", "AMBIGUOUS_FIELDS", "WRONG_APPLICATION", "STEP_CONTRACT", "STEP_CONTROL", "SUBMIT_CONTROL", "SUBMISSION_UNCONFIRMED", "NAVIGATION_UNCERTAIN", "FORM_UNREADABLE", "DAEMON_CONFLICT", "BROWSER_RUNTIME", "OPENCLI_VERSION", "TAB_MISSING", "DEPENDENCY_MISSING", "DEPENDENCY_REFUSED", "DEPENDENCY_UNAVAILABLE", "DAEMON_STOPPED", "CLI_CONFLICT", "OPTION_MISMATCH", "UNSUPPORTED_DRAFT", "UPLOAD_RECEIPT", "ACCOUNT_REQUIRED", "FORM_ERRORS", "SUBMIT_OBSCURED", "SUBMIT_STORAGE", "SUBMIT_PREFLIGHT", "ALREADY_ATTEMPTED"):
                        self.stop_question(rec, error)
                    raise
        if action in ("pick", "box", "approve-submit"):
            self.wake(action, pid)
        return result

    def deliveries(self):
        events = read_json(self.root / "data/events.json", [])
        pending = [event for event in events if not (event.get("receipt") or {}).get("ok")]
        return {"pending": len(pending), "events": events,
                "help": "Read career.py events, fix the named dependency or coordinator, then ask your agent to read the saved workspace. Delivery is never silently retried."}

    def wake(self, action, pid):
        with locked(self.root / ".events"):
            path = self.root / "data/events.json"
            events = read_json(path, [])
            event = {"n": len(events) + 1, "action": action, "posting_id": pid, "at": now()}
            events.append(event)
            write_json(path, events)
            try:
                event["receipt"] = self.agents.message(
                    f"[Career {action}] {pid}. Read the workspace record and pending edits. "
                    "Use career.py; only the application's exact approval authorizes submit.")
            except Refused as error:
                event["delivery_error"] = {"code": error.code, "error": str(error)}
            write_json(path, events)

    def pick(self, rec, decision, reason=None, by="web"):
        if by not in PERSON_BY:
            raise Refused("PERSON_APPROVAL", "Only the person's decision can pick a job.")
        if decision not in ("approved", "skipped") or (decision == "skipped" and not reason):
            raise Refused("BAD_DECISION", "Approve or skip with a reason.")
        if read_json(self.root / "data/active.json", {}).get("posting_id") == rec["id"]:
            raise Refused("FORM_ACTIVE", "Release the active form before changing its pick.")
        allowed = ("new", "enriched", "approved", "skipped")
        if rec.get("status") not in allowed:
            raise Refused("STATE", "This application has already been filled.")
        rec["status"] = decision
        rec["ruling"] = {"decision": decision, "reason": reason, "by": by, "decided_at": now()}
        self.event(rec, f"{decision} by {by}")
        self.save(rec)
        return rec

    def profile(self):
        path = self.root / "profile.yml"
        if not path.is_file():
            raise Refused("SETUP_REQUIRED", "Read the resume and save the profile agreed in chat or Questions.")
        return yaml.safe_load(path.read_text()) or {}

    def material(self, rec):
        profile = self.profile()
        resume = Path(self.settings.get("resume", "")).expanduser()
        if not resume.is_file():
            raise Refused("RESUME_REQUIRED", "Set the person's resume material path in settings.json.")
        # Setup accepts agent-extracted UTF-8 resume material. Uploaded PDFs are separate profile facts.
        text = resume.read_text()
        material = {"profile": confirmed_material(profile), "resume": text, "posting": rec}
        earlier = self.earlier_answers()
        if earlier:
            material["earlier_answers"] = earlier
        writing = self.root / "WRITING.md"
        if writing.is_file():
            material["writing_rules"] = writing.read_text()
        system = rec.get("ats") or (rec.get("posting") or {}).get("ats")
        if self.settings.get("recipes") == "Enabled" and isinstance(system, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]*", system):
            learned = read_json(self.root / "recipes" / system / "learning.json")
            if learned:
                material["recipe"] = learned  # Structural data only; never import or execute an adapter here.
        return material

    def active(self, pid):
        state = read_json(self.root / "data/active.json", {})
        if state.get("posting_id") != pid:
            raise Refused("NO_ACTIVE_FORM", "Fill this application first; one form is active at a time.")
        rec = self.record(pid)
        settings = read_json(self.root / "settings.json", {})
        if state.get("account") != settings.get("account") or state.get("site") != rec.get("site") or state.get("proof_site", rec.get("site")) != self.proof_site(rec) or state.get("url") != ((rec.get("posting") or {}).get("apply_url") or rec.get("url")):
            raise Refused("IDENTITY_OR_PAGE", "The chosen account or employer changed; release this form before opening another context.")
        self.browser.bind(state["handle"])

    def page(self, page, url, *, prefix=None, allow_empty=False):
        if isinstance(page, dict) and page.get("unreadable"):
            raise Refused("FORM_UNREADABLE", "The application contains controls the reader cannot inspect.")
        if isinstance(page, dict) and page.get("account_creation"):
            raise Refused("HUMAN_ACCOUNT", "The person must create their own account.")
        if not isinstance(page, dict) or page.get("blocked") or page.get("challenge") or page.get("signed_out"):
            raise Refused("HUMAN_SIGN_IN", "Stop for the person to handle account sign-in or a challenge through /sign-in.")
        if not page.get("identity_verified") or company_url(page.get("url", "")) != company_url(url):
            raise Refused("IDENTITY_OR_PAGE", "Identity or application origin does not match.")
        if page["url"].split("#")[0] != url.split("#")[0]:
            raise Refused("WRONG_APPLICATION", "The bridge is showing another application or step.")
        fields = page.get("fields")
        if not isinstance(fields, list) or (not fields and not allow_empty):
            raise Refused("FORM_UNREADABLE", "No named application fields were returned.")
        if any(not isinstance(field, dict) for field in fields):
            raise Refused("FIELD_CONTRACT", "Fields must be named objects.")
        names = [field.get("name") for field in fields]
        if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
            raise Refused("AMBIGUOUS_FIELDS", "Fields need unique accessible names; qualify repeated sections by name.")
        for field in fields:
            if personal_control(field) and not field.get("human_completed"):
                raise Refused("SENSITIVE_FIELD", "The person must handle passwords and checkbox consent directly.")
            if (field.get("type", "text") not in ("text", "email", "tel", "url", "number", "textarea", "date", "month",
                    "file", "select", "radio", "multiselect", "checkbox", "combobox", "search", "time", "datetime-local", "week", "color", "range") or
                    not isinstance(field.get("options", []), list) or
                    any(not isinstance(option, str) for option in field.get("options", []))):
                raise Refused("FIELD_CONTRACT", "Unsupported widget or ambiguous options; grow the named-field adapter first.")
        if prefix:
            return {prefix + " / " + field["name"]: dict(field, field_name=field["name"], name=prefix + " / " + field["name"]) for field in fields}
        return {field["name"]: field for field in fields}

    def cache_key(self, field, rec, *, personal=False):
        raw = field.get("field_name", field["name"])
        if re.search(r"\b(?:reference|referee|emergency contact|previous employer)\b", field["name"] + " " + field.get("section", ""), re.I):
            raw = field["name"]
        profile = confirmed_material(self.profile())
        if office_question(field) and not ROLE_FACT.search(raw):
            return digest(['office location preferences',sorted(field.get('options',[])), 'personal answer' if personal else self.material_digest(rec)])
        name = raw if (normalize(raw) in STANDARD or PERSON_FACT.search(raw) or
                       raw in (profile.get("standard_answers") or {})) else field["name"]
        standard = (profile.get("standard_answers") or {}).get(name)
        person_fact = bool(PERSON_FACT.search(name)) and not bool(re.search(r"\b(?:salary|compensation)\b", name, re.I))
        portable = (normalize(name) in STANDARD or standard is not None or person_fact) and not ROLE_FACT.search(name)
        if AUTH_FACT.search(name) and not ROLE_FACT.search(name):
            scope = ["authorization", self.country(rec) or rec["id"]]
        else:
            role_url = (rec.get("posting") or {}).get("apply_url") or rec.get("url")
            role = sorted(self.application_keys({"url":role_url})) if role_url else [rec["id"]]
            scope = "profile" if portable else ["role", role, self.country(rec)] if AUTH_FACT.search(name) else ["role", role]
        # Facts depend only on their own confirmed value; essays retain whole-material invalidation.
        if AUTH_FACT.search(name):
            version = "country fact"
        elif person_fact:
            version = "personal fact"
        elif portable:
            key = STANDARD.get(normalize(name))
            version = profile_value(profile, key) if key else standard
            if normalize(name)=='phone country':
                version=[profile_value(profile,'contact.phone'),version,profile_node(profile,'location.country')]
        else:
            version = "personal answer" if personal else self.material_digest(rec)
        meaning = FACT_MEANINGS.get(normalize(name), normalize(name))
        return digest([meaning, scope, version])

    def earlier_answers(self, limit=15):
        """The person's submitted and approved custom answers, newest first, for the drafter to reuse."""
        entries = read_json(self.root / "data/earlier_answers.json", {}).get("entries", [])
        keep = [e for e in entries if isinstance(e, dict) and e.get("status") in ("submitted", "approved") and e.get("answer")]
        keep.sort(key=lambda e: str(e.get("date") or ""), reverse=True)
        return [{k: e.get(k) for k in ("type", "status", "company", "role", "question", "answer")} for e in keep[:limit]]

    def add_earlier_answer(self, pid, name, value, by):
        """A custom answer the person approved joins the earlier answers the drafter reuses."""
        if by not in ("phone", "web", "terminal-person") or len(str(value or "").split()) < self.threshold():
            return
        rec = self.record(pid)
        path = self.root / "data/earlier_answers.json"
        with locked(self.root / ".answers"):
            data = read_json(path, {"entries": []})
            entries = [e for e in data.get("entries", []) if not (e.get("posting_id") == pid and e.get("question") == name)]
            entries.append({"type": "other", "status": "approved", "company": rec.get("company"), "role": rec.get("title"),
                            "date": now()[:10], "question": name, "answer": value, "posting_id": pid})
            write_json(path, {"entries": entries})

    def remember_answer(self, pid, name, value, by, field=None, *, personal=True):
        if personal_control(field or {"name":name}):
            return
        rec = self.record(pid)
        if AUTH_FACT.search(name) and not self.country(rec):
            return  # An unknown country never becomes a portable authorization answer.
        field = field or next((row for row in (self.fill(pid) or {}).get("rows", []) if row["name"] == name), {"name":name})
        with locked(self.root / ".answers"):
            path = self.root / "data/answers.json"
            cache = read_json(path, {})
            key = self.cache_key(field, rec, personal=personal)
            if cache.get(key, {}).get("value") == "Ask me every time" and value != "Ask me every time":
                return
            scope = ("role: " + rec.get("url", pid)) if ROLE_FACT.search(name) else "country: " + str(self.country(rec)) if AUTH_FACT.search(name) else "profile" if normalize(field.get("field_name", name)) in STANDARD or PERSON_FACT.search(name) or office_question(field) else "role: " + rec.get("url", pid)
            previous = cache.get(key, {})
            keep_edit = previous.get("value") == value and previous.get("edited_at")
            cache[key] = {"value":value, "name":name, "field_name":field.get("field_name", name), "scope":scope, "approved_at":now(),
                "posting_id":pid, "answer_source":by, "country":self.country(rec), "personal":personal or bool(keep_edit)}
            if keep_edit:
                cache[key]["edited_at"] = previous["edited_at"]
            write_json(path, cache)

    def answers(self):
        return [dict(row, id=key) for key, row in read_json(self.root / "data/answers.json", {}).items()]

    def change_answer(self, key, value, by="web"):
        if not isinstance(value, str) or not value.strip():
            raise Refused("BAD_VALUE", "Supply an answer or Ask me every time.")
        with locked(self.root / ".answers"):
            path = self.root / "data/answers.json"
            cache = read_json(path, {})
            if key not in cache:
                raise Refused("NOT_FOUND", "No such saved answer.")
            cache[key].update(value=value.strip(), answer_source=by, approved_at=now(), edited_at=now(), personal=True)
            row = cache[key]
            if row.get("posting_id") and row.get("name"):
                rec = self.record(row["posting_id"])
                rec["country"] = row.get("country")
                if row.get("scope", "").startswith("role: "):
                    rec["url"] = row["scope"].removeprefix("role: ")
                    rec["posting"] = {"apply_url":rec["url"]}
                field = next((f for f in (self.fill(rec["id"]) or {}).get("rows", []) if f["name"] == row["name"]), {"name":row["name"], "field_name":row.get("field_name", row["name"].rsplit(" / ",1)[-1] if row.get("scope")=="profile" else row["name"])})
                personal_key = self.cache_key(field, rec, personal=True)
                if personal_key != key:
                    cache[personal_key] = cache.pop(key)
                    key = personal_key
            write_json(path, cache)
            return dict(cache[key], id=key)

    @staticmethod
    def country(rec):
        return rec.get("country") or (rec.get("posting") or {}).get("country")

    def material_digest(self, rec=None):
        path = Path(self.settings.get("resume", "")).expanduser()
        if not path.is_file():
            raise Refused("RESUME_REQUIRED", "Resume material is unavailable.")
        mode = self.settings.get("resume_mode")
        selected = ((rec or {}).get("materials") or {}).get("resume_pdf") if mode == "tailored per application" else self.settings.get("resume_pdf")
        pdf = Path(selected).expanduser().resolve() if selected else None
        fingerprint = {"mode":mode, "path":str(pdf) if pdf else None,
                       "sha256":hashlib.sha256(pdf.read_bytes()).hexdigest() if pdf and pdf.is_file() else None}
        writing = self.root / "WRITING.md"
        role_material={key:(rec or {}).get(key) for key in ('posting','company_info','summary_ai')}
        return digest([DRAFT_CONTRACT,self.profile(), path.read_text(), fingerprint,role_material,
                       writing.read_text() if writing.is_file() else None])

    def proof_site(self, rec):
        ats = rec.get("ats") or str(rec.get("source", "")).removesuffix("-api")
        return self.settings.get("bridge_site") if ats in ("greenhouse", "lever", "ashby") else rec.get("site")

    def site_question(self, rec):
        import questions
        paths = questions.Paths(questions=self.root / "data/questions", postings=self.root / "data/postings", profile=self.root / "profile.yml", applications=self.root / "data/applications")
        if not any(((q.get("applies_to") or {}).get("job_site") or (q.get("applies_to") or {}).get("setting") == "bridge_site") and q.get("posting_id") == rec["id"] and q.get("status") == "open" for q in questions.list_q(paths)):
            questions.add(paths, topic="application", title="Choose this employer's verified identity",
                question="Name the site enrolled in /sign-in for this employer. The selected account remains your setup choice. Public no-login forms use the general bridge identity chosen in setup.",
                context=rec.get("url", ""), posting_id=rec["id"], applies_to={"setting":"bridge_site"} if (rec.get("ats") or str(rec.get("source", "")).removesuffix("-api")) in ("greenhouse","lever","ashby") else {"posting_id":rec["id"],"job_site":True})
            self.receipt("Choose the employer identity in Questions before filling this application.")

    def start(self, rec):
        pid = rec["id"]
        if self.settings.get("application_assistance") != "Enabled":
            raise Refused("SETUP_CHOICE_REQUIRED", "Choose application assistance in the setup Questions first.")
        if self.duplicate_application(rec):
            raise Refused("DUPLICATE_APPLICATION", "This application or a known variant was already attempted.")
        if rec.get("submission", {}).get("attempted_at"):
            raise Refused("SUBMISSION_UNCONFIRMED" if rec.get("status") == "submission_unconfirmed" else "ALREADY_ATTEMPTED", "An attempt is already recorded. Inspect its existing outcome; never restart it.")
        if rec.get("kind", "job") != "job" or rec.get("status") not in ("approved", "ready"):
            raise Refused("PICK_REQUIRED", "Pick a job in the inbox before filling.")
        active = read_json(self.root / "data/active.json", {})
        if active.get("posting_id") and active["posting_id"] != pid:
            raise Refused("FORM_BUSY", "Finish or release the active application first.")
        material = self.material(rec)
        url = (rec.get("posting") or {}).get("apply_url") or rec.get("url")
        company_url(url)
        account = self.settings.get("account")
        if self.settings.get("resume_mode") not in ("one resume", "tailored per application"):
            raise Refused("SETUP_CHOICE_REQUIRED", "Choose one resume or tailored per application in Questions.")
        proof_site = self.proof_site(rec)
        if not isinstance(account, str) or not account.strip() or not rec.get("site") or not proof_site:
            self.site_question(rec)
            raise Refused("ACCOUNT_REQUIRED", "Choose a verified employer identity or general bridge identity in Questions first.")
        if active.get("handle") and (active.get("account") != account or active.get("site") != rec["site"] or active.get("proof_site", rec["site"]) != proof_site or active.get("url") != url):
            raise Refused("IDENTITY_OR_PAGE", "The chosen account, employer or application changed; release the old form first.")
        input_digest = self.material_digest(rec)
        handle = active.get("handle") or "career-" + pid
        write_json(self.root / "data/active.json", {"posting_id": pid, "url": url, "account": account,
                                                     "site": rec["site"], "proof_site":proof_site, "handle": handle})
        self.restoring = False
        if active.get("handle"):
            try:
                self.browser.bind(handle)
                page = self.browser.read()
            except Refused as error:
                if error.code not in ("TAB_MISSING", "SESSION_LOST", "BRIDGE_NOT_RUNNING"):
                    raise
                self.restoring = True
                page = self.browser.open(url, account, proof_site, handle)
        else:
            try:
                page = self.browser.open(url, account, proof_site, handle)
            except Refused as error:
                if error.code == "BROWSER_WRITES_DISABLED":
                    write_json(self.root / "data/active.json", {})
                raise
        journal = read_json(self.adir(pid) / "steps.json", {})
        if page.get("confirmation"):
            self.navigation_outcome(rec, page)
        if journal.get("moving"):
            raise Refused("NAVIGATION_UNCERTAIN", "The previous navigation outcome is unknown. The person must inspect the tab before continuing.")
        known = [url] + [step["url"] for step in journal.get("steps", [])]
        if not self.posting_destination(url, page.get("url", "")):
            raise Refused("WRONG_APPLICATION", "The saved tab left the application and its observed steps.")
        return self._journey(rec, page, url, account, input_digest)

    def _fill_page(self, rec, page, url, prefix):
        pid = rec["id"]
        material = self.material(rec)
        fields = self.page(page, url, prefix=prefix, allow_empty=True)
        from posting_guard import check
        text = [rec.get("title") or "", (rec.get("posting") or {}).get("description") or "", page.get("text") or ""]
        text.extend(field["name"] + "\n" + "\n".join(field.get("options", [])) for field in fields.values())
        guard = check("\n".join(text))
        if guard["flags"]:
            raise Refused("UNTRUSTED_POSTING", "Posting or form text targets the agent; inspect the prompt-injection flags before continuing.")
        cache = read_json(self.root / "data/answers.json", {})
        old_country = (self.fill(pid) or {}).get("country")
        old_review = self.review(pid)
        old_boxes = {box["name"]: box for box in old_review["boxes"]}
        plan_path = self.adir(pid) / "plan.json"
        checkpoint = read_json(plan_path, {"rows": (self.fill(pid) or {}).get("rows", [])})
        if not old_review.get("agent_reviewed_at"):
            observed = {row["name"] for row in checkpoint["rows"] if row.get("step_id", "application") == self.step(page)["id"]}
            if observed - set(fields):
                raise Refused("FORM_CHANGED", "Previously observed fields disappeared before review; inspect the complete form before resuming.")
        current_material = self.material_digest(rec)
        planned = {row["name"]: row for row in checkpoint["rows"] if
                   not row.get("material_dependent", row.get("source") in ("draft", "reused")) or checkpoint.get("material_digest") == current_material}
        previous = {name: box for name, box in old_boxes.items() if box.get("source") in ("user edit", "prior edit")}
        answers = self.question_answers(pid)
        rows = []
        for name, field in fields.items():
            draft_attempted = False
            draft_note = None
            answer_name = field.get("field_name", name)
            if personal_control(field) and not field.get("human_completed"):
                raise Refused("SENSITIVE_FIELD", f"The person must handle {name}.")
            foreign_section = bool(re.search(r"\b(?:reference|referee|emergency contact|previous employer)\b", name + " " + field.get("section", ""), re.I))
            key = None if foreign_section else STANDARD.get(normalize(answer_name))
            value = profile_value(material["profile"], key) if key else None
            education = field.get('education')
            if education and isinstance(material['profile'].get('education'),list):
                index = education['index']
                entries = material['profile']['education']
                if 0 <= index < len(entries) and isinstance(entries[index],dict):
                    value = profile_value(entries[index],education['key'])
                    if value is not None: key = 'education.'+str(index)+'.'+education['key']
            if field.get('choice',{}).get('phone_country'):
                value=phone_country(material['profile'],field.get('options',[]))
                key='contact.phone_country'
            if key == 'contact.phone' and any(f.get('choice',{}).get('phone_country') for f in fields.values()):
                # The country picker carries the code, so the number box takes the national number.
                national = profile_value(material['profile'],'contact.phone_national')
                if national:
                    value, key = national, 'contact.phone_national'
            if education and education['key']=='degree' and field.get('options'):
                matched,degree_source=degree_answer(material['profile'],education['index'],field['options'])
                if matched is not None:value,key=matched,degree_source
            upload_label = field.get("visible_name") or answer_name
            if not foreign_section and field.get("type") == "file" and re.fullmatch(r"(?:(?:upload|attach)\s+)?(?:resume|résumé|cv)(?:\s*/\s*(?:resume|cv))?(?:\s*\(required\))?", normalize(upload_label)):
                tailored = self.settings.get("resume_mode") == "tailored per application"
                resume = (rec.get("materials") or {}).get("resume_pdf") if tailored else self.settings.get("resume_pdf")
                if not resume or not Path(resume).expanduser().is_file():
                    raise Refused("UPLOAD_REQUIRED", "Provide the chosen resume PDF; tailored mode requires this application's PDF.")
                value, key = str(Path(resume).expanduser().resolve()), "resume"
            elif field.get("type") == "file":
                upload = profile_value({'upload':(material['profile'].get('standard_answers') or {}).get(answer_name)},'upload')
                if upload is not None:
                    if not isinstance(upload,str) or not Path(upload).expanduser().is_file():
                        raise Refused('UPLOAD_REQUIRED','The supplied upload must name a readable local file: '+name)
                    value, key = str(Path(upload).expanduser().resolve()), 'standard_answers.'+answer_name
                elif field.get("required"):
                    raise Refused("UPLOAD_REQUIRED", "Prepare and choose the required upload in the person's application material first.")
                else:
                    value, key = "", "optional upload"
            if value is None:
                node = None if foreign_section else (material["profile"].get("standard_answers") or {}).get(answer_name)
                value = profile_value({"answer": node}, "answer")
                key = "standard_answers." + answer_name if value is not None else key
                role_url = (rec.get("posting") or {}).get("apply_url") or rec.get("url")
                if ROLE_FACT.search(name) and (not isinstance(node, dict) or node.get("role_url") != role_url):
                    value, key = None, None
                if AUTH_FACT.search(name) and (not isinstance(node, dict) or not self.country(rec) or node.get("country") != self.country(rec)):
                    value = None
            if field.get("human_completed"):
                value, key = field["value"], "personal action completed"
            cached = cache.get(self.cache_key(field, rec, personal=True)) or cache.get(self.cache_key(field, rec))
            ask_each_job = cached and cached.get("value") == "Ask me every time"
            if ask_each_job:
                value = None
            evidence = []
            source = "profile" if key else "draft"
            guidance = None
            if normalize(name) in answers and answers[normalize(name)] is not None:
                value, source = answers[normalize(name)], "person answer"
            if source == "person answer" and free_text(field) and motivation_question(field):
                # A reply in Questions is read, never pasted: for a drafted answer it is guidance for the drafter.
                guidance = None if instruction_only(value) else value
                value, source = None, "draft"
            prior = previous.get(name) if not AUTH_FACT.search(name) or old_country == self.country(rec) else None
            if prior and prior.get("options", []) == field.get("options", []):
                value, source = prior["value"], "prior edit"
            if value is None and cached and not ask_each_job:
                value, source = cached["value"], "reused"
            prior_plan = planned.get(name) if not AUTH_FACT.search(name) or old_country == self.country(rec) else None
            if value is None and not ask_each_job and prior_plan and prior_plan.get("source") not in ("profile", "empty optional"):
                value = None if prior_plan.get("missing") else prior_plan["value"]
                source, evidence = prior_plan["source"], prior_plan.get("evidence", [])
            if cached and cached.get("personal") and not ask_each_job:
                value, source, evidence = cached["value"], "reused", []
            # Propose, never ask: a motivation answer the last plan left missing is drafted again.
            retry_contract = bool(prior_plan and prior_plan.get('missing') and motivation_question(field))
            if (value is None and (prior_plan is None or retry_contract) and key is None and not PERSON_FACT.search(normalize(name)) and
                    not ask_each_job and not foreign_section and not office_question(field) and (field.get("type") == "multiselect" or not field.get("options")) and (field.get("type") == "multiselect" or motivation_question(field) or re.search(r"\b(?:describe|explain|how did|tell us|cover letter|essay|additional information)\b", answer_name, re.I))):
                # The drafter writes from the material, never from text already typed in the live form.
                blank = {k: v for k, v in field.items() if k != "value"}
                draft = self.agents.task("drafter", dict(material, field=blank, **({"guidance": guidance} if guidance else {})))
                draft_attempted = True
                source = 'draft'
                if not draft_supported(draft,material,field) and mixed_evidence(draft,material,field):
                    draft = self.agents.task('drafter', dict(material, field=blank, revision={
                        'draft':draft, 'reason':'Attribute every claim to person or role, using exact excerpts from that source. Return ordered claims composing value. Do not add personal facts.'}))
                value, evidence = draft.get("value"), draft.get("evidence") or []
                if value and not draft_supported(draft,material,field):
                    # Propose, never ask: the draft stays a proposal; the reviewer and the person check its claims.
                    draft_note = "Some claims are not quoted from your résumé or profile; check them before approving."
                    evidence = [q for claim in draft.get('claims') or [] for q in claim.get('evidence') or []] or evidence
                elif value and draft.get('claims'):
                    evidence=[q for claim in draft['claims'] for q in claim['evidence']]
            phone_country_missing = bool(field.get('choice',{}).get('phone_country') and value is None and profile_value(material['profile'],'contact.phone'))
            if value is None and not phone_country_missing and not field.get("required") and (field.get("options") or field.get("type") in ("color", "range")):
                value, source = field.get("value", ""), "existing optional"
            if field.get('type') == 'multiselect' and value is not None:
                from field_policy import multiselect_value
                try:
                    value = multiselect_value(value, field.get('options', []))
                except Refused:
                    value = None
            if isinstance(value, (datetime.date, datetime.datetime)):
                value = value.isoformat()
            elif isinstance(value, bool):
                value = "Yes" if value else "No"
            elif isinstance(value, (int, float)):
                value = str(value)
            elif value is not None and not isinstance(value, str):
                raise Refused("PROFILE_VALUE", "A field needs a scalar confirmed value, not profile metadata.")
            missing = value is None or (field.get("required") and isinstance(value, str) and not value.strip())
            if missing:
                value, source = "", "missing" if field.get("required") or phone_country_missing else "empty optional"
            if field.get("type") != "multiselect" and field.get("options") and value not in field["options"] and not (missing and field.get("required")):
                if not (value == "" and not field.get("required")):
                    node = ((material["profile"].get("standard_answers") or {}).get(answer_name)
                            if key and key.startswith("standard_answers.") else profile_node(material["profile"], key) if key else None)
                    alternatives = node.get("alternatives", []) if isinstance(node, dict) else []
                    matches = [option for option in field["options"] if normalize(option) == normalize(value)]
                    if not matches:
                        matches = [option for option in field["options"] if normalize(option) in {normalize(alt) for alt in alternatives}]
                    if len(matches) == 1:
                        value = matches[0]
                        missing = False
                    else:
                        value, source, missing = "", "missing", True
            row = {"name": name, "label": field.get("visible_name") or field.get("field_name",name), "type": field.get("type", "text"),
                   "required": bool(field.get("required")), "options": field.get("options", []),
                   "value": value, "source": source, "custom": not bool(key), "evidence": evidence, **({"draft_note": draft_note} if draft_note else {}),
                   "section":field.get("section", ""), "context":field.get("context", ""), "material_dependent":draft_attempted or source == "draft" or bool(source == "reused" and cached and not cached.get("personal")), "human_completed":bool(field.get("human_completed")), "missing": missing and (bool(field.get("required")) or source == "missing")}
            if draft_attempted:row['draft_contract'] = DRAFT_CONTRACT
            elif prior_plan and prior_plan.get('draft_contract'):row['draft_contract'] = prior_plan['draft_contract']
            row["step_id"] = self.step(page)["id"]
            row['intentional_empty'] = bool(prior and prior.get('intentional_empty') and not field.get('required') and value == '')
            if prefix:
                row.update(field_name=answer_name)
            rows.append(row)
            planned[name] = row
            write_json(plan_path, {"rows": list(planned.values()), "material_digest":current_material})
        for row in rows:
            empty_choice = row["type"] not in ("multiselect","combobox") and row.get("options") and row["value"] == "" and not row["required"]
            if not row.get("missing") and not row.get("human_completed") and row.get("source") != "existing optional" and not empty_choice and (row["type"] != "file" or row["value"]):
                if row["type"] == "file":
                    row["upload_name"] = Path(row["value"]).name
                    row["upload_sha256"] = hashlib.sha256(Path(row["value"]).read_bytes()).hexdigest()
                receipt = self.browser.fill(row.get("field_name", row["name"]), row["value"], row["type"])
                if row["type"] == "file" and receipt != {"name": row["upload_name"], "sha256": row["upload_sha256"]}:
                    raise Refused("UPLOAD_RECEIPT", "The bridge must confirm the exact uploaded filename and bytes.")
                if row['type'] == 'file':
                    rel = f"applications/{pid}/uploads/{row['upload_sha256'][:16]}-{row['upload_name']}"
                    target = self.adir(pid) / 'uploads' / Path(rel).name
                    target.parent.mkdir(parents=True,exist_ok=True)
                    raw = Path(row['value']).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != row['upload_sha256']:
                        raise Refused('UPLOAD_RECEIPT','The upload changed before its preview copy was saved.')
                    target.write_bytes(raw)
                    row['file_link'] = rel
        after = self.browser.read()
        current = self.page(after, url, prefix=prefix, allow_empty=True)
        for row in rows:
            actual = current.get(row["name"], {}).get("value")
            if (row["type"] == "tel" or normalize(row.get("field_name", row["name"])) in ("phone", "phone number")) and re.sub(r"\D", "", str(actual)) == re.sub(r"\D", "", str(row["value"])):
                row["value"] = actual  # Bind approval to the formatted value actually displayed.
        self.verify(rows, current)
        return rows, after

    @staticmethod
    def step(page):
        raw = page.get("step") or {"id":"application", "label":"Application"}
        if not isinstance(raw.get("id"), str) or not raw["id"] or not isinstance(raw.get("label"), str):
            raise Refused("STEP_CONTRACT", "A form step needs a stable identity and label.")
        return raw

    @staticmethod
    def posting_destination(origin, target):
        """A step remains under this posting, never merely on a shared ATS host."""
        try:
            same_origin = company_url(origin) == company_url(target)
        except Refused:
            return False
        if not same_origin:
            return False
        def path(url):
            value = urlsplit(url)
            base = value.path.rstrip("/")
            base = re.sub(r"/apply$", "", base)
            query = [(k, v) for k, v in parse_qsl(value.query) if not k.startswith("utm_") and k not in ("gh_src", "source", "ref")]
            return base, dict(query)
        base, query = path(origin)
        actual, changed = path(target)
        navigation_keys = {"step", "page", "stage"}
        if any(key not in query and key not in navigation_keys for key in changed):
            return False
        return bool(base and (actual == base or actual.startswith(base + "/")) and
                    all(changed.get(key) == value for key, value in query.items()))

    def navigation_outcome(self, rec, page):
        path = self.adir(rec["id"]) / "steps.json"
        journal = read_json(path, {})
        outside = not self.posting_destination((rec.get("posting") or {}).get("apply_url") or rec.get("url"), page.get("url", ""))
        if outside and not journal.get("moving"):
            journal["moving"] = {"name":"Unresolved navigation", "url":page.get("url")}
        if journal.get("moving") or page.get("confirmation"):
            journal.setdefault("outcomes", []).append({"at":now(),"url":page.get("url"),"heading":page.get("heading"),"confirmation":page.get("confirmation")})
            if journal.get("moving") and not page.get("confirmation") and self.posting_destination((rec.get("posting") or {}).get("apply_url") or rec.get("url"), page.get("url", "")):
                journal.update(moving=None, observed_destination=self.step(page))
            write_json(path, journal)
        if page.get("confirmation"):
            rec = self.record(rec["id"])
            rec.setdefault("submission", {}).setdefault("attempted_at", now())
            write_json(self.adir(rec["id"]) / "submit-intent.json", rec["submission"])
            rec["status"] = "submission_unconfirmed"
            self.save(rec)
            raise Refused("SUBMISSION_UNCONFIRMED", "A navigation reached a confirmation. Inspect the recorded outcome; do not repeat the transition.")

    def _journey(self, rec, page, url, account, input_digest):
        pid = rec["id"]
        material = self.material(rec)
        old_fill = self.fill(pid)
        old_ruling = copy.deepcopy(rec.get("ruling", {}))
        old_country = (old_fill or {}).get("country")
        old_review = self.review(pid)
        old_boxes = {box["name"]:box for box in old_review["boxes"]}
        journal_path = self.adir(pid) / "steps.json"
        if hasattr(self.browser, "first"):
            page = self.browser.first()
        steps, rows = [], []
        for _ in range(64):
            self.navigation_outcome(rec, page)
            if not self.posting_destination(url, page.get("url", "")):
                raise Refused("WRONG_APPLICATION", "The form left the selected posting. Its transition outcome is recorded; the person must inspect it.")
            descriptor = self.step(page)
            if page.get("apply_control") and not page.get("fields"):
                control = page["apply_control"]
                if control.get("tag") != "a" or not control.get("href") or control.get("in_form"):
                    raise Refused("STEP_CONTROL", "Only a plain Apply link can open a form; application buttons need the person.")
                if not self.posting_destination(url, control["href"]):
                    raise Refused("WRONG_APPLICATION", "Apply points to another posting; no navigation or fill was performed.")
                write_json(journal_path, {"steps":steps, "current":descriptor["id"], "moving":control})
                page = self.browser.advance("apply_control", control["name"])
                continue
            if descriptor["id"] in {step["id"] for step in steps}:
                raise Refused("STEP_CONTRACT", "A step repeated without a stable forward transition.")
            prefix = descriptor["label"] if page.get("step") and (page.get("next_control") or page.get("previous_control") or steps) else None
            page_rows, after = self._fill_page(rec, page, page["url"], prefix)
            rows.extend(page_rows)
            if len({row["name"] for row in rows}) != len(rows):
                raise Refused("AMBIGUOUS_FIELDS", "Repeated step labels need distinct accessible names.")
            entry = {**descriptor, "url":after["url"], "prefix":prefix, "heading":after.get("heading", descriptor["label"]), "rows":page_rows,
                     "next_control":after.get("next_control"), "submit_control":after.get("submit_control"), "submission_terms":after.get("submission_terms", "")}
            try:
                shot = self.browser.snapshot()
                name = 'filled-'+hashlib.sha256(descriptor['id'].encode()).hexdigest()[:12]+'.png'
                (self.adir(pid) / name).write_bytes(shot)
                entry['screenshot'] = {'file':f'applications/{pid}/{name}', 'caption':entry.get('heading') or 'Filled form'}
            except (Refused,OSError) as error:
                entry['screenshot_error'] = str(error)
            steps.append(entry)
            write_json(journal_path, {"steps":steps, "current":descriptor["id"], "moving":None})
            if not after.get("next_control"):
                page = after
                break
            control = after["next_control"]
            if str(control.get("name", "")).casefold() != "next" or str(control.get("visible_name", "")).casefold() != "next":
                raise Refused("STEP_CONTROL", "Only Next may advance during filling. Ask the person about Continue.")
            if not control.get("enabled"):
                page = after
                break
            write_json(journal_path, {"steps":steps, "current":descriptor["id"], "moving":control})
            try:
                page = self.browser.advance("next_control", control["name"])
            except Refused as error:
                if error.code != "STEP_CONTRACT" or not any(row.get("missing") for row in page_rows):
                    raise
                page = self.browser.read()
                if self.step(page)["id"] != descriptor["id"] or page.get("confirmation"):
                    raise
                write_json(journal_path, {"steps":steps,"current":descriptor["id"],"moving":None})
                break
        else:
            raise Refused("STEP_CONTRACT", "The form exceeded the bounded step traversal; inspect its recipe.")
        if not rows:
            raise Refused("FORM_UNREADABLE", "No named application fields were returned.")
        after = page
        complete = bool(after.get("submit_control") and not after.get("next_control"))
        fields = {row["name"]:row for row in rows}
        fill = {"posting_id": pid, "country":self.country(rec), "url": url, "filled_at": now(), "rows": rows,
                "submit_control": after.get("submit_control"), "material_digest": input_digest,
                "account": account, "site": rec["site"], "proof_site":self.proof_site(rec), "review_min_words": self.threshold(), "steps":steps, "complete":complete, "submission_terms":after.get("submission_terms", "")}
        info_notes = old_review.get("info_notes", [])
        seen_by_reviewer = {}
        reviewed_steps = set(old_review.get("reviewed_steps", []))
        unseen_steps = [step for step in steps if step["id"] not in reviewed_steps]
        unseen_names = {row["name"] for step in unseen_steps for row in step["rows"]}
        # One review per application: a box whose value or missing state changed since it keeps no stale reviewer
        # flags; it goes to the person as changed instead.
        stale = {row["name"] for row in rows if (box := old_boxes.get(row["name"])) is not None and "reviewed_value" in box and
                 (box["reviewed_value"] != row["value"] or bool(box.get("reviewed_missing")) != bool(row.get("missing")))}
        unseen = [row for row in rows if row["name"] in unseen_names]
        if old_review.get("agent_reviewed_at") and not unseen_steps:
            flags = {name: box.get("flags", []) for name, box in old_boxes.items() if name in fields}
            reviewed_at = old_review["agent_reviewed_at"]
        else:
            partial = bool(old_review.get("agent_reviewed_at"))
            review_fill = dict(fill, rows=unseen, steps=unseen_steps) if partial else fill
            result = self.agents.task("reviewer", dict(material, filled_form=review_fill))
            notes = result.get('notes',[])
            if not isinstance(notes,list) or any(not isinstance(note,dict) or not isinstance(note.get('label'),str) or not isinstance(note.get('text'),str) for note in notes):
                raise Refused('REVIEW_OUTPUT','Reviewer notes need a label and text.')
            info_notes = [*info_notes,*notes]
            flags = result.get("flags")
            if not isinstance(flags, dict):
                raise Refused("REVIEW_OUTPUT", "The reviewer must return flags keyed by the fields it reviewed.")
            # A reviewer may key a flag by the visible label or a shortened name; match those to the exact field.
            known = {row["name"] for row in review_fill["rows"]}
            aliases = {}
            for row in review_fill["rows"]:
                for label in (row["name"], row.get("label"), row["name"].split(" / ")[-1]):
                    if isinstance(label, str) and label.strip():
                        aliases.setdefault(normalize(label.rstrip("* ")), row["name"])
            matched = {}
            for name, value in flags.items():
                exact = name if name in known else aliases.get(normalize(str(name).rstrip("* ")))
                if exact is None:
                    raise Refused("REVIEW_OUTPUT", "The reviewer flagged a field that is not on the form: " + str(name)[:80])
                if not isinstance(value, list):
                    raise Refused("REVIEW_OUTPUT", "Invalid reviewer flags.")
                matched[exact] = matched.get(exact, []) + value
            flags = matched
            if partial:
                flags = {**{name: box.get("flags", []) for name, box in old_boxes.items() if name in fields}, **flags}
            reviewed_steps.update(step["id"] for step in review_fill["steps"])
            reviewed_at = now()
            seen_by_reviewer = {row["name"]: (row["value"], bool(row.get("missing"))) for row in review_fill["rows"]}
        prior_steps = (self.fill(pid) or {}).get("steps", [])
        prior_ids = [step["id"] for step in prior_steps]
        current_ids = [step["id"] for step in steps]
        changed_steps = set(current_ids) - set(prior_ids)
        threshold = self.threshold()
        boxes = []
        for row in rows:
            field_flags = flags.get(row["name"], [])
            if not isinstance(field_flags, list) or any(not isinstance(flag, dict) or
                    flag.get("severity") not in ("warn", "block") or not flag.get("text") for flag in field_flags):
                raise Refused("REVIEW_OUTPUT", "Invalid reviewer flags.")
            if old_review.get("agent_reviewed_at"):
                field_flags = [flag for flag in field_flags if flag.get("source") != "workflow"]
                if row["name"] in stale and row["name"] not in unseen_names:
                    field_flags = []
            else:
                field_flags = [{"severity":flag["severity"], "text":flag["text"]} for flag in field_flags]
            old = old_boxes.get(row["name"])
            changed = old_review.get("agent_reviewed_at") and (old is None or row.get("step_id") in changed_steps or self.definition(old) != self.definition(row) or (not old.get("missing") and old.get("value") != row["value"]) or (AUTH_FACT.search(row["name"]) and old_country is not None and old_country != self.country(rec)))
            changed = changed or bool(old and old.get("requires_person"))
            if row.get("missing"):
                field_flags = field_flags + [{"severity": "warn", "source":"workflow", "text": "A fact is missing; answer its question in Questions."}]
            if changed:
                field_flags = field_flags + [{"severity": "warn", "source":"workflow", "text": "This box changed since the agent review; the person must review it."}]
            if (row["source"] == "prior edit" and not row.get('intentional_empty')) or (row["source"] == "draft" and row["options"]):
                field_flags = field_flags + [{"severity": "warn", "source":"workflow", "text": "Confirm this proposed fact or retained edit."}]
            needs = bool(field_flags) or bool(row.get("draft_note")) or (row["custom"] and row["source"] not in ("reused", "person answer") and len(str(row["value"]).split()) >= threshold)
            approved = (old and not changed and old.get("value") == row["value"] and
                        old.get("approved_by") in ("phone", "web", "terminal-person") and not old.get("needs_apply"))
            seen = seen_by_reviewer.get(row["name"]) or ((old["reviewed_value"], old.get("reviewed_missing", False)) if old and "reviewed_value" in old else None)
            boxes.append(dict(row, **({"reviewed_value": seen[0], "reviewed_missing": seen[1]} if seen else {}), requires_person=bool(changed), flags=field_flags, state="approved" if approved or not needs else "pending",
                              approved_by=old["approved_by"] if approved else None if needs else "auto", needs_apply=False))
        for name, old in old_boxes.items():
            if name not in fields:
                acknowledged = old.get("removed") and old.get("approved_by") in PERSON_BY and old.get("state") == "approved"
                boxes.append(dict(old, removed=True, needs_apply=False, requires_person=not acknowledged,
                    state="approved" if acknowledged else "pending", approved_by=old.get("approved_by") if acknowledged else None,
                    flags=[{"severity":"warn","source":"workflow","text":"The form removed this box. Acknowledge this change before submission."}]))
        if getattr(self, "restoring", False) and old_fill and old_ruling.get("submit_digest") == digest(old_fill) and self.approval_content(fill) == self.approval_content(old_fill):
            write_json(self.adir(pid) / "form_fill.json", old_fill)
            write_json(self.adir(pid) / "review.json", old_review)
            rec = self.record(pid)
            rec["ruling"] = old_ruling
            self.save(rec)
            if complete:self.close_help_questions(rec)
            return old_review
        if self.fill(pid):
            archive = self.adir(pid) / "history" / digest(self.review(pid))[:16]
            write_json(archive / "form_fill.json", self.fill(pid))
            write_json(archive / "review.json", self.review(pid))
        write_json(self.adir(pid) / "form_fill.json", fill)
        review = {"posting_id": pid, "boxes": boxes, "info_notes":info_notes, "agent_reviewed_at": reviewed_at, "reviewed_steps":sorted(reviewed_steps), "coverage_complete":complete, "submission_terms":fill.get("submission_terms", ""), "destinations":[{"url":s["url"],"heading":s.get("heading",s["label"])} for s in steps], "fill_digest": digest(fill)}
        write_json(self.adir(pid) / "review.json", review)
        rec = self.record(pid)
        rec["status"] = "ready"
        rec.setdefault("materials", {}).update(application_dir=f"applications/{pid}",
                                              form_fill_json=f"applications/{pid}/form_fill.json")
        self.invalidate(rec)
        self.event(rec, "filled, read back, drafted and reviewed by agents")
        self.save(rec)
        if complete:self.close_help_questions(rec)
        missing_rows = [row for row in rows if row.get("missing")]
        self.missing_questions(rec, missing_rows)
        if missing_rows:
            raise Refused("MISSING_FACT", "The filled form was reviewed. Answer the missing facts in Questions, then resume.")
        if not complete:
            raise Refused("STEP_CONTROL", "The remaining application step is not ready. Resume after correcting the visible form error.")
        self.receipt(f"{rec['title']} at {rec['company']}: custom replies and flagged boxes are ready in the app.")
        return review

    @staticmethod
    def approval_content(fill):
        return {**{key:fill.get(key) for key in ("url","account","site","proof_site","country","material_digest","review_min_words","complete","submission_terms","submit_control")},
                "rows":[{key:row.get(key) for key in ("name","type","required","options","value","upload_name","upload_sha256","step_id")} for row in fill["rows"]],
                "steps":[{key:step.get(key) for key in ("id","url","prefix","heading","next_control","submit_control","submission_terms")} for step in fill.get("steps",[])]}

    @staticmethod
    def definition(row):
        return [row.get("type", "text"), bool(row.get("required")), row.get("options", []), row.get("context", "")]

    def question_answers(self, pid):
        answers = {}
        for path in sorted((self.root / "data/questions").glob("*.json")):
            question = read_json(path)
            target = question.get("applies_to") or {}
            if ((question.get("posting_id") or target.get("posting_id")) != pid or question.get("status") != "answered"):
                continue
            value = question.get("context") if question.get("answer") == "Confirmed" else question.get("answer")
            if target.get("form_question"):
                name = target["form_question"]
                if not AUTH_FACT.search(name) or target.get("country") == self.country(self.record(pid)):
                    answers[normalize(name)] = value
            elif target.get("form_facts") and value:
                try:
                    answers.update({normalize(name): value for name, value in json.loads(value).items() if value is not None and (not AUTH_FACT.search(name) or target.get("country") == self.country(self.record(pid)))})
                except (ValueError, AttributeError):
                    pass
        return answers

    def missing_questions(self, rec, rows):
        import questions
        paths = questions.Paths(questions=self.root / "data/questions", postings=self.root / "data/postings",
                                profile=self.root / "profile.yml", applications=self.root / "data/applications")
        existing = [q for q in questions.list_q(paths) if (q.get("posting_id") or (q.get('applies_to') or {}).get('posting_id')) == rec["id"]]
        missing={row['name'] for row in rows}
        for q in existing:
            target=q.get('applies_to') or {}
            if q.get('status')=='open' and (target.get('form_facts') or target.get('form_question') and target['form_question'] not in missing):
                questions.dismiss(paths,q['id'],by='career')
        need_country = not self.country(rec) and any(AUTH_FACT.search(row["name"]) for row in rows)
        if need_country and not any((q.get('applies_to') or {}).get('job_country') and q.get('status')=='open' for q in existing):
            questions.add(paths,topic='application',title="Job's work country",question="Which country are this job's work-authorization questions about? Use its country code, such as US or CA.",
                          posting_id=rec['id'],applies_to={'posting_id':rec['id'],'job_country':True})
        for row in rows:
            if need_country and AUTH_FACT.search(row['name']):continue
            options=row.get('options') or []
            kind='multiselect' if row['type']=='multiselect' else 'choice' if options else 'text'
            target={'posting_id':rec['id'],'form_question':row['name'],'country':self.country(rec)}
            label=re.sub(r'\s*\*\s*$','',row.get('label') or row['name'])
            title=label[:questions.MAX_TITLE]
            prompt=label if label.endswith('?') else label+'?'
            prompt=prompt[:questions.MAX_QUESTION]
            context=rec['company']+' · '+rec['title']
            candidates=[q for q in existing if (q.get('applies_to') or {}).get('form_question')==row['name']]
            current=next((q for q in candidates if q.get('status')=='open' and q.get('applies_to')==target),candidates[0] if candidates else None)
            for duplicate in candidates:
                if current and duplicate['id']!=current['id'] and duplicate.get('status')=='open':questions.dismiss(paths,duplicate['id'],by='career')
            if current and current.get('status')=='open' and current.get('applies_to')==target and current.get('options')==(options or None) and current['kind']==kind:continue
            if current:
                questions.reply(paths,current['id'],text=prompt,title=title,kind=kind,options=options,context=context,applies_to=target)
            else:
                questions.add(paths,topic='application',title=title,question=prompt,kind=kind,options=options,context=context,posting_id=rec['id'],applies_to=target)

    def close_help_questions(self, rec):
        import questions
        paths=questions.Paths(questions=self.root/'data/questions',postings=self.root/'data/postings',profile=self.root/'profile.yml',applications=self.root/'data/applications')
        for q in questions.list_q(paths,'open'):
            target=q.get('applies_to') or {}
            if (q.get('posting_id') or target.get('posting_id'))==rec['id'] and target.get('human_step') and target['human_step'] not in ('NAVIGATION_UNCERTAIN','SUBMISSION_UNCONFIRMED','ALREADY_ATTEMPTED'):
                questions.dismiss(paths,q['id'],by='career')

    def stop_question(self, rec, error):
        import questions
        paths = questions.Paths(questions=self.root / "data/questions", postings=self.root / "data/postings",
                                profile=self.root / "profile.yml", applications=self.root / "data/applications")
        existing = [q for q in questions.list_q(paths) if q.get("posting_id") == rec["id"] and
                    (q.get("applies_to") or {}).get("human_step") == error.code and q.get("status") == "open" and
                    (q.get("applies_to") or {}).get("human_scope") == getattr(self.browser,"pending_human",None)]
        human=error.code in ('SENSITIVE_FIELD','HUMAN_SIGN_IN','HUMAN_ACCOUNT')
        prompt=("Inspect the uncertain navigation yourself. If it sent an application, use confirm. Only after verifying the outcome permits resuming, answer exactly Confirmed to clear this navigation stop." if error.code=='NAVIGATION_UNCERTAIN' else
                    "Inspect the existing tab and tell your agent the outcome. Do not repeat Submit." if error.code in ('SUBMISSION_UNCONFIRMED','ALREADY_ATTEMPTED') else
                    "Handle the named personal action in the browser yourself. Optional boxes may remain unticked. Then answer exactly Confirmed (or tap Confirm) and resume. Never send passwords, codes, legal answers or account-creation values in Questions." if human else
                    str(error)[:250]+" Your agent needs to resolve this stop and resume. Is there any context you want your agent to use?")
        title=("Application needs your help" if human else "Application stopped: "+error.code.replace('_',' ').lower())[:questions.MAX_TITLE]
        if existing:
            if existing[0]['question']!=prompt or str(error) not in (existing[0].get('context') or ''):
                questions.reply(paths,existing[0]['id'],text=prompt,title=title,context=str(error))
        else:
            questions.add(paths, topic="application", title=title,
                          kind="confirm" if error.code in ("SENSITIVE_FIELD","HUMAN_SIGN_IN","HUMAN_ACCOUNT") else "text",
                          question=prompt,
                          context=str(error), posting_id=rec["id"],
                          applies_to={"posting_id": rec["id"], "human_step": error.code, "human_scope":getattr(self.browser,"pending_human",None), "navigation_digest":digest(read_json(self.adir(rec["id"]) / "steps.json", {})) if error.code == "NAVIGATION_UNCERTAIN" else None, "note": True})
            if self.record(rec["id"]).get("submission", {}).get("attempted_at"):
                self.receipt(f"{rec['company']}: sending is unconfirmed. Intent is not proof of sending or non-sending. Inspect the existing tab. Only if you verify nothing was sent, you may submit manually and run confirm. Details are in Questions.")
            else:
                self.receipt(f"{rec['company']}: application stopped for your help. Details are in Questions.")

    def resolve_uncertain(self, rec, by="web", reason=""):
        if by not in PERSON_BY:
            raise Refused("PERSON_APPROVAL", "Only the person may resolve an uncertain submission.")
        if not rec.get("submission", {}).get("attempted_at") or rec.get("status") != "submission_unconfirmed" or not reason.strip():
            raise Refused("UNCERTAIN_ONLY", "Supply the person's reason for releasing an uncertain submission.")
        rec["submission"].update(resolved_at=now(), resolved_by=by, resolution=reason)
        self.invalidate(rec)
        self.event(rec, "person released uncertain submission; resubmission remains forbidden")
        self.save(rec)
        active = read_json(self.root / "data/active.json", {})
        if active.get("posting_id") == rec["id"]:
            write_json(self.root / "data/active.json", {})
        return rec

    def threshold(self):
        threshold = self.settings.get("review_min_words", 25)
        try:
            threshold = int(threshold)
        except (ValueError, TypeError) as error:
            raise Refused("BAD_SETTING", "Review threshold must be a positive integer.") from error
        if threshold < 1:
            raise Refused("BAD_SETTING", "Review threshold must be a positive integer.")
        return threshold

    def verify(self, rows, fields):
        if {row["name"] for row in rows} != set(fields):
            raise Refused("FORM_CHANGED", "Named fields changed; rebuild the fill and its review.")
        for row in rows:
            field = fields[row["name"]]
            if row.get("missing"):
                row.update(verified=False, status="needs user")
                continue
            expected = row.get("upload_name", row["value"]) if row["type"] == "file" else row["value"]
            if (field.get("value") != expected or field.get("type", "text") != row["type"] or
                    field.get("options", []) != row["options"] or bool(field.get("required")) != row["required"] or field.get("context", "") != row.get("context", "") or
                    (row["type"] == "file" and field.get("sha256") != row.get("upload_sha256"))):
                raise Refused("READBACK_MISMATCH", f"The page does not match the approved value or definition of {row['name']}.")
            row["verified"] = True
            row["status"] = "filled"

    def box(self, rec, name, decision, value=None, by="web", seen=None):
        if by not in PERSON_BY:
            raise Refused("PERSON_APPROVAL", "An agent proposal cannot stand in for the person's box decision.")
        if rec.get("submission", {}).get("attempted_at"):
            raise Refused("ALREADY_ATTEMPTED", "The application has already reached submission.")
        review = self.review(rec["id"])
        box = next((box for box in review["boxes"] if box["name"] == name), None)
        if box is None or decision not in ("approve", "edit"):
            raise Refused("BAD_BOX", "Choose an existing box and approve or edit it.")
        if seen is not None and digest(box) != seen:
            raise Refused('FIELD_CHANGED', 'This answer changed. Read it again before approving.')
        changed = value is not None and value != box["value"]
        if decision == "edit" or (decision == "approve" and changed):
            if box["type"] == "file":
                raise Refused("UPLOAD_REQUIRED", "Choose upload material in the profile or application and rebuild the fill.")
            if not isinstance(value, str) or (box["required"] and not value.strip()):
                raise Refused("BAD_VALUE", "Supply a nonempty answer for a required field.")
            if box["type"] == "multiselect":
                from field_policy import multiselect_value
                value = multiselect_value(value, box["options"])
            if box["type"] != "multiselect" and box["options"] and value not in box["options"] and not (value == '' and not box['required']):
                raise Refused("OPTION_MISMATCH", "Choose an exact option.")
            box.update(value=value, needs_apply=True, flags=[], source="user edit")
        elif any(flag["severity"] == "block" for flag in box["flags"]):
            raise Refused("BLOCKED_BOX", "Edit the blocked answer before approval.")
        box.update(state="approved", approved_by=by, requires_person=False)
        box['intentional_empty'] = not box['required'] and box['value'] == ''
        write_json(self.adir(rec["id"]) / "review.json", review)
        if not box.get("removed"):
            self.remember_answer(rec["id"], name, box["value"], by, box, personal=box.get("source") in ("person answer","user edit","prior edit"))
            if box.get("custom"):
                self.add_earlier_answer(rec["id"], name, box["value"], by)
        # A preview edit is also the person's answer to the engine's missing-facts thread.
        # Resolve a batch only when every requested fact has a personal decision.
        import questions
        paths = questions.Paths(questions=self.root / 'data/questions', postings=self.root / 'data/postings',
                                profile=self.root / 'profile.yml', applications=self.root / 'data/applications')
        for question in questions.list_q(paths, 'open'):
            target = question.get('applies_to') or {}
            if (question.get('posting_id') or target.get('posting_id')) != rec['id']:
                continue
            if target.get('form_facts'):
                try:
                    requested, _ = json.JSONDecoder().raw_decode((question.get('context') or '{}').lstrip())
                except ValueError:
                    continue
                current = {b['name']:b for b in review['boxes']}
                if isinstance(requested,dict) and requested and name in requested and all(
                    key in current and current[key].get('approved_by') in PERSON_BY and
                    isinstance(current[key].get('value'),str) and current[key]['value'].strip() for key in requested):
                    questions.answer(paths,question['id'],json.dumps({key:current[key]['value'] for key in requested}),by=by)
            elif target.get('form_question') and target['form_question'] in (name, box.get('field_name')) and box['value']:
                questions.answer(paths,question['id'],box['value'],by=by)
        rec = self.record(rec['id'])
        self.invalidate(rec)
        self.event(rec, f"{decision} box: {name}")
        self.save(rec)
        return review

    def _walk_saved(self, fill, edits=None):
        entries = fill.get("steps") or [{"id":"application", "url":fill["url"], "prefix":None,
                                          "rows":fill["rows"], "next_control":None, "submit_control":fill.get("submit_control")}]
        observed = self.browser.read()
        observed_id = self.step(observed)["id"]
        all_rows = {row["name"]:row for row in fill["rows"]}
        for entry in entries:
            if entry["id"] == observed_id:
                current = self.page(observed, entry["url"], prefix=entry.get("prefix"), allow_empty=True)
                self.verify(copy.deepcopy([all_rows[row["name"]] for row in entry["rows"]]), current)
        page = self.browser.first() if hasattr(self.browser, "first") else observed
        boxes = {box["name"]:box for box in edits["boxes"]} if edits else {}
        for index, entry in enumerate(entries):
            if self.step(page)["id"] != entry["id"]:
                raise Refused("FORM_CHANGED", "The application added, removed or replaced a step; resume and review its changed boxes.")
            if page.get("heading", self.step(page)["label"]) != entry.get("heading", entry.get("label")):
                raise Refused("FORM_CHANGED", "The displayed role or step heading changed since the person's review.")
            current = self.page(page, entry["url"], prefix=entry.get("prefix"), allow_empty=True)
            rows = [all_rows[row["name"]] for row in entry["rows"]]
            if edits:
                for row in rows:
                    box = boxes[row["name"]]
                    if box.get("needs_apply"):
                        self.browser.fill(row.get("field_name", row["name"]), box["value"], box["type"])
                        row.update(value=box["value"], missing=False)
                page = self.browser.read()
                current = self.page(page, entry["url"], prefix=entry.get("prefix"), allow_empty=True)
            for row in rows:
                field = current.get(row["name"], {})
                # Revisited server pages can drop values. Restore only exact approved values,
                # only after our navigation, with unchanged definitions; never overwrite a different value.
                restore = not field.get("value") and row.get("value") and not row.get("missing") and not row.get("human_completed") and (index > 0 or observed_id != entry["id"])
                if not restore:
                    continue
                if not field or self.definition(field) != self.definition(row):
                    raise Refused("FORM_CHANGED", "A revisited field definition changed; no restoration was performed.")
                if row["type"] == "file":
                    path = Path(row["value"])
                    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row.get("upload_sha256"):
                        raise Refused("READBACK_MISMATCH", "The approved resume bytes are no longer available.")
                receipt = self.browser.fill(row.get("field_name", row["name"]), row["value"], row["type"])
                if row["type"] == "file" and receipt != {"name":row["upload_name"], "sha256":row["upload_sha256"]}:
                    raise Refused("UPLOAD_RECEIPT", "The restored upload differs from the approved bytes.")
                page = self.browser.read()
                current = self.page(page, entry["url"], prefix=entry.get("prefix"), allow_empty=True)
            self.verify(rows, current)
            if page.get("submission_terms", "") != entry.get("submission_terms", ""):
                raise Refused("FORM_CHANGED", "The text beside Submit changed; the person must see it.")
            if page.get("next_control") != entry.get("next_control") or page.get("submit_control") != entry.get("submit_control"):
                raise Refused("FORM_CHANGED", "A step's navigation or submit control changed.")
            entry["rows"] = rows
            if index + 1 < len(entries):
                control = page.get("next_control")
                if not control or not control.get("enabled"):
                    raise Refused("STEP_CONTROL", "A reviewed step can no longer advance.")
                write_json(self.adir(fill["posting_id"]) / "steps.json", {"steps":entries, "current":entry["id"], "moving":control})
                page = self.browser.advance("next_control", control["name"])
                self.navigation_outcome(self.record(fill["posting_id"]), page)
                if not self.posting_destination(fill["url"], page.get("url", "")):
                    raise Refused("WRONG_APPLICATION", "The reviewed transition left the posting; its outcome is recorded.")
        write_json(self.adir(fill["posting_id"]) / "steps.json", {"steps":entries, "current":entries[-1]["id"], "moving":None})
        return page

    def apply_edits(self, rec):
        pid = rec["id"]
        self.active(pid)
        fill, review = self.fill(pid), self.review(pid)
        self._walk_saved(fill, edits=review)
        rows = fill["rows"]
        fill.update(rows=rows, filled_at=now())
        for box in review["boxes"]:
            box["needs_apply"] = False
        review["fill_digest"] = digest(fill)
        write_json(self.adir(pid) / "form_fill.json", fill)
        write_json(self.adir(pid) / "review.json", review)
        self.invalidate(rec)
        self.event(rec, "person's edits applied and read back by the working agent")
        self.save(rec)
        return review

    def approve(self, rec, by="web", note=None, fill_digest=None):
        if by not in PERSON_BY:
            raise Refused("PERSON_APPROVAL", "Only the person may approve submission.")
        if not fill_digest or fill_digest != self.review(rec["id"]).get("fill_digest") or fill_digest != digest(self.fill(rec["id"])):
            raise Refused("STALE_APPROVAL", "Reload the application: approval must include the exact digest displayed to the person.")
        missing = self.gate(rec["id"])
        if missing:
            raise Refused("GATE", "; ".join(missing))
        rec["ruling"].update(submit_approved_at=now(), submit_approved_by=by,
                             submit_digest=digest(self.fill(rec["id"])))
        for row in self.fill(rec["id"])["rows"]:
            self.remember_answer(rec["id"], row["name"], row["value"], by, row,
                                 personal=row["source"] in ("person answer", "user edit", "prior edit"))
        self.event(rec, f"person approved this application for submit via {by}")
        self.save(rec)
        return rec

    def submit(self, rec):
        pid = rec["id"]
        if self.duplicate_application(rec):
            raise Refused("DUPLICATE_APPLICATION", "This application or a known variant was already attempted.")
        try:
            self.active(pid)
        except Refused as error:
            if error.code not in ("TAB_MISSING", "SESSION_LOST"):
                raise
            self.start(rec)
            self.active(pid)
            rec = self.record(pid)
        missing = self.gate(pid)
        fill = self.fill(pid)
        if missing or rec.get("ruling", {}).get("submit_digest") != digest(fill):
            raise Refused("GATE", "; ".join(missing) or "Approve this exact application before submitting.")
        try:
            if hasattr(self.browser, "prepare_submit"):
                self.browser.prepare_submit()
            page = self._walk_saved(copy.deepcopy(fill))
        except Refused as error:
            if error.code not in ("FORM_CHANGED", "READBACK_MISMATCH"):
                raise
            self.invalidate(rec)
            self.save(rec)
            self.start(rec)  # Resume saved drafts; the completed full review is never rerun.
            raise Refused("FORM_CHANGED", str(error) + " The changed application is back in the app for personal review.") from error
        control = page.get("submit_control")
        if not control or control != fill.get("submit_control") or not control.get("name") or not control.get("enabled"):
            raise Refused("SUBMIT_CONTROL", "The visible submit control has changed or is disabled.")
        if ACCOUNT_CONTROL.search(control["name"]):
            raise Refused("HUMAN_ACCOUNT", "Account creation and consent belong to the person; this is not an application submit.")
        last = fill["steps"][-1]
        approved = (copy.deepcopy(last["rows"]), last.get("prefix"), last["url"], last)
        if hasattr(self.browser, "preflight_submit"):
            self.browser.preflight_submit(control["name"], approved=approved)
        # Persist before the irreversible call. A crash or ambiguous result never permits a second click.
        with locked(self.root):
            rec = self.record(pid)
            if self.duplicate_application(rec):
                raise Refused("DUPLICATE_APPLICATION", "Another record already attempted this application.")
            missing = self.gate(pid)
            if missing or rec.get("ruling", {}).get("submit_digest") != digest(fill):
                raise Refused("GATE", "; ".join(missing) or "The person changed the application approval.")
            rec["submission"] = {"attempted_at": now()}
            write_json(self.adir(pid) / "submit-intent.json", rec["submission"])
            rec["status"] = "submission_unconfirmed"
            self.save(rec)
        try:
            last = fill["steps"][-1]
            self.browser.submit(control["name"], approved=(copy.deepcopy(last["rows"]), last.get("prefix"), last["url"], last))
            return self.confirm(rec)
        except Refused:
            self.receipt(f"Sending is unconfirmed: {rec['title']} at {rec['company']}. Intent is not proof of sending or non-sending. Inspect the existing tab. If you verify nothing was sent, you may submit manually and run confirm. Resolve the outcome in the app before releasing it.")
            raise

    def confirm(self, rec, by="agent"):
        if by != 'agent' and by not in PERSON_BY:
            raise Refused('PERSON_APPROVAL','Only the person can confirm a manual submission.')
        if not rec.get("submission", {}).get("attempted_at"):
            raise Refused("NO_SUBMIT", "No submission attempt to confirm.")
        self.active(rec["id"])
        page = self.browser.await_confirmation() if hasattr(self.browser,"await_confirmation") else self.browser.read()
        if (not page.get("identity_verified") or page.get("blocked") or page.get("challenge") or page.get("signed_out") or
                page.get("errors") or not page.get("confirmation")):
            raise Refused("SUBMISSION_UNCONFIRMED", "No positive confirmation. Inspect the existing tab. Only if you verify nothing was sent, you may submit manually and run confirm.")
        if company_url(page.get("url", "")) != company_url(self.fill(rec["id"])["url"]):
            raise Refused("WRONG_CONFIRMATION", "The confirmation origin does not match this application.")
        shot = self.browser.snapshot()
        if not isinstance(shot, bytes) or not shot.startswith(b"\x89PNG\r\n\x1a\n"):
            raise Refused("SNAPSHOT_REQUIRED", "Save a PNG confirmation snapshot before marking applied.")
        try:
            with Image.open(io.BytesIO(shot)) as image:
                image.verify()
        except (OSError, SyntaxError) as error:
            raise Refused("SNAPSHOT_REQUIRED", "The confirmation snapshot is not a valid image.") from error
        adir = self.adir(rec["id"])
        (adir / "confirmation.png").write_bytes(shot)
        write_text(adir / "confirmation.txt", page["url"] + "\n" + str(page["confirmation"]) + "\n")
        rec["status"] = "applied"
        rec["submission"].update(confirmed_at=now(), confirmation=page["confirmation"], url=page["url"])
        rec.setdefault("materials", {}).update(confirmation_png=f"applications/{rec['id']}/confirmation.png",
                                              confirmation_txt=f"applications/{rec['id']}/confirmation.txt")
        self.event(rec, "Submitted; confirmation saved" if by == 'agent'
                   else "You confirmed the submission; confirmation saved")
        self.save(rec)
        try:
            self.browser.close()
        except Refused as error:
            # Confirmation is durable. A tab-close failure must not lose or repeat a successful submit.
            rec["submission"]["close_error"] = {"code": error.code, "error": str(error)}
            self.save(rec)
        write_json(self.root / "data/active.json", {})
        self.receipt(f"Applied: {rec['title']} at {rec['company']}. Confirmation is saved in the app.")
        return rec

    @staticmethod
    def application_keys(rec):
        keys = set()
        for url in (rec.get("url"), (rec.get("posting") or {}).get("apply_url"), rec.get("variant_of")):
            if not url:
                continue
            value = urlsplit(url)
            path = re.sub(r"/apply$", "", value.path.rstrip("/"))
            original_query = parse_qsl(value.query, keep_blank_values=True)
            job_id = next((v for k, v in original_query if k == "gh_jid"), None)
            if job_id and path.rsplit("/", 1)[-1] != job_id:
                path += "/jobs/" + job_id
            query = [(k, v) for k, v in original_query
                     if not k.lower().startswith("utm_") and k.lower() not in ("ref", "source", "gh_src", "gh_jid")]
            keys.add(urlunsplit((value.scheme.lower(), value.netloc.lower(), path, urlencode(sorted(query)), "")))
        return keys

    def duplicate_application(self, rec):
        keys = self.application_keys(rec)
        for path in (self.root / "data/postings").glob("*.json"):
            other = read_json(path)
            if other.get("id") != rec["id"] and ((other.get("submission") or {}).get("attempted_at") or read_json(self.adir(other["id"]) / "submit-intent.json", {}).get("attempted_at")) and keys & self.application_keys(other):
                return other["id"]
        return None

    def release(self, rec):
        state = read_json(self.root / "data/active.json", {})
        if state.get("posting_id") not in (None, rec["id"]):
            raise Refused("FORM_BUSY", "Another application owns the active form.")
        if rec.get("submission", {}).get("attempted_at") and rec.get("status") != "applied":
            raise Refused("SUBMISSION_UNCONFIRMED", "Keep the attempted submission open for confirmation.")
        if state.get("posting_id") == rec["id"]:
            try:
                self.browser.bind(state["handle"])
                self.browser.close()
            except Refused as error:
                self.event(rec, "form slot released after unavailable tab: " + error.code)
        self.invalidate(rec)
        if rec.get("status") not in ("applied","under_review","interview","offer","rejected","closed"):
            rec["status"] = rec.get("ruling", {}).get("decision", "approved")
        self.save(rec)
        write_json(self.root / "data/active.json", {})
        return rec

    def recipe(self, rec, system, learning=None):
        if self.settings.get("recipes") != "Enabled":
            raise Refused("SETUP_CHOICE_REQUIRED", "Choose whether to save recipes in setup Questions first.")
        if rec.get("status") not in ("applied","under_review","interview","offer","rejected","closed"):
            raise Refused("LEARN_FIRST", "Save a recipe after a confirmed application.")
        slug(system)
        learning = learning or {}
        if not isinstance(learning, dict) or set(learning) - {"steps", "confirmation_pattern"}:
            raise Refused("BAD_RECIPE", "Learning accepts steps and a generic confirmation_pattern only.")
        if (not isinstance(learning.get("steps", []), list) or
                any(not isinstance(step, str) for step in learning.get("steps", [])) or
                not isinstance(learning.get("confirmation_pattern", ""), str)):
            raise Refused("BAD_RECIPE", "Use step-name strings and a confirmation-pattern string.")
        path = self.root / "recipes" / system
        if path.exists():
            raise Refused("RECIPE_EXISTS", "Review and update the person's existing adapter in place.")
        learned = {"system": system, "fields": [{"name": row["name"], "type": row["type"]}
                   for row in self.fill(rec["id"])["rows"]], **learning}
        # A local metadata adapter supplies learned names to the generic working agent. No form values or permissions.
        source = ("import { cli, Strategy } from '@jackwener/opencli/registry';\n"
                  "const learned = " + json.dumps(learned, indent=2) + ";\n"
                  "cli({site:" + json.dumps(system) + ",name:'recipe',access:'read',browser:false,"
                  "strategy:Strategy.LOCAL,description:'Learned application form structure',"
                  "args:[],func:async()=>learned});\n")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=path.parent, prefix=".recipe-") as temporary:
            staged = Path(temporary)
            write_json(staged / "package.json", {"name": "opencli-plugin-" + system, "version": "0.1.0", "type": "module",
                                                 "peerDependencies": {"@jackwener/opencli": "2.0.2"}})
            write_json(staged / "opencli-plugin.json", {"name":system, "version":"0.1.0",
                       "description":"Learned application form structure"})
            write_json(staged / "learning.json", learned)
            write_text(staged / "index.js", source)
            checked = subprocess.run(["node", "--check", str(staged / "index.js")], capture_output=True, text=True)
            if checked.returncode:
                raise Refused("BAD_RECIPE", "The generated adapter failed its JavaScript syntax check.")
            os.replace(staged, path)
        return {"path": str(path / "index.js"), "loaded": False, "syntax_verified": True}

    def chat(self, text=None, by="web", reply=False, resolve=None, posting_id=None, field_name=None):
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise Refused("CHAT_TEXT", "Write a message of 1 to 8000 characters.")
        if posting_id:
            self.record(posting_id)
        if field_name is not None:
            if not isinstance(field_name,str) or not field_name or not posting_id or field_name not in {b['name'] for b in self.review(posting_id)['boxes']}:
                raise Refused("BAD_BOX", "Choose a field from this application's preview.")
        path = self.root / "data/chat.json"
        messages = read_json(path, [])
        message = {"n": len(messages) + 1, "at": now(), "from": "agent" if reply else "user",
                   "text": text, "by": by, "posting_id": posting_id, "field_name": field_name}
        if resolve is not None:
            previous = next((item for item in messages if item["n"] == resolve), None)
            if not reply or not previous or previous["from"] != "user":
                raise Refused("CHAT_RESOLVE", "An agent reply can resolve an existing user comment.")
            previous["resolved_at"] = now()
        messages.append(message)
        write_json(path, messages)
        if not reply and posting_id:
            rec = self.record(posting_id)
            self.invalidate(rec)
            self.save(rec)
        return message

    def deliver_chat(self, message):
        try:
            receipt = self.agents.message(f"[Career app message {message['n']}] {message['text']}\nField: {message.get('field_name') or 'application'}\n"
                f"Application: {message.get('posting_id') or 'general'}. Reply with the career chat command.")
            fields = {"delivery": receipt}
        except Refused as error:
            fields = {"delivery_error": {"code": error.code, "error": str(error)}}
        with locked(self.root):
            path = self.root / "data/chat.json"
            messages = read_json(path, [])
            for stored in messages:
                if stored["n"] == message["n"]:
                    stored.update(fields)
                    message.update(fields)
            write_json(path, messages)

    def messages(self):
        return read_json(self.root / "data/chat.json", [])

    def setup(self, resume, by="agent"):
        """Propose from resume text for a short chat; retain all saved decisions."""
        resume = Path(resume).expanduser().resolve()
        if not resume.is_file():
            raise Refused("RESUME_REQUIRED", "Provide extracted resume text in the person's workspace.")
        path = self.root / "data/setup.json"
        state = read_json(path, {})
        proposal = state.get("proposal") or self.agents.task("setup", {"resume": resume.read_text()})
        if not isinstance(proposal.get("profile"), dict) or not isinstance(proposal.get("targets"), dict):
            raise Refused("SETUP_OUTPUT", "The agent must propose profile and targets objects.")
        for name, filename in (("profile", "profile.yml"), ("targets", "companies.yml")):
            target = self.root / filename
            current = yaml.safe_load(target.read_text()) if target.exists() else None
            if current and (name == "profile" or current.get("companies")):
                proposal[name] = current
        write_json(path, {"proposal": proposal})
        self.settings = read_json(self.root / "settings.json", {})
        self.settings.setdefault("resume", str(resume))
        write_json(self.root / "settings.json", self.settings)
        return proposal

    def save_decision(self, target, value):
        """Save what the person said in chat through the same effect as Questions."""
        import questions
        paths = questions.Paths(questions=self.root / "data/questions",
                                postings=self.root / "data/postings",
                                profile=self.root / "profile.yml",
                                applications=self.root / "data/applications")
        if not isinstance(target, dict) or not target:
            raise Refused("BAD_TARGET", "Supply a nonempty answer target object.")
        if not any(target.get(key) for key in ("question_id", "setup_object", "setting", "profile_key",
                                               "job_site", "job_country", "form_facts", "form_question")):
            raise Refused("BAD_TARGET", "Choose a profile, setting, job fact or existing question target.")
        if target.get("question_id"):
            return questions.answer(paths, target["question_id"], value.strip(), by="chat")
        q = {"id": "chat", "applies_to": target, "answer": value.strip(), "status": "answered",
             "answered_at": now(), "answer_source": "chat", "kind": "text"}
        with questions.locked(paths):
            return {"saved": questions._effect(paths, q)}
