"""Payload tests (K5): the Update body keeps form order, swaps the owner."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import kayako_parse as kp
import kayako_payload as kpay

FIX = Path(__file__).resolve().parent / "fixtures"


def _view():
    return kp.parse_ticket_view((FIX / "ticket_view.html").read_text())


def test_build_general_body_replaces_only_owner_and_keeps_order():
    view = _view()
    body = kpay.build_general_body(view, "102")
    assert list(body) == list(view["fields"])
    assert body["genownerstaffid"] == "102"
    for key, value in view["fields"].items():
        if key == "genownerstaffid":
            continue
        assert body[key] == value
    assert body["csrfhash"] == view["fields"]["csrfhash"]
    assert body["isajax"] == view["fields"]["isajax"]


def test_build_general_body_stores_id_as_string():
    view = _view()
    assert kpay.build_general_body(view, 102)["genownerstaffid"] == "102"


def test_build_general_body_does_not_mutate_view():
    view = _view()
    before = dict(view["fields"])
    kpay.build_general_body(view, "103")
    assert dict(view["fields"]) == before


def test_masked_masks_csrfhash_and_returns_a_copy():
    body = {"csrfhash": "secret123", "isajax": "1", "genownerstaffid": "102"}
    shown = kpay.masked(body)
    assert shown["csrfhash"] == "MASKED"
    assert shown["isajax"] == "1"
    assert shown["genownerstaffid"] == "102"
    assert body["csrfhash"] == "secret123"


def test_masked_without_csrfhash_keeps_body():
    body = {"isajax": "1"}
    assert kpay.masked(body) == {"isajax": "1"}


def test_mask_text_replaces_each_secret_by_value():
    assert kpay.mask_text("a abc b", ["abc"]) == "a MASKED b"
    assert kpay.mask_text("see abc and sess9", ["abc", "sess9"]) == "see MASKED and MASKED"
    assert kpay.mask_text("nothing secret here", ["abc"]) == "nothing secret here"
    assert kpay.mask_text("abc", []) == "abc"


def test_masked_with_secrets_replaces_values_everywhere():
    body = {"csrfhash": "abc", "formtoken": "abc", "gennotes": "see abc and sess9", "isajax": "1"}
    shown = kpay.masked(body, ["abc", "sess9"])
    assert shown == {
        "csrfhash": "MASKED",
        "formtoken": "MASKED",
        "gennotes": "see MASKED and MASKED",
        "isajax": "1",
    }
    assert body["formtoken"] == "abc"
    assert body["gennotes"] == "see abc and sess9"
