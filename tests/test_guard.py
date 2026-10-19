"""Wire-level guard tests (A4, AD-1). Offline: a recording stand-in replaces the network.

A real ``requests.Session`` carries the guard; whatever the stand-in receives is
what would have left the machine. Every attack must raise ``SendRefused`` with
nothing reaching the stand-in.
"""

import socket
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kayako_guard as kg

BASE = "https://helpdesk.example.test/support/staff/index.php?"
LOGIN = BASE + "/Core/Default/Login"
NEW = BASE + "/Tickets/Ticket/NewTicket/"
NEW_FORM = BASE + "/Tickets/Ticket/NewTicketForm"
SEND = BASE + "/Tickets/Ticket/NewTicketSubmit/sendmail"
VIEW = BASE + "/Tickets/Ticket/View/1234567/inbox/-1/-1/-1"
WRITES = [LOGIN, NEW, NEW_FORM]


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("a socket was opened; the guard let a request through to the network")

    monkeypatch.setattr(socket.socket, "connect", _blocked)


class Recorder:
    def __init__(self, redirect_to=None):
        self.requests = []
        self.redirect_to = redirect_to

    def __call__(self, request, **kwargs):
        self.requests.append((request.method, request.url))
        resp = requests.Response()
        resp.request = request
        resp.url = request.url
        if self.redirect_to and len(self.requests) == 1:
            resp.status_code = 302
            resp.headers["Location"] = self.redirect_to
        else:
            resp.status_code = 200
        return resp


def _session(send_url=None, redirect_to=None):
    rec = Recorder(redirect_to)
    session = requests.Session()
    kg.arm(session, BASE, WRITES, send_url=send_url, inner_send=rec)
    return session, rec


# Every way to name a submit route: plain, encoded, double-encoded, dot segments, case,
# backslash, separators, stray control characters.
SUBMIT_URLS = [
    BASE + "/Tickets/Ticket/GeneralSubmit/1234567/inbox/-1/-1/-1/0",
    BASE + "/Tickets/Ticket/ReplySubmit/1234567",
    BASE + "/Tickets/Ticket/Dispatch/1234567",
    BASE + "/Tickets/Ticket/FollowUpSubmit/1234567",
    BASE + "/Tickets/Ticket/SaveAsDraft/1234567",
    BASE + "/Tickets/Ticket/SaveDraft/1234567",
    BASE + "/Tickets/Ticket/NewTicketSubmit/user",
    BASE + "/Tickets/Ticket/%47eneralSubmit/1",
    BASE + "/Tickets/Ticket/General%53ubmit/1",
    BASE + "/Tickets/Ticket/General%2553ubmit/1",
    BASE + "/Tickets/./Ticket/GeneralSubmit/1",
    BASE + "/Tickets/x/../Ticket/GeneralSubmit/1",
    BASE + "/Tickets/Ticket/GENERALSUBMIT/1",
    BASE + "/Tickets/Ticket%5CGeneralSubmit/1",
    BASE + "/Tickets/Ticket/General-Submit/1",
    BASE + "/Tickets/Ticket/General%00Submit/1",
    BASE + "/Tickets/Ticket/General%20Submit/1",
    BASE + "/Tickets/Ticket/View/1?x=ReplySubmit",
    BASE + "/Tickets/Ticket/%44ispatch/1",
]


@pytest.mark.parametrize("target", SUBMIT_URLS)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_submit_routes_never_reach_the_network(target, method):
    session, rec = _session()
    with pytest.raises(kg.SendRefused):
        session.request(method, target, allow_redirects=False)
    assert rec.requests == []


@pytest.mark.parametrize("target", SUBMIT_URLS)
def test_a_direct_send_of_a_prepared_request_is_refused(target):
    session, rec = _session()
    prepared = session.prepare_request(requests.Request("POST", target))
    with pytest.raises(kg.SendRefused):
        session.send(prepared)
    assert rec.requests == []


def test_reads_and_the_three_allowlisted_posts_pass():
    session, rec = _session()
    session.get(VIEW, allow_redirects=False)
    for target in (LOGIN, NEW, NEW_FORM):
        session.post(target, data={"a": "b"}, allow_redirects=False)
    assert [m for m, _ in rec.requests] == ["GET", "POST", "POST", "POST"]


