#!/usr/bin/env python3
"""Local career API: python tools/serve.py --bind 127.0.0.1 --link-bind off.

CAREER_HOME selects the private workspace. Static app code ships with the plugin.
The API, CLI and clients share workflow.py; all API routes require the bearer token.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
import urllib.parse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import questions as ask
from runtime import HOME, CODE, Refused, read_json
from workflow import Workflow

WORKSPACE_ROOT = HOME                       # workspace/
DEFAULT_POSTINGS = WORKSPACE_ROOT / "data" / "postings"
CAREER_ROOT = WORKSPACE_ROOT                # Career/ — materials.* paths are relative to this
DEFAULT_APPLICATIONS = WORKSPACE_ROOT / "data" / "applications"
TOKEN_PATH = Path(os.environ.get("CAREER_TOKEN_PATH", WORKSPACE_ROOT / ".private" / "api_token"))
EXAMPLES = CODE / "skills/jobs/examples"
LINK_PAGE = (CODE / "app" if (CODE / "app").is_dir() else EXAMPLES / "app") / "link/index.html"
WEB_ROOT = CODE / "web" if (CODE / "web").is_dir() else EXAMPLES / "web"
WEB_ASSET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,80}\.(html|css|js|svg|png|ico|webmanifest)$")
WEB_API_PREFIX = "/web/api"
API_PREFIX = "/api"                              # the iOS app's API mount (Funnel :8443/api -> loopback /api)
# Public base for the phone, no VPN needed: Tailscale Funnel on :8443 proxies /app, /web and /api to the loopback
# listener. Turn it off with `tailscale funnel --https=8443 off` (see serve.md).
FUNNEL_BASE = os.environ.get("CAREER_FUNNEL_BASE", "")
AUTH_FAIL_LIMIT, AUTH_FAIL_WINDOW = 20, 600      # 401s per client IP per 10 minutes, then 429
# The web page may talk only to its own origin; blob: covers file previews fetched with the token.
WEB_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
           "media-src 'self' blob:; frame-src 'self' blob:; object-src blob:; connect-src 'self'; "
           "manifest-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
DEFAULT_PORT = 7490
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,200}$")
TAILSCALE_BINS = ["tailscale", "/Applications/Tailscale.app/Contents/MacOS/Tailscale"]

# Inbox order: things the phone still has to rule on come first.
STATUS_ORDER = {
    "new": 0, "enriched": 0,
    "approved": 1, "tailored": 1, "ready": 1,
    "skipped": 2,
    "applied": 3, "interview": 3, "offer": 3, "rejected": 3, "closed": 3,
}

_write_lock = threading.RLock()


# ---------- helpers ----------

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def tailscale_ipv4() -> str | None:
    for b in TAILSCALE_BINS:
        exe = shutil.which(b) if not b.startswith("/") else (b if os.path.exists(b) else None)
        if not exe:
            continue
        try:
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        ip = out.stdout.strip().splitlines()[0].strip() if out.stdout.strip() else ""
        if ip:
            return ip
    return None


def load_or_create_token() -> str:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if TOKEN_PATH.exists():
        tok = TOKEN_PATH.read_text().strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(32)
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tok + "\n")
    os.chmod(TOKEN_PATH, 0o600)
    return tok


# Opening the web app on this machine: a one-time code in the link's fragment is traded for the token, so the
# person never pastes it and the token never sits in a URL or browser history.
OPEN_CODE_PATH = TOKEN_PATH.parent / "web_open_code.json"
SERVER_PATH = TOKEN_PATH.parent / "server.json"
OPEN_CODE_TTL = 60
PHONE_CODE_PATH = TOKEN_PATH.parent / "phone_pair_code.json"


def _write_private(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)


def take_open_code(given: str) -> bool:
    """True once for the current unexpired code; any attempt consumes it."""
    try:
        saved = json.loads(OPEN_CODE_PATH.read_text())
        OPEN_CODE_PATH.unlink()
    except (OSError, ValueError):
        return False
    return (bool(given) and time.time() < saved.get("expires", 0)
            and secrets.compare_digest(str(saved.get("code", "")).encode(), given.encode()))


def open_web() -> str:
    """Open the running server's web app in the default browser, already paired. Returns the page without the code."""
    try:
        server = json.loads(SERVER_PATH.read_text())
    except (OSError, ValueError):
        raise Refused("SERVER_NOT_RUNNING", "Start the server first: python tools/serve.py")
    code = secrets.token_urlsafe(18)
    _write_private(OPEN_CODE_PATH, {"code": code, "expires": time.time() + OPEN_CODE_TTL})
    page = f"http://{server['host']}:{server['port']}/web/"
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.run([opener, f"{page}#/open/{code}"], check=True, timeout=15)
    return page


