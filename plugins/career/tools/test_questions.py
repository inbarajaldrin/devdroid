# Tests for ask.py — the Questions inbox: store, answer validation, the comment-keeping profile.yml edit,
# and each applies_to effect on a scratch copy (never the live data).
import json
import os
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import questions as Q

PROFILE_TEXT = (HERE / "fixtures" / "profile_test.yml").read_text()
SAMPLE_ID = "exampleco--software-intern--20270115"




@pytest.fixture
def paths(tmp_path):
    (tmp_path / "data/postings").mkdir(parents=True)
    rec = json.loads((HERE / "fixtures" / "posting_test.json").read_text())
    # Each test starts from an unruled synthetic posting.
    rec["status"] = "enriched"
    rec["ruling"] = {"decision": None, "reason": None, "decided_at": None}
    (tmp_path / "data/postings" / f"{SAMPLE_ID}.json").write_text(json.dumps(rec, indent=2))
    (tmp_path / "profile.yml").write_text(PROFILE_TEXT)
    (tmp_path / "applications").mkdir()
    return Q.Paths(questions=tmp_path / "questions", postings=tmp_path / "data/postings",
                   profile=tmp_path / "profile.yml", applications=tmp_path / "applications")


def read_profile(p):
    return yaml.safe_load(p.profile.read_text())


def kept_lines(before: str, after: str) -> set:
    return set(before.splitlines()) - set(after.splitlines())


# ── profile.yml edits ──

def test_add_missing_leaf_keeps_every_line():
    new, what = Q.set_profile_value(PROFILE_TEXT, "answers.how_heard_level2", "LinkedIn")
    assert "added" in what
    assert kept_lines(PROFILE_TEXT, new) == set()           # nothing removed or rewritten
    assert yaml.safe_load(new)["answers"]["how_heard_level2"] == "LinkedIn"


def test_flow_map_sets_value_and_clears_confirm():
    text = "a:\n  b: {value: null, confirm: true, query: \"ask, later\"}   # keep me\n  c: 1\n"
    new, what = Q.set_profile_value(text, "a.b", "x, y")
    d = yaml.safe_load(new)
    assert d["a"]["b"] == {"value": "x, y", "query": "ask, later"}
    assert "# keep me" in new and "confirm cleared" in what


def test_flow_map_confirm_only():
    text = "a:\n  b: {value: \"2029\", confirm: true, note: \"x\"}\n"
    new, _ = Q.set_profile_value(text, "a.b", None)
    assert yaml.safe_load(new)["a"]["b"] == {"value": "2029", "note": "x"}


def test_block_map_value_and_confirm_line():
    text = "answers:\n  gpa:\n    value: \"3.9\"   # old\n    confirm: true\n    note: n\n  other: 1\n"
    new, _ = Q.set_profile_value(text, "answers.gpa", "4.0")
    d = yaml.safe_load(new)
    assert d["answers"]["gpa"] == {"value": "4.0", "note": "n"} and d["answers"]["other"] == 1
    assert "# old" in new


def test_plain_scalar_keeps_comment():
    new, _ = Q.set_profile_value(PROFILE_TEXT, "contact.website", "https://example.org")
    line = next(ln for ln in new.splitlines() if ln.strip().startswith("website:"))
    assert "# none in the CV" in line
    assert yaml.safe_load(new)["contact"]["website"] == "https://example.org"


def test_list_index_path():
    new, _ = Q.set_profile_value(PROFILE_TEXT, "education.0.end", "2029-05")
    d = yaml.safe_load(new)
    assert d["education"][0]["end"]["value"] == "2029-05"
    assert d["education"][1]["end"] == "2024-12"


def test_quotes_are_escaped():
    new, _ = Q.set_profile_value(PROFILE_TEXT, "answers.referral", 'Jane "JD" Doe: eng # 2')
    assert yaml.safe_load(new)["answers"]["referral"] == 'Jane "JD" Doe: eng # 2'


def test_missing_intermediate_is_an_error():
    with pytest.raises(Q.AskError):
        Q.set_profile_value(PROFILE_TEXT, "nope.child", "x")


# ── store + validation ──

