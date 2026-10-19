"""Command tests (K4): staff, view and plan. Offline with a fake session."""

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kayako_filer as kf

BASE = "https://helpdesk.example.test/support/staff/index.php?"
FIX = Path(__file__).resolve().parent / "fixtures"
VIEW_HTML = (FIX / "ticket_view.html").read_text()
STAFF_JSON = (FIX / "online_staff.json").read_text()
LOGIN_OK = (FIX / "login_ok.html").read_text()


class _FakeResp:
    def __init__(self, text, status_code=200, url=""):
        self.text = text
        self.status_code = status_code
        self.url = url


class _FakeSession:
    """POST serves the login body; GET routes by URL and records everything."""

    def __init__(self, view_html=VIEW_HTML, staff_json=STAFF_JSON, cookies=None):
        self.view_html = view_html
        self.staff_json = staff_json
        self.cookies = cookies
        self.calls = []

    def post(self, url, data=None, headers=None, **kwargs):
        self.calls.append(("POST", url, dict(data or {}), headers, kwargs))
        return _FakeResp(LOGIN_OK, 200, url)

    def get(self, url, headers=None, **kwargs):
        self.calls.append(("GET", url, headers, kwargs))
        if "/Base/AJAX/OnlineStaff" in url:
            return _FakeResp(self.staff_json, 200, url)
        if "/Tickets/Ticket/View/" in url:
            return _FakeResp(self.view_html, 200, url)
        return _FakeResp("", 200, url)


def _patch_session(monkeypatch, fake):
    monkeypatch.setattr(kf, "load_creds", lambda: ("u", "p"))
    monkeypatch.setattr(kf.requests, "Session", lambda: fake)
    return fake