def pairing_url(host: str, port: int | None, code: str) -> str:
    """`host` is "100.x:7490"-style (tailnet) or a full base URL such as FUNNEL_BASE + API_PREFIX."""
    q = urllib.parse.urlencode({"host": f"{host}:{port}" if port else host, "code": code})
    return f"careerapp://pair?{q}"


def print_pairing(host: str, port: int | None, token: str) -> None:
    code = secrets.token_urlsafe(24)
    _write_private(PHONE_CODE_PATH, {"code":code,"expires":time.time()+300})
    url = pairing_url(host, port, code)
    print("One-time phone pairing URL (expires in five minutes):")
    print("  " + url)
    try:
        import qrcode  # optional: `uv run --with qrcode python3 serve.py --pair`
    except ImportError:
        print("(install-free QR: run `uv run --with qrcode python3 serve.py --pair`)")
        return
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


class Store:
    """All reads and writes of workspace/data/postings/*.json go through here."""

    def __init__(self, postings_dir: Path, applications_dir: Path = DEFAULT_APPLICATIONS):
        self.dir = postings_dir
        self.applications = applications_dir

    def path_for(self, pid: str) -> Path:
        if not SLUG_RE.match(pid):
            raise KeyError(pid)
        p = self.dir / f"{pid}.json"
        if not p.is_file():
            raise KeyError(pid)
        return p

    def read(self, pid: str) -> dict:
        with open(self.path_for(pid), encoding="utf-8") as f:
            return self.with_delivery(json.load(f))

    def with_delivery(self, rec):
        pending = [event for event in self.workflow().deliveries()["events"]
                   if event.get("posting_id") == rec["id"] and not (event.get("receipt") or {}).get("ok")]
        rec["coordination_pending"] = len(pending)
        from presentation import posting
        return posting(self.workflow(), rec)

    def list(self, status: str | None = None) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                with open(p, encoding="utf-8") as f:
                    rec = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                print(f"[serve] skipping {p.name}: {e}", file=sys.stderr)
                continue
            if not isinstance(rec, dict) or "id" not in rec:
                continue
            # Reject malformed records before decoding the client array.
            missing = [k for k in ("id", "company", "title", "status") if not isinstance(rec.get(k), str)]
            if missing:
                print(f"[serve] skipping {p.name}: missing {', '.join(missing)}", file=sys.stderr)
                continue
            if status and rec.get("status") != status:
                continue
            out.append(self.with_delivery(rec))
        out.sort(key=lambda r: (STATUS_ORDER.get(r.get("status"), 9),
                                -(_date_key(r.get("first_seen"))),
                                r.get("company", ""), r.get("title", "")))
        return out

    def write(self, pid: str, rec: dict) -> None:
        p = self.path_for(pid)
        tmp = p.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, p)

    # -- mutations (read-modify-write; only ruling/status/history change) --

    def workflow(self):
        return getattr(self, "_workflow", None) or Workflow(self.dir.parent.parent)

    def rule(self, pid, decision, reason, by):
        self.workflow().dispatch("pick", pid, decision=decision, reason=reason, by=by)
        return self.read(pid)

    # -- the Apply stage's form_fill.json and the submit gate --

    def career_path(self, rel) -> Path | None:
        """A materials.* path (relative to the workspace/) → a file under workspace/data/applications/, else None."""
        if not rel or not isinstance(rel, str):
            return None
        parts = rel.strip().split("/")
        if parts[0] != "applications" or ".." in parts or "\\" in rel or "\0" in rel:
            return None
        target = (self.applications / "/".join(parts[1:])).resolve()
        try:
            target.relative_to(self.applications.resolve())
        except ValueError:
            return None
        return target

    def fill_path(self, rec: dict) -> Path | None:
        m = rec.get("materials") or {}
        if m.get("form_fill_json"):
            return self.career_path(m["form_fill_json"])
        if m.get("application_dir"):
            d = self.career_path(str(m["application_dir"]).rstrip("/"))
            return d / "form_fill.json" if d else None
        return None

    def fill(self, rec: dict) -> dict | None:
        p = self.fill_path(rec)
        if not p or not p.is_file():
            return None
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None

    def gate(self, pid):
        return self.workflow().gate(pid)

    def approve_submit(self, pid, by, note, fill_digest):
        self.workflow().dispatch("approve-submit", pid, by=by, note=note, fill_digest=fill_digest)
        return self.read(pid)


