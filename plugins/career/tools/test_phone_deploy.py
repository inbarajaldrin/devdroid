import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ota as O  # noqa: E402
import phone_deploy as D  # noqa: E402

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
BUNDLE = "org.example.careerapp"
DEVICE = "synthetic-device"


class Phone:
    """Stands in for devicectl: records the calls, answers like the real JSON's `result`."""

    def __init__(self, tunnel="disconnected", locked=False, build="10", install_ok=True, lock_ok=True, apps_ok=True):
        self.tunnel, self.locked, self.build, self.install_ok = tunnel, locked, build, install_ok
        self.lock_ok, self.apps_ok = lock_ok, apps_ok
        self.calls = []

    def __call__(self, args, timeout=25):
        self.calls.append(args[:3])
        if args[:2] == ["list", "devices"]:
            return {"devices": [{"identifier": "other", "connectionProperties": {"tunnelState": "connected"}},
                                {"identifier": DEVICE, "connectionProperties": {"tunnelState": self.tunnel}}]}, ""
        if "lockState" in args:
            return ({"passcodeRequired": self.locked, "unlockedSinceBoot": True}, "") if self.lock_ok else (None, "timed out")
        if "apps" in args:
            if not self.apps_ok:
                return None, "timed out"
            apps = [{"bundleIdentifier": BUNDLE, "bundleVersion": self.build}] if self.build else []
            return {"apps": apps}, ""
        if args[:3] == ["device", "install", "app"]:
            return ({"installedApplications": []}, "") if self.install_ok else (None, "device locked")
        raise AssertionError(args)

    @property
    def installed(self):
        return ["device", "install", "app"] in self.calls


@pytest.fixture
def ota_dir(tmp_path, monkeypatch):
    ipa = tmp_path / "x.ipa"
    ipa.write_bytes(b"PK")
    d = tmp_path / "ota"
    O.publish(ipa, "0.1.0", "11", BUNDLE, d)
    wa = []
    def send(self,message,*,group=None):
        assert group == 'synthetic-career@g.us'
        wa.append(message)
        return {"ok":True,"id":"fake-text"}
    monkeypatch.setattr(D.Text,"send",send)
    monkeypatch.setattr(D, "_wa", wa, raising=False)
    return d


def run(monkeypatch, phone, d, **kw):
    monkeypatch.setattr(D, "devicectl", phone)
    monkeypatch.setattr(D, "unpack", lambda ipa, dest: dest / "Payload" / "Career.app")
    return D.run_once(d, device=kw.pop("device", DEVICE), now=kw.pop("now", NOW), settings={"notifications":"Enabled","notification_group":{"id":"synthetic-career@g.us","name":"Career updates"}}, **kw)


def test_older_build_installs_and_notifies_through_text(ota_dir, monkeypatch):
    p = Phone()
    out = run(monkeypatch, p, ota_dir)
    assert out.action == "installed" and out.why == "build 11" and p.installed
    assert D._wa == ["App updated to build 11"]
    assert json.loads((ota_dir / D.STATE_NAME).read_text())["installed_build"] == 11


@pytest.mark.parametrize('settings',[{}, {'notifications':'Disabled'}, {'notifications':'Enabled'}])
def test_phone_update_has_no_dm_fallback_or_optional_transport_dependency(ota_dir,monkeypatch,settings):
    monkeypatch.setattr(D,'devicectl',Phone())
    monkeypatch.setattr(D,'unpack',lambda ipa,dest:dest/'Payload'/'Career.app')
    monkeypatch.setattr(D.Text,'send',lambda *a,**k:pytest.fail('No group was configured; transport must stay untouched'))
    result=D.run_once(ota_dir,device=DEVICE,now=NOW,settings=settings)
    notice=json.loads((ota_dir/'notification.json').read_text())
    assert result.action == 'installed'
    assert notice['state'] == ('pending' if settings.get('notifications') == 'Enabled' else 'disabled')
    if settings.get('notifications') == 'Enabled': assert 'dedicated career group' in notice['error']


