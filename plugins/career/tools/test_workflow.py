"""The real workflow against fake browser, /sign-in, /text and capable agents."""
import copy
import http.client
import json
import struct
import subprocess
import threading
import zlib
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

import integrations
import questions
import serve
from doctor import inspect
from runtime import Refused, read_json, write_json
from schedule import tick
from setup import prepare
from workflow import Workflow

MATERIAL = "I built a sensor calibration tool and tested it on a lab robot. " * 3


def png():
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b'\x00\xff\xff\xff')) + chunk(b'IEND', b''))


class Browser:
    def __init__(self, storage=None):
        self.storage = storage
        self.fields = [
            {"name": "Email", "type": "email", "required": True, "value": ""},
            {"name": "Resume", "type": "file", "required": True, "value": ""},
            {"name": "Why this role?", "type": "textarea", "required": True, "value": ""},
            {"name": "Start date", "type": "text", "required": True, "value": ""},
        ]
        self.opened = []
        self.writes = []
        self.clicks = []
        self.closed = 0
        self.confirmation = False
        self.show_confirmation = True
        self.blocked = False
        self.control = {"name": "Submit application", "enabled": True}
        self.url = "https://careers.example.org/job/1"

    def persist(self):
        if self.storage:
            write_json(self.storage, {key: value for key, value in vars(self).items() if key != "storage"})

    def bind(self, handle):
        if self.storage and self.storage.exists():
            vars(self).update(read_json(self.storage))
        self.handle = handle

    def open(self, url, account, site, handle):
        self.handle = handle
        self.url = url
        self.confirmation = False
        self.opened.append((url, account, site))
        self.persist()
        return self.read()

    def read(self):
        if self.confirmation and self.show_confirmation:
            return {"url": self.url + "/confirmation", "identity_verified": True,
                    "confirmation": "Application received. Reference DEMO-1."}
        return {"url": self.url, "identity_verified": True, "fields": copy.deepcopy(self.fields),
                "submit_control": self.control, "challenge": self.blocked}

    def fill(self, name, value, kind):
        self.writes.append((name, value, kind))
        next(field for field in self.fields if field["name"] == name)["value"] = Path(value).name if kind == "file" else value
        self.persist()
        if kind == "file":
            import hashlib
            next(field for field in self.fields if field["name"] == name)["sha256"] = hashlib.sha256(Path(value).read_bytes()).hexdigest()
            self.persist()
            return {"name": Path(value).name, "sha256": hashlib.sha256(Path(value).read_bytes()).hexdigest()}

    def submit(self, name, approved=None):
        self.clicks.append(name)
        self.confirmation = True
        self.persist()

    def snapshot(self):
        return png()

    def close(self):
        self.closed += 1
        self.persist()


class Agents:
    def __init__(self):
        self.tasks = []
        self.messages = []
        self.flags = {}
        self.offline = False

    def task(self, role, context):
        self.tasks.append((role, copy.deepcopy(context)))
        if role == "reviewer":
            return {"flags": self.flags}
        if role == "setup":
            return {"profile": {"contact": {"email": "person@example.org"}},
                    "targets": {"companies": [], "title_filter": {"positive": ["engineer"]}}}
        return {"value": MATERIAL.strip(), "evidence": [MATERIAL.strip()]}

    def message(self, text):
        if self.offline:
            raise Refused("OFFLINE", "The coordinator is offline.")
        self.messages.append(text)
        return {"ok": True}


class Text:
    def __init__(self):
        self.messages = []
        self.offline = False

    def send(self, text, *, group=None):
        assert group == "synthetic-career@g.us"
        if self.offline:
            raise Refused("OFFLINE", "The text dependency is offline.")
        self.messages.append(text)
        return {"ok": True, "id": f"fake-{len(self.messages)}"}


