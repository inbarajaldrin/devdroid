# Shared pytest setup for workspace/tools tests.
import importlib

import pytest


@pytest.fixture(autouse=True)
def private_dependency_home(tmp_path, monkeypatch):
    """All dependency discovery, including registry reads, stays in the test workspace."""
    from pathlib import Path
    monkeypatch.setattr(Path, "home", lambda:tmp_path / "dependency-home")
    for name in ("ASK_SCRIPT", "SIGN_IN_SCRIPT", "TEXT_SCRIPT", "APPLE_DOCS_SKILL", "CLAUDE_PLUGIN_ROOT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def fake_dependencies_only(monkeypatch):
    import integrations
    def refuse(*args, **kwargs):
        raise AssertionError("Tests must inject a fake dependency; external processes are forbidden.")
    monkeypatch.setattr(integrations, "run", refuse)


@pytest.fixture(autouse=True)
def fresh_auth_limiter(monkeypatch):
    """serve.py keeps a process-wide window of failed auth attempts per client IP (20 per 10 minutes, then 429).
    Tests make many deliberate 401s from 127.0.0.1, so each test starts with an empty window and a limit high enough
    not to interfere. The test that exercises the limiter sets its own low limit."""
    serve = importlib.import_module("serve")
    serve.Handler._auth_fails.clear()
    monkeypatch.setattr(serve, "AUTH_FAIL_LIMIT", 10_000)
    yield
    serve.Handler._auth_fails.clear()


@pytest.fixture(autouse=True)
def live_data_untouched():
    """No test may create or delete distributable runtime records."""
    from pathlib import Path
    data = Path(__file__).resolve().parent.parent / "data"
    dirs = [data / "postings", data / "questions"]
    before = {d: {f.name for f in d.glob("*.json")} for d in dirs if d.is_dir()}
    yield
    for d, names in before.items():
        now = {f.name for f in d.glob("*.json")}
        assert now == names, f"test changed live data in {d}: added {sorted(now - names)}, removed {sorted(names - now)}"


@pytest.fixture(autouse=True)
def skip_when_sandbox_blocks_loopback(monkeypatch):
    """Run HTTP tests normally, but skip them when the execution sandbox denies local bind."""
    import socketserver
    original = socketserver.TCPServer.server_bind

    def guarded_bind(server):
        try:
            return original(server)
        except PermissionError:
            pytest.skip("sandbox blocks loopback sockets")

    monkeypatch.setattr(socketserver.TCPServer, "server_bind", guarded_bind)
