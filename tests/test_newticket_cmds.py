"""New ticket command tests (C8 to C10): ticket-plan, ticket-log, file. Offline with a fake session."""

import json
import re
import sys
from pathlib import Path

import pytest
import requests
from requests.adapters import HTTPAdapter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kayako_filer as kf
import kayako_guard as kg

BASE = "https://helpdesk.example.test/support/staff/index.php?"
SEND = BASE + "/Tickets/Ticket/NewTicketSubmit/sendmail"
FIX = Path(__file__).resolve().parent / "fixtures"
DIALOG = FIX / "new_ticket_dialog.html"
FORM = FIX / "new_ticket_form.html"
LOGIN_OK = (FIX / "login_ok.html").read_text()

TICKET = '''
department = "Sales & Support - Accounts"
owner = "Amara Perera"
to = "amara.perera@example.test"
subject = "TEST - ignore"
body = """
Line one
Line two
"""
'''


class _Resp:
    def __init__(self, text, status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = dict(headers or {})


class _FakeSession:
    """Serves the fixtures by route, records every call, and can fail the send."""

    def __init__(self, send_status=200, send_text="<p>Ticket created</p>", send_exc=None, cookies=None,
                 form_text=None):
        self.calls = []
        self.send_status = send_status
        self.send_text = send_text
        self.send_exc = send_exc
        self.cookies = cookies
        self.form_text = form_text

    def post(self, url, data=None, headers=None, files=None, **kwargs):
        self.calls.append(("POST", url, dict(data or {}), files, headers, kwargs))
        if url.endswith("/Core/Default/Login"):
            return _Resp(LOGIN_OK)
        if url.endswith("/Tickets/Ticket/NewTicket/"):
            return _Resp(DIALOG.read_text())
        if url.endswith("/Tickets/Ticket/NewTicketForm"):
            return _Resp(self.form_text or FORM.read_text())
        if url == SEND:
            if self.send_exc:
                raise self.send_exc
            return _Resp(self.send_text, self.send_status)
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, headers=None, **kwargs):
        self.calls.append(("GET", url, headers, kwargs))
        if url.endswith("/Core/Default/Logout"):
            return _Resp("")
        raise AssertionError(f"unexpected GET {url}")

    def posts_to(self, suffix):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(suffix)]


def _live(monkeypatch, fake):
    monkeypatch.setattr(kf, "load_creds", lambda: ("u", "p"))
    monkeypatch.setattr(kf.requests, "Session", lambda: fake)
    return fake


def _no_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("no network or credentials allowed here")

    monkeypatch.setattr(kf.requests, "Session", _boom)
    monkeypatch.setattr(kf, "load_creds", _boom)


