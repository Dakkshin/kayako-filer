"""Parsers for the Kayako staff UI (SPEC C4). Standard library only.

- ``parse_online_staff`` maps ``Base/AJAX/OnlineStaff`` JSON to ``{full name: id}``.
- ``parse_ticket_view`` reads the ``View_Ticketform`` form: its action, every
  field a submit would carry (in form order), and the department/owner option
  lists with the selected value of each.
- ``selected_option`` reads the selected ``(value, label)`` of any other
  select in the view (type, status, priority).
- ``parse_new_ticket_dialog`` and ``parse_new_ticket_form`` read the two New
  Ticket fetches (SPEC C8): the department dialog, then the full form.
- ``is_login_form`` reports whether a page is the login form.
- ``resolve`` looks a staff name up in an option list, case- and
  space-insensitively.

Ids are returned as strings, exactly as they appear in the source, so values
from ``parse_ticket_view`` compare directly against ``fields`` entries. A name
shared by more than one id maps to a tuple of every id (in source order) so
``resolve`` can refuse it by naming each id instead of silently picking one.
"""

import difflib
import json
import re
from html.parser import HTMLParser

FORM_NAME = "View_Ticketform"
DIALOG_FORM_NAME = "newticketdialogform"
NEW_TICKET_FORM_NAME = "newticketform"
LOGIN_ROUTE = "/Core/Default/Login"
DEPT_FIELD = "gendepartmentid"
OWNER_FIELD = "genownerstaffid"


def _name_id_map(pairs):
    """Build ``{display name: id}`` from ``(id, name)`` pairs.

    Grouping is case- and space-insensitive; the key keeps the first-seen
    display form. A name with one id maps to that id, a repeated name to a
    tuple of every id in source order.
    """
    grouped = {}
    order = []
    for sid, name in pairs:
        label = name.strip()
        key = label.casefold()
        if key not in grouped:
            grouped[key] = [label, []]
            order.append(key)
        grouped[key][1].append(str(sid))
    result = {}
    for key in order:
        label, ids = grouped[key]
        result[label] = ids[0] if len(ids) == 1 else tuple(ids)
    return result