def test_install_hands_devicectl_the_app_inside_the_ipa(tmp_path, monkeypatch):
    import zipfile
    ipa = tmp_path / "a.ipa"
    with zipfile.ZipFile(ipa, "w") as z:
        z.writestr("Payload/Career.app/Info.plist", "x")
    seen = {}

    def fake(args, timeout=25):
        seen["path"] = Path(args[-1])
        seen["there"] = (seen["path"] / "Info.plist").is_file()
        return {}, ""
    monkeypatch.setattr(D, "devicectl", fake)
    assert D.install(ipa, DEVICE) == (True, "")
    assert seen["path"].name == "Career.app" and seen["there"] is True
    junk = tmp_path / "b.ipa"
    junk.write_bytes(b"not a zip")
    ok, why = D.install(junk, DEVICE)
    assert ok is False and "no .app" in why


def test_a_phone_without_the_app_gets_it(ota_dir, monkeypatch):
    p = Phone(build=None)
    assert run(monkeypatch, p, ota_dir).action == "installed"


@pytest.mark.parametrize("phone,why", [
    (Phone(tunnel="unavailable"), "not reachable"),
    (Phone(locked=True), "locked"),
    (Phone(lock_ok=False), "locked"),
    (Phone(apps_ok=False), "stopped answering"),
    (Phone(build="11"), "already has build 11"),
    (Phone(build="12"), "already has build 12"),
])
def test_nothing_happens_and_nothing_is_said(ota_dir, monkeypatch, phone, why):
    phone.calls = []
    out = run(monkeypatch, phone, ota_dir)
    assert out.action == "idle" and why in out.why and not phone.installed and D._wa == []
    assert not (ota_dir / D.STATE_NAME).exists()


def test_an_absent_phone_costs_one_table_read(ota_dir, monkeypatch):
    p = Phone(tunnel="unavailable")
    run(monkeypatch, p, ota_dir)
    assert p.calls == [["list", "devices"]]


def test_a_phone_missing_from_the_list_is_unreachable(ota_dir, monkeypatch):
    p = Phone()
    assert run(monkeypatch, p, ota_dir, device="missing").why == "phone not reachable"


def test_nothing_published(tmp_path, monkeypatch):
    p = Phone()
    assert run(monkeypatch, p, tmp_path / "empty").why == "nothing published" and p.calls == []


def test_a_failed_install_waits_then_tries_again_and_stays_quiet(ota_dir, monkeypatch):
    p = Phone(install_ok=False)
    out = run(monkeypatch, p, ota_dir)
    assert out.action == "failed" and "device locked" in out.why and D._wa == []
    p.calls = []
    later = run(monkeypatch, p, ota_dir, now=NOW + timedelta(minutes=D.RETRY_MINUTES - 1))
    assert later.action == "idle" and "failed recently" in later.why and not p.installed
    p.calls, p.install_ok = [], True
    assert run(monkeypatch, p, ota_dir, now=NOW + timedelta(minutes=D.RETRY_MINUTES + 1)).action == "installed"


def test_a_newer_build_is_not_held_back_by_the_old_failure(ota_dir, monkeypatch):
    (ota_dir / D.STATE_NAME).write_text(json.dumps({"failed_build": 10, "failed_at": "2026-11-02T11:59:00Z"}))
    assert run(monkeypatch, Phone(), ota_dir).action == "installed"


def test_dry_run_installs_nothing(ota_dir, monkeypatch):
    p = Phone()
    assert run(monkeypatch, p, ota_dir, dry_run=True).action == "would-install" and not p.installed and D._wa == []


def test_main_is_silent_when_idle_and_says_so_with_once(ota_dir, monkeypatch, capsys):
    monkeypatch.setattr(D, "run_once", lambda dry_run=False: D.Outcome("idle", "phone locked"))
    assert D.main([]) == 0 and capsys.readouterr().out == ""
    assert D.main(["--once"]) == 0 and "phone locked" in capsys.readouterr().out