def test_add_validates(paths):
    with pytest.raises(Q.AskError):
        Q.add(paths, topic="profile", title="x" * 61, question="q")
    with pytest.raises(Q.AskError):
        Q.add(paths, topic="profile", title="t", question="q", kind="choice", options=[])
    with pytest.raises(Q.AskError):
        Q.add(paths, topic="nope", title="t", question="q")
    q = Q.add(paths, topic="other", title="Hello there", question="q?")
    assert q["status"] == "open" and q["applies_to"] == {"note": True}
    assert Q.add(paths, topic="other", title="Hello there", question="q?")["id"] == q["id"] + "-2"


def test_multiselect_uses_plain_exact_options_and_canonical_order(paths):
    q=Q.add(paths,topic='profile',title='Office preferences',question='Which offices?',kind='multiselect',options=['Northport, ZZ','Eastfield, ZZ'])
    assert Q.answer(paths,q['id'],['Eastfield, ZZ','Northport, ZZ'])['answer']=='Northport, ZZ; Eastfield, ZZ'
    with pytest.raises(Q.AskError):Q.answer(paths,q['id'],['Not a listed office'])
    with pytest.raises(Q.AskError):Q.answer(paths,q['id'],[])


def test_answer_normalizes_by_kind(paths):
    yn = Q.add(paths, topic="other", title="yn", question="?", kind="yesno")
    assert Q.answer(paths, yn["id"], "yes", by="web")["answer"] == "Yes"
    ch = Q.add(paths, topic="other", title="ch", question="?", kind="choice", options=["LinkedIn", "Other"])
    with pytest.raises(Q.AskError):
        Q.answer(paths, ch["id"], "Twitter", by="web")
    assert Q.answer(paths, ch["id"], "linkedin", by="web")["answer"] == "LinkedIn"
    with pytest.raises(Q.AskError):
        Q.answer(paths, "../etc", "x", by="web")


# ── effects ──

def test_profile_key_effect_and_idempotent(paths):
    q = Q.add(paths, topic="profile", title="lvl2", question="?", kind="choice",
              options=["Company careers site", "LinkedIn"], applies_to={"profile_key": "answers.how_heard_level2"})
    q = Q.answer(paths, q["id"], "LinkedIn", by="phone")
    assert q["applied"] and "added" in q["applied"]["result"]
    assert read_profile(paths)["answers"]["how_heard_level2"] == "LinkedIn"
    before = paths.profile.read_text()
    assert Q.apply_all(paths) == []                         # already applied: nothing to do
    assert paths.profile.read_text() == before
    q = Q.answer(paths, q["id"], "Company careers site", by="phone")   # re-answer replaces, no duplicate key
    assert read_profile(paths)["answers"]["how_heard_level2"] == "Company careers site"
    assert paths.profile.read_text().count("how_heard_level2:") == 1


def test_confirm_kind_profile_only_clears_flag(paths):
    paths.profile.write_text(PROFILE_TEXT.replace('over_18: {value: "Yes", note:', 'over_18: {value: "Yes", confirm: true, note:'))
    q = Q.add(paths, topic="profile", title="18", question="?", kind="confirm", applies_to={"profile_key": "answers.over_18"})
    Q.answer(paths, q["id"], "confirm", by="web")
    assert read_profile(paths)["answers"]["over_18"] == {"value": "Yes", "note": "age is not in the CV"}


def test_form_question_effect(paths):
    draft = "Line one.\n\nLine two https://portfolio.example.org/project/"
    q = Q.add(paths, topic="application", title="Why ExampleCo?", question="Use this?", kind="confirm", posting_id=SAMPLE_ID,
              context=draft, applies_to={"posting_id": SAMPLE_ID, "form_question": "Why ExampleCo?"})
    q = Q.answer(paths, q["id"], "Confirmed", by="phone")
    rec = json.loads((paths.postings / f"{SAMPLE_ID}.json").read_text())
    rel = rec["materials"]["form_answers_md"]
    assert rel == "applications/exampleco/2027-01_software-intern/form_answers.md"
    md = (paths.applications / rel.split("/", 1)[1]).read_text()
    sys.path.insert(0, str(HERE))
    assert "### Why ExampleCo?" in md and draft in md
    n_hist = len(rec["history"])
    Q.answer(paths, q["id"], "Confirmed", by="phone")      # same answer again: file and record unchanged
    rec2 = json.loads((paths.postings / f"{SAMPLE_ID}.json").read_text())
    assert len(rec2["history"]) == n_hist and md == (paths.applications / rel.split("/", 1)[1]).read_text()
    Q.answer(paths, q["id"], "Edited answer.", by="phone")
    md = (paths.applications / rel.split("/", 1)[1]).read_text()
    assert "Edited answer." in md and md.count("### Why ExampleCo?") == 1