def test_plan_view_file_prints_current_to_new_no_network(monkeypatch, capsys):
    def _boom(*a, **k):
        raise AssertionError("no network allowed with --view-file")

    monkeypatch.setattr(kf.requests, "Session", _boom)
    monkeypatch.setattr(kf, "load_creds", _boom)
    rc = kf.main(["plan", "1234567", "--to", "John Smith",
                  "--view-file", str(FIX / "ticket_view.html")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Jane Doe" in out and "John Smith" in out
    assert "\u2192" in out
    assert "department" in out


def test_plan_view_file_case_insensitive_target(capsys):
    rc = kf.main(["plan", "1234567", "--to", "  JOHN smith ",
                  "--view-file", str(FIX / "ticket_view.html")])
    assert rc == 0
    assert "John Smith" in capsys.readouterr().out


def test_plan_current_owner_exits_1_already_owner(capsys):
    rc = kf.main(["plan", "1234567", "--to", "Jane Doe",
                  "--view-file", str(FIX / "ticket_view.html")])
    assert rc == 1
    assert "already the owner" in capsys.readouterr().err


def test_plan_unknown_name_exits_1_with_close_matches(capsys):
    rc = kf.main(["plan", "1234567", "--to", "Jon Smth",
                  "--view-file", str(FIX / "ticket_view.html")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "John Smith" in err


def test_plan_ambiguous_name_exits_1(tmp_path, capsys):
    dup = """<html><body>
<form name="View_Ticketform" method="post" enctype="multipart/form-data"
      action="https://helpdesk.example.test/support/staff/index.php?/Tickets/Ticket/GeneralSubmit/1/inbox/-1/-1/-1/0">
  <input type="hidden" name="csrfhash" value="X" />
  <input type="hidden" name="isajax" value="1" />
  <select name="gendepartmentid"><option value="10" selected="selected">Support</option></select>
  <select name="genownerstaffid">
    <option value="101" selected="selected">Jane Doe</option>
    <option value="104">Jane Doe</option>
    <option value="102">John Smith</option>
  </select>
</form></body></html>"""
    p = tmp_path / "dup.html"
    p.write_text(dup)
    rc = kf.main(["plan", "1", "--to", "jane doe", "--view-file", str(p)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "101" in err and "104" in err


def test_plan_missing_view_file_exits_2(tmp_path, capsys):
    rc = kf.main(["plan", "1", "--to", "John Smith",
                  "--view-file", str(tmp_path / "nope.html")])
    assert rc == 2


def test_view_prints_ticket_fields(monkeypatch, capsys):
    fake = _patch_session(monkeypatch, _FakeSession())
    assert kf.main(["view", "1234567"]) == 0
    out = capsys.readouterr().out
    for needle in ("Sales & Support - Accounts", "Jane Doe", "Question",
                   "New Tickets", "Normal", "John Smith", "Amara Perera"):
        assert needle in out
    gets = [c for c in fake.calls if c[0] == "GET" and "/Tickets/Ticket/View/1234567/inbox/-1/-1/-1" in c[1]]
    assert len(gets) == 1
    assert gets[0][2].get("X-Requested-With") == "XMLHttpRequest"
    assert any(c[0] == "GET" and c[1] == BASE + "/Core/Default/Logout" for c in fake.calls)


class _FakeCookies(dict):
    """Minimal cookie jar: only .get() is used for the session id."""

    def get(self, key, default=None):
        return super().get(key, default)


HOSTILE_HASH = "AbC123hashXYZ789"
HOSTILE_SESSION = "sessID999live"


def _hostile_view_html():
    return """<html><body>
<form name="View_Ticketform" method="post" enctype="multipart/form-data"
      action="https://helpdesk.example.test/support/staff/index.php?/Tickets/Ticket/GeneralSubmit/1234567/inbox/-1/-1/-1/0">
  <input type=hidden name=csrfhash value=%s>
  <input type="hidden" name="isajax" value="1" />
  <select name="gendepartmentid">
    <option value="10" selected="selected">Support</option>
  </select>
  <select name="genownerstaffid">
    <option value="101" selected="selected">Jane Doe</option>
    <option value="102">John Smith</option>
  </select>
  <select name="gentickettypeid"><option value="1" selected="selected">Question</option></select>
  <select name="genticketstatusid"><option value="1" selected="selected">New Tickets</option></select>
  <select name="genticketpriorityid"><option value="2" selected="selected">Normal</option></select>
</form>
<script>var csrf = "%s"; var sid = "%s";</script>
<a href="https://helpdesk.example.test/support/staff/index.php?/Tickets/Ticket/FollowUp/%s?sid=%s">follow up</a>
</body></html>""" % (HOSTILE_HASH, HOSTILE_HASH, HOSTILE_SESSION, HOSTILE_HASH, HOSTILE_SESSION)


def test_view_save_masks_hash_everywhere_and_session_id(monkeypatch, tmp_path):
    fake = _FakeSession(view_html=_hostile_view_html(),
                        cookies=_FakeCookies({"SWIFT_sessionid20": HOSTILE_SESSION}))
    _patch_session(monkeypatch, fake)
    assert kf.main(["view", "1234567", "--save", "--out-dir", str(tmp_path)]) == 0
    text = (tmp_path / "view_1234567.html").read_text()
    assert HOSTILE_HASH not in text
    assert HOSTILE_SESSION not in text


def test_mask_hides_every_session_cookie_in_a_real_jar():
    first, second = "sessAAA111live", "sessBBB222live"
    session = requests.Session()
    session.cookies.set("SWIFT_sessionid20", first, domain="helpdesk.example.test")
    session.cookies.set("SWIFT_sessionid20", second, domain="example.test")
    html = "<html><body>sid %s and sid %s</body></html>" % (first, second)
    masked = kf.mask_csrf_html(html, session)
    assert first not in masked
    assert second not in masked


def test_plan_non_ascii_digits_exit_2(capsys):
    assert kf.main(["plan", "\u00b2", "--to", "John Smith",
                    "--view-file", str(FIX / "ticket_view.html")]) == 2
    assert "digits" in capsys.readouterr().err


def test_view_bad_ticket_exits_2_and_saves_nothing(monkeypatch, tmp_path, capsys):
    fake = _patch_session(monkeypatch, _FakeSession())
    assert kf.main(["view", "../x", "--save", "--out-dir", str(tmp_path)]) == 2
    assert "digits" in capsys.readouterr().err
    assert fake.calls == []
    assert list(tmp_path.iterdir()) == []
    assert not (tmp_path.parent / "view_..").exists()


def test_plan_bad_ticket_exits_2_without_network(monkeypatch, capsys):
    def _boom(*a, **k):
        raise AssertionError("no network allowed for a bad ticket")

    monkeypatch.setattr(kf.requests, "Session", _boom)
    monkeypatch.setattr(kf, "load_creds", _boom)
    assert kf.main(["plan", "../x", "--to", "John Smith",
                    "--view-file", str(FIX / "ticket_view.html")]) == 2
    assert "digits" in capsys.readouterr().err


def test_view_save_masks_csrfhash(monkeypatch, tmp_path, capsys):
    _patch_session(monkeypatch, _FakeSession())
    assert kf.main(["view", "1234567", "--save", "--out-dir", str(tmp_path)]) == 0
    saved = tmp_path / "view_1234567.html"
    assert saved.exists()
    text = saved.read_text()
    assert 'value="MASKED"' in text
    assert "PLACEHOLDER_CSRF" not in text
    out = capsys.readouterr().out
    assert "PLACEHOLDER_CSRF" not in out


def test_staff_lists_names_and_ids(monkeypatch, capsys):
    fake = _patch_session(monkeypatch, _FakeSession())
    assert kf.main(["staff"]) == 0
    out = capsys.readouterr().out
    assert "Jane Doe: 101" in out
    assert "John Smith: 102" in out
    assert "Amara Perera: 103" in out
    assert any(c[0] == "GET" and "/Base/AJAX/OnlineStaff" in c[1] for c in fake.calls)
    assert any(c[0] == "GET" and c[1] == BASE + "/Core/Default/Logout" for c in fake.calls)


def test_staff_grep_filters(monkeypatch, capsys):
    _patch_session(monkeypatch, _FakeSession())
    assert kf.main(["staff", "--grep", "amara"]) == 0
    out = capsys.readouterr().out
    assert "Amara Perera" in out
    assert "Jane Doe" not in out


def test_help_lists_new_commands(capsys):
    assert kf.main([]) == 2
    out = capsys.readouterr().out
    assert "staff" in out and "view" in out and "plan" in out


def test_no_secrets_on_stdout(monkeypatch, capsys):
    fake = _patch_session(monkeypatch, _FakeSession())
    monkeypatch.setattr(kf, "load_creds", lambda: ("u", "pw-probe-456"))
    kf.main(["staff"])
    kf.main(["view", "1234567"])
    out = capsys.readouterr()
    assert "pw-probe-456" not in out.out and "pw-probe-456" not in out.err
    assert "PLACEHOLDER_CSRF" not in out.out
    assert not any(c[0] == "GET" and "Submit" in c[1] for c in fake.calls)
