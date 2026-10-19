"""Log command tests (K5): print masked, write JSON, never send (AD-1)."""

import datetime as _datetime
import json
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kayako_filer as kf

BASE = "https://helpdesk.example.test/support/staff/index.php?"
FIX = Path(__file__).resolve().parent / "fixtures"
VIEW_HTML = (FIX / "ticket_view.html").read_text()
LOGIN_OK = (FIX / "login_ok.html").read_text()


class _FakeResp:
    def __init__(self, text, status_code=200, url="", headers=None):
        self.text = text
        self.status_code = status_code
        self.url = url
        self.headers = dict(headers or {})


class _FakeSession:
    """Serves fixture HTML by route and records every call."""

    def __init__(self, view_html=VIEW_HTML, cookies=None):
        self.calls = []
        self.view_html = view_html
        self.cookies = cookies

    def post(self, url, data=None, headers=None, **kwargs):
        self.calls.append(("POST", url, dict(data or {}), headers, kwargs))
        if "/Core/Default/Login" in url:
            return _FakeResp(LOGIN_OK, 200, url)
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, headers=None, **kwargs):
        self.calls.append(("GET", url, headers, kwargs))
        if "/Tickets/Ticket/View/" in url:
            return _FakeResp(self.view_html, 200, url)
        if "/Core/Default/Logout" in url:
            return _FakeResp("", 200, url)
        raise AssertionError(f"unexpected GET {url}")


def _patch_session(monkeypatch, fake):
    monkeypatch.setattr(kf, "load_creds", lambda: ("u", "p"))
    monkeypatch.setattr(kf.requests, "Session", lambda: fake)
    return fake


def _no_submit_or_dispatch(calls):
    urls = [call[1] for call in calls]
    assert not any("Submit" in url for url in urls)
    assert not any("Dispatch" in url for url in urls)