def parse_online_staff(text):
    """Map the OnlineStaff ``onlineusersarray`` JSON to ``{full name: id}``.

    Raises ``ValueError`` when the body has no ``onlineusersarray`` (an error
    or session-expired body must never parse as a staff list).
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        data = json.loads(text[start:end + 1])
    if not isinstance(data, dict) or "onlineusersarray" not in data:
        raise ValueError("OnlineStaff response has no onlineusersarray")
    arr = data["onlineusersarray"]
    if isinstance(arr, dict):
        # The live server sends {"<id>": {"fullname": "<team>; <name>"}}; the
        # recon notes showed {"<id>": "<name>"}. Accept both, keep the label whole.
        pairs = []
        for sid, entry in arr.items():
            name = entry.get("fullname") if isinstance(entry, dict) else entry
            if isinstance(name, str):
                pairs.append((sid, name))
        return _name_id_map(pairs)
    if isinstance(arr, list):
        pairs = []
        for item in arr:
            if isinstance(item, dict):
                sid = item.get("id", item.get("staffid"))
                name = item.get("name", item.get("fullname"))
                if sid is not None and name is not None:
                    pairs.append((sid, name))
        return _name_id_map(pairs)
    raise ValueError("onlineusersarray is not an object")


class _TicketViewParser(HTMLParser):
    """Collect the submittable controls of one named form (``View_Ticketform`` by default).

    ``radios`` also collects each checked radio button, which the Update form
    never carries but the New Ticket forms do. Every select's options and
    selected value land in ``selects`` as well, for name lookups.
    """

    def __init__(self, form_name=FORM_NAME, radios=False):
        super().__init__(convert_charrefs=True)
        self.form_name = form_name
        self.radios = radios
        self.found = False
        self.in_form = False
        self.action = ""
        self.fields = {}
        self.selects = {}
        self.departments = {}
        self.owners = {}
        self.selected_department = None
        self.selected_owner = None
        self._select = None
        self._option = None
        self._textarea = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            if not self.found and attrs.get("name", attrs.get("id", "")) == self.form_name:
                self.found = True
                self.in_form = True
                action = attrs.get("action", "")
                self.action = action.strip() if action else ""
            return
        if not self.in_form or "disabled" in attrs:
            return
        if tag == "input":
            self._handle_input(attrs)
        elif tag == "select":
            if attrs.get("name"):
                self._select = {"name": attrs["name"], "options": [], "selected": None}
        elif tag == "option" and self._select is not None:
            self._option = {
                "value": attrs.get("value"),
                "selected": "selected" in attrs,
                "text": [],
            }
        elif tag == "textarea":
            if attrs.get("name"):
                self._textarea = {"name": attrs["name"], "chunks": []}

    def handle_data(self, data):
        if self._option is not None:
            self._option["text"].append(data)
        if self._textarea is not None:
            self._textarea["chunks"].append(data)

    def handle_endtag(self, tag):
        if tag == "form" and self.in_form:
            self.in_form = False
        if not self.found:
            return
        if tag == "option" and self._option is not None and self._select is not None:
            text = re.sub(r"\s+", " ", "".join(self._option["text"])).strip()
            value = self._option["value"] if self._option["value"] is not None else text
            self._select["options"].append((value, text))
            if self._option["selected"]:
                self._select["selected"] = value
            self._option = None
        elif tag == "select" and self._select is not None:
            sel = self._select
            value = sel["selected"]
            if value is None and sel["options"]:
                value = sel["options"][0][0]
            if value is not None:
                self.fields[sel["name"]] = value
            self.selects[sel["name"]] = {"options": list(sel["options"]), "selected": value}
            if sel["name"] == DEPT_FIELD:
                self.departments = _name_id_map([(v, t) for v, t in sel["options"]])
                self.selected_department = value
            elif sel["name"] == OWNER_FIELD:
                self.owners = _name_id_map([(v, t) for v, t in sel["options"]])
                self.selected_owner = value
            self._select = None
        elif tag == "textarea" and self._textarea is not None:
            self.fields[self._textarea["name"]] = "".join(self._textarea["chunks"])
            self._textarea = None

    def _handle_input(self, attrs):
        name = attrs.get("name", "")
        if not name:
            return
        kind = attrs.get("type") or "text"
        kind = kind.strip().lower()
        if kind in ("hidden", "text"):
            value = attrs.get("value", "")
            self.fields[name] = value if value is not None else ""
        elif kind == "checkbox" and "checked" in attrs:
            value = attrs.get("value", "on")
            self.fields[name] = value if value is not None else "on"
        elif kind == "radio" and self.radios and "checked" in attrs:
            value = attrs.get("value", "on")
            self.fields[name] = value if value is not None else "on"


def parse_ticket_view(html):
    """Parse the ``View_Ticketform`` form in ``html``.

    Returns ``{"action", "fields", "departments", "owners",
    "selected_department", "selected_owner"}`` where ``fields`` holds every
    value a submit would carry, in form order. Raises ``ValueError`` when the
    form is absent.
    """
    parser = _TicketViewParser()
    parser.feed(html)
    parser.close()
    if not parser.found:
        raise ValueError("View_Ticketform not found")
    return {
        "action": parser.action,
        "fields": parser.fields,
        "departments": parser.departments,
        "owners": parser.owners,
        "selected_department": parser.selected_department,
        "selected_owner": parser.selected_owner,
    }


def _select_maps(parser):
    """``{select name: {"options": {label: id}, "selected": value}}`` for every select parsed."""
    return {
        name: {"options": _name_id_map([(v, t) for v, t in sel["options"]]), "selected": sel["selected"]}
        for name, sel in parser.selects.items()
    }


def parse_new_ticket_dialog(html):
    """Parse the first New Ticket fetch, the ``newticketdialogform`` department dialog.

    Returns ``{"action", "fields", "departments", "selected_department"}``:
    ``fields`` holds ``csrfhash`` and the checked ``tickettype`` radio, and
    ``departments`` maps each department label to its id. Raises ``ValueError``
    when the form, its ``departmentid`` select or its ``csrfhash`` is absent.
    """
    parser = _TicketViewParser(form_name=DIALOG_FORM_NAME, radios=True)
    parser.feed(html)
    parser.close()
    if not parser.found:
        raise ValueError(f"{DIALOG_FORM_NAME} not found")
    if "departmentid" not in parser.selects:
        raise ValueError(f"{DIALOG_FORM_NAME} has no departmentid select")
    if not parser.fields.get("csrfhash"):
        raise ValueError(f"{DIALOG_FORM_NAME} has no csrfhash")
    maps = _select_maps(parser)["departmentid"]
    return {
        "action": parser.action,
        "fields": parser.fields,
        "departments": maps["options"],
        "selected_department": maps["selected"],
    }


def parse_new_ticket_form(html):
    """Parse the second New Ticket fetch, the full ``newticketform``.

    Returns ``{"action", "fields", "selects"}``: ``fields`` is every value a
    send would carry, in form order (checked radios and checkboxes included,
    file inputs left out), and ``selects`` maps each select's name to its
    ``options`` (``{label: id}``) and ``selected`` id. Raises ``ValueError``
    when the form is absent.
    """
    parser = _TicketViewParser(form_name=NEW_TICKET_FORM_NAME, radios=True)
    parser.feed(html)
    parser.close()
    if not parser.found:
        raise ValueError(f"{NEW_TICKET_FORM_NAME} not found")
    return {"action": parser.action, "fields": parser.fields, "selects": _select_maps(parser)}


class _SelectedOptionProbe(HTMLParser):
    """Record the selected ``(value, label)`` of one ``<select name>``."""

    def __init__(self, field):
        super().__init__(convert_charrefs=True)
        self.field = field
        self.in_select = False
        self.options = []
        self.selected = None
        self._text = None
        self._value = None
        self._is_selected = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select" and attrs.get("name") == self.field:
            self.in_select = True
            return
        if self.in_select and tag == "option":
            self._value = attrs.get("value")
            self._is_selected = "selected" in attrs
            self._text = []

    def handle_data(self, data):
        if self.in_select and self._text is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "select" and self.in_select:
            self.in_select = False
        if self.in_select and tag == "option" and self._text is not None:
            label = re.sub(r"\s+", " ", "".join(self._text)).strip()
            value = self._value if self._value is not None else label
            self.options.append((value, label))
            if self._is_selected:
                self.selected = (value, label)
            self._text = None


def selected_option(html, field):
    """Return ``(value, label)`` of the selected option for ``field``'s select.

    Falls back to the first option when none is marked selected; returns
    ``(None, None)`` when the select is absent.
    """
    probe = _SelectedOptionProbe(field)
    probe.feed(html or "")
    probe.close()
    if probe.selected is not None:
        return probe.selected
    if probe.options:
        return probe.options[0]
    return (None, None)


class _LoginProbe(HTMLParser):
    """Record each form's action plus its input names and ``_ca`` value."""

    def __init__(self):
        super().__init__()
        self.forms = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            action = attrs.get("action", "")
            self._current = {"action": action if action else "", "names": set(), "ca": ""}
            self.forms.append(self._current)
        elif tag == "input" and self._current is not None:
            name = attrs.get("name", "")
            if name:
                self._current["names"].add(name)
                if name == "_ca":
                    self._current["ca"] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self._current = None


