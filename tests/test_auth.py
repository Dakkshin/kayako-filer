"""Auth tests (K2): login, logout, staff_session, login command. No network."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kayako_filer as kf
import kayako_parse as kp

BASE = "https://helpdesk.example.test/support/staff/index.php?"
LOGIN_URL = BASE + "/Core/Default/Login"
LOGOUT_URL = BASE + "/Core/Default/Logout"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

EXPECTED_FIELDS = {
    "username": "someone",
    "password": "s3cret",
    "submitbutton": "Login",
    "remember": "0",
    "languagecode": "en-us",
    "_ca": "login",
    "_redirectAction": "",
}


def _cfg():
    return {"server": {"base_url": BASE, "verify_tls": True}}


class _FakeResp:
    def __init__(self, text, status_code=200, url=""):
        self.text = text
        self.status_code = status_code
        self.url = url


class _FakeSession:
    """Serves one body for every POST; records every call."""

    def __init__(self, body, status=200):
        self.body = body
        self.status = status
        self.calls = []

    def post(self, url, data=None, headers=None, **kwargs):
        self.calls.append(("POST", url, dict(data or {}), headers, kwargs))
        return _FakeResp(self.body, self.status, url)

    def get(self, url, headers=None, **kwargs):
        self.calls.append(("GET", url, headers, kwargs))
        return _FakeResp("", 200, url)


class _RaisingPostSession(_FakeSession):
    """Its POST raises, like a dead connection during login."""

    def post(self, url, data=None, headers=None, **kwargs):
        self.calls.append(("POST", url, dict(data or {}), headers, kwargs))
        raise RuntimeError("connection down")


def _ok_session():
    return _FakeSession((FIXTURES / "login_ok.html").read_text())


def _form_session():
    return _FakeSession((FIXTURES / "login_form.html").read_text())


def test_fixtures_have_expected_shape():
    ok = (FIXTURES / "login_ok.html").read_text()
    form = (FIXTURES / "login_form.html").read_text()
    assert "/Base/Home/Index" in ok
    assert not kp.is_login_form(ok)
    assert kp.is_login_form(form)


def test_is_login_form_needs_both_markers():
    assert kp.is_login_form(
        '<form><input name="username"><input type="password" name="password">'
        '<input name="_ca" value="login"></form>'
    )
    assert not kp.is_login_form(
        '<form><input name="username"><input type="password" name="password"></form>'
    )
    assert not kp.is_login_form(
        '<form><input name="username"><input name="_ca" value="login"></form>'
    )


def test_login_ok_posts_c3_fields():
    fake = _ok_session()
    ok, resp = kf.login(fake, _cfg(), "someone", "s3cret")
    assert ok is True
    assert resp.status_code == 200
    method, url, data, _, _ = fake.calls[0]
    assert method == "POST"
    assert url == LOGIN_URL
    assert data == EXPECTED_FIELDS


def test_login_form_is_failure():
    fake = _form_session()
    ok, _ = kf.login(fake, _cfg(), "someone", "s3cret")
    assert ok is False


def test_login_non_200_is_failure():
    fake = _FakeSession((FIXTURES / "login_ok.html").read_text(), status=500)
    ok, _ = kf.login(fake, _cfg(), "someone", "s3cret")
    assert ok is False


def test_login_ok_without_redirect_is_failure():
    fake = _FakeSession("<html><body>hello</body></html>")
    ok, _ = kf.login(fake, _cfg(), "someone", "s3cret")
    assert ok is False


def test_logout_gets_logout_route():
    fake = _ok_session()
    kf.logout(fake, _cfg())
    method, url, _, _ = fake.calls[0]
    assert method == "GET"
    assert url == LOGOUT_URL


def test_staff_session_logs_out_on_exit(monkeypatch):
    monkeypatch.setattr(kf, "load_creds", lambda: ("u", "p"))
    fake = _ok_session()
    with kf.staff_session(_cfg(), session=fake) as sess:
        assert sess is fake
    posts = [c for c in fake.calls if c[0] == "POST"]
    gets = [c for c in fake.calls if c[0] == "GET" and c[1] == LOGOUT_URL]
    assert len(posts) == 1 and len(gets) == 1


def test_staff_session_logs_out_when_body_raises(monkeypatch):
    monkeypatch.setattr(kf, "load_creds", lambda: ("u", "p"))
    fake = _ok_session()
    with pytest.raises(RuntimeError, match="boom"):
        with kf.staff_session(_cfg(), session=fake):
            raise RuntimeError("boom")
    gets = [c for c in fake.calls if c[0] == "GET" and c[1] == LOGOUT_URL]
    assert len(gets) == 1


def test_staff_session_exits_when_login_fails(monkeypatch):
    monkeypatch.setattr(kf, "load_creds", lambda: ("u", "p"))
    fake = _form_session()
    with pytest.raises(SystemExit):
        with kf.staff_session(_cfg(), session=fake):
            pass  # pragma: no cover


def test_staff_session_logs_out_when_login_raises(monkeypatch):
    monkeypatch.setattr(kf, "load_creds", lambda: ("u", "p"))
    fake = _RaisingPostSession((FIXTURES / "login_ok.html").read_text())
    with pytest.raises(RuntimeError, match="connection down"):
        with kf.staff_session(_cfg(), session=fake):
            pass  # pragma: no cover
    gets = [c for c in fake.calls if c[0] == "GET" and c[1] == LOGOUT_URL]
    assert len(gets) == 1


def test_logout_failure_warns_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr(kf, "load_creds", lambda: ("u", "p"))
    fake = _ok_session()

    def _boom(url, headers=None, **kwargs):
        raise RuntimeError("gone")

    fake.get = _boom
    with kf.staff_session(_cfg(), session=fake):
        pass
    assert "logout failed" in capsys.readouterr().err


def test_cmd_login_ok_and_failed(monkeypatch, capsys):
    monkeypatch.setattr(kf, "load_creds", lambda: ("u", "pw-probe-123"))

    fake_ok = _ok_session()
    monkeypatch.setattr(kf.requests, "Session", lambda: fake_ok)
    assert kf.main(["login"]) == 0
    out = capsys.readouterr()
    assert "OK" in out.out
    assert "pw-probe-123" not in out.out
    assert "pw-probe-123" not in out.err
    assert any(c[0] == "GET" and c[1] == LOGOUT_URL for c in fake_ok.calls)

    fake_bad = _form_session()
    monkeypatch.setattr(kf.requests, "Session", lambda: fake_bad)
    assert kf.main(["login"]) == 1
    out = capsys.readouterr()
    assert "FAILED (still the login form)" in out.out
    assert "pw-probe-123" not in out.out
    assert "pw-probe-123" not in out.err
    assert any(c[0] == "GET" and c[1] == LOGOUT_URL for c in fake_bad.calls)


def test_cmd_login_logs_out_when_login_raises(monkeypatch):
    monkeypatch.setattr(kf, "load_creds", lambda: ("u", "p"))
    fake = _RaisingPostSession((FIXTURES / "login_ok.html").read_text())
    monkeypatch.setattr(kf.requests, "Session", lambda: fake)
    with pytest.raises(RuntimeError, match="connection down"):
        kf.main(["login"])
    gets = [c for c in fake.calls if c[0] == "GET" and c[1] == LOGOUT_URL]
    assert len(gets) == 1


def test_login_help_lists_command(capsys):
    assert kf.main([]) == 2
    out = capsys.readouterr()
    assert "login" in out.out


def test_no_session_or_cookie_file_written():
    root = kf.SCRIPT_DIR
    offenders = [
        p for p in root.iterdir()
        if p.is_file() and p.suffix not in (".md",)
        and any(k in p.name.lower() for k in ("session", "cookie", "swift"))
    ]
    assert offenders == []
    for name in ("tests/fixtures/login_ok.html", "tests/fixtures/login_form.html"):
        assert (root / name).exists()