def _write(tmp_path, text=TICKET, name="ticket.toml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _config(tmp_path, allow_to):
    path = tmp_path / "kayako.toml"
    path.write_text(f'[server]\nbase_url = "{BASE}"\nverify_tls = true\n[send]\nallow_to = {json.dumps(allow_to)}\n')
    return str(path)


def _code(capsys_out):
    return re.search(r"confirm code: (\w+)", capsys_out).group(1)


# --- ticket-plan --------------------------------------------------------------


def test_ticket_plan_offline_prints_the_resolved_ticket_and_touches_no_network(monkeypatch, tmp_path, capsys):
    _no_network(monkeypatch)
    rc = kf.main(["ticket-plan", _write(tmp_path), "--dialog-file", str(DIALOG), "--form-file", str(FORM)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "department: Sales & Support - Accounts (276)" in out
    assert "owner: Amara Perera (703)" in out
    assert "type: SELECT TYPE (form default)" in out
    assert "status: New Tickets (form default)" in out
    assert "priority: Initial Reply (form default)" in out
    assert "to: amara.perera@example.test" in out
    assert "cc: -" in out
    assert "email to the recipients: yes" in out
    assert "subject: TEST - ignore" in out
    assert "body: 2 lines, 17 characters" in out
    assert re.search(r"confirm code: [0-9a-f]{10}", out)
    assert "newticketsubject" not in out  # plan does not print the body


def test_ticket_plan_resolves_the_plain_name_inside_a_team_label_and_named_choices(monkeypatch, tmp_path, capsys):
    _no_network(monkeypatch)
    text = TICKET.replace("Amara Perera", "John Smith") + 'type = "Incident"\nstatus = "On Hold"\npriority = "24 Hur"\n'
    rc = kf.main(["ticket-plan", _write(tmp_path, text), "--dialog-file", str(DIALOG), "--form-file", str(FORM)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "owner: Sales Team; John Smith (Johnny) (702)" in out
    assert "type: Incident" in out and "(form default)" not in out.split("type:")[1].splitlines()[0]
    assert "status: On Hold" in out
    assert "priority: 24 Hur" in out


@pytest.mark.parametrize("field, wrong, hint", [
    ("owner", "Amra Perera", "Amara Perera"),
    ("department", "Sales & Support - Account", "Accounts"),
])
def test_ticket_plan_unknown_names_exit_1_with_close_matches(monkeypatch, tmp_path, capsys, field, wrong, hint):
    _no_network(monkeypatch)
    right = {"owner": "Amara Perera", "department": "Sales & Support - Accounts"}[field]
    rc = kf.main(["ticket-plan", _write(tmp_path, TICKET.replace(right, wrong)),
                  "--dialog-file", str(DIALOG), "--form-file", str(FORM)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "Unknown" in err and hint in err


def test_ticket_plan_an_unknown_type_names_the_kind_not_staff(monkeypatch, tmp_path, capsys):
    _no_network(monkeypatch)
    rc = kf.main(["ticket-plan", _write(tmp_path, TICKET + 'type = "Nonsense"\n'),
                  "--dialog-file", str(DIALOG), "--form-file", str(FORM)])
    assert rc == 1
    assert "Unknown type 'Nonsense'" in capsys.readouterr().err


def test_ticket_plan_live_opens_the_dialog_then_the_form_and_submits_nothing(monkeypatch, tmp_path, capsys):
    fake = _live(monkeypatch, _FakeSession())
    rc = kf.main(["ticket-plan", _write(tmp_path)])
    assert rc == 0
    assert [(c[0], c[1].replace(BASE, "")) for c in fake.calls] == [
        ("POST", "/Core/Default/Login"),
        ("POST", "/Tickets/Ticket/NewTicket/"),
        ("POST", "/Tickets/Ticket/NewTicketForm"),
        ("GET", "/Core/Default/Logout"),
    ]
    assert fake.posts_to("/NewTicketForm")[0][2] == {
        "tickettype": "sendmail", "departmentid": "276", "csrfhash": "PLACEHOLDER_CSRF"}
    assert not any("Submit" in c[1] for c in fake.calls)


def test_ticket_plan_logs_out_even_when_a_name_does_not_resolve(monkeypatch, tmp_path):
    fake = _live(monkeypatch, _FakeSession())
    rc = kf.main(["ticket-plan", _write(tmp_path, TICKET.replace("Amara Perera", "Nobody Here"))])
    assert rc == 1
    assert fake.calls[-1][1].endswith("/Core/Default/Logout")


@pytest.mark.parametrize("args", [
    ["--dialog-file", str(DIALOG)],
    ["--form-file", str(FORM)],
    ["--dialog-file", "/nope/missing.html", "--form-file", str(FORM)],
])
def test_ticket_plan_saved_form_options_are_a_pair_and_must_exist(monkeypatch, tmp_path, capsys, args):
    _no_network(monkeypatch)
    assert kf.main(["ticket-plan", _write(tmp_path)] + args) == 2


def test_a_bad_ticket_file_is_a_usage_error(monkeypatch, tmp_path, capsys):
    _no_network(monkeypatch)
    rc = kf.main(["ticket-plan", _write(tmp_path, TICKET + 'prioritty = "x"\n')])
    assert rc == 2
    assert "prioritty" in capsys.readouterr().err


def test_a_login_page_instead_of_the_form_is_a_session_error(monkeypatch, tmp_path, capsys):
    class Expired(_FakeSession):
        def post(self, url, data=None, headers=None, files=None, **kwargs):
            if url.endswith("/Tickets/Ticket/NewTicket/"):
                self.calls.append(("POST", url, {}, None, headers, kwargs))
                return _Resp((FIX / "login_form.html").read_text())
            return super().post(url, data=data, headers=headers, files=files, **kwargs)

    _live(monkeypatch, Expired())
    rc = kf.main(["ticket-plan", _write(tmp_path)])
    assert rc == 1
    assert "session expired" in capsys.readouterr().err


# --- ticket-log ---------------------------------------------------------------


def test_ticket_log_prints_every_field_masked_saves_json_and_sends_nothing(monkeypatch, tmp_path, capsys):
    fake = _live(monkeypatch, _FakeSession())
    rc = kf.main(["ticket-log", _write(tmp_path), "--out-dir", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PLACEHOLDER_CSRF" not in out
    assert "csrfhash = MASKED" in out
    assert "target: " + SEND in out
    for line in ("newticketsubject = TEST - ignore", "newticketownerstaffid = 703", "newticketdepartmentid = 276",
                 "newticketcontents = Line one\\nLine two", "taginputcheck_newticketto[] = amara.perera@example.test",
                 "containertaginput_newticketto[] = amara.perera@example.test", "recurrencetype = 0",
                 "optnewticket_sendemail = 1"):
        assert line in out
    saved = json.loads(next((tmp_path / "out").glob("new_ticket_body_*.json")).read_text())
    assert saved["csrfhash"] == "MASKED"
    assert saved["taginputcheck_newticketto[]"] == ["amara.perera@example.test"]
    assert fake.posts_to("/NewTicketSubmit/sendmail") == []


def test_ticket_log_masks_a_session_id_echoed_in_the_form(monkeypatch, tmp_path, capsys):
    echoing = FORM.read_text().replace('<textarea name="newticketticketnotes">',
                                       '<textarea name="newticketticketnotes">SESSIONSECRET')
    _live(monkeypatch, _FakeSession(cookies={"SWIFT_sessionid20": "SESSIONSECRET"}, form_text=echoing))
    rc = kf.main(["ticket-log", _write(tmp_path), "--out-dir", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "SESSIONSECRET" not in out
    assert "newticketticketnotes = MASKED" in out
    assert "SESSIONSECRET" not in next((tmp_path / "out").glob("new_ticket_body_*.json")).read_text()


# --- file: dry run ------------------------------------------------------------


def test_file_without_send_is_a_dry_run_that_prints_the_code_and_posts_no_submit(monkeypatch, tmp_path, capsys):
    fake = _live(monkeypatch, _FakeSession())
    rc = kf.main(["file", _write(tmp_path), "--out-dir", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "dry run: nothing sent" in out
    assert f"--send --confirm {_code(out)}" in out
    assert fake.posts_to("/NewTicketSubmit/sendmail") == []


def test_file_dry_run_works_offline_with_saved_forms(monkeypatch, tmp_path, capsys):
    _no_network(monkeypatch)
    rc = kf.main(["file", _write(tmp_path), "--dialog-file", str(DIALOG), "--form-file", str(FORM),
                  "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    assert "dry run: nothing sent" in capsys.readouterr().out


def test_the_confirm_code_is_stable_between_runs_and_changes_with_the_ticket(monkeypatch, tmp_path, capsys):
    _no_network(monkeypatch)
    offline = ["--dialog-file", str(DIALOG), "--form-file", str(FORM), "--out-dir", str(tmp_path / "out")]
    codes = []
    for subject in ("TEST - ignore", "TEST - ignore", "TEST - ignore!"):
        kf.main(["file", _write(tmp_path, TICKET.replace("TEST - ignore", subject))] + offline)
        codes.append(_code(capsys.readouterr().out))
    assert codes[0] == codes[1] != codes[2]


# --- file: sending ------------------------------------------------------------


def _dry_code(monkeypatch, tmp_path, capsys, ticket_text=TICKET):
    _live(monkeypatch, _FakeSession())
    kf.main(["file", _write(tmp_path, ticket_text), "--out-dir", str(tmp_path / "out")])
    return _code(capsys.readouterr().out)


@pytest.mark.parametrize("args", [["--send"], ["--confirm", "abc"]])
def test_send_and_confirm_go_together(monkeypatch, tmp_path, capsys, args):
    _no_network(monkeypatch)
    assert kf.main(["file", _write(tmp_path)] + args) == 2


def test_send_with_saved_forms_is_refused_as_usage(monkeypatch, tmp_path, capsys):
    _no_network(monkeypatch)
    rc = kf.main(["file", _write(tmp_path), "--send", "--confirm", "abc",
                  "--dialog-file", str(DIALOG), "--form-file", str(FORM)])
    assert rc == 2
    assert "live form" in capsys.readouterr().err


@pytest.mark.parametrize("allow", [[], ["someone.else@example.test"]])
def test_send_is_refused_before_any_login_when_the_recipient_is_not_allowed(monkeypatch, tmp_path, capsys, allow):
    code = _dry_code(monkeypatch, tmp_path, capsys)
    _no_network(monkeypatch)  # a login would blow up: the refusal must come first
    rc = kf.main(["file", _write(tmp_path), "--send", "--confirm", code, "--config", _config(tmp_path, allow)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "allow_to" in err


def test_a_cc_that_is_not_allowed_blocks_the_send_too(monkeypatch, tmp_path, capsys):
    text = TICKET + 'cc = ["outsider@example.test"]\n'
    code = _dry_code(monkeypatch, tmp_path, capsys, text)
    _no_network(monkeypatch)
    rc = kf.main(["file", _write(tmp_path, text), "--send", "--confirm", code,
                  "--config", _config(tmp_path, ["amara.perera@example.test"])])
    assert rc == 1
    assert "outsider@example.test" in capsys.readouterr().err


def test_a_wrong_confirm_code_sends_nothing_and_logs_out(monkeypatch, tmp_path, capsys):
    fake = _live(monkeypatch, _FakeSession())
    rc = kf.main(["file", _write(tmp_path), "--send", "--confirm", "0000000000", "--out-dir", str(tmp_path / "out"),
                  "--config", _config(tmp_path, ["amara.perera@example.test"])])
    assert rc == 1
    assert "does not match" in capsys.readouterr().err
    assert fake.posts_to("/NewTicketSubmit/sendmail") == []
    assert fake.calls[-1][1].endswith("/Core/Default/Logout")


def test_a_confirm_code_for_another_ticket_does_not_send(monkeypatch, tmp_path, capsys):
    code = _dry_code(monkeypatch, tmp_path, capsys)
    fake = _live(monkeypatch, _FakeSession())
    changed = TICKET.replace("TEST - ignore", "TEST - changed after the dry run")
    rc = kf.main(["file", _write(tmp_path, changed), "--send", "--confirm", code, "--out-dir", str(tmp_path / "out"),
                  "--config", _config(tmp_path, ["amara.perera@example.test"])])
    assert rc == 1
    assert fake.posts_to("/NewTicketSubmit/sendmail") == []


def test_a_matching_code_and_an_allowed_recipient_sends_exactly_one_multipart_post(monkeypatch, tmp_path, capsys):
    code = _dry_code(monkeypatch, tmp_path, capsys)
    fake = _live(monkeypatch, _FakeSession(cookies={"SWIFT_sessionid20": "SESSIONSECRET"},
                                           send_text="<p>Created SESSIONSECRET</p>"))
    rc = kf.main(["file", _write(tmp_path), "--send", "--confirm", code, "--out-dir", str(tmp_path / "out"),
                  "--config", _config(tmp_path, ["Amara.Perera@Example.test"])])  # allowlist ignores case
    out = capsys.readouterr().out
    assert rc == 0
    posts = fake.posts_to("/NewTicketSubmit/sendmail")
    assert len(posts) == 1
    _, url, data, files, headers, kwargs = posts[0]
    assert url == SEND
    assert kwargs["allow_redirects"] is False
    assert headers["X-Requested-With"] == "XMLHttpRequest"
    parts = [(name, value) for name, (_, value) in files]
    assert ("newticketsubject", "TEST - ignore") in parts
    assert ("newticketcontents", "Line one\r\nLine two") in parts
    assert ("newticketownerstaffid", "703") in parts
    assert ("taginputcheck_newticketto[]", "amara.perera@example.test") in parts
    assert ("csrfhash", "PLACEHOLDER_CSRF") in parts  # the real value goes on the wire, never on screen
    assert all(file_part[1][0] is None for file_part in files)  # text parts: no filename
    assert "sent: HTTP 200" in out
    assert "SESSIONSECRET" not in out and "PLACEHOLDER_CSRF" not in out
    saved = next((tmp_path / "out").glob("new_ticket_response_*.txt")).read_text()
    assert "SESSIONSECRET" not in saved and "Created MASKED" in saved
    assert fake.calls[-1][1].endswith("/Core/Default/Logout")


def test_a_server_error_status_is_reported_as_a_failure(monkeypatch, tmp_path, capsys):
    code = _dry_code(monkeypatch, tmp_path, capsys)
    _live(monkeypatch, _FakeSession(send_status=500, send_text="boom"))
    rc = kf.main(["file", _write(tmp_path), "--send", "--confirm", code, "--out-dir", str(tmp_path / "out"),
                  "--config", _config(tmp_path, ["amara.perera@example.test"])])
    assert rc == 1
    assert "sent: HTTP 500" in capsys.readouterr().out


def test_a_network_failure_on_the_send_is_unknown_never_retried_and_exit_3(monkeypatch, tmp_path, capsys):
    code = _dry_code(monkeypatch, tmp_path, capsys)
    fake = _live(monkeypatch, _FakeSession(send_exc=requests.ConnectionError("reset")))
    rc = kf.main(["file", _write(tmp_path), "--send", "--confirm", code, "--out-dir", str(tmp_path / "out"),
                  "--config", _config(tmp_path, ["amara.perera@example.test"])])
    assert rc == 3
    assert "UNKNOWN" in capsys.readouterr().err
    assert len(fake.posts_to("/NewTicketSubmit/sendmail")) == 1
    assert fake.calls[-1][1].endswith("/Core/Default/Logout")


# --- the wire-level guard, end to end -----------------------------------------


class _WireRecorder:
    """Stands in for the network under a real session, answering by URL."""

    def __init__(self):
        self.requests = []

    def __call__(self, request, **kwargs):
        # Set on HTTPAdapter as a class attribute; a callable instance is not bound, so no `self` adapter arrives.
        self.requests.append((request.method, request.url))
        text = {
            "/Core/Default/Login": LOGIN_OK,
            "/Tickets/Ticket/NewTicket/": DIALOG.read_text(),
            "/Tickets/Ticket/NewTicketForm": FORM.read_text(),
        }.get(request.url.replace(BASE, ""), "<p>ok</p>")
        resp = requests.Response()
        resp.status_code = 200
        resp._content = text.encode("utf-8")
        resp.url = request.url
        resp.request = request
        return resp


@pytest.fixture
def wire(monkeypatch):
    rec = _WireRecorder()
    monkeypatch.setattr(HTTPAdapter, "send", rec)
    monkeypatch.setattr(kf, "load_creds", lambda: ("u", "p"))
    return rec


def test_end_to_end_a_real_session_sends_only_the_one_allowed_submit(wire, tmp_path, capsys):
    kf.main(["file", _write(tmp_path), "--out-dir", str(tmp_path / "o")])
    code = _code(capsys.readouterr().out)
    assert not any("Submit" in url for _, url in wire.requests)  # the dry run reached no submit route
    wire.requests.clear()
    rc = kf.main(["file", _write(tmp_path), "--send", "--confirm", code, "--out-dir", str(tmp_path / "o"),
                  "--config", _config(tmp_path, ["amara.perera@example.test"])])
    assert rc == 0
    submits = [(m, u) for m, u in wire.requests if "Submit" in u]
    assert submits == [("POST", SEND)]


def test_end_to_end_a_session_not_armed_for_the_send_refuses_it_on_the_wire(wire, tmp_path):
    cfg = kf.load_config(kf.SCRIPT_DIR / "kayako.example.toml")
    session = requests.Session()
    kf._arm(session, cfg)  # what ticket-plan, ticket-log and the dry run use
    with pytest.raises(kg.SendRefused):
        kf.send_new_ticket(session, cfg, {"newticketsubject": "x"})
    assert not any("Submit" in url for _, url in wire.requests)


def test_end_to_end_even_an_armed_session_refuses_every_other_submit_route(wire, tmp_path):
    cfg = kf.load_config(kf.SCRIPT_DIR / "kayako.example.toml")
    session = requests.Session()
    kf._arm(session, cfg, allow_send=True)
    for route in ("/Tickets/Ticket/GeneralSubmit/1", "/Tickets/Ticket/ReplySubmit/1", "/Tickets/Ticket/Dispatch/1",
                  "/Tickets/Ticket/NewTicketSubmit/user"):
        with pytest.raises(kg.SendRefused):
            session.post(BASE + route, data={}, allow_redirects=False)
    assert wire.requests == []