@pytest.mark.parametrize("target", [
    BASE + "/Tickets/Ticket/Watch/1234567",
    BASE + "/Tickets/Ticket/NewTicket/extra",
    BASE + "/Tickets/Ticket/NewTicketForm?x=1",
    BASE + "/Core/Default/Logout",
    BASE + "/Core/Default/Login/",
])
def test_every_other_write_is_refused(target):
    session, rec = _session()
    with pytest.raises(kg.SendRefused):
        session.post(target, data={}, allow_redirects=False)
    assert rec.requests == []


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
@pytest.mark.parametrize("target", WRITES)
def test_only_a_post_passes_on_an_allowlisted_url(method, target):
    session, rec = _session()
    with pytest.raises(kg.SendRefused):
        session.request(method, target, data={"a": "b"}, allow_redirects=False)
    assert rec.requests == []


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_other_methods_are_refused_off_the_allowlist(method):
    session, rec = _session()
    with pytest.raises(kg.SendRefused):
        session.request(method, VIEW, allow_redirects=False)
    assert rec.requests == []


@pytest.mark.parametrize("target", [
    "https://evil.example/support/staff/index.php?/Tickets/Ticket/View/1",
    "http://helpdesk.example.test/support/staff/index.php?/Tickets/Ticket/View/1",
    "https://helpdesk.example.test:8443/support/staff/index.php?/Tickets/Ticket/View/1",
    "https://helpdesk.example.test.evil.example/support/staff/index.php?/Tickets/Ticket/View/1",
])
def test_another_origin_is_refused_even_for_a_read(target):
    session, rec = _session()
    with pytest.raises(kg.SendRefused):
        session.get(target, allow_redirects=False)
    assert rec.requests == []


def test_a_redirect_to_a_submit_route_is_refused_on_the_second_hop():
    session, rec = _session(redirect_to=BASE + "/Tickets/Ticket/GeneralSubmit/1")
    with pytest.raises(kg.SendRefused):
        session.get(VIEW, allow_redirects=True)
    assert rec.requests == [("GET", VIEW)]


# --- the one send URL ---------------------------------------------------------


def test_the_prepared_send_url_is_exactly_the_configured_string():
    # The guard compares the exact string, so requests must not rewrite it.
    assert requests.Request("POST", SEND).prepare().url == SEND


def test_requests_unquotes_plain_letters_so_the_wire_url_is_the_allowed_one():
    # %73 is "s": requests rewrites it before the guard sees the request, so this
    # request is byte-for-byte the allowed send, not a disguised other route.
    for disguised in (SEND.replace("sendmail", "%73endmail"),
                      BASE + "/Tickets/Ticket/%4EewTicketSubmit/sendmail"):
        assert requests.Request("POST", disguised).prepare().url == SEND


def test_the_send_route_is_refused_when_no_send_url_is_armed():
    session, rec = _session(send_url=None)
    with pytest.raises(kg.SendRefused):
        session.post(SEND, data={"a": "b"}, allow_redirects=False)
    assert rec.requests == []


def test_the_send_route_passes_as_a_post_when_armed():
    session, rec = _session(send_url=SEND)
    session.post(SEND, files=[("a", (None, "b"))], allow_redirects=False)
    assert rec.requests == [("POST", SEND)]


@pytest.mark.parametrize("target", [
    SEND + "/",
    SEND + "?x=1",
    SEND.replace("sendmail", "user"),
    SEND.replace("NewTicketSubmit", "newticketsubmit"),
    SEND.replace("NewTicketSubmit/", "NewTicketSubmit%2F"),
    BASE + "/Tickets/Ticket/ReplySubmit/1",
])
def test_an_armed_send_url_opens_nothing_else(target):
    session, rec = _session(send_url=SEND)
    with pytest.raises(kg.SendRefused):
        session.post(target, data={}, allow_redirects=False)
    assert rec.requests == []


def test_an_armed_send_url_is_post_only():
    session, rec = _session(send_url=SEND)
    for method in ("GET", "HEAD", "PUT"):
        with pytest.raises(kg.SendRefused):
            session.request(method, SEND, allow_redirects=False)
    assert rec.requests == []


def test_the_send_route_cannot_redirect_into_another_submit():
    session, rec = _session(send_url=SEND, redirect_to=BASE + "/Tickets/Ticket/ReplySubmit/1")
    with pytest.raises(kg.SendRefused):
        session.post(SEND, data={}, allow_redirects=True)
    assert rec.requests == [("POST", SEND)]


def test_arm_leaves_a_fake_without_mount_alone():
    class Fake:
        pass

    assert kg.arm(Fake(), BASE, WRITES) is None


def test_routing_text_collapses_every_disguise():
    plain = kg.routing_text(BASE + "/Tickets/Ticket/GeneralSubmit/1")
    for variant in ("/Tickets/Ticket/%47eneralSubmit/1", "/Tickets/./Ticket/General-Submit/1",
                    "/Tickets/Ticket%5CGENERALSUBMIT/1", "/Tickets/Ticket/General%2553ubmit/1"):
        assert "generalsubmit" in kg.routing_text(BASE + variant)
    assert "generalsubmit" in plain
