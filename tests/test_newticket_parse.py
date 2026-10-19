"""New Ticket parser tests (C8): the department dialog and the full form. Offline, synthetic fixtures."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kayako_parse as kp

FIX = Path(__file__).resolve().parent / "fixtures"


def _dialog_html():
    return (FIX / "new_ticket_dialog.html").read_text()


def _form_html():
    return (FIX / "new_ticket_form.html").read_text()


def test_dialog_departments_by_label_with_plain_spaces_and_the_selected_one():
    dialog = kp.parse_new_ticket_dialog(_dialog_html())
    assert dialog["action"].endswith("/Tickets/Ticket/NewTicketForm")
    assert dialog["departments"]["Sales & Support - Accounts"] == "276"
    assert dialog["departments"]["|- Development - Bug Reporting"] == "31"
    assert dialog["selected_department"] == "18"


def test_dialog_fields_carry_the_checked_ticket_type_and_the_csrfhash():
    fields = kp.parse_new_ticket_dialog(_dialog_html())["fields"]
    assert fields["tickettype"] == "sendmail"
    assert fields["csrfhash"] == "PLACEHOLDER_CSRF"


def test_dialog_resolves_a_department_name_even_behind_a_sub_department_marker():
    departments = kp.parse_new_ticket_dialog(_dialog_html())["departments"]
    assert kp.resolve("sales & support - accounts", departments) == "276"
    assert kp.resolve("Development - Bug Reporting", departments) == "31"


@pytest.mark.parametrize("broken", [
    "<html><body>no form</body></html>",
    '<form name="newticketdialogform"><input type="hidden" name="csrfhash" value="x"></form>',
])
def test_dialog_missing_parts_raise(broken):
    with pytest.raises(ValueError):
        kp.parse_new_ticket_dialog(broken)


def test_dialog_without_a_csrfhash_raises():
    html = _dialog_html().replace('<input type="hidden" name="csrfhash" value="PLACEHOLDER_CSRF" />', "")
    with pytest.raises(ValueError):
        kp.parse_new_ticket_dialog(html)


def test_form_fields_keep_form_order_and_the_browser_defaults():
    form = kp.parse_new_ticket_form(_form_html())
    fields = form["fields"]
    names = list(fields)
    assert names[:3] == ["newticketfrom", "newticketsubject", "taginput_newticketto"]
    assert names[-2:] == ["isajax", "csrfhash"]
    assert fields["newticketfrom"] == "0"
    assert fields["newticketownerstaffid"] == "610"
    assert fields["newtickettickettypeid"] == "1"  # no option marked selected: the first one
    assert fields["newticketticketstatusid"] == "1"
    assert fields["newticketticketpriorityid"] == "27"
    assert fields["newticketcontents"] == ""
    assert fields["newticketdepartmentid"] == "276"
    assert fields["taginput_newtickettags"] == "Start typing to insert tags..."


def test_form_collects_checked_radios_and_checkboxes_only_and_skips_files():
    fields = kp.parse_new_ticket_form(_form_html())["fields"]
    assert fields["recurrencetype"] == "0"
    assert fields["newticketnotetype"] == "ticket"
    assert fields["recurrence_daily_type"] == "default"
    assert fields["recurrence_endtype"] == "1"
    assert fields["optnewticket_sendemail"] == "1"
    assert fields["recurrence_weekly_ismonday"] == "1"
    assert "optnewticket_addkb" not in fields
    assert "recurrence_weekly_issaturday" not in fields
    assert "newticketattachments[]" not in fields


def test_form_selects_map_labels_to_ids_with_plain_spaces():
    selects = kp.parse_new_ticket_form(_form_html())["selects"]
    owners = selects["newticketownerstaffid"]
    assert owners["selected"] == "610"
    assert owners["options"]["Amara Perera"] == "703"
    assert owners["options"]["Client Account Manager - Team#3 Mia Lee (Mimi)"] == "704"
    assert selects["newticketticketstatusid"]["options"]["On Hold"] == "3"


def test_form_owner_is_found_by_the_plain_name_inside_a_team_label():
    owners = kp.parse_new_ticket_form(_form_html())["selects"]["newticketownerstaffid"]["options"]
    assert kp.resolve("John Smith", owners) == "702"
    assert kp.resolve("mia lee", owners) == "704"


def test_form_missing_raises():
    with pytest.raises(ValueError):
        kp.parse_new_ticket_form("<html><body>nothing</body></html>")


def test_the_update_form_parser_still_ignores_radios():
    html = """<form name="View_Ticketform" action="x">
      <input type="radio" name="r" value="a" checked="checked" />
      <input type="hidden" name="csrfhash" value="C" />
    </form>"""
    assert kp.parse_ticket_view(html)["fields"] == {"csrfhash": "C"}