def is_login_form(html):
    """Return True when ``html`` holds the staff login form."""
    probe = _LoginProbe()
    probe.feed(html)
    probe.close()
    for form in probe.forms:
        if LOGIN_ROUTE in form["action"]:
            return True
        if {"username", "password"} <= form["names"] and form["ca"] == "login":
            return True
    return False


def _word_matches(name, options):
    """Keys of ``options`` that hold every word of ``name``, whole and in order.

    Real Kayako labels carry a team before the name and sometimes a nickname
    after it (``Sales Team; Jane Doe (JD)``), so the plain name is only a run
    of words inside the label. Whole words only: ``Jan`` never matches ``Jane``.
    """
    want = re.findall(r"\w+", name.casefold())
    if not want:
        return []
    size = len(want)
    hits = []
    for key in options:
        words = re.findall(r"\w+", key.casefold())
        if any(words[i:i + size] == want for i in range(len(words) - size + 1)):
            hits.append(key)
    return hits


def _close_matches(name, options, n=3, cutoff=0.6):
    """Up to ``n`` keys of ``options`` holding a run of words that looks like ``name``.

    Scoring the typed name against the whole label would sink every long
    ``Team; Name (Nick)`` label, so it is scored against each run of the same
    number of words inside the label and the best run counts.
    """
    want_words = re.findall(r"\w+", name.casefold())
    if not want_words:
        return []
    want = " ".join(want_words)
    size = len(want_words)
    scored = []
    for key in options:
        words = re.findall(r"\w+", key.casefold())
        runs = [" ".join(words[i:i + size]) for i in range(max(len(words) - size, 0) + 1)]
        best = max((difflib.SequenceMatcher(None, want, run).ratio() for run in runs), default=0.0)
        if best >= cutoff:
            scored.append((best, key))
    scored.sort(key=lambda pair: -pair[0])
    return [key for _, key in scored[:n]]


def resolve(name, options, what="staff name"):
    """Return the id for ``name`` in ``options`` (``{display name: id}``).

    An exact match (ignoring case and surrounding space) wins. Failing that, a
    name matches a label that contains its words whole and in order. A name
    holding more than one id, a name matching several labels, or an unknown
    name raise ``LookupError``; the message names each id or label or, for
    unknown names, up to three close matches. ``what`` words those messages
    (``"department"``, ``"owner"``) for options that are not staff.
    """
    want = name.strip().casefold()
    hits = [key for key in options if key.strip().casefold() == want]
    if not hits:
        hits = _word_matches(name, options)
    if len(hits) > 1:
        detail = ", ".join(f"{key!r} -> {options[key]!r}" for key in hits[:3])
        extra = ", ..." if len(hits) > 3 else ""
        raise LookupError(f"Ambiguous {what} {name!r}: {len(hits)} matches ({detail}{extra})")
    if hits:
        value = options[hits[0]]
        if isinstance(value, (tuple, list)):
            if len(value) == 1:
                return value[0]
            ids = ", ".join(value)
            raise LookupError(f"Ambiguous {what} {name!r}: {len(value)} options share this name (ids {ids})")
        return value
    suggestions = _close_matches(name, options)
    if suggestions:
        raise LookupError(f"Unknown {what} {name!r}. Did you mean: {', '.join(suggestions)}?")
    raise LookupError(f"Unknown {what} {name!r} (no close matches)")