@pytest.fixture
def system(tmp_path):
    (tmp_path / "resume.txt").write_text(MATERIAL)
    (tmp_path / "resume.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (tmp_path / "profile.yml").write_text(yaml.safe_dump({
        "contact": {"email": "person@example.org"}, "standard_answers": {"Start date": "2027-01-01"}}))
    write_json(tmp_path / "settings.json", {"resume": str(tmp_path / "resume.txt"),
        "resume_pdf": str(tmp_path / "resume.pdf"), "account": "demo", "resume_mode": "one resume", "review_min_words": 25})
    settings = read_json(tmp_path / "settings.json")
    settings.update(application_assistance="Enabled", notifications="Enabled", notification_group={"id":"synthetic-career@g.us","name":"Career updates"}, recipes="Enabled")
    write_json(tmp_path / "settings.json", settings)
    browser, agents, text = Browser(), Agents(), Text()
    workflow = Workflow(tmp_path, browser=browser, agents=agents, text=text)
    seed(workflow, "job-one")
    return workflow


def seed(workflow, pid, **extra):
    write_json(workflow.root / f"data/postings/{pid}.json", dict(id=pid, company="Example Company",
        title="Software engineer", status="new", url="https://careers.example.org/job/" + pid, site="example-company", **extra))


def ready(workflow, pid="job-one"):
    workflow.dispatch("pick", pid, decision="approved")
    workflow.dispatch("fill", pid)
    workflow.dispatch("box", pid, name="Why this role?", decision="approve")
    workflow.dispatch("approve-submit", pid, fill_digest=workflow.review(pid)["fill_digest"])


