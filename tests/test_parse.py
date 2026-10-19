"""Parser tests (K3): staff list and ticket view. Offline, synthetic fixtures."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kayako_parse as kp

FIX = Path(__file__).resolve().parent / "fixtures"

LOGIN_HTML = """<html><body>
<form name="LoginForm" method="post"
      action="https://helpdesk.example.test/support/staff/index.php?/Core/Default/Login">
  <input type="text" name="username" value="" />
  <input type="password" name="password" value="" />
  <input type="hidden" name="_ca" value="login" />
  <input type="hidden" name="_redirectAction" value="" />
  <input type="submit" name="submitbutton" value="Login" />
</form>
</body></html>"""

DUP_OWNER_HTML = """<html><body>
<form name="View_Ticketform" method="post" enctype="multipart/form-data"
      action="https://helpdesk.example.test/support/staff/index.php?/Tickets/Ticket/GeneralSubmit/1234567/inbox/-1/-1/-1/0">
  <input type="hidden" name="csrfhash" value="PLACEHOLDER_CSRF" />
  <input type="hidden" name="isajax" value="1" />
  <select name="gendepartmentid">
    <option value="10" selected="selected">Support</option>
  </select>
  <select name="genownerstaffid">
    <option value="101">Jane Doe</option>
    <option value="104" selected="selected">Jane Doe</option>
    <option value="102">John Smith</option>
  </select>
</form>
</body></html>"""


def _staff_text():
    return (FIX / "online_staff.json").read_text()


def _view():
    return kp.parse_ticket_view((FIX / "ticket_view.html").read_text())


def test_parse_online_staff_maps_every_name():
    assert kp.parse_online_staff(_staff_text()) == {
        "Jane Doe": "101",
        "John Smith": "102",
        "Amara Perera": "103",
    }


def test_parse_ticket_view_action_fields_and_options():
    view = _view()
    assert view["action"].endswith("/Tickets/Ticket/GeneralSubmit/1234567/inbox/-1/-1/-1/0")
    assert view["fields"]["csrfhash"] == "PLACEHOLDER_CSRF"
    assert view["fields"]["isajax"] == "1"
    assert list(view["fields"]) == [
        "csrfhash", "isajax", "gentags", "gendepartmentid", "genownerstaffid",
        "gentickettypeid", "genticketstatusid", "genticketpriorityid", "genwatch", "gennotes",
    ]
    assert view["fields"]["gennotes"] == "Follow up next week"
    assert "gennotify" not in view["fields"]
    assert "GeneralSubmit" not in view["fields"]
    assert "query" not in view["fields"]
    assert view["departments"] == {
        "Sales & Support - Accounts": "10",
        "Support": "20",
    }
    assert view["owners"] == {"Jane Doe": "101", "John Smith": "102", "Amara Perera": "103"}
    assert view["selected_department"] == "10"
    assert view["selected_owner"] == "101"
    assert view["fields"]["gendepartmentid"] == "10"
    assert view["fields"]["genownerstaffid"] == "101"


def test_parse_ticket_view_missing_form_raises():
    with pytest.raises(ValueError):
        kp.parse_ticket_view("<html><body>No ticket form here</body></html>")


def test_resolve_ignores_case_and_space():
    owners = _view()["owners"]
    assert kp.resolve("  jane doe ", owners) == owners["Jane Doe"]


def test_resolve_unknown_raises_with_close_matches():
    owners = _view()["owners"]
    with pytest.raises(LookupError) as exc:
        kp.resolve("Jon Smth", owners)
    assert "John Smith" in str(exc.value)


def test_resolve_duplicate_raises():
    with pytest.raises(LookupError):
        kp.resolve("jane doe", {"Jane Doe": "101", "  JANE DOE ": "102"})


def test_duplicate_owner_name_keeps_every_id_and_resolve_names_them():
    owners = kp.parse_ticket_view(DUP_OWNER_HTML)["owners"]
    assert owners["Jane Doe"] == ("101", "104")
    with pytest.raises(LookupError) as exc:
        kp.resolve("  Jane Doe ", owners)
    assert "101" in str(exc.value) and "104" in str(exc.value)


def test_parse_online_staff_duplicate_name_keeps_every_id():
    body = json.dumps({"onlineusersarray": {"101": "Jane Doe", "104": "Jane Doe", "102": "John Smith"}})
    staff = kp.parse_online_staff(body)
    assert staff["Jane Doe"] == ("101", "104")
    with pytest.raises(LookupError) as exc:
        kp.resolve("jane doe", staff)
    assert "101" in str(exc.value) and "104" in str(exc.value)


def _labelled_staff():
    return kp.parse_online_staff((FIX / "online_staff_labelled.json").read_text())


def test_parse_online_staff_live_shape_keeps_the_whole_label():
    staff = _labelled_staff()
    assert staff["Support - Team Lead; Jane Doe"] == "201"
    assert staff["Client Account Manager - Team#3 Mia Lee (Mimi)"] == "204"
    assert staff["Amara Perera"] == "203"
    assert len(staff) == 5


def test_resolve_finds_the_plain_name_inside_a_team_label():
    staff = _labelled_staff()
    assert kp.resolve("John Smith", staff) == "202"
    assert kp.resolve("  mia lee ", staff) == "204"
    assert kp.resolve("Amara Perera", staff) == "203"


def test_resolve_partial_match_needs_whole_words_in_order():
    staff = _labelled_staff()
    for bad in ("Jon Smith", "Smith John", "Mi Lee", "Joh"):
        with pytest.raises(LookupError):
            kp.resolve(bad, staff)


def test_resolve_partial_match_with_several_labels_is_ambiguous():
    with pytest.raises(LookupError) as exc:
        kp.resolve("Jane Doe", _labelled_staff())
    msg = str(exc.value)
    assert "Ambiguous" in msg and "201" in msg and "205" in msg


def test_resolve_typo_suggests_the_whole_label():
    staff = _labelled_staff()
    with pytest.raises(LookupError) as exc:
        kp.resolve("Jon Smth", staff)
    assert "Sales Team; John Smith (Johnny)" in str(exc.value)
    with pytest.raises(LookupError) as exc:
        kp.resolve("Mya Lee", staff)
    assert "Mia Lee (Mimi)" in str(exc.value)


def test_resolve_exact_label_beats_partial_matches():
    staff = _labelled_staff()
    assert kp.resolve("support - team lead; jane doe", staff) == "201"


def test_parse_online_staff_error_body_raises():
    with pytest.raises(ValueError):
        kp.parse_online_staff(json.dumps({"status": "error", "message": "Session expired"}))


def test_is_login_form():
    assert kp.is_login_form(LOGIN_HTML) is True
    assert kp.is_login_form((FIX / "ticket_view.html").read_text()) is False


def test_online_staff_fixture_is_documented_shape():
    data = json.loads(_staff_text())
    assert set(data) == {"onlineusersarray"}
    assert all(isinstance(v, str) for v in data["onlineusersarray"].values())