def test_log_prints_every_field_masked_writes_json_and_logs_out(monkeypatch, tmp_path, capsys):
    fake = _patch_session(monkeypatch, _FakeSession())
    rc = kf.main(["log", "1234567", "--to", "John Smith", "--out-dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PLACEHOLDER_CSRF" not in out
    assert "MASKED" in out
    assert "GeneralSubmit" in out
    for field in ("csrfhash", "isajax", "gentags", "gendepartmentid", "genownerstaffid",
                  "gentickettypeid", "genticketstatusid", "genticketpriorityid"):
        assert field in out
    assert "genownerstaffid = 102" in out
    assert "John Smith" in out
    files = list(tmp_path.glob("general_body_1234567_*.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text())
    assert saved["genownerstaffid"] == "102"
    assert saved["csrfhash"] == "MASKED"
    assert saved["isajax"] == "1"
    assert "PLACEHOLDER_CSRF" not in files[0].read_text()
    gets = [c for c in fake.calls if c[0] == "GET" and "/Tickets/Ticket/View/1234567/inbox/-1/-1/-1" in c[1]]
    assert len(gets) == 1
    assert gets[0][2].get("X-Requested-With") == "XMLHttpRequest"
    assert any(c[0] == "GET" and c[1] == BASE + "/Core/Default/Logout" for c in fake.calls)
    _no_submit_or_dispatch(fake.calls)


def test_log_unknown_name_exits_1_with_close_matches(monkeypatch, tmp_path, capsys):
    fake = _patch_session(monkeypatch, _FakeSession())
    rc = kf.main(["log", "1234567", "--to", "Jon Smth", "--out-dir", str(tmp_path)])
    assert rc == 1
    assert "John Smith" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
    _no_submit_or_dispatch(fake.calls)


def test_log_already_owner_exits_1(monkeypatch, tmp_path, capsys):
    fake = _patch_session(monkeypatch, _FakeSession())
    rc = kf.main(["log", "1234567", "--to", "Jane Doe", "--out-dir", str(tmp_path)])
    assert rc == 1
    assert "already the owner" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
    _no_submit_or_dispatch(fake.calls)


def test_log_bad_ticket_exits_2_without_network(monkeypatch, tmp_path, capsys):
    def _boom(*a, **k):
        raise AssertionError("no network allowed for a bad ticket")

    monkeypatch.setattr(kf.requests, "Session", _boom)
    monkeypatch.setattr(kf, "load_creds", _boom)
    assert kf.main(["log", "../x", "--to", "John Smith", "--out-dir", str(tmp_path)]) == 2
    assert "digits" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_log_never_requests_submit_route(monkeypatch, tmp_path, capsys):
    fake = _patch_session(monkeypatch, _FakeSession())
    assert kf.main(["log", "1234567", "--to", "Amara Perera", "--out-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    _no_submit_or_dispatch(fake.calls)
    saved = json.loads(next(tmp_path.glob("general_body_*.json")).read_text())
    assert saved["genownerstaffid"] == "103"


def _cfg():
    return {"server": {"base_url": BASE, "verify_tls": True}}


def test_post_to_general_submit_raises_before_any_request():
    fake = _FakeSession()
    with pytest.raises(ValueError):
        kf.post(fake, _cfg(), "/Tickets/Ticket/GeneralSubmit/1234567/inbox/-1/-1/-1/0",
                data={"genownerstaffid": "102"})
    assert fake.calls == []


def test_get_to_dispatch_raises_before_any_request():
    fake = _FakeSession()
    with pytest.raises(ValueError):
        kf.get(fake, _cfg(), "/Tickets/Ticket/Dispatch/1234567")
    assert fake.calls == []


def test_post_to_lowercase_submit_route_raises_before_any_request():
    fake = _FakeSession()
    with pytest.raises(ValueError):
        kf.post(fake, _cfg(), "/tickets/ticket/generalsubmit/1", data={"x": "y"})
    assert fake.calls == []


def test_get_to_saveasdraft_raises_before_any_request():
    fake = _FakeSession()
    with pytest.raises(ValueError):
        kf.get(fake, _cfg(), "/Tickets/Ticket/SaveAsDraft/1234567")
    assert fake.calls == []


def test_log_requests_do_not_follow_redirects(monkeypatch, tmp_path, capsys):
    fake = _patch_session(monkeypatch, _FakeSession())
    assert kf.main(["log", "1234567", "--to", "John Smith", "--out-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert fake.calls, "log should issue requests"
    for call in fake.calls:
        assert call[-1].get("allow_redirects") is False


def test_log_second_run_within_same_second_does_not_overwrite(monkeypatch, tmp_path, capsys):
    fixed = _datetime.datetime(2026, 9, 27, 12, 0, 0, tzinfo=_datetime.timezone.utc)

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return fixed

    monkeypatch.setattr(kf, "datetime", _FrozenDatetime)
    fake = _patch_session(monkeypatch, _FakeSession())
    assert kf.main(["log", "1234567", "--to", "John Smith", "--out-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    assert kf.main(["log", "1234567", "--to", "Amara Perera", "--out-dir", str(tmp_path)]) == 0
    capsys.readouterr()
    files = sorted(tmp_path.glob("general_body_1234567_*.json"))
    assert len(files) == 2
    owners = {json.loads(path.read_text())["genownerstaffid"] for path in files}
    assert owners == {"102", "103"}
    _no_submit_or_dispatch(fake.calls)


def test_help_lists_log(capsys):
    assert kf.main([]) == 2
    assert "log" in capsys.readouterr().out


HOSTILE_HASH = "AbC123hashXYZ789"
HOSTILE_SESSION = "sessID999live"


def _hostile_log_view_html():
    return """<html><body>
<form name="View_Ticketform" method="post" enctype="multipart/form-data"
      action="https://helpdesk.example.test/support/staff/index.php?/Tickets/Ticket/GeneralSubmit/1234567/inbox/-1/-1/-1/0?csrf=%s">
  <input type="hidden" name="csrfhash" value="%s" />
  <input type="hidden" name="isajax" value="1" />
  <input type="hidden" name="formtoken" value="%s" />
  <input type="hidden" name="sessiontoken" value="%s" />
  <input type="text" name="gentags" value="promo" />
  <select name="gendepartmentid">
    <option value="10" selected="selected">Support</option>
  </select>
  <select name="genownerstaffid">
    <option value="101" selected="selected">Jane Doe</option>
    <option value="102">John Smith</option>
  </select>
  <textarea name="gennotes">call %s about %s</textarea>
</form>
</body></html>""" % (HOSTILE_HASH, HOSTILE_HASH, HOSTILE_HASH,
                     HOSTILE_SESSION, HOSTILE_HASH, HOSTILE_SESSION)


def test_log_redirect_to_login_says_session_expired(monkeypatch, tmp_path, capsys):
    fake = _FakeSession()
    login_url = BASE + "/Core/Default/Login"
    real_get = fake.get

    def _redirect_view(url, headers=None, **kwargs):
        if "/Tickets/Ticket/View/" in url:
            fake.calls.append(("GET", url, headers, kwargs))
            return _FakeResp("", 302, url, headers={"Location": login_url})
        return real_get(url, headers=headers, **kwargs)

    fake.get = _redirect_view
    _patch_session(monkeypatch, fake)
    assert kf.main(["log", "1234567", "--to", "John Smith", "--out-dir", str(tmp_path)]) == 1
    assert "session expired" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
    _no_submit_or_dispatch(fake.calls)


def test_log_redirect_location_with_session_id_is_masked(monkeypatch, tmp_path, capsys):
    store = requests.Session()
    store.cookies.set("SWIFT_sessionid20", HOSTILE_SESSION, domain="helpdesk.example.test")
    fake = _FakeSession(cookies=store.cookies)
    location = BASE + "/Tickets/Ticket/View/1234567?sid=" + HOSTILE_SESSION
    real_get = fake.get

    def _redirect_view(url, headers=None, **kwargs):
        if "/Tickets/Ticket/View/" in url:
            fake.calls.append(("GET", url, headers, kwargs))
            return _FakeResp("", 302, url, headers={"Location": location})
        return real_get(url, headers=headers, **kwargs)

    fake.get = _redirect_view
    _patch_session(monkeypatch, fake)
    assert kf.main(["log", "1234567", "--to", "John Smith", "--out-dir", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "unexpected redirect" in err
    assert HOSTILE_SESSION not in err
    assert list(tmp_path.iterdir()) == []
    _no_submit_or_dispatch(fake.calls)


def test_log_hostile_page_leaks_no_secret(monkeypatch, tmp_path, capsys):
    store = requests.Session()
    store.cookies.set("SWIFT_sessionid20", HOSTILE_SESSION, domain="helpdesk.example.test")
    fake = _FakeSession(view_html=_hostile_log_view_html(), cookies=store.cookies)
    _patch_session(monkeypatch, fake)
    assert kf.main(["log", "1234567", "--to", "John Smith", "--out-dir", str(tmp_path)]) == 0
    out = capsys.readouterr()
    assert HOSTILE_HASH not in out.out
    assert HOSTILE_SESSION not in out.out
    assert HOSTILE_HASH not in out.err
    assert HOSTILE_SESSION not in out.err
    files = list(tmp_path.glob("general_body_1234567_*.json"))
    assert len(files) == 1
    blob = files[0].read_text()
    assert HOSTILE_HASH not in blob
    assert HOSTILE_SESSION not in blob
    saved = json.loads(blob)
    assert saved["genownerstaffid"] == "102"
    _no_submit_or_dispatch(fake.calls)