def test_ruling_effect(paths):
    q = Q.add(paths, topic="application", title="Apply?", question="?", kind="yesno", posting_id=SAMPLE_ID,
              applies_to={"ruling": "approve"})
    q = Q.answer(paths, q["id"], "No", by="phone")
    assert "no ruling change" in q["applied"]["result"]
    assert json.loads((paths.postings / f"{SAMPLE_ID}.json").read_text())["status"] == "enriched"
    q = Q.answer(paths, q["id"], "Yes", by="phone")
    rec = json.loads((paths.postings / f"{SAMPLE_ID}.json").read_text())
    assert rec["status"] == "approved" and rec["ruling"]["decision"] == "approved" and rec["ruling"]["by"] == "phone"
    assert rec["company_info"] and rec["posting"]                         # other keys kept
    q = Q.answer(paths, q["id"], "Yes", by="phone")
    assert "already approved" in q["applied"]["result"]


def test_apply_error_is_recorded_not_raised(paths):
    q = Q.add(paths, topic="profile", title="bad", question="?", applies_to={"profile_key": "nope.child"})
    q = Q.answer(paths, q["id"], "x", by="web")
    assert q["apply_error"] and not q["applied"]
    saved=Q.add(paths,topic='application',title='Saved grouped question',question='Supply facts',posting_id=SAMPLE_ID,
                applies_to={'posting_id':SAMPLE_ID,'form_facts':True})
    saved=Q.answer(paths,saved['id'],'I prefer Northport.',by='web')
    assert 'individual field questions' in saved['apply_error'] and 'JSON' not in saved['apply_error']


# ── app links ──

def test_app_link_shapes():
    assert Q.app_link() == "http://127.0.0.1:7490/app/"
    assert Q.app_link("20261019-nvidia-x") == "http://127.0.0.1:7490/app/?q=20261019-nvidia-x"
    with pytest.raises(Q.AskError):
        Q.app_link("../etc?token=x")


