import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

import career
import questions
from runtime import Refused, read_json, write_json
from schedule import tick
from test_workflow import Agents, Browser, Text, ready, seed, system
from workflow import Workflow


def qpaths(workflow):
    return questions.Paths(questions=workflow.root / "data/questions", postings=workflow.root / "data/postings",
                            profile=workflow.root / "profile.yml", applications=workflow.root / "data/applications")


def test_application_survives_a_real_python_process_restart(system):
    storage = system.root / "fake-browser.json"
    system.browser = Browser(storage=storage)
    ready(system)
    program = """from pathlib import Path
import sys
from test_workflow import Browser, Agents, Text
from workflow import Workflow
root=Path(sys.argv[1])
w=Workflow(root,browser=Browser(storage=root/'fake-browser.json'),agents=Agents(),text=Text())
assert w.dispatch('submit','job-one')['status']=='applied'
"""
    subprocess.run([sys.executable, "-c", program, str(system.root)], check=True,
                   env=dict(os.environ, PYTHONPATH=str(Path(__file__).parent.resolve())))
    assert system.record("job-one")["status"] == "applied"
    assert read_json(storage)["clicks"] == ["Submit application"]


def test_missing_fact_question_resumes_after_review_without_guessing_missing_values(system):
    rec=system.record("job-one");rec["country"]="US";system.save(rec)
    system.browser.fields.append({"name": "Require sponsorship?", "type": "select", "required": True,
                                  "options": ["Yes", "No"], "value": ""})
    system.dispatch("pick", "job-one", decision="approved")
    with pytest.raises(Refused) as refused:
        system.dispatch("fill", "job-one")
    assert refused.value.code == "MISSING_FACT"
    assert not any(name == "Require sponsorship?" for name, *_ in system.browser.writes)
    assert [role for role, _ in system.agents.tasks].count("reviewer") == 1
    pending = questions.list_q(qpaths(system), "open")
    assert len(pending) == 1
    questions.answer(qpaths(system), pending[0]["id"], "No", by="phone")
    review = system.dispatch("fill", "job-one")
    assert len(system.browser.opened) == 1
    assert next(box for box in review["boxes"] if box["name"] == "Require sponsorship?")["value"] == "No"


def test_unconfirmed_contact_fact_is_not_given_to_a_drafter(system):
    profile = yaml.safe_load((system.root / "profile.yml").read_text())
    profile["contact"]["email"] = {"value": "unconfirmed@example.org", "confirm": True}
    (system.root / "profile.yml").write_text(yaml.safe_dump(profile))
    system.dispatch("pick", "job-one", decision="approved")
    with pytest.raises(Refused) as refused: system.dispatch("fill", "job-one")
    assert refused.value.code == "MISSING_FACT"
    assert [role for role, _ in system.agents.tasks].count("reviewer") == 1
    assert not any(name == "Email" for name, *_ in system.browser.writes)
    assert all("unconfirmed@example.org" not in json.dumps(context) for _, context in system.agents.tasks)


def test_a_rebuild_archives_and_retains_the_persons_edit(system):
    ready(system)
    system.dispatch("box", "job-one", name="Why this role?", decision="edit", value="My own edited reply.")
    system.dispatch("apply-edits", "job-one")
    system.dispatch("release", "job-one")
    review = system.dispatch("fill", "job-one")
    box = next(box for box in review["boxes"] if box["name"] == "Why this role?")
    assert box["value"] == "My own edited reply." and box["state"] == "approved"
    assert [role for role, _ in system.agents.tasks].count("reviewer") == 1
    assert list((system.adir("job-one") / "history").glob("*/review.json"))


def test_person_can_release_uncertain_form_without_reenabling_submit(system):
    ready(system)
    system.browser.show_confirmation = False
    with pytest.raises(Refused): system.dispatch("submit", "job-one")
    system.dispatch("resolve-uncertain", "job-one", reason="Checked and closed the old tab.", by="phone")
    assert system.record("job-one")["status"] == "submission_unconfirmed"
    seed(system, "job-two")
    system.dispatch("pick", "job-two", decision="approved")
    system.dispatch("fill", "job-two")
    with pytest.raises(Refused): system.dispatch("submit", "job-one")
    assert len(system.browser.clicks) == 1


def test_cli_personal_decisions_refuse_noninteractive_approval(system, monkeypatch):
    ready(system)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    for args in (["box", "job-one", "Why this role?", "approve"], ["approve-submit", "job-one", "--digest", system.review("job-one")["fill_digest"]]):
        with pytest.raises(Refused) as error:
            career.main(["--home", str(system.root)] + args)
        assert error.value.code == "PERSON_APPROVAL"


