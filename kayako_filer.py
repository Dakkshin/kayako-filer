#!/usr/bin/env python3
"""File Kayako tickets from the staff control panel.

Commands: login, staff, view, plan, log (an owner change, previewed only), and
ticket-plan, ticket-log, file (a new ticket: resolve, preview, and send only with
--send --confirm CODE to an allow-listed recipient). Run `--help` for each.

Credentials, first found wins:
  KAYAKO_USER / KAYAKO_PASS environment variables
  ./.env                      KEY=value lines (gitignored)
  ~/.config/kayako/creds      KEY=value lines (chmod 600 recommended)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from pathlib import Path

import kayako_guard
import kayako_parse
import kayako_payload
import kayako_ticket

try:
    import requests  # noqa: F401  (used by later tasks; keeps the dep wired)
except ImportError:  # pragma: no cover
    sys.exit("requests is required: pip install -r requirements.txt")

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "kayako.toml"
SAMPLE_BASE_URL = "https://helpdesk.example.test/support/staff/index.php?"

UA ="Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0"

LOGIN_ROUTE = kayako_parse.LOGIN_ROUTE
NEW_TICKET_ROUTE = "/Tickets/Ticket/NewTicket/"
NEW_TICKET_FORM_ROUTE = "/Tickets/Ticket/NewTicketForm"
NEW_TICKET_SEND_ROUTE = "/Tickets/Ticket/NewTicketSubmit/sendmail"


# --- config and credentials -------------------------------------------------


def load_config(path):
    p = Path(path)
    if not p.exists():
        print(f"config not found: {path} (copy kayako.example.toml to kayako.toml and set base_url)", file=sys.stderr)
        sys.exit(2)
    try:
        cfg = tomllib.loads(p.read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"{path}: cannot parse config: {exc}", file=sys.stderr)
        sys.exit(2)
    if "server" not in cfg:
        print(f"{path}: missing [server] section", file=sys.stderr)
        sys.exit(2)
    if not cfg["server"].get("base_url"):
        print(f"{path}: missing server.base_url", file=sys.stderr)
        sys.exit(2)
    cfg["server"].setdefault("verify_tls", True)
    return cfg


def parse_kv(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip("'\"")
    return out


def load_creds():
    """Return (user, password). Never print the password."""
    user, pw = os.environ.get("KAYAKO_USER"), os.environ.get("KAYAKO_PASS")
    if user and pw:
        return user, pw

    env = SCRIPT_DIR / ".env"
    if env.exists():
        vals = parse_kv(env.read_text())
        if vals.get("KAYAKO_USER") and vals.get("KAYAKO_PASS"):
            return vals["KAYAKO_USER"], vals["KAYAKO_PASS"]

    creds = Path.home() / ".config" / "kayako" / "creds"
    if creds.exists():
        mode = oct(creds.stat().st_mode & 0o777)
        if mode != "0o600":
            print(f"warning: {creds} mode is {mode}, expected 0o600", file=sys.stderr)
        vals = parse_kv(creds.read_text())
        u = vals.get("KAYAKO_USER") or vals.get("user") or vals.get("username")
        p = vals.get("KAYAKO_PASS") or vals.get("password") or vals.get("pass")
        if u and p:
            return u, p

    print(
        "No Kayako credentials found. Set KAYAKO_USER / KAYAKO_PASS, create "
        f"{SCRIPT_DIR / '.env'}, or ~/.config/kayako/creds (chmod 600).",
        file=sys.stderr,
    )
    sys.exit(2)


# --- urls and session -------------------------------------------------------


def url(cfg, route):
    """Join a route onto the base URL by plain concatenation.

    The base ends in ``index.php?`` so ``url(cfg, "/Core/Default/Login")``
    is ``.../index.php?/Core/Default/Login``.
    """
    base = cfg["server"]["base_url"]
    if not route.startswith("/"):
        route = "/" + route
    return base + route


def base_headers(cfg):
    return {
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": cfg["server"]["base_url"],
        "Sec-GPC": "1",
    }


def _refuse_submit(cfg, route):
    """Return the joined URL, raising when it targets a submit route (AD-1).

    Checked before any request is issued, so a refused route never reaches
    the session and leaves no call behind.
    """
    target = url(cfg, route)
    folded = target.casefold()
    if "submit" in folded or "dispatch" in folded or "savedraft" in folded or "saveasdraft" in folded:
        raise ValueError(f"refusing submit route {route!r} (AD-1: no submit)")
    return target


def get(session, cfg, route, xhr=False):
    """GET ``route`` without following redirects; refuse submit routes (AD-1)."""
    target = _refuse_submit(cfg, route)
    headers = base_headers(cfg)
    if xhr:
        headers["X-Requested-With"] = "XMLHttpRequest"
    return session.get(
        target,
        headers=headers,
        timeout=30,
        verify=cfg["server"].get("verify_tls", True),
        allow_redirects=False,
    )


def post(session, cfg, route, data=None, xhr=False):
    """POST ``route`` without following redirects; refuse submit routes (AD-1)."""
    target = _refuse_submit(cfg, route)
    headers = base_headers(cfg)
    headers["Origin"] = cfg["server"]["base_url"].rstrip("/")
    if xhr:
        headers["X-Requested-With"] = "XMLHttpRequest"
    return session.post(
        target,
        data=data or {},
        headers=headers,
        timeout=30,
        verify=cfg["server"].get("verify_tls", True),
        allow_redirects=False,
    )


# --- auth ---------------------------------------------------------------------


def login(session, cfg, user, password):
    """POST the login form; return (ok, response).

    Success is HTTP 200 whose body redirects to ``/Base/Home/Index`` and
    is not the login form. Never writes a cookie or session file (AD-3).
    """
    resp = post(
        session,
        cfg,
        "/Core/Default/Login",
        data={
            "username": user,
            "password": password,
            "submitbutton": "Login",
            "remember": "0",
            "languagecode": "en-us",
            "_ca": "login",
            "_redirectAction": "",
        },
    )
    body = getattr(resp, "text", "") or ""
    ok = resp.status_code == 200 and "/Base/Home/Index" in body and not kayako_parse.is_login_form(body)
    return ok, resp


def logout(session, cfg):
    """GET the logout route. Always run at the end of a session."""
    return get(session, cfg, "/Core/Default/Logout")


def _logout_quietly(session, cfg):
    """Best-effort logout; warn on stderr instead of raising."""
    try:
        logout(session, cfg)
    except Exception as exc:
        print(f"warning: logout failed ({exc}); the server session may still be open.", file=sys.stderr)


def _arm(session, cfg, allow_send=False):
    """Mount the wire-level guard on ``session`` (A4); only ``file --send`` passes ``allow_send``.

    The write allowlist is login plus the two New Ticket form fetches. With
    ``allow_send`` the exact New Ticket send URL is allowed as one more POST;
    every other submit route stays refused on the wire either way.
    """
    writes = [url(cfg, route) for route in (LOGIN_ROUTE, NEW_TICKET_ROUTE, NEW_TICKET_FORM_ROUTE)]
    send = url(cfg, NEW_TICKET_SEND_ROUTE) if allow_send else None
    return kayako_guard.arm(session, cfg["server"]["base_url"], writes, send_url=send)


@contextmanager
def staff_session(cfg, session=None, allow_send=False):
    """Log in, yield the session, and always log out, even on error."""
    if session is None:
        session = requests.Session()
    _arm(session, cfg, allow_send=allow_send)
    user, password = load_creds()
    try:
        ok, _ = login(session, cfg, user, password)
        if not ok:
            sys.exit("login failed (still the login form). Check KAYAKO_USER / KAYAKO_PASS.")
        yield session
    finally:
        _logout_quietly(session, cfg)


def cmd_login(cfg, args):
    del args
    session = requests.Session()
    _arm(session, cfg)
    user, password = load_creds()
    try:
        ok, _ = login(session, cfg, user, password)
    finally:
        _logout_quietly(session, cfg)
    print("OK" if ok else "FAILED (still the login form)")
    return 0 if ok else 1


# --- staff, view, plan (SPEC C6) ----------------------------------------------


def _display_name(options, sid):
    """Return the display name in ``options`` ({name: id}) for ``sid``."""
    want = str(sid)
    for name, val in options.items():
        if isinstance(val, (tuple, list)):
            if want in [str(v) for v in val]:
                return name
        elif str(val) == want:
            return name
    return None


def _format_staff_id(val):
    if isinstance(val, (tuple, list)):
        return ", ".join(str(v) for v in val)
    return str(val)


def _selected_option(html, field):
    """Return ``(value, label)`` of the selected option for ``field`` (C4)."""
    return kayako_parse.selected_option(html, field)


def _csrfhash_values(html):
    """Collect every csrfhash input value on the page (quoted or unquoted)."""
    values = []
    for tag in re.findall(r"<input[^>]*>", html or "", flags=re.IGNORECASE):
        if "csrfhash" not in tag.casefold():
            continue
        m = re.search(r"value\s*=\s*(\"([^\"]*)\"|'([^']*)'|([^\s>\"']+))", tag, flags=re.IGNORECASE)
        if m:
            val = m.group(2) if m.group(2) is not None else (m.group(3) if m.group(3) is not None else m.group(4))
            if val:
                values.append(val)
    return values


def _session_ids(session):
    """Every live ``SWIFT_sessionid20`` value in the session's cookie store.

    Loops the store instead of a single lookup: a real jar can hold one
    cookie per domain (a lone ``get`` raises when the name repeats), and
    every value must be masked.
    """
    jar = getattr(session, "cookies", None)
    if jar is None:
        return []
    if isinstance(jar, dict):
        val = jar.get("SWIFT_sessionid20")
        return [val] if val else []
    return [
        cookie.value
        for cookie in jar
        if getattr(cookie, "name", None) == "SWIFT_sessionid20" and getattr(cookie, "value", None)
    ]


def mask_csrf_html(html, session=None):
    """Return ``html`` with each csrfhash value replaced by ``MASKED`` everywhere.

    Values are replaced wherever they appear (inputs, scripts, links), never
    just in the input tag. The tag rewrite stays as a fallback for values the
    collector misses. Every live ``SWIFT_sessionid20`` cookie value gets the
    same treatment when the session carries any (AD-3).
    """
    text = html or ""
    secrets = _csrfhash_values(text)
    secrets.extend(_session_ids(session))
    text = kayako_payload.mask_text(text, secrets)

    def _mask_tag(match):
        tag = match.group(0)
        return re.sub(r'value\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)', 'value="MASKED"', tag)

    return re.sub(r"<input[^>]*csrfhash[^>]*>", _mask_tag, text, flags=re.IGNORECASE)


def _check_ticket(ticket):
    """Return an error string unless ``ticket`` is ASCII digits only (exit 2)."""
    if re.fullmatch(r"[0-9]+", str(ticket)) is None:
        return f"error: bad ticket {ticket!r} (digits only)"
    return None


def _ticket_view_route(ticket):
    return f"/Tickets/Ticket/View/{ticket}/inbox/-1/-1/-1"


def _fetch_view_html(cfg, session, ticket):
    resp = get(session, cfg, _ticket_view_route(ticket), xhr=True)
    if getattr(resp, "status_code", 200) in (301, 302, 303, 307, 308):
        try:
            location = (getattr(resp, "headers", {}) or {}).get("Location", "")
        except AttributeError:
            location = ""
        if kayako_parse.LOGIN_ROUTE in (location or ""):
            print("error: session expired (redirected to the login page)", file=sys.stderr)
        else:
            shown = kayako_payload.mask_text(location, _session_ids(session))
            print(f"error: unexpected redirect to {shown or '(unknown location)'}", file=sys.stderr)
        return None
    html = getattr(resp, "text", "") or ""
    if kayako_parse.is_login_form(html):
        print("error: session expired (the login form came back)", file=sys.stderr)
        return None
    try:
        kayako_parse.parse_ticket_view(html)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None
    return html


def cmd_staff(cfg, args):
    grep = (getattr(args, "grep", None) or "").casefold()
    with staff_session(cfg) as session:
        resp = get(session, cfg, "/Base/AJAX/OnlineStaff")
        body = getattr(resp, "text", "") or ""
        if kayako_parse.is_login_form(body):
            print("error: session expired (the login form came back)", file=sys.stderr)
            return 1
        try:
            staff = kayako_parse.parse_online_staff(body)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    for name, sid in staff.items():
        if grep and grep not in name.casefold():
            continue
        print(f"{name}: {_format_staff_id(sid)}")
    return 0


def cmd_view(cfg, args):
    ticket = str(args.ticket)
    bad = _check_ticket(ticket)
    if bad:
        print(bad, file=sys.stderr)
        return 2
    with staff_session(cfg) as session:
        html = _fetch_view_html(cfg, session, ticket)
        if html is None:
            return 1
        view = kayako_parse.parse_ticket_view(html)
        dept_name = _display_name(view["departments"], view["selected_department"])
        owner_name = _display_name(view["owners"], view["selected_owner"])
        type_value, type_label = _selected_option(html, "gentickettypeid")
        status_value, status_label = _selected_option(html, "genticketstatusid")
        priority_value, priority_label = _selected_option(html, "genticketpriorityid")
        print(f"ticket: {ticket}")
        print(f"department: {dept_name or view['selected_department']} ({view['selected_department']})")
        print(f"owner: {owner_name or view['selected_owner']} ({view['selected_owner']})")
        print(f"type: {type_label or type_value} ({type_value})")
        print(f"status: {status_label or status_value} ({status_value})")
        print(f"priority: {priority_label or priority_value} ({priority_value})")
        print("owners:")
        for name, sid in view["owners"].items():
            print(f"  {name}: {_format_staff_id(sid)}")
        if getattr(args, "save", False):
            out_dir = Path(getattr(args, "out_dir", None) or (SCRIPT_DIR / "out"))
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / f"view_{ticket}.html"
            dest.write_text(mask_csrf_html(html, session))
            print(f"saved: {dest}")
    return 0


def cmd_plan(cfg, args):
    ticket = str(args.ticket)
    bad = _check_ticket(ticket)
    if bad:
        print(bad, file=sys.stderr)
        return 2
    target = args.to
    view_file = getattr(args, "view_file", None)
    if view_file:
        try:
            html = Path(view_file).read_text()
        except OSError as exc:
            print(f"error: cannot read view file: {exc}", file=sys.stderr)
            return 2
    else:
        with staff_session(cfg) as session:
            html = _fetch_view_html(cfg, session, ticket)
            if html is None:
                return 1
    if kayako_parse.is_login_form(html):
        print("error: session expired (the login form came back)", file=sys.stderr)
        return 1
    try:
        view = kayako_parse.parse_ticket_view(html)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        new_id = kayako_parse.resolve(target, view["owners"])
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    new_id = str(new_id)
    current_id = str(view["selected_owner"])
    current_name = _display_name(view["owners"], current_id) or current_id
    if new_id == current_id:
        print(f"error: {current_name} is already the owner", file=sys.stderr)
        return 1
    new_name = _display_name(view["owners"], new_id) or target.strip()
    dept_name = _display_name(view["departments"], view["selected_department"])
    dept_name = dept_name or str(view["selected_department"])
    print(f"owner: {current_name} \u2192 {new_name} (department {dept_name})")
    return 0


def cmd_log(cfg, args):
    """Print the exact Update (General) body with the owner changed.

    Logs in, GETs the ticket view fresh (AD-2) so ``csrfhash`` and ``isajax``
    come from the same run, resolves the new owner as :func:`cmd_plan` does,
    then prints each body field with secrets masked by value plus the target
    action (the view's GeneralSubmit URL, masked the same way) and writes
    the masked body to ``out/general_body_<ticket>_<stamp>.json``. Sends
    nothing (AD-1): it never requests any submit route.
    """
    ticket = str(args.ticket)
    bad = _check_ticket(ticket)
    if bad:
        print(bad, file=sys.stderr)
        return 2
    target = args.to
    with staff_session(cfg) as session:
        html = _fetch_view_html(cfg, session, ticket)
        if html is None:
            return 1
        view = kayako_parse.parse_ticket_view(html)
        try:
            new_id = kayako_parse.resolve(target, view["owners"])
        except LookupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        new_id = str(new_id)
        current_id = str(view["selected_owner"])
        current_name = _display_name(view["owners"], current_id) or current_id
        if new_id == current_id:
            print(f"error: {current_name} is already the owner", file=sys.stderr)
            return 1
        new_name = _display_name(view["owners"], new_id) or target.strip()
        body = kayako_payload.build_general_body(view, new_id)
        secrets = _csrfhash_values(html)
        secrets.extend(_session_ids(session))
        shown = kayako_payload.masked(body, secrets)
        print(f"owner: {current_name} \u2192 {new_name}")
        print(f"target: {kayako_payload.mask_text(view['action'], secrets)}")
        for field, value in shown.items():
            print(f"  {field} = {value}")
        out_dir = Path(getattr(args, "out_dir", None) or (SCRIPT_DIR / "out"))
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        dest = out_dir / f"general_body_{ticket}_{stamp}.json"
        suffix = 1
        while dest.exists():
            dest = out_dir / f"general_body_{ticket}_{stamp}-{suffix}.json"
            suffix += 1
        dest.write_text(json.dumps(shown, indent=2), encoding="utf-8")
        print(f"saved -> {dest}")
    return 0


# --- new tickets (SPEC C7 to C10) ---------------------------------------------


class TicketError(Exception):
    """A new ticket cannot be prepared or sent; printed as ``error: ...``, exit 1."""


def _form_text(session, cfg, route, data=None):
    """POST one of the two form-opening routes (never a submit) and return its HTML."""
    resp = post(session, cfg, route, data=data, xhr=True)
    status = getattr(resp, "status_code", 200)
    if status >= 300:
        raise TicketError(f"{route} answered HTTP {status}")
    text = getattr(resp, "text", "") or ""
    if kayako_parse.is_login_form(text):
        raise TicketError("session expired (the login form came back)")
    return text


def _pick(options, name, what):
    try:
        return kayako_parse.resolve(name, options, what=what)
    except LookupError as exc:
        raise TicketError(str(exc)) from exc


_OWNER_SELECT = "newticketownerstaffid"
_CHOICE_SELECTS = (
    ("type", "type_id", "newtickettickettypeid"),
    ("status", "status_id", "newticketticketstatusid"),
    ("priority", "priority_id", "newticketticketpriorityid"),
)


def _prepare_ticket(cfg, ticket, session=None, dialog_html=None, form_html=None):
    """Open the New Ticket dialog and form (or take saved copies), resolve every name, build the body.

    The dialog gives the department list and the ``csrfhash``; the form, opened for
    the resolved department, gives every other field and the options for owner,
    type, status and priority (AD-2: nothing here is stored or reused). Returns
    ``{"resolved", "body", "labels", "email", "secrets", "code"}``: ``resolved``
    is what would be sent, ``labels`` the names behind the ids, ``code`` the
    confirm code. Raises ``TicketError``.
    """
    if dialog_html is None:
        dialog_html = _form_text(session, cfg, NEW_TICKET_ROUTE)
    try:
        dialog = kayako_parse.parse_new_ticket_dialog(dialog_html)
    except ValueError as exc:
        raise TicketError(str(exc)) from exc
    dept_id = _pick(dialog["departments"], ticket["department"], "department")
    if form_html is None:
        form_html = _form_text(session, cfg, NEW_TICKET_FORM_ROUTE, data={
            "tickettype": "sendmail", "departmentid": dept_id, "csrfhash": dialog["fields"]["csrfhash"]})
    try:
        form = kayako_parse.parse_new_ticket_form(form_html)
    except ValueError as exc:
        raise TicketError(str(exc)) from exc
    selects = form["selects"]
    if _OWNER_SELECT not in selects:
        raise TicketError("the New Ticket form has no owner select (has the form changed?)")
    owners = selects[_OWNER_SELECT]["options"]
    owner_id = _pick(owners, ticket["owner"], "owner")
    resolved = {
        "department_id": dept_id, "owner_id": owner_id, "to": ticket["to"], "cc": ticket["cc"],
        "subject": ticket["subject"], "body": ticket["body"], "send_email": ticket["send_email"],
    }
    labels = {
        "department": _display_name(dialog["departments"], dept_id) or dept_id,
        "owner": _display_name(owners, owner_id) or owner_id,
    }
    for key, id_key, field in _CHOICE_SELECTS:
        if field not in selects:
            if ticket[key] is not None:
                raise TicketError(f"the New Ticket form has no {key} select (has the form changed?)")
            resolved[id_key], labels[key] = None, "(not on the form)"
            continue
        options = selects[field]["options"]
        if ticket[key] is None:
            default = selects[field]["selected"]
            resolved[id_key] = None
            labels[key] = f"{_display_name(options, default) or default} (form default)"
        else:
            resolved[id_key] = _pick(options, ticket[key], key)
            labels[key] = _display_name(options, resolved[id_key]) or resolved[id_key]
    try:
        body = kayako_payload.build_new_ticket_body(form, resolved)
    except ValueError as exc:
        raise TicketError(str(exc)) from exc
    email = resolved["send_email"]
    if email is None:
        email = "optnewticket_sendemail" in form["fields"]
    secrets = _csrfhash_values(dialog_html) + _csrfhash_values(form_html) + _session_ids(session)
    return {
        "resolved": resolved, "body": body, "labels": labels, "email": email, "secrets": secrets,
        "code": kayako_ticket.confirm_code(dict(resolved, send_email=email)),
    }


def _print_ticket_summary(prep):
    resolved, labels = prep["resolved"], prep["labels"]
    print(f"department: {labels['department']} ({resolved['department_id']})")
    print(f"owner: {labels['owner']} ({resolved['owner_id']})")
    for key in ("type", "status", "priority"):
        print(f"{key}: {labels[key]}")
    print(f"to: {', '.join(resolved['to'])}")
    print(f"cc: {', '.join(resolved['cc']) or '-'}")
    print(f"email to the recipients: {'yes' if prep['email'] else 'no'}")
    print(f"subject: {resolved['subject']}")
    print(f"body: {resolved['body'].count(chr(10)) + 1} lines, {len(resolved['body'])} characters")
    print(f"confirm code: {prep['code']}")


def _out_path(out_dir, stem, suffix):
    """A fresh ``<out_dir>/<stem>_<utc stamp>.<suffix>`` path, creating the directory."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest, extra = out / f"{stem}_{stamp}.{suffix}", 0
    while dest.exists():
        extra += 1
        dest = out / f"{stem}_{stamp}-{extra}.{suffix}"
    return dest


def _print_ticket_body(cfg, prep, out_dir):
    """Print every field of the body that a send would carry, masked, and save it to ``out_dir``."""
    shown = kayako_payload.masked(prep["body"], prep["secrets"])
    print(f"target: {url(cfg, NEW_TICKET_SEND_ROUTE)}  (a send would POST multipart/form-data here)")
    for field, value in shown.items():
        for item in value if isinstance(value, list) else [value]:
            print(f"  {field} = {str(item).replace(chr(13) + chr(10), chr(92) + 'n')}")
    dest = _out_path(out_dir, "new_ticket_body", "json")
    dest.write_text(json.dumps(shown, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"saved -> {dest}")


def _saved_forms(args):
    """``(dialog_html, form_html)`` from ``--dialog-file`` and ``--form-file``; ``(None, None)`` means a live fetch.

    Raises ``ValueError`` (a usage error) when only one is given or a file cannot be read.
    """
    if bool(args.dialog_file) != bool(args.form_file):
        raise ValueError("--dialog-file and --form-file go together")
    if not args.dialog_file:
        return None, None
    try:
        return Path(args.dialog_file).read_text(), Path(args.form_file).read_text()
    except OSError as exc:
        raise ValueError(f"cannot read a saved form: {exc}") from exc


def _ticket_command(cfg, args, show_body):
    try:
        ticket = kayako_ticket.read_ticket_file(args.ticket_file)
        dialog_html, form_html = _saved_forms(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        with (staff_session(cfg) if dialog_html is None else nullcontext(None)) as session:
            prep = _prepare_ticket(cfg, ticket, session, dialog_html, form_html)
    except (TicketError, kayako_guard.SendRefused) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_ticket_summary(prep)
    if show_body:
        _print_ticket_body(cfg, prep, args.out_dir)
    return 0


def cmd_ticket_plan(cfg, args):
    """Resolve every name in a ticket file against the live form and print what would be created.

    Opens the New Ticket dialog and form (two form-opening POSTs, no submit); with
    ``--dialog-file`` and ``--form-file`` it reads saved copies and touches no network.
    """
    return _ticket_command(cfg, args, show_body=False)


def cmd_ticket_log(cfg, args):
    """Like ``ticket-plan``, then print the exact body a send would carry (csrfhash masked). Sends nothing."""
    return _ticket_command(cfg, args, show_body=True)


def _recipient_refusal(cfg, ticket):
    """Why a send to this ticket's recipients is refused, or ``None`` (the recipient allowlist)."""
    allowed = (cfg.get("send") or {}).get("allow_to", [])
    if not isinstance(allowed, list) or not all(isinstance(a, str) for a in allowed):
        return "refusing to send: [send] allow_to in kayako.toml must be a list of addresses"
    if not allowed:
        return ("refusing to send: [send] allow_to in kayako.toml is empty, so no recipient is allowed. "
                "Put the address a test may email there first.")
    allowed_folded = {a.strip().casefold() for a in allowed}
    blocked = [a for a in ticket["to"] + ticket["cc"] if a.casefold() not in allowed_folded]
    if blocked:
        return f"refusing to send: not on [send] allow_to: {', '.join(blocked)}"
    return None


def send_new_ticket(session, cfg, body):
    """POST the New Ticket form. The only submit this tool can make; reached only from ``file --send``.

    Bypasses ``_refuse_submit`` on purpose: the route is a submit route. The wire-level
    guard on ``session`` allows exactly this URL, and only when armed for it, so a
    session built any other way refuses it. Redirects are not followed and nothing retries.
    """
    headers = base_headers(cfg)
    headers["Origin"] = cfg["server"]["base_url"].rstrip("/")
    headers["X-Requested-With"] = "XMLHttpRequest"
    return session.post(
        url(cfg, NEW_TICKET_SEND_ROUTE),
        files=kayako_payload.to_multipart(body),
        headers=headers,
        timeout=30,
        verify=cfg["server"].get("verify_tls", True),
        allow_redirects=False,
    )


def _send_and_report(cfg, session, prep, out_dir):
    try:
        resp = send_new_ticket(session, cfg, prep["body"])
    except kayako_guard.SendRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"error: the send outcome is UNKNOWN ({type(exc).__name__}). Do not retry yet: "
              "look in the Tickets grid for the subject first.", file=sys.stderr)
        return 3
    status = getattr(resp, "status_code", 0)
    secrets = prep["secrets"]
    text = kayako_payload.mask_text(getattr(resp, "text", "") or "", secrets)
    location = kayako_payload.mask_text((getattr(resp, "headers", None) or {}).get("Location", ""), secrets)
    dest = _out_path(out_dir, "new_ticket_response", "txt")
    dest.write_text(f"HTTP {status}\nLocation: {location}\n\n{text}\n", encoding="utf-8")
    print(f"sent: HTTP {status}")
    if location:
        print(f"redirect: {location}")
    print(f"response: {' '.join(re.sub(r'<[^>]+>', ' ', text).split())[:400] or '(empty)'}")
    print(f"saved -> {dest}")
    print("not verified: a success status does not prove a ticket was created (SPEC open question 2). "
          "Search the Tickets grid for the subject.")
    return 0 if status < 400 else 1


def cmd_file(cfg, args):
    """Dry-run a new ticket, or with ``--send --confirm CODE`` send it (SPEC C10).

    The dry run is ``ticket-log`` plus the confirm code. A send needs: ``--send``,
    the code printed by a dry run of this exact ticket, and every recipient on
    ``[send] allow_to`` in ``kayako.toml``. It never retries.
    """
    if bool(args.send) != bool(args.confirm):
        print("error: --send and --confirm CODE go together (the code comes from a dry run)", file=sys.stderr)
        return 2
    try:
        ticket = kayako_ticket.read_ticket_file(args.ticket_file)
        dialog_html, form_html = _saved_forms(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.send:
        if dialog_html is not None:
            print("error: --send needs the live form; drop --dialog-file and --form-file", file=sys.stderr)
            return 2
        refusal = _recipient_refusal(cfg, ticket)
        if refusal:
            print(f"error: {refusal}", file=sys.stderr)
            return 1
    try:
        with (staff_session(cfg, allow_send=args.send) if dialog_html is None else nullcontext(None)) as session:
            prep = _prepare_ticket(cfg, ticket, session, dialog_html, form_html)
            _print_ticket_summary(prep)
            _print_ticket_body(cfg, prep, args.out_dir)
            if not args.send:
                print("dry run: nothing sent. To send this exact ticket: "
                      f"file {args.ticket_file} --send --confirm {prep['code']}")
                return 0
            if args.confirm != prep["code"]:
                print("error: --confirm does not match this ticket (it changed since the dry run); "
                      "run the dry run again", file=sys.stderr)
                return 1
            return _send_and_report(cfg, session, prep, args.out_dir)
    except (TicketError, kayako_guard.SendRefused) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


# --- selftest ---------------------------------------------------------------


def run_selftest():
    failures = []
    # A built-in sample, never the user's kayako.toml: the selftest must pass on a fresh checkout.
    cfg = {"server": {"base_url": SAMPLE_BASE_URL, "verify_tls": True}}
    base = cfg["server"]["base_url"]

    login_url = url(cfg, "/Core/Default/Login")
    expected = SAMPLE_BASE_URL + "/Core/Default/Login"
    if login_url != expected:
        failures.append(f"URL join wrong: {login_url!r} != {expected!r}")

    sample = "# comment\nKAYAKO_USER='me' \nKAYAKO_PASS=\"s3cret\"\nEMPTY=\nno-equals\n"
    vals = parse_kv(sample)
    if vals.get("KAYAKO_USER") != "me" or vals.get("KAYAKO_PASS") != "s3cret":
        failures.append(f"parse_kv wrong: {vals!r}")

    saved = (os.environ.get("KAYAKO_USER"), os.environ.get("KAYAKO_PASS"))
    os.environ["KAYAKO_USER"] = "__selftest_user__"
    os.environ["KAYAKO_PASS"] = "__selftest_pass__"
    try:
        u, p = load_creds()
        if (u, p) != ("__selftest_user__", "__selftest_pass__"):
            failures.append("load_creds did not prefer the environment")
    except SystemExit as exc:
        failures.append(f"load_creds exited with env set: {exc}")
    finally:
        if saved[0] is None:
            os.environ.pop("KAYAKO_USER", None)
        else:
            os.environ["KAYAKO_USER"] = saved[0]
        if saved[1] is None:
            os.environ.pop("KAYAKO_PASS", None)
        else:
            os.environ["KAYAKO_PASS"] = saved[1]

    class _FakeSession:
        def __init__(self):
            self.calls = []

        def get(self, url_, headers=None, **kwargs):
            self.calls.append(("GET", url_, headers, kwargs))
            return ("GET", url_, headers)

        def post(self, url_, data=None, headers=None, **kwargs):
            self.calls.append(("POST", url_, data, headers, kwargs))
            return ("POST", url_, data, headers)

    fake = _FakeSession()
    get(fake, cfg, "/Core/Default/Login", xhr=True)
    get(fake, cfg, "/Core/Default/Login")
    post(fake, cfg, "/Core/Default/Login", data={"a": "b"}, xhr=True)
    plain_get = fake.calls[1]
    xhr_get = fake.calls[0]
    xhr_post = fake.calls[2]
    if "X-Requested-With" in plain_get[2]:
        failures.append("get sent X-Requested-With without xhr=True")
    if xhr_get[2].get("X-Requested-With") != "XMLHttpRequest":
        failures.append("get missed X-Requested-With with xhr=True")
    if xhr_post[3].get("X-Requested-With") != "XMLHttpRequest":
        failures.append("post missed X-Requested-With with xhr=True")
    if xhr_get[1] != expected:
        failures.append(f"get used the wrong URL: {xhr_get[1]!r}")

    if "User-Agent" not in base_headers(cfg):
        failures.append("base_headers missed User-Agent")

    guard = kayako_guard.GuardAdapter(base, [url(cfg, LOGIN_ROUTE)])
    for route in ("/Tickets/Ticket/GeneralSubmit/1", "/Tickets/Ticket/ReplySubmit/1", NEW_TICKET_SEND_ROUTE):
        if not guard.refusal("POST", url(cfg, route)):
            failures.append(f"the guard let {route} through with no send armed")
    if guard.refusal("GET", url(cfg, _ticket_view_route("1"))):
        failures.append("the guard refused a plain ticket view")
    armed = kayako_guard.GuardAdapter(base, [], send_url=url(cfg, NEW_TICKET_SEND_ROUTE))
    if armed.refusal("POST", url(cfg, NEW_TICKET_SEND_ROUTE)) or not armed.refusal("POST", url(cfg, "/Tickets/Ticket/ReplySubmit/1")):
        failures.append("an armed guard must allow only the one send URL")

    if failures:
        print("SELFTEST FAILED:")
        for f in failures:
            print("  -", f)
        return False
    print("SELFTEST PASSED")
    return True


# --- cli --------------------------------------------------------------------


def cmd_selftest(cfg, args):
    del cfg, args
    return 0 if run_selftest() else 1


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=str(DEFAULT_CONFIG))
    common.add_argument("--out-dir", default=str(SCRIPT_DIR / "out"), help="where to save responses")

    parser = argparse.ArgumentParser(description=(__doc__ or "Kayako filer").splitlines()[0])
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("selftest", parents=[common], help="offline checks, no network")
    sub.add_parser("login", parents=[common], help="log in, report OK or FAILED, log out")

    p_staff = sub.add_parser("staff", parents=[common], help="list OnlineStaff names and ids")
    p_staff.add_argument("--grep", default=None, help="print matching names instead of all")

    p_view = sub.add_parser("view", parents=[common], help="show a ticket's department, owner and owner choices")
    p_view.add_argument("ticket", help="ticket id")
    p_view.add_argument("--save", action="store_true", help="write the HTML to out/ with csrfhash masked")

    p_plan = sub.add_parser("plan", parents=[common], help="show current owner and the resolved new owner")
    p_plan.add_argument("ticket", help="ticket id")
    p_plan.add_argument("--to", required=True, help="new owner full name")
    p_plan.add_argument("--view-file", default=None, help="read a saved ticket view instead of fetching")

    p_log = sub.add_parser("log", parents=[common], help="print the Update body with the owner changed, send nothing")
    p_log.add_argument("ticket", help="ticket id")
    p_log.add_argument("--to", required=True, help="new owner full name")

    ticket_cmds = (
        ("ticket-plan", "resolve a new-ticket file against the live form and print what would be created"),
        ("ticket-log", "like ticket-plan, plus the exact body a send would carry; sends nothing"),
        ("file", "dry-run a new ticket; with --send --confirm CODE, send it (recipients must be on allow_to)"),
    )
    for name, help_text in ticket_cmds:
        p_ticket = sub.add_parser(name, parents=[common], help=help_text)
        p_ticket.add_argument("ticket_file", help="a new-ticket TOML file (see tickets/example.toml)")
        p_ticket.add_argument("--dialog-file", default=None, help="a saved New Ticket dialog (offline, with --form-file)")
        p_ticket.add_argument("--form-file", default=None, help="a saved New Ticket form (offline, with --dialog-file)")
        if name == "file":
            p_ticket.add_argument("--send", action="store_true", help="really create the ticket (needs --confirm)")
            p_ticket.add_argument("--confirm", default=None, help="the confirm code a dry run of this ticket printed")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.command:
        build_parser().print_help()
        return 2
    if args.command == "selftest":
        return 0 if run_selftest() else 1

    cfg = load_config(args.config)
    dispatch = {
        "selftest": cmd_selftest,
        "login": cmd_login,
        "staff": cmd_staff,
        "view": cmd_view,
        "plan": cmd_plan,
        "log": cmd_log,
        "ticket-plan": cmd_ticket_plan,
        "ticket-log": cmd_ticket_log,
        "file": cmd_file,
    }
    return dispatch[args.command](cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