def test_link_verb_and_add_message(paths, monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(Q, "notify", lambda text, **kwargs: sent.append(text) or True)
    base = ["--questions", str(paths.questions), "--postings", str(paths.postings),
            "--profile", str(paths.profile), "--applications", str(paths.applications)]
    assert Q.main(base + ["add", "--topic", "other", "--title", "Pick one", "--question", "Which?"]) == 0
    qid = capsys.readouterr().out.strip()
    assert sent == [f"Pick one — answer in the app: http://127.0.0.1:7490/app/?q={qid}"]
    assert Q.main(base + ["link", qid]) == 0
    assert capsys.readouterr().out.strip() == f"http://127.0.0.1:7490/app/?q={qid}"
    assert Q.main(base + ["link", "no-such-question"]) == 2
    assert Q.main(base + ["ping"]) == 0
    assert sent[-1] == "1 question waiting in the app: http://127.0.0.1:7490/app/"


# ── comments (note) on any kind ──

def _yesno(paths):
    return Q.add(paths, topic="other", title="Go?", question="Go ahead?", kind="yesno")


def test_comment_only_resolves_question(paths):
    q = _yesno(paths)
    q = Q.comment(paths, q["id"], "  ask me after Friday  ", by="phone")
    assert q["status"] == "answered" and q["answer"] is None and q["applied"] is False and q["needs_reading"]
    assert q["answer_source"] == "phone" and q["answered_at"] == q["note_at"]
    assert q["note"] == "ask me after Friday" and q["note_by"] == "phone"
    assert [n["text"] for n in q["notes"]] == ["ask me after Friday"]
    with pytest.raises(Q.AskError):
        Q.comment(paths, q["id"], "   ", by="web")


def test_answer_with_note_then_without_keeps_note(paths):
    q = _yesno(paths)
    q = Q.answer(paths, q["id"], "yes", by="phone", note="only if remote")
    assert q["answer"] == "Yes" and q["status"] == "answered" and q["note"] == "only if remote"
    assert q["notes"][-1]["with_answer"] == "Yes"
    q = Q.answer(paths, q["id"], "no", by="phone")
    assert q["answer"] == "No" and q["note"] == "only if remote" and len(q["notes"]) == 1


def test_note_on_choice_and_effect_unchanged(paths):
    q = Q.add(paths, topic="profile", title="Pick", question="Which?", kind="choice", options=["A", "B"],
              applies_to={"note": True})
    q = Q.answer(paths, q["id"], "b", note="B, but A is close", by="web")
    assert q["answer"] == "B" and q["applied"] and q["note"] == "B, but A is close"


def test_records_without_note_still_apply_and_list(paths, capsys):
    q = _yesno(paths)
    q.pop("notes", None)
    Q.answer(paths, q["id"], "Yes", apply_now=False, by="web")
    assert Q.apply_all(paths)[0]["applied"]
    base = ["--questions", str(paths.questions), "--postings", str(paths.postings),
            "--profile", str(paths.profile), "--applications", str(paths.applications)]
    assert Q.main(base + ["list"]) == 0
    row = [l for l in capsys.readouterr().out.splitlines() if q["id"] in l][0]
    assert row.endswith("|  | 2 (1 asks) |")          # empty note column; ask + answer turns


def test_comment_cli_events_and_no_whatsapp(paths, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda:True)
    monkeypatch.setattr("builtins.input", lambda _:"yes")
    sent = []
    monkeypatch.setattr(Q, "notify", lambda text, **kwargs: sent.append(text) or True)
    q = _yesno(paths)
    base = ["--questions", str(paths.questions), "--postings", str(paths.postings),
            "--profile", str(paths.profile), "--applications", str(paths.applications)]
    assert Q.main(base + ["comment", q["id"], "need", "a", "day"]) == 0
    q = Q.read_q(paths, q["id"])
    assert Q.event_line(q, q["events"][-1]) == f"[App] Go? — answer: none; comment: need a day (id {q['id']})"
    assert q["events"][-1]["delivered_via"] == "self"          # written by a session: not pending
    capsys.readouterr()
    assert Q.main(base + ["list"]) == 0
    assert "| answered (comment) |" in capsys.readouterr().out
    assert Q.main(base + ["show", q["id"]]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["answer"] is None and shown["answer_from_comment"] == "need a day"
    assert Q.main(base + ["answer", q["id"], "yes", "--note", "fine", "--by", "terminal-person"]) == 0
    q = Q.read_q(paths, q["id"])
    assert "needs_reading" not in q and q["applied"]
    assert Q.event_line(q, q["events"][-1]) == f"[App] Go? — answer: Yes; comment: fine (id {q['id']})"
    assert sent == []                                            # comments never go to WhatsApp
    capsys.readouterr()
    assert Q.main(base + ["list"]) == 0
    assert "| fine |" in capsys.readouterr().out


def test_serve_answer_endpoint_note(paths, monkeypatch):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    import serve as S
    monkeypatch.setattr(S.Handler, "qpaths", paths, raising=False)
    monkeypatch.setattr(S.Handler, "token", "t0k", raising=False)
    monkeypatch.setattr(S.Handler, "deliver", False)
    monkeypatch.setattr(S.Handler, "log_message", lambda *a: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    q = _yesno(paths)

    def post(body):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("POST", f"/questions/{q['id']}/answer", json.dumps(body),
                  {"Authorization": "Bearer t0k", "Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    try:
        assert post({"by": "phone"})[0] == 400
        assert post({"answer": "  ", "note": " "})[0] == 400
        st, got = post({"note": "thinking", "by": "phone"})
        assert st == 200 and got["status"] == "answered" and got["answer"] is None and got["needs_reading"]
        assert got["note"] == "thinking" and got["applied"] is False
        st, got = post({"answer": "No", "note": "not this cycle", "by": "phone"})
        assert st == 200 and got["answer"] == "No" and got["note"] == "not this cycle" and len(got["notes"]) == 2
        assert "needs_reading" not in got
        st, got = post({"answer": "Yes", "by": "phone"})
        assert st == 200 and got["answer"] == "Yes" and got["note"] == "not this cycle"
    finally:
        srv.shutdown()


def test_serve_link_page_is_the_only_public_route(paths, monkeypatch):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    import serve as S
    monkeypatch.setattr(S.Handler, "qpaths", paths, raising=False)
    monkeypatch.setattr(S.Handler, "token", "t0k", raising=False)
    monkeypatch.setattr(S.Handler, "log_message", lambda *a: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def req(method, path, auth=False):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request(method, path, "{}" if method == "POST" else None, {"Authorization": "Bearer t0k"} if auth else {})
        r = c.getresponse()
        return r.status, r.getheader("Content-Type") or "", r.read()

    try:
        for path in ("/app", "/app/", "/app/?q=20261019-nvidia-x", "/app/index.html", "/app/#x"):
            st, ctype, body = req("GET", path)
            assert st == 200 and ctype.startswith("text/html"), path
            assert b"careerapp://questions" in body and b"t0k" not in body
        for path in ("/", "/health", "/questions", "/postings", "/files/profile.yml", "/app/../health",
                     "/app/x", "/apps/", "/app/index.html/x", "/index.html"):
            assert req("GET", path)[0] == 401, path
        assert req("POST", "/app/")[0] == 401
        assert req("POST", "/questions/x/answer")[0] == 401
        assert req("GET", "/questions", auth=True)[0] == 200
    finally:
        srv.shutdown()


def test_loopback_link_listener_serves_only_the_page(monkeypatch):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    import serve as S
    monkeypatch.setattr(S.LinkPageHandler, "log_message", lambda *a: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), S.LinkPageHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def req(method, path):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request(method, path, "{}" if method == "POST" else None, {"Authorization": "Bearer anything"})
        r = c.getresponse()
        return r.status, r.read()

    try:
        st, body = req("GET", "/app/?q=20261019-nvidia-x")
        assert st == 200 and b"careerapp://questions" in body
        for path in ("/", "/health", "/questions", "/postings", "/files/profile.yml", "/app/health"):
            assert req("GET", path)[0] == 404, path
        assert req("POST", "/questions/x/answer")[0] == 404
    finally:
        srv.shutdown()


def test_comment_resolve_paths_by_kind_and_state(paths):
    # needs_reading records are skipped by apply, by id and in bulk
    q = _yesno(paths)
    Q.comment(paths, q["id"], "depends on the start date", by="web")
    assert Q.apply_all(paths) == []
    assert Q.apply_one(paths, q["id"])["applied"] is False
    # a comment on an already-answered question stays a comment
    q2 = _yesno(paths)
    Q.answer(paths, q2["id"], "Yes", by="web")
    q2 = Q.comment(paths, q2["id"], "and soon", by="web")
    assert q2["answer"] == "Yes" and q2["applied"] and "needs_reading" not in q2 and q2["note"] == "and soon"
    # a dismissed question is resolved by a comment
    q3 = _yesno(paths)
    Q.dismiss(paths, q3["id"])
    assert Q.comment(paths, q3["id"], "actually: later", by="web")["needs_reading"]
    # text kind: the comment is the answer, applied normally
    t = Q.add(paths, topic="other", title="Why?", question="Why this team?", kind="text")
    t = Q.comment(paths, t["id"], "Because of the robots.", by="web")
    assert t["answer"] == "Because of the robots." and t["applied"] and "needs_reading" not in t


def test_thread_migrates_from_legacy_record(paths):
    q = _yesno(paths)
    rec = Q.read_raw(paths, q["id"])
    for k in ("thread", "events"):
        rec.pop(k)
    rec.update(status="answered", answer="Yes", answered_at="2026-10-20T07:00:00Z", answer_source="phone",
               note="but later", notes=[{"at": "2026-10-20T06:59:00Z", "by": "phone", "text": "but later"}])
    Q.write_q(paths, rec)
    th = Q.read_q(paths, q["id"])["thread"]
    assert [(t["n"], t["role"]) for t in th] == [(1, "ask"), (2, "user"), (3, "user")]
    assert th[0]["text"] == "Go ahead?" and th[0]["kind"] == "yesno"
    assert th[1]["text"] == "but later" and th[1]["answer"] is None
    assert th[2]["answer"] == "Yes" and th[2]["at"] == "2026-10-20T07:00:00Z"
    assert Q.migrate(paths) == 1 and Q.migrate(paths) == 0          # persisted once, idempotent
    assert Q.read_raw(paths, q["id"])["thread"] == th


def test_reply_appends_ask_turn_and_reopens(paths):
    q = _yesno(paths)
    Q.comment(paths, q["id"], "only if remote", by="phone")
    q = Q.reply(paths, q["id"], text="Filter to remote roles only?", context="Two of five are remote.")
    assert q["status"] == "open" and q["answer"] is None and "needs_reading" not in q and q["applied"] is None
    assert [t["role"] for t in q["thread"]] == ["ask", "user", "ask"]
    assert q["question"] == "Filter to remote roles only?" and q["kind"] == "yesno"
    assert q["context"] == "Two of five are remote." and q["thread"][-1]["context"] == q["context"]
    assert len([p for p in paths.questions.glob("*.json")]) == 1             # no new id
    q = Q.reply(paths, q["id"], text="Which?", kind="choice", options=["Remote", "Hybrid"], title="Remote or hybrid?")
    assert q["options"] == ["Remote", "Hybrid"] and q["title"] == "Remote or hybrid?"
    q = Q.answer(paths, q["id"], "hybrid", by="phone", note="Phoenix only")
    assert q["thread"][-1] == {**q["thread"][-1], "role": "user", "answer": "Hybrid", "text": "Phoenix only"}
    with pytest.raises(Q.AskError):
        Q.reply(paths, q["id"], text="Pick", kind="choice", options=[])
    # an ask turn is the agent's own: nothing new to deliver
    assert [e["type"] for e in q["events"]] == ["comment", "answer"]



# ── commands (a shell command the user copies from the app) ──

SIGN_IN = "opencli auth login --site workday --profile jobs && echo 'signed in'"


def test_command_on_add_and_reply_mirrors_latest_ask(paths):
    plain = _yesno(paths)
    assert "command" not in plain and "command" not in plain["thread"][0]      # records without one are unchanged
    q = Q.add(paths, topic="site", title="Sign in to Workday", question="Run this on the Mac, then say Done.",
              kind="yesno", command=f"  {SIGN_IN}\n")
    assert q["command"] == SIGN_IN and q["thread"][0]["command"] == SIGN_IN
    assert Q.read_raw(paths, q["id"])["command"] == SIGN_IN
    q = Q.reply(paths, q["id"], text="That timed out. Once more?", command="opencli auth login --site workday")
    assert q["command"] == "opencli auth login --site workday"
    assert [t.get("command") for t in q["thread"]] == [SIGN_IN, "opencli auth login --site workday"]
    q = Q.reply(paths, q["id"], text="Signed in now?")                      # a re-ask without one clears it
    assert "command" not in q and "command" not in q["thread"][-1]
    assert q["thread"][0]["command"] == SIGN_IN                              # earlier turns keep theirs
    with pytest.raises(Q.AskError):
        Q.add(paths, topic="site", title="Too long", question="Run it?", command="x" * (Q.MAX_COMMAND + 1))


def test_command_survives_legacy_thread_build_and_merge(paths):
    o = _yesno(paths)
    raw = Q.read_raw(paths, o["id"])
    raw.pop("thread")
    raw["command"] = SIGN_IN
    Q.write_q(paths, raw)
    assert Q.read_q(paths, o["id"])["thread"][0]["command"] == SIGN_IN
    f = Q.add(paths, topic="site", title="Again", question="Run the new one?", kind="yesno", command="echo two")
    q = Q.merge(paths, f["id"], o["id"])
    assert q["command"] == "echo two" and q["thread"][-1]["command"] == "echo two"


def test_command_cli_flags(paths, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(Q, "notify", lambda text, **kwargs: True)
    base = ["--questions", str(paths.questions), "--postings", str(paths.postings),
            "--profile", str(paths.profile), "--applications", str(paths.applications)]
    assert Q.main(base + ["add", "--topic", "site", "--title", "Sign in", "--question", "Run it?", "--kind", "yesno",
                          "--command", SIGN_IN, "--quiet"]) == 0
    qid = capsys.readouterr().out.strip()
    assert Q.read_raw(paths, qid)["command"] == SIGN_IN
    f = tmp_path / "cmd.sh"
    f.write_text("gh auth login \\\n  --web\n")
    assert Q.main(base + ["reply", qid, "--text", "And this one?", "--command-file", str(f)]) == 0
    assert Q.read_raw(paths, qid)["command"] == "gh auth login \\\n  --web"
    with pytest.raises(SystemExit):
        Q.main(base + ["reply", qid, "--text", "x", "--command", "a", "--command-file", str(f)])

def test_merge_folds_follow_up_and_hides_it(paths, capsys):
    o = _yesno(paths)
    Q.comment(paths, o["id"], "You can delete that", by="phone")
    o_rec = Q.read_raw(paths, o["id"])
    o_rec.update(status="open", answer=None, applied=None)
    o_rec.pop("needs_reading")
    Q.write_q(paths, o_rec)                                  # as recorded under the old rule
    f = Q.add(paths, topic="other", title="Delete it?", question="Delete the repo? Permanent.", kind="yesno",
              context="Earlier question: Go ahead?\n\nYou said: \u201cYou can delete that\u201d\n\nOne commit.")
    q = Q.merge(paths, f["id"], o["id"])
    assert [(t["role"], t.get("text")) for t in q["thread"]] == [
        ("ask", "Go ahead?"), ("user", "You can delete that"), ("ask", "Delete the repo? Permanent.")]
    assert q["thread"][2]["context"] == "One commit." and q["question"] == "Delete the repo? Permanent."
    assert q["title"] == "Delete it?" and q["status"] == "open" and q["merged_from"] == [f["id"]]
    assert Q.read_raw(paths, f["id"])["merged_into"] == o["id"]                # file kept
    assert [x["id"] for x in Q.list_q(paths)] == [o["id"]]                      # hidden from lists
    assert Q.read_q(paths, f["id"])["id"] == o["id"]                            # its id resolves to the thread
    assert Q.answer(paths, f["id"], "Yes", by="phone")["id"] == o["id"]         # stale clients land in the thread
    assert Q.merge(paths, f["id"], o["id"])["id"] == o["id"]                    # idempotent
    with pytest.raises(Q.AskError):
        Q.merge(paths, o["id"], f["id"])
    base = ["--questions", str(paths.questions), "--postings", str(paths.postings),
            "--profile", str(paths.profile), "--applications", str(paths.applications)]
    capsys.readouterr()
    assert Q.main(base + ["list"]) == 0
    assert f["id"] not in capsys.readouterr().out


def test_api_hides_merged_and_returns_threads(paths, monkeypatch):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    import serve as S
    monkeypatch.setattr(S.Handler, "qpaths", paths, raising=False)
    monkeypatch.setattr(S.Handler, "token", "t0k", raising=False)
    monkeypatch.setattr(S.Handler, "deliver", False)
    monkeypatch.setattr(S.Handler, "log_message", lambda *a: None)
    o, f = _yesno(paths), Q.add(paths, topic="other", title="Again?", question="Again?", kind="yesno")
    Q.merge(paths, f["id"], o["id"])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def get(path):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("GET", path, headers={"Authorization": "Bearer t0k"})
        r = c.getresponse()
        return r.status, json.loads(r.read())

    try:
        st, qs = get("/questions")
        assert st == 200 and [q["id"] for q in qs] == [o["id"]] and len(qs[0]["thread"]) == 2
        st, one = get(f"/questions/{f['id']}")
        assert st == 200 and one["id"] == o["id"]
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request("POST", f"/questions/{o['id']}/reply", "{}", {"Authorization": "Bearer t0k"})
        assert c.getresponse().status == 404                                    # reply is CLI-only
    finally:
        srv.shutdown()


# ── delivery to the asking session ──









def test_legacy_comments_show_as_pending_for_the_lead(paths):
    q = _yesno(paths)
    rec = Q.read_q(paths, q["id"])
    for k in ("events", "asked_by_session"):
        rec.pop(k)
    rec["note"] = "You can delete that"
    rec["notes"] = [{"at": "2026-10-20T07:16:14Z", "by": "phone", "text": "You can delete that"}]
    Q.write_q(paths, rec)
    items = Q.pending(paths, Q.LEAD_SESSION)
    assert [Q.event_line(a, e) for a, e in items] == [
        f"[App] Go? — answer: none; comment: You can delete that (id {q['id']})"]
    assert Q.mark_delivered(paths, items) == 1 and Q.pending(paths) == []
    # a later answer keeps the converted history and adds one event
    q2 = Q.answer(paths, q["id"], "Yes", by="phone")
    assert [e["type"] for e in q2["events"]] == ["comment", "answer"] and q2["events"][0]["delivered_at"]



# ── Funnel: /api mount, rate limit, real client IP ──

def _serve(paths, monkeypatch, handler="Handler"):
    import threading
    from http.server import ThreadingHTTPServer
    import serve as S
    monkeypatch.setattr(S.Handler, "qpaths", paths, raising=False)
    monkeypatch.setattr(S.Handler, "token", "t0k", raising=False)
    monkeypatch.setattr(S.Handler, "deliver", False)
    monkeypatch.setattr(S.Handler, "log_message", lambda *a: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), getattr(S, handler))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _req(srv, method, path, headers=None):
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    c.request(method, path, "{}" if method == "POST" else None, headers or {})
    r = c.getresponse()
    return r.status, dict(r.getheaders()), r.read()


def test_api_prefix_on_loopback_listener(paths, monkeypatch):
    _yesno(paths)
    srv = _serve(paths, monkeypatch, "LinkPageHandler")
    auth = {"Authorization": "Bearer t0k"}
    try:
        assert _req(srv, "GET", "/api/questions")[0] == 401                      # token still required
        st, _, body = _req(srv, "GET", "/api/questions", auth)
        assert st == 200 and json.loads(body)[0]["title"] == "Go?"
        assert _req(srv, "GET", "/questions", auth)[0] == 404                     # bare paths stay closed here
        assert _req(srv, "GET", "/apix/questions", auth)[0] == 404
        assert _req(srv, "GET", "/api/files/../profile.yml", auth)[0] in (400, 404)
        assert _req(srv, "GET", "/app/")[0] == 200
    finally:
        srv.shutdown()


def test_failed_auth_limit_per_forwarded_ip(paths, monkeypatch):
    import serve as S
    monkeypatch.setattr(S, "AUTH_FAIL_LIMIT", 3)
    srv = _serve(paths, monkeypatch, "LinkPageHandler")
    a = {"X-Forwarded-For": "203.0.113.7"}
    b = {"X-Forwarded-For": "198.51.100.9, 10.0.0.1"}
    try:
        assert [_req(srv, "GET", "/api/health", {**a, "Authorization": "Bearer nope"})[0] for _ in range(3)] == [401] * 3
        st, hdrs, _ = _req(srv, "GET", "/api/questions", {**a, "Authorization": "Bearer t0k"})
        assert st == 429 and int(hdrs["Retry-After"]) > 0                        # even the right token, once blocked
        assert _req(srv, "GET", "/api/questions", {**b, "Authorization": "Bearer t0k"})[0] == 200   # other IP unaffected
        assert _req(srv, "GET", "/api/health", {**b, "Authorization": "Bearer \u00e9"})[0] == 401  # non-ASCII: 401, not 500
    finally:
        srv.shutdown()
    assert "203.0.113.7" in S.Handler._auth_fails and "198.51.100.9" in S.Handler._auth_fails


def test_forwarded_for_trusted_only_from_loopback():
    import serve as S

    class Fake(S.Handler):
        def __init__(self, peer, fwd):
            self.client_address = (peer, 1)
            self.headers = {"X-Forwarded-For": fwd}

    assert Fake("127.0.0.1", "203.0.113.7").client_ip() == "203.0.113.7"
    assert Fake("192.0.2.10", "203.0.113.7").client_ip() == "192.0.2.10"   # tailnet caller cannot spoof


def test_pairing_url_forms():
    import serve as S
    funnel = S.pairing_url("https://phone.example.org:8443/api", None, "tok")
    assert funnel == ("careerapp://pair?host=https%3A%2F%2Fphone.example.org%3A8443%2Fapi"
                      "&code=tok")
    assert S.pairing_url("192.0.2.10", 7490, "tok") == "careerapp://pair?host=192.0.2.10%3A7490&code=tok"


def test_posting_questions_carry_links(paths):
    """A question about a posting gets the posting's URL and its web-app link in its context (not for confirm)."""
    (paths.postings / "acme--intern--20261024.json").write_text(json.dumps({"id": "acme--intern--20261024", "url": "https://jobs.acme.test/1"}))
    q = Q.add(paths, topic="application", title="Apply to Acme?", question="Apply?", kind="yesno",
              posting_id="acme--intern--20261024", context="Why: fits.")
    assert q["context"].startswith("Why: fits.")
    assert "Posting: https://jobs.acme.test/1" in q["context"] and "/web/#/posting/acme--intern--20261024" in q["context"]
    c = Q.add(paths, topic="application", title="Use this answer?", question="Use it?", kind="confirm",
              posting_id="acme--intern--20261024", context="The proposed answer.")
    assert c["context"] == "The proposed answer."
