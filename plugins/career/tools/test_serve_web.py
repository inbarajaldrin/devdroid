# Tests for serve.py's web app routes: /web/ static files without a token, /web/api/ = the token-checked API on both
# listeners (tailnet Handler and the loopback LinkPageHandler behind `tailscale serve`), path traversal refused,
# and the app-opener page sending desktop browsers to /web/. Scratch data only, never the live records.
import http.client
import json
import shutil
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import questions as Q  # noqa: E402
import serve as S  # noqa: E402

TOKEN = "web-test-token-0123456789"
SAMPLE_ID = "exampleco--software-intern--20270115"


@pytest.fixture(autouse=True)
def no_real_sessions(tmp_path, monkeypatch):
    """Never resolve or post to a live Claude session from a test."""
    (tmp_path / "sessions").mkdir(exist_ok=True)
    monkeypatch.setattr(Q.Agents, "message", lambda *args: {"ok": True})
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_SOCKET", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_MESSAGING_TOKEN", raising=False)


@pytest.fixture
def paths(tmp_path):
    (tmp_path / "postings").mkdir()
    shutil.copy(HERE / "fixtures" / "posting_test.json", tmp_path / "postings" / f"{SAMPLE_ID}.json")
    (tmp_path / "profile.yml").write_text((HERE / "fixtures" / "profile_test.yml").read_text())
    (tmp_path / "applications").mkdir()
    return Q.Paths(questions=tmp_path / "questions", postings=tmp_path / "postings",
                   profile=tmp_path / "profile.yml", applications=tmp_path / "applications")


