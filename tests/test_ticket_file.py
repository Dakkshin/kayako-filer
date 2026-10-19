"""Ticket file tests (C7): validation and the confirm code. Offline."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kayako_ticket as kt

ROOT = Path(__file__).resolve().parents[1]

MINIMAL = '''
department = "Sales & Support - Accounts"
owner = "Jane Doe"
to = "jane.doe@example.test"
subject = "TEST - ignore"
body = """
Line one
Line two
"""
'''


def _write(tmp_path, text):
    path = tmp_path / "ticket.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_minimal_ticket_is_normalised(tmp_path):
    ticket = kt.read_ticket_file(_write(tmp_path, MINIMAL))
    assert ticket == {
        "department": "Sales & Support - Accounts",
        "owner": "Jane Doe",
        "to": ["jane.doe@example.test"],
        "cc": [],
        "subject": "TEST - ignore",
        "body": "Line one\nLine two",
        "type": None,
        "status": None,
        "priority": None,
        "send_email": None,
    }


def test_optional_fields_and_address_lists(tmp_path):
    text = MINIMAL.replace('to = "jane.doe@example.test"', 'to = ["a@example.test", " b@example.test "]') + '''
cc = ["c@example.test"]
type = "Incident"
status = "New Tickets"
priority = "24 Hur"
send_email = false
'''
    ticket = kt.read_ticket_file(_write(tmp_path, text))
    assert ticket["to"] == ["a@example.test", "b@example.test"]
    assert ticket["cc"] == ["c@example.test"]
    assert (ticket["type"], ticket["status"], ticket["priority"]) == ("Incident", "New Tickets", "24 Hur")
    assert ticket["send_email"] is False


def test_windows_line_endings_in_the_body_become_plain_newlines(tmp_path):
    text = MINIMAL.replace("Line one\nLine two", "Line one\r\nLine two")
    path = tmp_path / "t.toml"
    path.write_bytes(text.encode("utf-8"))
    assert kt.read_ticket_file(path)["body"] == "Line one\nLine two"


def _without(key):
    """MINIMAL with one required key left out (the body spans lines, so it goes by marker)."""
    if key == "body":
        return MINIMAL.split('body = """')[0]
    return "\n".join(line for line in MINIMAL.splitlines() if not line.startswith(key + " ="))


@pytest.mark.parametrize("missing", ["department", "owner", "to", "subject", "body"])
def test_every_required_key_is_required(tmp_path, missing):
    with pytest.raises(kt.TicketFileError) as exc:
        kt.read_ticket_file(_write(tmp_path, _without(missing)))
    assert missing in str(exc.value)


def test_an_unknown_key_is_refused_so_a_typo_cannot_drop_a_field(tmp_path):
    with pytest.raises(kt.TicketFileError) as exc:
        kt.read_ticket_file(_write(tmp_path, MINIMAL + '\nprioritty = "High"\n'))
    assert "prioritty" in str(exc.value)


@pytest.mark.parametrize("bad", ["not-an-address", "a@b", "a b@example.test", "a@example.test, b@example.test"])
def test_a_bad_recipient_is_refused(tmp_path, bad):
    text = MINIMAL.replace("jane.doe@example.test", bad)
    with pytest.raises(kt.TicketFileError):
        kt.read_ticket_file(_write(tmp_path, text))


def test_an_empty_recipient_list_is_refused(tmp_path):
    with pytest.raises(kt.TicketFileError):
        kt.read_ticket_file(_write(tmp_path, MINIMAL.replace('to = "jane.doe@example.test"', "to = []")))


def test_a_multi_line_or_overlong_subject_is_refused(tmp_path):
    with pytest.raises(kt.TicketFileError):
        kt.read_ticket_file(_write(tmp_path, MINIMAL.replace('"TEST - ignore"', '"one\\ntwo"')))
    with pytest.raises(kt.TicketFileError):
        kt.read_ticket_file(_write(tmp_path, MINIMAL.replace("TEST - ignore", "x" * 300)))


def test_send_email_must_be_a_bool(tmp_path):
    with pytest.raises(kt.TicketFileError):
        kt.read_ticket_file(_write(tmp_path, MINIMAL + '\nsend_email = "no"\n'))


def test_invalid_toml_and_a_missing_file_are_ticket_file_errors(tmp_path):
    with pytest.raises(kt.TicketFileError):
        kt.read_ticket_file(_write(tmp_path, "this is = = not toml"))
    with pytest.raises(kt.TicketFileError):
        kt.read_ticket_file(tmp_path / "nope.toml")


def test_the_shipped_example_is_a_valid_ticket():
    ticket = kt.read_ticket_file(ROOT / "tickets" / "example.toml")
    assert ticket["to"] and all(a.endswith(".test") for a in ticket["to"] + ticket["cc"])


# --- the confirm code ---------------------------------------------------------

RESOLVED = {
    "department_id": "276", "owner_id": "703", "type_id": None, "status_id": None, "priority_id": None,
    "to": ["a@example.test"], "cc": [], "subject": "TEST", "body": "hello", "send_email": None,
}


def test_the_confirm_code_is_stable_and_short():
    assert kt.confirm_code(RESOLVED) == kt.confirm_code(dict(RESOLVED))
    assert len(kt.confirm_code(RESOLVED)) == 10


@pytest.mark.parametrize("field, value", [
    ("department_id", "277"), ("owner_id", "704"), ("type_id", "2"), ("status_id", "2"),
    ("priority_id", "28"), ("to", ["b@example.test"]), ("cc", ["c@example.test"]),
    ("subject", "TEST 2"), ("body", "hello!"), ("send_email", False),
])
def test_any_change_to_the_ticket_changes_the_confirm_code(field, value):
    changed = dict(RESOLVED, **{field: value})
    assert kt.confirm_code(changed) != kt.confirm_code(RESOLVED)
