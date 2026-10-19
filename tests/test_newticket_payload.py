"""New Ticket body tests (C9): built from the form's own fields, then the ticket applied. Offline."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kayako_parse as kp
import kayako_payload as pl

FIX = Path(__file__).resolve().parent / "fixtures"


def _form():
    return kp.parse_new_ticket_form((FIX / "new_ticket_form.html").read_text())


def _resolved(**over):
    base = {
        "department_id": "276", "owner_id": "703", "type_id": None, "status_id": None, "priority_id": None,
        "to": ["amara.perera@example.test"], "cc": [], "subject": "TEST - ignore",
        "body": "Line one\nLine two", "send_email": None,
    }
    base.update(over)
    return base


def test_the_ticket_is_applied_on_top_of_the_forms_own_fields_in_form_order():
    form = _form()
    body = pl.build_new_ticket_body(form, _resolved())
    assert body["newticketsubject"] == "TEST - ignore"
    assert body["newticketownerstaffid"] == "703"
    assert body["newticketdepartmentid"] == "276"
    assert body["newticketcontents"] == "Line one\r\nLine two"
    originals = [name for name in body if name in form["fields"]]
    assert originals == list(form["fields"])
    assert list(body)[-2:] == ["isajax", "csrfhash"]


def test_recipients_sit_next_to_their_empty_text_input_as_repeated_fields():
    body = pl.build_new_ticket_body(_form(), _resolved(to=["a@example.test", "b@example.test"]))
    names = list(body)
    at = names.index("taginput_newticketto")
    assert names[at + 1:at + 3] == ["taginputcheck_newticketto[]", "containertaginput_newticketto[]"]
    assert body["taginput_newticketto"] == ""
    assert body["taginputcheck_newticketto[]"] == ["a@example.test", "b@example.test"]
    assert body["containertaginput_newticketto[]"] == ["a@example.test", "b@example.test"]
    assert "taginputcheck_newticketcc[]" not in body


def test_cc_recipients_are_written_the_same_way():
    body = pl.build_new_ticket_body(_form(), _resolved(cc=["c@example.test"]))
    assert body["taginputcheck_newticketcc[]"] == ["c@example.test"]
    assert body["containertaginput_newticketcc[]"] == ["c@example.test"]


def test_type_status_and_priority_change_only_when_given():
    form = _form()
    kept = pl.build_new_ticket_body(form, _resolved())
    assert (kept["newtickettickettypeid"], kept["newticketticketstatusid"],
            kept["newticketticketpriorityid"]) == ("1", "1", "27")
    set_ = pl.build_new_ticket_body(form, _resolved(type_id="2", status_id="3", priority_id="28"))
    assert (set_["newtickettickettypeid"], set_["newticketticketstatusid"],
            set_["newticketticketpriorityid"]) == ("2", "3", "28")


def test_the_send_email_checkbox_follows_the_ticket_and_defaults_to_the_form():
    form = _form()
    assert pl.build_new_ticket_body(form, _resolved())["optnewticket_sendemail"] == "1"
    assert "optnewticket_sendemail" not in pl.build_new_ticket_body(form, _resolved(send_email=False))
    assert pl.build_new_ticket_body(form, _resolved(send_email=True))["optnewticket_sendemail"] == "1"


def test_the_tag_placeholder_is_blanked_and_unrelated_fields_are_untouched():
    body = pl.build_new_ticket_body(_form(), _resolved())
    assert body["taginput_newtickettags"] == ""
    assert body["recurrencetype"] == "0"
    assert body["newticketnotetype"] == "ticket"
    assert body["newticketfrom"] == "0"
    assert body["newticketdue"] == ""
    assert body["csrfhash"] == "PLACEHOLDER_CSRF"


def test_the_parsed_form_is_not_changed_by_building_a_body():
    form = _form()
    before = copy.deepcopy(form)
    pl.build_new_ticket_body(form, _resolved(cc=["c@example.test"], send_email=False))
    assert form == before


def test_a_form_missing_a_needed_field_is_refused_by_name():
    form = _form()
    del form["fields"]["newticketsubject"]
    with pytest.raises(ValueError) as exc:
        pl.build_new_ticket_body(form, _resolved())
    assert "newticketsubject" in str(exc.value)


def test_a_form_without_the_cc_input_is_refused_only_when_cc_is_used():
    form = _form()
    del form["fields"]["taginput_newticketcc"]
    pl.build_new_ticket_body(form, _resolved())
    with pytest.raises(ValueError):
        pl.build_new_ticket_body(form, _resolved(cc=["c@example.test"]))


def test_a_form_for_another_department_is_refused():
    with pytest.raises(ValueError):
        pl.build_new_ticket_body(_form(), _resolved(department_id="277"))


def test_masking_covers_list_values_and_the_csrfhash():
    body = {"csrfhash": "SECRET1", "taginputcheck_x[]": ["a SECRET2 b", "c"], "newticketsubject": "s SECRET2"}
    shown = pl.masked(body, ["SECRET1", "SECRET2"])
    assert shown["csrfhash"] == "MASKED"
    assert shown["taginputcheck_x[]"] == ["a MASKED b", "c"]
    assert shown["newticketsubject"] == "s MASKED"


def test_to_multipart_expands_repeated_fields_and_sends_text_parts_without_a_filename():
    parts = pl.to_multipart({"a": "1", "b[]": ["x", "y"]})
    assert parts == [("a", (None, "1")), ("b[]", (None, "x")), ("b[]", (None, "y"))]