@pytest.fixture
def api(system, monkeypatch):
    store = serve.Store(system.root / "data/postings", system.root / "data/applications")
    store._workflow = system
    monkeypatch.setattr(serve.Handler, "store", store, raising=False)
    monkeypatch.setattr(serve.Handler, "token", "synthetic-api-token", raising=False)
    monkeypatch.setattr(serve.Handler, "files_root", system.root, raising=False)
    monkeypatch.setattr(serve.Handler, "qpaths", questions.Paths(questions=system.root / "data/questions",
        postings=system.root / "data/postings", profile=system.root / "profile.yml",
        applications=system.root / "data/applications"), raising=False)
    monkeypatch.setattr(serve.Handler, "deliver", False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(method, path, body=None, token="synthetic-api-token"):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request(method, "/web/api" + path, body=json.dumps(body) if body is not None else None,
                           headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        response = connection.getresponse()
        data = response.read()
        connection.close()
        try:
            data = json.loads(data)
        except ValueError:
            pass
        return response.status, data
    yield request
    server.shutdown()
    server.server_close()
    thread.join()


def test_first_end_to_end_slice_across_app_api_and_agent(system, api):
    assert api("POST", "/postings/job-one/ruling", {"decision": "approved", "by": "phone"})[0] == 200
    review = system.dispatch("fill", "job-one")
    assert [box["label"] for box in review["boxes"] if box["state"] == "pending"] == ["Why this role?"]
    assert system.browser.clicks == []
    assert api("POST", "/postings/job-one/approve-submit", {})[0] == 409
    assert api("POST", "/postings/job-one/review", {"name": "Why this role?", "decision": "edit",
        "value": "My corrected account of the calibration tool.", "by": "phone"})[0] == 200
    assert api("GET", "/postings/job-one/approve-submit")[1]["allowed"] is False
    system.dispatch("apply-edits", "job-one")
    assert [task[0] for task in system.agents.tasks].count("reviewer") == 1
    assert api("GET", "/postings/job-one/approve-submit")[1]["allowed"] is True
    assert api("POST", "/postings/job-one/approve-submit", {"by": "phone", "fill_digest":system.review("job-one")["fill_digest"]})[0] == 200
    applied = system.dispatch("submit", "job-one")
    assert applied["status"] == "applied" and applied["submission"]["confirmed_at"]
    assert system.browser.clicks == ["Submit application"]
    assert system.browser.closed == 1
    assert api("GET", "/files/applications/job-one/confirmation.png")[1] == png()
    assert "applied" in (system.root / "data/applications/TRACKER.md").read_text()
    assert len(system.text.messages) == 2
    assert api("POST", "/postings/job-one/applied", {})[0] == 409
    restarted = Workflow(system.root, browser=system.browser, agents=system.agents, text=system.text)
    with pytest.raises(Refused):
        restarted.dispatch("submit", "job-one")
    assert len(system.browser.clicks) == 1


def test_stale_approval_and_readback_mismatch_block_submit(system):
    ready(system)
    system.browser.fields[0]["value"] = "changed@example.org"
    with pytest.raises(Refused, match="page does not match"):
        system.dispatch("submit", "job-one")
    assert not system.browser.clicks
    system.dispatch("box", "job-one", name="Email", decision="edit", value="corrected@example.org")
    assert "submit_digest" not in system.record("job-one")["ruling"]
    system.dispatch("apply-edits", "job-one")
    with pytest.raises(Refused, match="Approve this exact"):
        system.dispatch("submit", "job-one")


def test_profile_and_questions_changes_hold_an_already_approved_application(system):
    ready(system)
    paths = questions.Paths(questions=system.root / "data/questions", postings=system.root / "data/postings",
                            profile=system.root / "profile.yml", applications=system.root / "data/applications")
    question = questions.add(paths, topic="application", title="Correct reply", question="Use this revision?",
        posting_id="job-one", applies_to={"form_question": "Why this role?", "posting_id": "job-one"})
    questions.answer(paths, question["id"], "A corrected answer from the person.", by="phone")
    with pytest.raises(Refused, match="Questions correction"):
        system.dispatch("submit", "job-one")
    assert not system.browser.clicks
    system.dispatch("box", "job-one", name="Why this role?", decision="edit", value="A corrected answer from the person.")
    system.dispatch("apply-edits", "job-one")
    assert not system.gate("job-one")
    system.dispatch("approve-submit", "job-one", fill_digest=system.review("job-one")["fill_digest"])
    (system.root / "profile.yml").write_text('contact:\n  email: changed@example.org\n')
    with pytest.raises(Refused, match="material changed"):
        system.dispatch("submit", "job-one")


@pytest.mark.parametrize("words,pending", [(24, False), (25, True)])
def test_exact_custom_answer_threshold(system, words, pending):
    original = system.agents.task
    value = " ".join(MATERIAL.split()[:words])
    def task(role, context):
        return {"value": value, "evidence": [value]} if role == "drafter" else original(role, context)
    system.agents.task = task
    system.dispatch("pick", "job-one", decision="approved")
    review = system.dispatch("fill", "job-one")
    answer = next(box for box in review["boxes"] if box["name"] == "Why this role?")
    assert (answer["state"] == "pending") is pending


@pytest.mark.parametrize('evidence',[[],['Invented evidence absent from the supplied material.'],['short'],[42]])
def test_an_untraced_draft_is_proposed_for_review_never_asked_or_cached(system,evidence):
    original=system.agents.task
    proposal='An untraced custom answer.'
    system.agents.task=lambda role,context:({'value':proposal,'evidence':evidence} if role=='drafter' else original(role,context))
    system.dispatch('pick','job-one',decision='approved')
    review=system.dispatch('fill','job-one')
    row=next(row for row in system.fill('job-one')['rows'] if row['name']=='Why this role?')
    assert row['value']==proposal and row['source']=='draft' and not row.get('missing') and row.get('draft_note')
    assert any(write[0]=='Why this role?' for write in system.browser.writes)
    assert not any(row.get('value')==proposal for row in read_json(system.root/'data/answers.json',{}).values())
    assert not any(read_json(path).get('question')=='Why this role?' for path in (system.root/'data/questions').glob('*.json'))
    box=next(box for box in review['boxes'] if box['name']=='Why this role?')
    assert box['state']=='pending'
    system.dispatch('box','job-one',name='Why this role?',decision='edit',value='The person supplied this exact answer.')
    system.dispatch('apply-edits','job-one')
    assert next(row for row in system.fill('job-one')['rows'] if row['name']=='Why this role?')['value']=='The person supplied this exact answer.'


def test_an_instruction_reply_is_not_an_answer():
    from answer_fields import instruction_only
    assert instruction_only('fill it') and instruction_only('Please draft this.') and instruction_only('your call')
    assert not instruction_only('I want to build robot foundation models.')


def test_snapshot_and_delivery_failure_are_explicit(system):
    ready(system)
    system.browser.snapshot = lambda: b"not-an-image"
    with pytest.raises(Refused, match="PNG confirmation"):
        system.dispatch("submit", "job-one")
    assert system.record("job-one")["status"] == "submission_unconfirmed"
    system.browser.snapshot = png
    system.text.offline = True
    assert system.dispatch("confirm", "job-one")["status"] == "applied"
    outbox = read_json(system.root / "data/notifications.json")
    assert outbox[-1]["state"] == "unconfirmed" and outbox[-1]["code"] == "OFFLINE"


def test_uncertain_submit_never_repeats_and_can_recover_confirmation(system):
    ready(system)
    system.browser.show_confirmation = False
    with pytest.raises(Refused, match="No positive confirmation"):
        system.dispatch("submit", "job-one")
    assert system.record("job-one")["status"] == "submission_unconfirmed"
    with pytest.raises(Refused):
        system.dispatch("submit", "job-one")
    assert len(system.browser.clicks) == 1
    system.browser.show_confirmation = True
    assert system.dispatch("confirm", "job-one")["status"] == "applied"


@pytest.mark.parametrize("change", ["captcha", "fields", "options", "required", "control", "url"])
def test_final_site_changes_refuse_before_submit(system, change):
    ready(system)
    if change == "captcha": system.browser.blocked = True
    if change == "fields": system.browser.fields.append({"name": "New question"})
    if change == "options": system.browser.fields[0]["options"] = ["changed"]
    if change == "required": system.browser.fields[0]["required"] = False
    if change == "control": system.browser.control = {"name": "Different submit", "enabled": True}
    if change == "url": system.browser.url = "https://careers.example.org/another-application"
    with pytest.raises(Refused): system.dispatch("submit", "job-one")
    assert not system.browser.clicks


def test_reviewer_flags_reuse_profile_facts_and_draft_for_the_distinct_role(system):
    system.agents.flags = {"Start date": [{"severity": "warn", "text": "Check this date."}]}
    system.dispatch("pick", "job-one", decision="approved")
    review = system.dispatch("fill", "job-one")
    assert {box["label"] for box in review["boxes"] if box["state"] == "pending"} == {"Why this role?", "Start date"}
    for name in ("Why this role?", "Start date"):
        system.dispatch("box", "job-one", name=name, decision="approve")
    system.dispatch("approve-submit", "job-one", fill_digest=system.review("job-one")["fill_digest"])
    system.dispatch("submit", "job-one")
    seed(system, "job-two")
    system.agents.flags = {}
    before = len([role for role, context in system.agents.tasks if role == "drafter"])
    system.dispatch("pick", "job-two", decision="approved")
    review = system.dispatch("fill", "job-two")
    assert len([role for role, context in system.agents.tasks if role == "drafter"]) == before + 1
    assert all(box["state"] == "approved" for box in review["boxes"] if box["name"] != "Why this role?")
    assert next(box for box in review["boxes"] if box["name"] == "Why this role?")["state"] == "pending"


def test_threshold_and_single_active_form(system):
    settings = system.settings | {"review_min_words": 100}
    write_json(system.root / "settings.json", settings)
    ready(system)
    seed(system, "job-two")
    system.dispatch("pick", "job-two", decision="approved")
    with pytest.raises(Refused, match="active application"):
        system.dispatch("fill", "job-two")
    system.dispatch("release", "job-one")
    system.dispatch("fill", "job-two")
    assert not system.gate("job-two")


def test_chat_holds_application_and_retains_offline_message(system, api):
    ready(system)
    system.agents.offline = True
    code, message = api("POST", "/chat", {"text": "Change my answer first", "posting_id": "job-one"})
    assert code == 200 and message["delivery_error"]["code"] == "OFFLINE"
    with pytest.raises(Refused): system.dispatch("submit", "job-one")
    system.agents.offline = False
    system.dispatch("chat", text="Read and resolved.", reply=True, resolve=message["n"])
    assert len(api("GET", "/chat")[1]) == 2
    assert not system.gate("job-one")
    assert "submit_digest" not in system.record("job-one")["ruling"]


def test_real_sign_in_is_not_bypassed(system, monkeypatch):
    calls = []
    monkeypatch.setattr(integrations, "run", lambda *args, **kwargs: calls.append(args))
    system.browser = integrations.OpenCLIBrowser(system.settings, root=system.root)
    system.dispatch("pick", "job-one", decision="approved")
    with pytest.raises(Refused) as error:
        system.dispatch("fill", "job-one")
    assert error.value.code == "BROWSER_WRITES_DISABLED" and not calls




def test_question_and_text_delivery_use_documented_dependencies(system, tmp_path, monkeypatch):
    ask = tmp_path / "ask.py"; ask.touch()
    text = tmp_path / "text.cjs"; text.touch()
    sign = tmp_path / "sign-in.mjs"; sign.touch()
    for key, value in (("ASK_SCRIPT", ask), ("TEXT_SCRIPT", text), ("SIGN_IN_SCRIPT", sign)):
        monkeypatch.setenv(key, str(value))
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if "--prompt-file" in argv:
            assert Path(argv[argv.index("--prompt-file") + 1]).read_text() == "Literal $HOME `text`\nnext line"
            return ""
        return '{"ok":true,"id":"fake-publication"}'
    monkeypatch.setattr(integrations, "run", run)
    agents = integrations.Agents({"coordinator": {"agent": "codex", "session": "demo-thread"}})
    agents.message("Literal $HOME `text`\nnext line")
    assert "--codex" in calls[0] and "--no-wait" in calls[0]
    monkeypatch.setattr(integrations.Text,"check_group",lambda self,group:{"ok":True})
    assert integrations.Text().send("Question ready", group="synthetic-career@g.us")["id"] == "fake-publication"
    assert calls[1][2:4] == ["send", "synthetic-career@g.us"]
    monkeypatch.setattr(integrations, "run", lambda *args, **kwargs: '{"ok":true,"held":true,"draftId":"fake"}')
    with pytest.raises(Refused): integrations.Text().send("Held", group="synthetic-career@g.us")


def test_linux_and_mac_setup_only_stage_services_and_catch_up(tmp_path):
    linux = tmp_path / "Linux owned workspace"
    result = prepare(linux, platform="linux")
    assert not result["activated"]
    timer = (linux / ".private/services/career-scan.timer").read_text()
    assert "Persistent=true" in timer
    service = (linux / ".private/services/career-server.service").read_text()
    assert 'WorkingDirectory="' in service and "APPLE_TEAM" not in service
    mac = tmp_path / "Mac owned workspace"
    prepare(mac, platform="darwin")
    import plistlib
    scan = plistlib.loads((mac / ".private/services/dev.career.scan.plist").read_bytes())
    assert scan["StartCalendarInterval"] == {"Minute": 0} and scan["RunAtLoad"]
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0)
    write_json(linux / "settings.json", {"scheduled_scans":"Enabled"})
    instant = datetime(2027, 1, 1, tzinfo=timezone.utc)
    tick(linux, run=run, instant=instant)
    tick(linux, run=run, instant=instant + timedelta(hours=1))
    assert len(calls) == 1
    tick(linux, run=run, instant=instant + timedelta(days=8))
    assert len(calls) == 2


def test_recipe_is_an_unloaded_adapter_in_persons_folder(system):
    ready(system)
    system.dispatch("submit", "job-one")
    result = system.dispatch("recipe", "job-one", system="example-system", learning={"steps": ["Application"],
                             "confirmation_pattern": "Application received"})
    source = Path(result["path"]).read_text()
    assert result["loaded"] is False and result["syntax_verified"]
    assert "person@example.org" not in source and MATERIAL.strip() not in source
    # A fake OpenCLI registry loads the real generated adapter and executes only its local metadata function.
    package = Path(result["path"]).parent / "node_modules/@jackwener/opencli"
    package.mkdir(parents=True)
    write_json(package / "package.json", {"type": "module", "exports": {"./registry": "./registry.js"}})
    (package / "registry.js").write_text("export const Strategy={LOCAL:'local'}; export function cli(c){globalThis.command=c;}\n")
    runner = "await import(process.argv[1]); console.log(JSON.stringify(await globalThis.command.func()));"
    loaded = subprocess.run(["node", "--input-type=module", "-e", runner, result["path"]], text=True, capture_output=True, check=True)
    assert json.loads(loaded.stdout)["fields"][0] == {"name": "Email", "type": "email"}
    with pytest.raises(Refused): system.dispatch("recipe", "job-one", system="example-system")


@pytest.mark.parametrize("url", ["http://careers.example.org/jobs/1", "https://user:secret@careers.example.org/jobs/1"])
def test_applications_need_plain_https_without_credentials(system, url):
    rec = system.record("job-one")
    rec["url"] = url
    write_json(system.root / "data/postings/job-one.json", rec)
    system.dispatch("pick", "job-one", decision="approved")
    with pytest.raises(Refused): system.dispatch("fill", "job-one")
    assert not system.browser.opened


def test_delivery_failure_is_visible_in_the_app_response_and_backlog(system,api):
    system.agents.offline=True
    code,record=api('POST','/postings/job-one/ruling',{'decision':'approved','by':'phone'})
    assert code==200 and record['coordination_pending']==1
    code,events=api('GET','/events')
    assert code==200 and events['pending']==1 and events['events'][0]['delivery_error']['code']=='OFFLINE'
    assert api('GET','/events',token='wrong')[0]==401
    system.dispatch('fill','job-one')
    system.dispatch('box','job-one',name='Why this role?',decision='approve')
    code,record=api('POST','/postings/job-one/approve-submit',{'by':'phone','fill_digest':system.review('job-one')['fill_digest']})
    assert code==200 and record['coordination_pending']==3


def test_a_box_changed_after_the_one_review_keeps_no_stale_flags(system):
    original=system.agents.task
    def task(role,context):
        if role=='reviewer':
            return {'flags':{row['name']:[{'severity':'block','text':'Required and empty.'}] for row in context['filled_form']['rows'] if row['name']=='Why this role?'}}
        return original(role,context)
    system.agents.task=task
    system.dispatch('pick','job-one',decision='approved')
    system.dispatch('fill','job-one')
    box=next(b for b in system.review('job-one')['boxes'] if b['name']=='Why this role?')
    assert 'reviewed_value' in box
    system.dispatch('box','job-one',name='Why this role?',decision='edit',value='A different answer written by the person.')
    system.dispatch('apply-edits','job-one')
    box=next(b for b in system.review('job-one')['boxes'] if b['name']=='Why this role?')
    assert not any(f['text']=='Required and empty.' for f in box['flags'])
    assert [r for r,_ in system.agents.tasks].count('reviewer')<=1


def test_with_a_country_picker_the_phone_box_takes_the_national_number():
    from answer_fields import profile_value
    profile={'contact':{'phone':'+1 555 010 0000','phone_national':'555 010 0000'}}
    assert profile_value(profile,'contact.phone_national')=='555 010 0000'


def test_the_drafter_reuses_earlier_answers_and_approvals_join_them(system):
    from runtime import write_json
    write_json(system.root/'data/earlier_answers.json',{'entries':[{'type':'accomplishments','status':'submitted','company':'Prior Co','question':'Projects you are proud of','answer':'An earlier approved answer about the bench rig.','date':'2026-10-01'},
                                                                    {'type':'other','status':'drafted','company':'X','question':'Q','answer':'not approved yet','date':'2026-10-02'}]})
    seen=[]
    original=system.agents.task
    system.agents.task=lambda role,context:(seen.append(context.get('earlier_answers')) or original(role,context))
    system.dispatch('pick','job-one',decision='approved')
    system.dispatch('fill','job-one')
    assert any(e and e[0]['answer'].startswith('An earlier approved answer') and len(e)==1 for e in seen)
    long_answer=' '.join(['word']*40)
    system.dispatch('box','job-one',name='Why this role?',decision='edit',value=long_answer)
    system.dispatch('apply-edits','job-one')
    box=next(b for b in system.review('job-one')['boxes'] if b['name']=='Why this role?')
    system.dispatch('box','job-one',name='Why this role?',decision='approve')
    entries=read_json(system.root/'data/earlier_answers.json',{})['entries']
    assert any(e['answer']==long_answer and e['status']=='approved' for e in entries)