def _date_key(s) -> int:
    try:
        return int(str(s).replace("-", "")[:8])
    except ValueError:
        return 0


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "Career/1"
    store: Store
    token: str
    files_root: Path
    qpaths: ask.Paths
    deliver = True         # deliver stored answers/comments through /ask; tests may turn it off

    # Failed-auth timestamps per client IP (sliding window); shared by every listener in the process.
    _auth_fails: dict = {}
    _auth_lock = threading.Lock()

    def log_message(self, fmt, *args):   # one line per request, no token leakage
        line = re.sub(r"/app/install/[^/\s?]+", "/app/install/[redacted]", fmt % args)
        line = re.sub(r"\?[^\s]*", "?[redacted]", line)
        sys.stderr.write("%s - %s\n" % (self.client_ip(), line))

    def client_ip(self) -> str:
        """The caller's address. Behind Tailscale serve/Funnel every request reaches the loopback listener from
        127.0.0.1, so there (and only there) the proxy's X-Forwarded-For names the real client."""
        peer = self.client_address[0] if self.client_address else "?"
        if peer in ("127.0.0.1", "::1"):
            fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if fwd:
                return fwd[:64]
        return peer

    # -- plumbing --
    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, status: int, msg: str) -> None:
        self._json(status, {"error": msg})

    def _auth_blocked(self, ip: str) -> int:
        """Seconds until this IP may try again (0 = not blocked); prunes the window as a side effect."""
        now = time.monotonic()
        with self._auth_lock:
            q = self._auth_fails.get(ip)
            if not q:
                return 0
            while q and now - q[0] > AUTH_FAIL_WINDOW:
                q.popleft()
            if not q:
                self._auth_fails.pop(ip, None)
                return 0
            return int(AUTH_FAIL_WINDOW - (now - q[0])) + 1 if len(q) >= AUTH_FAIL_LIMIT else 0

    def _note_auth_fail(self, ip: str) -> None:
        with self._auth_lock:
            self._auth_fails.setdefault(ip, deque()).append(time.monotonic())

    def _authed(self) -> bool:
        ip = self.client_ip()
        wait = self._auth_blocked(ip)
        if wait:
            # Checked BEFORE the token, so a blocked caller learns nothing, not even whether a guess was right.
            sys.stderr.write(f"[auth] 429 {ip} {self.command} {urllib.parse.urlsplit(self.path).path}\n")
            body = b'{"error": "too many failed attempts"}'
            self.send_response(429)
            self.send_header("Retry-After", str(wait))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False
        hdr = self.headers.get("Authorization", "")
        given = hdr[7:].strip() if hdr.startswith("Bearer ") else ""
        # Constant-time, on bytes: compare_digest on str raises for non-ASCII input.
        ok = bool(given) and secrets.compare_digest(given.encode("utf-8"), self.token.encode("utf-8"))
        if not ok:
            self._note_auth_fail(ip)
            sys.stderr.write(f"[auth] 401 {ip} {self.command} {urllib.parse.urlsplit(self.path).path}\n")
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", 'Bearer realm="career"')
            body = b'{"error": "unauthorized"}'
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        return ok

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("body too large")
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    # The app-opener page (workspace/app/link/index.html) is the ONE unauthenticated route: GET/HEAD only, these
    # exact paths. Tailscale serves it tailnet-only at https://<mac>.ts.net/app/ (`tailscale serve --set-path /app`
    # proxies here and keeps the /app prefix). It is static, carries no data, and never sees a token.
    PUBLIC_PAGE_PATHS = ("/app", "/app/", "/app/index.html")

    def _serve_link_page(self) -> None:
        try:
            body = LINK_PAGE.read_bytes()
        except OSError as e:
            return self._error(500, f"link page: {e}")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    # -- the web app (workspace/web/): static files without a token, its API under /web/api/ with one --

    def _serve_web_asset(self, path: str) -> None:
        """GET /web/ or /web/<asset>: one flat directory, names checked against WEB_ASSET_RE, nothing else."""
        if path == "/web":
            self.send_response(301)
            self.send_header("Location", "/web/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        name = path[len("/web/"):] or "index.html"
        if not WEB_ASSET_RE.match(name):
            return self._error(404, "not found")
        root = WEB_ROOT.resolve()
        target = (root / name).resolve()
        if target.parent != root or not target.is_file():
            return self._error(404, "not found")
        body = target.read_bytes()
        ctype = {"html": "text/html; charset=utf-8", "css": "text/css; charset=utf-8",
                 "js": "text/javascript; charset=utf-8", "webmanifest": "application/manifest+json"}.get(
            name.rsplit(".", 1)[1]) or mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Security-Policy", WEB_CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    @staticmethod
    def _web_route(raw_path: str) -> str | None:
        """'api' for /web/api[/...], 'asset' for any other /web path, None outside /web."""
        path = urllib.parse.urlsplit(raw_path).path
        if path == WEB_API_PREFIX or path.startswith(WEB_API_PREFIX + "/"):
            return "api"
        if path == "/web" or path.startswith("/web/"):
            return "asset"
        return None

    @staticmethod
    def _is_api_prefixed(raw_path: str) -> bool:
        path = urllib.parse.urlsplit(raw_path).path
        return path == API_PREFIX or path.startswith(API_PREFIX + "/")

    def _strip_api(self) -> None:
        """/api/postings?x=1 -> /postings?x=1 (the iOS app's mount; same token check as everywhere)."""
        self.path = self.path[len(API_PREFIX):] or "/"
        if not self.path.startswith("/"):
            self.path = "/" + self.path

    def _strip_web_api(self) -> None:
        """/web/api/postings?x=1 -> /postings?x=1, so the API router below sees the bare path."""
        self.path = self.path[len(WEB_API_PREFIX):] or "/"
        if not self.path.startswith("/"):
            self.path = "/" + self.path

    # -- routing --
    def _serve_ota(self, path):
        """An install token opens exactly three build resources, never the API."""
        import ota
        from runtime import read_json
        if self._auth_blocked(self.client_ip()):
            return self._error(429, "too many failed attempts")
        root = self.files_root
        token_path = root / ".private/ota_token"
        expected = token_path.read_text().strip() if token_path.is_file() else ""
        parts = path[len(ota.PREFIX) + 1:].split("/")
        supplied = parts[0] if parts else ""
        resource = parts[1] if len(parts) == 2 else None
        if not expected or not secrets.compare_digest(expected.encode(), supplied.encode()) or resource not in ("", "manifest.plist", ota.IPA_NAME):
            self._note_auth_fail(self.client_ip())
            return self._error(404, "not found")
        directory = root / "data/ota"
        inf = ota.info(directory)
        base = read_json(root / "settings.json", {}).get("ota_base") or ota.FUNNEL_BASE
        try:
            if resource == "":
                body, mime = ota.page(inf, expected, base), "text/html; charset=utf-8"
            elif resource == "manifest.plist" and inf:
                body, mime = ota.manifest(inf, expected, base), "application/xml"
            elif resource == ota.IPA_NAME and inf:
                body, mime = (directory / ota.IPA_NAME).read_bytes(), "application/octet-stream"
            else:
                return self._error(404, "not found")
        except Refused as error:
            return self._error(503, str(error))
        self.send_response(200)
        for key, value in (("Content-Type", mime), ("Content-Length", str(len(body))),
                           ("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer"),
                           ("X-Robots-Tag", "noindex, nofollow")):
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/app/install/"):
            return self._serve_ota(path)
        if urllib.parse.urlsplit(self.path).path in self.PUBLIC_PAGE_PATHS:
            return self._serve_link_page()
        web = self._web_route(self.path)
        if web == "asset":
            return self._serve_web_asset(urllib.parse.urlsplit(self.path).path)
        if web == "api":
            self._strip_web_api()
        elif self._is_api_prefixed(self.path):
            self._strip_api()
        return self._api_get()

    def _api_get(self):
        if not self._authed():
            return
        u = urllib.parse.urlsplit(self.path)
        parts = [p for p in u.path.split("/") if p]
        qs = urllib.parse.parse_qs(u.query)
        try:
            if parts == ["health"]:
                return self._json(200, {"ok": True, "postings": len(self.store.list()), "version": "1",
                                        "questions_open": len(ask.list_q(self.qpaths, "open"))})
            if parts == ["stamp"]:
                root = self.store.dir.parent
                workspace = root.parent
                files = [*self.store.dir.glob('*.json'), *self.qpaths.questions.glob('*.json'),
                         *self.store.applications.rglob('*.json'), root / 'chat.json', root / 'answers.json',
                         root / 'batch.json', root / 'notifications.json', root / 'ota/notification.json',
                         root / 'ota/info.json', root / 'ota/Career.ipa', workspace / '.private/ota_token',
                         workspace / 'profile.yml', workspace / 'settings.json', workspace / 'WRITING.md']
                def fingerprint(paths):
                    values = []
                    for path in sorted(paths):
                        if path.is_file():
                            st = path.stat()
                            values.append(f'{path.name}:{st.st_mtime_ns}:{st.st_size}')
                    return hashlib.sha256('|'.join(values).encode()).hexdigest()
                out = {'stamp':fingerprint(files), 'web':fingerprint(WEB_ROOT.glob('*'))}
                import ota
                inf = ota.info(root / 'ota')
                token_path = workspace / '.private/ota_token'
                base = read_json(workspace / 'settings.json', {}).get('ota_base') or ota.FUNNEL_BASE
                if inf and token_path.is_file():
                    install_token = token_path.read_text().strip()
                    if install_token and str(inf.get('build', '')).isdigit():
                        try:
                            out.update(build=str(inf['build']), install=ota.install_link(install_token, base))
                        except Refused:
                            pass  # No install link until a reachable HTTPS origin is configured.
                return self._json(200, out)
            if parts == ["batch"]:
                return self._json(200, self.store.workflow().batch())
            if parts == ["notifications"]:
                from notifications import status
                return self._json(200, status(self.files_root))
            if parts == ["settings"]:
                from models import roles
                settings = roles(read_json(self.files_root / "settings.json",{}))
                return self._json(200,{key:settings.get(key) for key in ('drafter','reviewer')})
            if parts == ["models"]:
                from models import catalog
                return self._json(200,catalog())
            if parts == ["answers"]:
                return self._json(200, self.store.workflow().answers())
            if parts == ["questions"]:
                return self._json(200, ask.list_q(self.qpaths, (qs.get("status") or [None])[0]))
            if len(parts) == 2 and parts[0] == "questions":
                return self._json(200, ask.read_q(self.qpaths, parts[1]))
            if parts == ["events"]:
                return self._json(200, self.store.workflow().deliveries())
            if parts in (["chat"], ["chat", "page"]):
                try:
                    since = int((qs.get('since') or ['0'])[0])
                except ValueError:
                    return self._error(400, 'since must be a message number')
                messages = self.store.workflow().messages()
                rows = [message for message in messages if message['n'] > since]
                return self._json(200, dict(messages=rows, last=max((m['n'] for m in messages), default=0))
                                  if len(parts) == 2 else rows)
            if len(parts) == 3 and parts[0] == "postings" and parts[2] == "review":
                self.store.read(parts[1])
                from presentation import review
                return self._json(200, review(self.store.workflow(), parts[1]))
            if len(parts) == 3 and parts[0] == "postings" and parts[2] == "preview":
                return self._json(200, self.store.workflow().preview(parts[1]))
            if parts == ["postings"]:
                return self._json(200, self.store.list(status=(qs.get("status") or [None])[0]))
            if len(parts) == 2 and parts[0] == "postings":
                return self._json(200, self.store.read(parts[1]))
            if len(parts) == 3 and parts[0] == "postings" and parts[2] == "fill":
                fill = self.store.fill(self.store.read(parts[1]))
                if fill is None:
                    return self._error(404, "no form_fill.json for this posting (apply has not run)")
                return self._json(200, fill)
            if len(parts) == 3 and parts[0] == "postings" and parts[2] == "approve-submit":
                missing = self.store.gate(parts[1])
                return self._json(200, {"allowed": not missing, "missing": missing})
            if parts and parts[0] == "files" and len(parts) > 1:
                return self._serve_file(u.path[len("/files/"):])
        except ask.AskError as e:
            return self._error(e.status, str(e))
        except Refused as e:
            return self._json(409, {"error": str(e), "code": e.code})
        except KeyError:
            return self._error(404, "no such posting")
        except (OSError, json.JSONDecodeError) as e:
            return self._error(500, f"unreadable form_fill.json: {e}")
        return self._error(404, "not found")

    def do_HEAD(self):
        self.do_GET()

    def _web_open(self) -> None:
        """POST /web/open {code}: only a browser on this machine, never through a proxy, may trade the code."""
        ip = self.client_ip()
        peer = self.client_address[0] if self.client_address else ""
        if self.headers.get("X-Forwarded-For") or peer not in ("127.0.0.1", "::1", self.server.server_address[0]):
            return self._error(404, "not found")
        if self._auth_blocked(ip):
            return self._error(429, "too many failed attempts")
        try:
            code = str(self._read_body().get("code") or "")
        except (ValueError, UnicodeDecodeError) as error:
            return self._error(400, str(error))
        if not take_open_code(code):
            self._note_auth_fail(ip)
            return self._error(401, "this link has expired; open the app again")
        return self._json(200, {"token": self.token})

    def do_POST(self):
        if urllib.parse.urlsplit(self.path).path == "/web/open":
            return self._web_open()
        if self._web_route(self.path) == "api":
            self._strip_web_api()
        elif self._is_api_prefixed(self.path):
            self._strip_api()
        return self._api_post()

    def _api_post(self):
        parts = [p for p in urllib.parse.urlsplit(self.path).path.split("/") if p]
        if parts == ["pair"]:
            if self._auth_blocked(self.client_ip()):
                return self._error(429,"too many failed attempts")
            try:
                given = self._read_body().get('code')
                path = self.files_root / '.private/phone_pair_code.json'
                saved = json.loads(path.read_text())
                path.unlink()
                valid = isinstance(given,str) and bool(given) and time.time()<saved.get('expires',0) and secrets.compare_digest(str(saved.get('code','')).encode(),given.encode())
            except (OSError, ValueError):
                valid = False
            if not valid:
                self._note_auth_fail(self.client_ip())
                return self._error(401,'Pairing link expired; ask your agent for another code.')
            return self._json(200,{'token':self.token})
        if not self._authed():
            return
        parts = [p for p in urllib.parse.urlsplit(self.path).path.split("/") if p]
        if len(parts) == 5 and parts[0] == 'postings' and parts[2] == 'review' and parts[4] in ('approve','edit','comment'):
            try:
                from presentation import box_key, box_seen, review
                body = self._read_body()
                workflow = self.store.workflow()
                box = next((b for b in workflow.review(parts[1]).get('boxes', []) if box_key(b) == parts[3]), None)
                if box is None:return self._error(404, 'No such review field')
                by = body.get('by', 'phone')
                if parts[4] == 'comment':
                    workflow.dispatch('chat', text=body.get('text'), by=by, posting_id=parts[1], field_name=box['name'])
                else:
                    if parts[4] == 'approve' and body.get('seen') != box_seen(box):
                        raise Refused('FIELD_CHANGED', 'This answer changed. Read it again before approving.')
                    workflow.dispatch('box', parts[1], name=box['name'], decision='approve' if parts[4] == 'approve' else 'edit',
                                      value=body.get('text'), by=by, seen=body.get('seen') if parts[4] == 'approve' else None)
                return self._json(200, review(workflow, parts[1]))
            except Refused as error:
                return self._json(409, dict(error=str(error), code=error.code))
            except KeyError:
                return self._error(404, 'No such posting')
            except (ValueError, UnicodeDecodeError) as error:
                return self._error(400, str(error))
        if len(parts) == 3 and parts[0] == 'postings' and parts[2] == 'applied':
            try:
                body = self._read_body()
                from runtime import PERSON_BY
                if body.get('by','phone') not in PERSON_BY:
                    raise Refused('PERSON_APPROVAL','Confirm this outcome in the app.')
                self.store.workflow().dispatch('confirm', parts[1], by=body.get('by','phone'))
                return self._json(200, self.store.read(parts[1]))
            except Refused as error:
                return self._json(409, dict(error=str(error), code=error.code))
            except KeyError:
                return self._error(404, 'No such posting')
            except (ValueError, UnicodeDecodeError) as error:
                return self._error(400, str(error))
        if parts == ["settings"]:
            try:
                body = self._read_body()
                role = body.get('role')
                if role not in ('drafter','reviewer'):
                    return self._error(400,'Choose drafter or reviewer')
                self.store.workflow().dispatch('save',target={'setting':role},value=json.dumps(body.get('choice')))
                return self._json(200,{'saved':role})
            except ask.AskError as error:
                return self._error(error.status,str(error))
            except Refused as error:
                return self._json(409,{'code':error.code,'error':str(error)})
            except (ValueError,UnicodeDecodeError) as error:
                return self._error(400,str(error))
        if parts == ["notifications"]:
            try:
                body = self._read_body()
                if not isinstance(body.get('enabled'),bool):
                    return self._error(400, 'enabled must be true or false')
                self.store.workflow().dispatch('save',target={'setting':'notifications'},
                                               value='Enabled' if body['enabled'] else 'Disabled')
                from notifications import status
                return self._json(200,status(self.files_root))
            except Refused as error:
                return self._json(409,{'code':error.code,'error':str(error)})
            except (ValueError,UnicodeDecodeError) as error:
                return self._error(400,str(error))
        if len(parts) == 2 and parts[0] == "answers":
            try:
                body = self._read_body()
                result = self.store.workflow().change_answer(parts[1], body.get("value"), by=body.get("by", "web"))
                return self._json(200, result)
            except Refused as error:
                return self._json(409, {"code":error.code, "error":str(error)})
            except (ValueError, UnicodeDecodeError) as error:
                return self._error(400, str(error))
        if parts == ["chat"] or (len(parts) == 3 and parts[0] == "postings" and parts[2] == "review"):
            try:
                body = self._read_body()
                if parts == ["chat"]:
                    # App callers write user messages. Agent replies use the CLI.
                    result = self.store.workflow().dispatch("chat", text=body.get("text"), by=body.get("by", "web"),
                                                            posting_id=body.get("posting_id"), field_name=body.get("field_name"))
                else:
                    result = self.store.workflow().dispatch("box", parts[1], name=body.get("name"),
                        decision=body.get("decision"), value=body.get("value"), by=body.get("by", "web"))
                return self._json(200, result)
            except Refused as error:
                return self._json(409, {"error": str(error), "code": error.code})
            except (ValueError, UnicodeDecodeError) as error:
                return self._error(400, str(error))
        if len(parts) == 3 and parts[0] == "questions" and parts[2] in ("answer", "dismiss"):
            return self._question_post(parts[1], parts[2])
        if len(parts) != 3 or parts[0] != "postings" or parts[2] not in ("ruling", "approve-submit", "resolve-uncertain", "confirm"):
            return self._error(404, "not found")
        pid, action = parts[1], parts[2]
        try:
            body = self._read_body()
        except (ValueError, UnicodeDecodeError) as e:
            return self._error(400, f"bad body: {e}")
        by = str(body.get("by") or "phone")[:40]
        try:
            if action == "ruling":
                decision = body.get("decision")
                if decision not in ("approved", "skipped"):
                    return self._error(400, "decision must be 'approved' or 'skipped'")
                reason = body.get("reason")
                reason = str(reason).strip()[:500] if reason not in (None, "") else None
                if decision == "skipped" and not reason:
                    return self._error(400, "skipping needs a reason")
                rec = self.store.rule(pid, decision, reason, by)
            elif action == "approve-submit":
                note = body.get("note")
                note = str(note).strip()[:500] if note not in (None, "") else None
                rec = self.store.approve_submit(pid, by, note, body.get("fill_digest"))
            elif action == "confirm":
                from runtime import PERSON_BY
                if by not in PERSON_BY:
                    raise Refused('PERSON_APPROVAL','Confirm this outcome in the app.')
                rec = self.store.workflow().dispatch('confirm',pid,by=by)
            else:
                rec = self.store.workflow().dispatch("resolve-uncertain", pid, by=by, reason=str(body.get("reason") or ""))
        except Refused as e:
            return self._json(409, {"error": str(e), "code": e.code})
        except KeyError:
            return self._error(404, "no such posting")
        except (OSError, json.JSONDecodeError) as e:
            return self._error(500, f"unreadable form_fill.json: {e}")
        return self._json(200, rec)

    def _question_post(self, qid: str, action: str):
        try:
            body = self._read_body()
        except (ValueError, UnicodeDecodeError) as e:
            return self._error(400, f"bad body: {e}")
        by = str(body.get("by") or "phone")[:40]
        try:
            with _write_lock:      # ask.py also takes its own file lock, shared with the CLI
                note = ask.clean_note(body.get("note")) if action == "answer" else None
                has_answer = str(body.get("answer") if body.get("answer") is not None else "").strip() != ""
                if action == "answer" and not has_answer and note is None:
                    return self._error(400, "empty body: send an answer, a note, or both")
                if action == "answer" and has_answer:
                    q = ask.answer(self.qpaths, qid, body.get("answer"), by=by, note=note)
                elif action == "answer":
                    q = ask.comment(self.qpaths, qid, note, by=by)
                else:
                    q = ask.dismiss(self.qpaths, qid, by=by)
        except ask.AskError as e:
            return self._error(e.status, str(e))
        except (OSError, json.JSONDecodeError) as e:
            return self._error(500, f"question store: {e}")
        if action == "answer" and self.deliver:
            # The event is already on the record; delivery to the asking session is best-effort and off the request
            # thread. Anything it cannot deliver stays listed by `ask.py pending`. Never WhatsApp.
            threading.Thread(target=self._deliver, args=(qid,), daemon=True).start()
        return self._json(200, q)

    def _deliver(self, qid: str) -> None:
        try:
            ask.deliver_new(self.qpaths, qid)      # takes ask's file lock only around the record writes
        except (ask.AskError, OSError, ValueError) as e:
            sys.stderr.write(f"[serve] delivery for {qid} failed: {e}\n")

    def _serve_file(self, rel: str):
        rel = urllib.parse.unquote(rel)
        if not rel or rel.startswith("/") or ".." in rel.split("/") or "\\" in rel or "\0" in rel:
            return self._error(400, "bad path")
        # Two roots: `applications/...` (materials.* paths, relative to the workspace/) → workspace/data/applications/;
        # anything else → workspace/ (logos, images).
        if rel.split("/")[0] == "applications":
            root, sub = self.store.applications.resolve(), rel.split("/", 1)[1] if "/" in rel else ""
        else:
            root, sub = self.files_root.resolve(), rel
        if not sub:
            return self._error(400, "bad path")
        if root == self.files_root.resolve() and not sub.startswith("data/postings/media/"):
            return self._error(403, "only posting media and application artifacts may be served")
        target = (root / sub).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return self._error(400, "bad path")
        if not target.is_file():
            return self._error(404, "no such file")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        size = target.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(target, "rb") as f:
            shutil.copyfileobj(f, self.wfile)


class LinkPageHandler(Handler):
    """The loopback listener behind `tailscale serve --set-path /app` and `--set-path /web`: Tailscale on macOS cannot
    proxy to the Mac's own tailnet IP, only to 127.0.0.1. This handler serves the app-opener page, the web app's
    static files, and the API under /web/api/ and /api/ only (Funnel :8443 publishes these), with the same
    bearer-token check, 401 rate limit and logging as the tailnet listener. Every bare API path (/health,
    /postings, /files/...) is 404 here."""

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/app/install/"):
            return self._serve_ota(path)
        if urllib.parse.urlsplit(self.path).path in self.PUBLIC_PAGE_PATHS:
            return self._serve_link_page()
        web = self._web_route(self.path)
        if web == "asset":
            return self._serve_web_asset(urllib.parse.urlsplit(self.path).path)
        if web == "api":
            self._strip_web_api()
            return self._api_get()
        if self._is_api_prefixed(self.path):
            self._strip_api()
            return self._api_get()
        return self._error(404, "not found")

    def do_POST(self):
        if self._web_route(self.path) == "api":
            self._strip_web_api()
            return self._api_post()
        if self._is_api_prefixed(self.path):
            self._strip_api()
            return self._api_post()
        return self._error(404, "not found")


# ---------- main ----------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--postings", type=Path, default=DEFAULT_POSTINGS, help="directory of <id>.json (default: workspace/data/postings)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--bind", help="override bind address (default: loopback)")
    ap.add_argument("--files-root", type=Path, default=WORKSPACE_ROOT, help="root for GET /files/<path> (default: workspace/)")
    ap.add_argument("--applications", type=Path, default=DEFAULT_APPLICATIONS,
                    help="what `applications/` in materials.* paths means: GET /files/applications/<path> and "
                         "form_fill.json lookups (default: data/applications)")
    ap.add_argument("--questions", type=Path, default=ask.QUESTIONS,
                    help="directory of question <id>.json (default: workspace/data/questions)")
    ap.add_argument("--profile", type=Path, default=ask.PROFILE,
                    help="profile.yml that answered profile questions write to (default: workspace/profile.yml)")
    ap.add_argument("--link-bind", default="127.0.0.1",
                    help="also serve the /app/ opener page, /web/ and /web/api/ here, same port, for `tailscale serve` "
                         "(default 127.0.0.1; 'off' to disable; skipped when --bind is already this address)")
    ap.add_argument("--pair", action="store_true",
                    help="print the careerapp://pair URL for the Funnel base (works without the VPN) and exit")
    ap.add_argument("--pair-tailnet", action="store_true",
                    help="print the tailnet-only pairing URL (<tailscale ip>:<port>) and exit")
    ap.add_argument("--funnel-base", default=FUNNEL_BASE, help=f"public base for --pair (default {FUNNEL_BASE})")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)   # banner shows up even when stdout is a file

    host = args.bind or (tailscale_ipv4() if args.pair_tailnet else "127.0.0.1")
    if not host:
        print("No Tailscale IPv4 found (is Tailscale running?). Pass --bind <ip> to override.", file=sys.stderr)
        return 2
    token = load_or_create_token()

    if args.pair_tailnet:
        print_pairing(host, args.port, token)
        return 0
    if args.pair:
        if not args.funnel_base:
            print("Choose CAREER_FUNNEL_BASE or --funnel-base for phone pairing, or use --pair-tailnet for local pairing.", file=sys.stderr)
            return 2
        print_pairing(args.funnel_base.rstrip("/") + API_PREFIX, None, token)
        return 0

    postings = args.postings.resolve()
    if not postings.is_dir():
        print(f"postings dir not found: {postings}", file=sys.stderr)
        return 2

    Handler.store = Store(postings, args.applications.resolve())
    Handler.token = token
    Handler.files_root = args.files_root.resolve()
    args.questions.mkdir(parents=True, exist_ok=True)
    Handler.qpaths = ask.Paths(questions=args.questions.resolve(), postings=postings,
                               profile=args.profile.resolve(), applications=args.applications.resolve())

    srv = ThreadingHTTPServer((host, args.port), Handler)
    srv.daemon_threads = True
    link_srv = None
    if args.link_bind not in ("off", "", host):
        try:
            link_srv = ThreadingHTTPServer((args.link_bind, args.port), LinkPageHandler)
            link_srv.daemon_threads = True
            threading.Thread(target=link_srv.serve_forever, daemon=True).start()
        except OSError as e:
            print(f"  link page listener on {args.link_bind}:{args.port} not started: {e}", file=sys.stderr)
    print(f"Career API on http://{host}:{args.port}  (pid {os.getpid()})")
    print(f"  postings:  {postings}  ({len(Handler.store.list())} records)")
    print(f"  files:     {Handler.files_root}  +  applications/ → {Handler.store.applications}")
    print(f"  questions: {Handler.qpaths.questions}  ({len(ask.list_q(Handler.qpaths, 'open'))} open)"
          f"  · profile: {Handler.qpaths.profile}")
    print(f"  token:     {TOKEN_PATH}  — use --pair locally to pair your own device")
    if link_srv:
        print(f"  link page: http://{args.link_bind}:{args.port}/app/  + web app /web/ (API at /web/api/, token)"
              "  for `tailscale serve`")
    print("  open:      python tools/career.py open  (opens the web app here, already paired)")
    print("  stop:      Ctrl-C")
    _write_private(SERVER_PATH, {"host": host, "port": args.port, "pid": os.getpid()})
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        SERVER_PATH.unlink(missing_ok=True)
        srv.server_close()
        if link_srv:
            link_srv.shutdown()
            link_srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
