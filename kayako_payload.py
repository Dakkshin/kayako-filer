"""Update bodies for the Kayako staff ticket view (SPEC C5).

- ``build_general_body(view, new_owner_id)`` returns every field of
  ``parse_ticket_view`` in form order with ``genownerstaffid`` replaced.
- ``masked(body, secrets)`` returns a copy with the ``csrfhash`` field as
  ``MASKED`` and every value in ``secrets`` replaced wherever it appears in
  the other field values (masking by value, not just by field name).
- ``mask_text(text, secrets)`` replaces each secret value in ``text``.
- ``build_new_ticket_body(form, resolved)`` returns every field of a parsed New
  Ticket form (SPEC C9) with the ticket's values applied; a repeated field
  (the tag controls' ``[]`` names) holds a list. ``to_multipart(body)`` turns
  it into the ``files=`` pairs requests needs for ``multipart/form-data``.
"""

from __future__ import annotations

OWNER_FIELD = "genownerstaffid"
CSRF_FIELD = "csrfhash"
MASKED_VALUE = "MASKED"

TAG_PLACEHOLDER = "Start typing to insert tags..."
NEW_TICKET_REQUIRED = (
    "newticketsubject", "newticketcontents", "newticketownerstaffid", "newticketdepartmentid",
    "taginput_newticketto", "isajax", CSRF_FIELD,
)
NEW_TICKET_SELECTS = (
    ("type_id", "newtickettickettypeid"),
    ("status_id", "newticketticketstatusid"),
    ("priority_id", "newticketticketpriorityid"),
)


def build_general_body(view, new_owner_id):
    """Return every field of ``view`` in form order with the owner replaced.

    ``view`` is the dict from ``parse_ticket_view``; only ``genownerstaffid``
    changes, everything else (including ``csrfhash`` and ``isajax`` from the
    same fetch) is kept as-is. The new id is stored as a string, exactly as
    the form carries it.
    """
    body = dict(view["fields"])
    body[OWNER_FIELD] = str(new_owner_id)
    return body


def mask_text(text, secrets):
    """Return ``text`` with every value in ``secrets`` replaced by ``MASKED``."""
    out = text or ""
    for secret in secrets or ():
        if secret and secret != MASKED_VALUE:
            out = out.replace(secret, MASKED_VALUE)
    return out


def masked(body, secrets=None):
    """Return a copy of ``body`` with secrets masked by field and by value.

    The ``csrfhash`` field is always ``MASKED``; every value in ``secrets``
    (csrfhash values scraped from the page, live session ids) is replaced
    wherever it appears in the remaining field values, so a secret echoed
    in another field can never leak.
    """
    copy = dict(body)
    if CSRF_FIELD in copy:
        copy[CSRF_FIELD] = MASKED_VALUE
    if secrets:
        for key, value in copy.items():
            if isinstance(value, str):
                copy[key] = mask_text(value, secrets)
            elif isinstance(value, list):
                copy[key] = [mask_text(item, secrets) if isinstance(item, str) else item for item in value]
    return copy


def build_new_ticket_body(form, resolved):
    """Return every field of ``form`` (``parse_new_ticket_form``) with the ticket applied.

    ``resolved`` holds ``department_id``, ``owner_id``, ``type_id``,
    ``status_id`` and ``priority_id`` (the last three ``None`` to keep the
    form's default), ``to`` and ``cc`` address lists, ``subject``, ``body`` and
    ``send_email`` (``None`` keeps the form's checkbox). Everything else, the
    ``csrfhash`` and ``isajax`` included, stays as the same fetch delivered it.

    The recipient lists are written the way the tag control writes them in the
    other forms, as ``taginputcheck_<field>[]`` and ``containertaginput_<field>[]``
    next to the empty text input; that encoding is inferred, not yet seen on a
    New Ticket send. A tag input still holding its placeholder text is blanked.
    Raises ``ValueError`` when the form lacks a field this needs or belongs to a
    different department than ``resolved``.
    """
    fields = form["fields"]
    needed: list[str] = list(NEW_TICKET_REQUIRED)
    if resolved["cc"]:
        needed.append("taginput_newticketcc")
    needed += [name for key, name in NEW_TICKET_SELECTS if resolved.get(key) is not None]
    missing = [name for name in needed if name not in fields]
    if missing:
        raise ValueError(f"the New Ticket form has no field {', '.join(missing)} (has the form changed?)")
    if str(fields["newticketdepartmentid"]) != str(resolved["department_id"]):
        raise ValueError("the New Ticket form belongs to another department than the one asked for")

    body = {}
    for name, value in fields.items():
        body[name] = value
        if name == "taginput_newticketto":
            body["taginputcheck_newticketto[]"] = list(resolved["to"])
            body["containertaginput_newticketto[]"] = list(resolved["to"])
        elif name == "taginput_newticketcc" and resolved["cc"]:
            body["taginputcheck_newticketcc[]"] = list(resolved["cc"])
            body["containertaginput_newticketcc[]"] = list(resolved["cc"])
    body["newticketsubject"] = resolved["subject"]
    body["newticketcontents"] = resolved["body"].replace("\r\n", "\n").replace("\n", "\r\n")
    body["newticketownerstaffid"] = str(resolved["owner_id"])
    for key, name in NEW_TICKET_SELECTS:
        if resolved.get(key) is not None:
            body[name] = str(resolved[key])
    if resolved.get("send_email") is True:
        body["optnewticket_sendemail"] = "1"
    elif resolved.get("send_email") is False:
        body.pop("optnewticket_sendemail", None)
    for name, value in body.items():
        if name.startswith("taginput_") and value == TAG_PLACEHOLDER:
            body[name] = ""
    return body


def to_multipart(body):
    """Return ``body`` as ``files=`` pairs: one ``(name, (None, value))`` per value, repeats expanded."""
    parts = []
    for name, value in body.items():
        for item in value if isinstance(value, list) else [value]:
            parts.append((name, (None, item)))
    return parts
