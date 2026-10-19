"""Read and validate a new-ticket file (SPEC C7). Standard library only.

One ticket per TOML file::

    department = "Sales & Support - Accounts"
    owner = "Jane Doe"
    to = "jane.doe@example.com"          # one address or a list
    subject = "..."
    body = \"\"\"...\"\"\"
    # optional: type, status, priority, cc, send_email

Department, owner, type, status and priority are names, resolved against the
live form by the commands; no ids live in the file. Unknown keys are refused so a
typo can never silently drop a field.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib

REQUIRED = ("department", "owner", "to", "subject", "body")
OPTIONAL = ("type", "status", "priority", "cc", "send_email")
MAX_SUBJECT = 255
_EMAIL = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+$")


class TicketFileError(ValueError):
    """The ticket file is missing, unreadable or invalid."""


def _text(data, key, path):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TicketFileError(f"{path}: {key!r} must be a non-empty string")
    return value


def _addresses(data, key, path, required):
    value = data.get(key)
    if value is None:
        if required:
            raise TicketFileError(f"{path}: {key!r} is required")
        return []
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        raise TicketFileError(f"{path}: {key!r} must be an address or a list of addresses")
    cleaned = [i.strip() for i in items]
    if required and not cleaned:
        raise TicketFileError(f"{path}: {key!r} needs at least one address")
    for address in cleaned:
        if not _EMAIL.match(address):
            raise TicketFileError(f"{path}: {key!r} has a bad address {address!r}")
    return cleaned


def read_ticket_file(path):
    """Read and validate the ticket file at ``path``.

    Returns ``{department, owner, to, cc, subject, body, type, status,
    priority, send_email}`` with ``to`` and ``cc`` as lists, ``type``,
    ``status`` and ``priority`` as ``None`` when absent (the form's own default
    stays) and ``send_email`` as ``None`` or a bool. Raises ``TicketFileError``.
    """
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise TicketFileError(f"{path}: cannot read the ticket file: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise TicketFileError(f"{path}: not valid TOML: {exc}") from exc

    unknown = sorted(set(data) - set(REQUIRED) - set(OPTIONAL))
    if unknown:
        raise TicketFileError(f"{path}: unknown key(s) {', '.join(unknown)} (allowed: "
                              f"{', '.join(REQUIRED + OPTIONAL)})")
    ticket = {
        "department": _text(data, "department", path).strip(),
        "owner": _text(data, "owner", path).strip(),
        "to": _addresses(data, "to", path, required=True),
        "cc": _addresses(data, "cc", path, required=False),
        "subject": _text(data, "subject", path).strip(),
        "body": _text(data, "body", path).replace("\r\n", "\n").strip("\n"),
    }
    if "\n" in ticket["subject"] or "\r" in ticket["subject"]:
        raise TicketFileError(f"{path}: 'subject' must be one line")
    if len(ticket["subject"]) > MAX_SUBJECT:
        raise TicketFileError(f"{path}: 'subject' is longer than {MAX_SUBJECT} characters")
    for key in ("type", "status", "priority"):
        ticket[key] = _text(data, key, path).strip() if key in data else None
    flag = data.get("send_email")
    if flag is not None and not isinstance(flag, bool):
        raise TicketFileError(f"{path}: 'send_email' must be true or false")
    ticket["send_email"] = flag
    return ticket


def confirm_code(resolved):
    """A short code that pins a send to exactly this content.

    ``resolved`` holds what would be sent: department id, owner id, type,
    status and priority ids, recipients, subject, body and the send-email flag.
    The code changes with any of them, so a ``--confirm`` the owner saw in a dry
    run only works for that exact ticket. The csrfhash is never part of it.
    """
    canonical = json.dumps(resolved, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]