def _server(handler, paths, monkeypatch):
    for cls in (S.Handler, handler):
        monkeypatch.setattr(cls, "log_message", lambda *a: None)
    monkeypatch.setattr(S.Handler, "qpaths", paths, raising=False)
    monkeypatch.setattr(S.Handler, "token", TOKEN, raising=False)
    monkeypatch.setattr(S.Handler, "store", S.Store(paths.postings, paths.applications), raising=False)
    monkeypatch.setattr(S.Handler, "files_root", S.WORKSPACE_ROOT, raising=False)
    monkeypatch.setattr(S.Handler, "deliver", False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _req(srv, method, path, token=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    c.request(method, path, json.dumps(body) if body is not None else ("{}" if method == "POST" else None), headers)
    r = c.getresponse()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()


@pytest.fixture(params=["tailnet", "loopback"])
def srv(request, paths, monkeypatch):
    handler = S.Handler if request.param == "tailnet" else S.LinkPageHandler
    s = _server(handler, paths, monkeypatch)
    yield s
    s.shutdown()
    s.server_close()


# ── /web/ static files: no token, no data, locked-down headers ──

def test_web_page_serves_without_a_token(srv):
    st, hd, body = _req(srv, "GET", "/web/")
    assert st == 200 and hd["content-type"].startswith("text/html")
    assert b'src="app.js"' in body and TOKEN.encode() not in body
    assert "default-src 'none'" in hd["content-security-policy"] and "connect-src 'self'" in hd["content-security-policy"]
    assert hd["x-content-type-options"] == "nosniff" and hd["referrer-policy"] == "no-referrer"
    for path, ctype in (("/web/index.html", "text/html"), ("/web/app.js", "text/javascript"),
                        ("/web/app.css", "text/css"), ("/web/favicon.svg", "image/svg+xml"),
                        ("/web/favicon.ico", "image/"), ("/web/apple-touch-icon.png", "image/png"),
                        ("/web/icon-192.png", "image/png"), ("/web/site.webmanifest", "application/manifest+json")):
        st, hd, body = _req(srv, "GET", path)
        assert st == 200 and hd["content-type"].startswith(ctype), path
        assert TOKEN.encode() not in body
    st, hd, _ = _req(srv, "GET", "/web")
    assert st == 301 and hd["location"] == "/web/"
    st, _, body = _req(srv, "HEAD", "/web/app.js")
    assert st == 200 and body == b""


def test_web_api_needs_the_token(srv):
    for path in ("/web/api/health", "/web/api/postings", "/web/api/questions", f"/web/api/postings/{SAMPLE_ID}",
                 "/web/api/files/profile.yml", "/web/api/files/data/postings/SCHEMA.md", "/web/api", "/web/api/"):
        assert _req(srv, "GET", path)[0] == 401, path
        assert _req(srv, "GET", path, token="wrong")[0] == 401, path
    for path in ("/web/api/questions/x/answer", "/web/api/questions/x/dismiss",
                 f"/web/api/postings/{SAMPLE_ID}/ruling", f"/web/api/postings/{SAMPLE_ID}/approve-submit"):
        assert _req(srv, "POST", path, body={"by": "web"})[0] == 401, path
    st, hd, body = _req(srv, "GET", "/web/api/health", token=TOKEN)
    assert st == 200 and json.loads(body)["ok"] is True and hd["cache-control"] == "no-store"
    st, _, body = _req(srv, "GET", "/web/api/postings", token=TOKEN)
    assert st == 200 and [r["id"] for r in json.loads(body)] == [SAMPLE_ID]
    assert _req(srv, "GET", f"/web/api/postings/{SAMPLE_ID}", token=TOKEN)[0] == 200
    st, hd, _ = _req(srv, "GET", "/web/api/files/data/postings/SCHEMA.md", token=TOKEN)
    assert st == 403
    assert _req(srv, "GET", "/web/api/files/profile.yml", token=TOKEN)[0] == 403
    assert _req(srv, "GET", "/web/api/nothing-here", token=TOKEN)[0] == 404


def test_web_api_answers_a_question_as_web(srv, paths):
    q = Q.add(paths, topic="other", title="Go?", question="Go ahead?", kind="yesno")
    assert _req(srv, "POST", f"/web/api/questions/{q['id']}/answer", body={"answer": "Yes", "by": "web"})[0] == 401
    st, _, body = _req(srv, "POST", f"/web/api/questions/{q['id']}/answer", token=TOKEN,
                       body={"answer": "Yes", "note": "from the laptop", "by": "web"})
    got = json.loads(body)
    assert st == 200 and got["answer"] == "Yes" and got["answer_source"] == "web" and got["note"] == "from the laptop"
    # An answer from the web app is for the asking session, exactly like one from the phone: it stays pending.
    assert [e["by"] for _, e in Q.pending(paths)] == ["web"]


def test_traversal_and_odd_paths_are_refused(srv):
    for path in ("/web/../tools/serve.py", "/web/..%2ftools%2fserve.py", "/web/%2e%2e/tools/serve.py",
                 "/web/../profile.yml", "/web//etc/passwd", "/web/sub/app.js", "/web/.hidden.js", "/web/app.py",
                 "/web/app.js/x", "/web/app.js%00.css", "/web/..", "/web/apix", "/web/app.json"):
        st, _, body = _req(srv, "GET", path)
        assert st == 404, path
        assert b"def main" not in body and b"token" not in body.lower(), path
    assert _req(srv, "GET", "/webx/")[0] in (401, 404)            # not a web path: the API's 401, or loopback's 404
    for path in ("/web/api/files/../tools/serve.py", "/web/api/files/%2e%2e/tools/serve.py",
                 "/web/api/files/applications/../workspace/tools/serve.py", "/web/api/files/data/../../applications/x",
                 "/web/api/files/..%2ftools%2fserve.py"):
        st, _, body = _req(srv, "GET", path, token=TOKEN)
        assert st == 400, path
        assert b"def main" not in body, path
    for path in ("/web/api/../health", "/web/api/../../tools/serve.py"):
        assert _req(srv, "GET", path)[0] == 401, path              # still the API: no token, nothing
        assert _req(srv, "GET", path, token=TOKEN)[0] == 404, path


def test_symlink_out_of_the_web_root_is_refused(tmp_path, paths, monkeypatch):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>t</title>")
    secret = tmp_path / "secret.js"
    secret.write_text("SECRET")
    (web / "leak.js").symlink_to(secret)
    monkeypatch.setattr(S, "WEB_ROOT", web)
    s = _server(S.LinkPageHandler, paths, monkeypatch)
    try:
        assert _req(s, "GET", "/web/")[0] == 200
        st, _, body = _req(s, "GET", "/web/leak.js")
        assert st == 404 and b"SECRET" not in body
    finally:
        s.shutdown()
        s.server_close()


# ── the loopback listener keeps bare API paths off, and the tailnet one keeps them on ──

def test_loopback_serves_only_page_web_and_web_api(paths, monkeypatch):
    s = _server(S.LinkPageHandler, paths, monkeypatch)
    try:
        for path in ("/health", "/postings", "/questions", "/files/profile.yml", "/", "/index.html"):
            assert _req(s, "GET", path, token=TOKEN)[0] == 404, path
        assert _req(s, "POST", "/questions/x/answer", token=TOKEN)[0] == 404
        assert _req(s, "GET", "/app/")[0] == 200
        assert _req(s, "GET", "/web/")[0] == 200
        assert _req(s, "GET", "/web/api/health", token=TOKEN)[0] == 200
    finally:
        s.shutdown()
        s.server_close()


def test_tailnet_keeps_the_bare_api_for_the_phone(paths, monkeypatch):
    s = _server(S.Handler, paths, monkeypatch)
    try:
        assert _req(s, "GET", "/health")[0] == 401
        assert _req(s, "GET", "/health", token=TOKEN)[0] == 200
        assert _req(s, "GET", "/app/?q=20261019-x")[0] == 200
    finally:
        s.shutdown()
        s.server_close()


# ── the opener page: phone app on iOS, web app elsewhere; never a token ──

def test_opener_sends_desktops_to_the_web_app():
    html = S.LINK_PAGE.read_text()
    assert "careerapp://questions" in html                     # iPhone/iPad still open the app
    assert '"/web/#/questions"' in html                            # desktops go to the web app
    assert "iPhone|iPad|iPod" in html and "maxTouchPoints" in html  # iPadOS reports a Mac user agent
    script = html.split("<script>", 1)[1]
    assert "token" not in script.lower() and "token=" not in html


def test_web_app_never_puts_the_token_in_a_url():
    js = (S.WEB_ROOT / "app.js").read_text()
    assert "localStorage" in js and "Authorization" in js
    # "token=" appears only where a pasted pairing link is parsed or described, never where a URL is built.
    assert all("[?&]token=" in line or "placeholder" in line for line in js.splitlines() if "token=" in line)
    assert ".innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js


def test_no_em_dashes_in_web_ui_text():
    for name in ("index.html", "app.js", "app.css"):
        text = (S.WEB_ROOT / name).read_text()
        assert "—" not in text and "–" not in text, name


def test_page_links_the_icons_and_manifest():
    html = (S.WEB_ROOT / "index.html").read_text()
    for name in ("favicon.ico", "favicon.svg", "apple-touch-icon.png", "site.webmanifest"):
        assert f'href="{name}"' in html, name
        assert (S.WEB_ROOT / name).is_file(), name
    manifest = json.loads((S.WEB_ROOT / "site.webmanifest").read_text())
    for icon in manifest["icons"]:                       # flat names: serve.py serves no subfolders under /web/
        assert "/" not in icon["src"] and (S.WEB_ROOT / icon["src"]).is_file(), icon["src"]
    assert 'class="blip"' in html and "style=" not in html  # the inline mark is coloured from app.css (CSP: no inline styles)


# ── career open: a one-time code in the link's fragment is traded for the token ──

def test_open_code_is_traded_once_for_the_token(paths, monkeypatch, tmp_path):
    monkeypatch.setattr(S, "OPEN_CODE_PATH", tmp_path / "web_open_code.json")
    S._write_private(S.OPEN_CODE_PATH, {"code": "one-time-code", "expires": S.time.time() + 60})
    s = _server(S.Handler, paths, monkeypatch)
    try:
        status, _, body = _req(s, "POST", "/web/open", body={"code": "one-time-code"})
        assert status == 200 and json.loads(body)["token"] == TOKEN
        status, _, _ = _req(s, "POST", "/web/open", body={"code": "one-time-code"})
        assert status == 401
    finally:
        s.shutdown(); s.server_close()


def test_open_code_refuses_a_wrong_or_expired_code_and_any_proxy(paths, monkeypatch, tmp_path):
    monkeypatch.setattr(S, "OPEN_CODE_PATH", tmp_path / "web_open_code.json")
    s = _server(S.Handler, paths, monkeypatch)
    try:
        S._write_private(S.OPEN_CODE_PATH, {"code": "right", "expires": S.time.time() + 60})
        assert _req(s, "POST", "/web/open", body={"code": "wrong"})[0] == 401
        assert not S.OPEN_CODE_PATH.exists()  # a wrong guess burns the code
        S._write_private(S.OPEN_CODE_PATH, {"code": "old", "expires": S.time.time() - 1})
        assert _req(s, "POST", "/web/open", body={"code": "old"})[0] == 401
        S._write_private(S.OPEN_CODE_PATH, {"code": "proxied", "expires": S.time.time() + 60})
        c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=5)
        c.request("POST", "/web/open", json.dumps({"code": "proxied"}),
                  {"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.9"})
        assert c.getresponse().status == 404
    finally:
        s.shutdown(); s.server_close()


def test_open_web_puts_the_code_only_in_the_fragment(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "OPEN_CODE_PATH", tmp_path / "web_open_code.json")
    monkeypatch.setattr(S, "SERVER_PATH", tmp_path / "server.json")
    S._write_private(S.SERVER_PATH, {"host": "127.0.0.1", "port": 7490})
    opened = []
    monkeypatch.setattr(S.subprocess, "run", lambda cmd, **kw: opened.append(cmd[-1]))
    assert S.open_web() == "http://127.0.0.1:7490/web/"
    code = json.loads(S.OPEN_CODE_PATH.read_text())["code"]
    assert opened == [f"http://127.0.0.1:7490/web/#/open/{code}"] and TOKEN not in opened[0]