def test_profile_empty_maps_and_dotted_labels_are_corrected(system):
    paths = qpaths(system)
    paths.profile.write_text("answers: {}\nstandard_answers: {}\n")
    question = questions.add(paths, topic="profile", title="Authorization", question="Confirm your fact.",
                             applies_to={"profile_key": "answers.work_authorized"})
    answered = questions.answer(paths, question["id"], "Yes", by="phone")
    assert answered.get("applied") and not answered.get("apply_error")
    question = questions.add(paths, topic="profile", title="Residency", question="Exact form wording.",
                             applies_to={"profile_key": "standard_answers.U.S. resident?"})
    questions.answer(paths, question["id"], "No", by="phone")
    assert yaml.safe_load(paths.profile.read_text())["standard_answers"]["U.S. resident?"] == "No"


def test_scan_failures_back_off(tmp_path):
    write_json(tmp_path / "settings.json", {"scheduled_scans":"Enabled"})
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if argv[1].endswith('/scan.mjs') else 0)
    instant = datetime(2027, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(Refused): tick(tmp_path, run=run, instant=instant)
    state = read_json(tmp_path / "data/schedule.json")
    assert len(calls) == 1 and not state["jobs"].get("completed_at")
    tick(tmp_path, run=run, instant=instant + timedelta(minutes=30))
    assert len(calls) == 1
    with pytest.raises(Refused):tick(tmp_path, run=run, instant=instant + timedelta(hours=1))
    assert len(calls) == 2


def test_chat_is_available_while_the_reviewer_is_working(system):
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    original = system.agents.task
    failures = []
    def task(role, context):
        if role == "reviewer":
            started.set()
            assert release.wait(5)
        return original(role, context)
    system.agents.task = task
    system.dispatch("pick", "job-one", decision="approved")
    def fill():
        try: system.dispatch("fill", "job-one")
        except Exception as error: failures.append(error)
        finally: done.set()
    thread = threading.Thread(target=fill)
    thread.start()
    try:
        assert started.wait(3)
        other = Workflow(system.root, browser=system.browser, agents=system.agents, text=system.text)
        other.dispatch("chat", text="Please hold this application", posting_id="job-one")
        assert not done.is_set()
    finally:
        release.set()
        thread.join(5)
    assert not failures and "resolve the application comment in chat" in system.gate("job-one")


def test_comment_arriving_during_final_readback_prevents_the_click(system):
    ready(system)
    original = system.browser.read
    def read():
        other = Workflow(system.root, browser=system.browser, agents=system.agents, text=system.text)
        other.dispatch("chat", text="Hold this application", posting_id="job-one")
        return original()
    system.browser.read = read
    with pytest.raises(Refused) as error:
        system.dispatch("submit", "job-one")
    assert error.value.code == "GATE" and not system.browser.clicks




def test_question_enqueue_is_not_mistaken_for_agent_reading(system, monkeypatch):
    paths = qpaths(system)
    question = questions.add(paths, topic='other', title='Decision', question='Choose?', kind='yesno')
    questions.answer(paths, question['id'], 'Yes', by='phone')
    sent = []
    monkeypatch.setattr(questions.Agents, 'message', lambda self, text: sent.append(text) or {'ok': True, 'state': 'queued'})
    questions.deliver_new(paths, question['id'])
    assert len(sent) == 1 and questions.pending(paths)
    questions.deliver_new(paths, question['id'])
    assert len(sent) == 1
    questions.mark_delivered(paths, questions.pending(paths))
    assert not questions.pending(paths)


def test_answer_reuse_distinguishes_portable_facts_from_role_specific_replies(system):
    record=system.record("job-one");record["country"]="US";system.save(record)
    system.browser.fields.append({"name": "Require sponsorship?", "type": "select", "required": True,
                                  "options": ["Yes", "No"], "value": ""})
    system.dispatch("pick", "job-one", decision="approved")
    with pytest.raises(Refused): system.dispatch("fill", "job-one")
    question = questions.list_q(qpaths(system), "open")[0]
    questions.answer(qpaths(system), question["id"], "No", by="phone")
    system.dispatch("fill", "job-one")
    system.dispatch("box", "job-one", name="Why this role?", decision="approve")
    system.dispatch("box", "job-one", name="Require sponsorship?", decision="approve")
    system.dispatch("approve-submit", "job-one", fill_digest=system.review("job-one")["fill_digest"])
    system.dispatch("submit", "job-one")
    seed(system, "job-two")
    record = system.record("job-two")
    record.update(company="Other Example", title="Different role", country="US")
    write_json(system.root / "data/postings/job-two.json", record)
    system.dispatch("pick", "job-two", decision="approved")
    review = system.dispatch("fill", "job-two")
    fact = next(box for box in review["boxes"] if box["name"] == "Require sponsorship?")
    essay = next(box for box in review["boxes"] if box["name"] == "Why this role?")
    assert fact["source"] == "reused" and fact["value"] == "No" and fact["state"] == "approved"
    assert essay["source"] == "draft" and essay["state"] == "pending"
    assert not [q for q in questions.list_q(qpaths(system)) if q.get("posting_id") == "job-two"]
