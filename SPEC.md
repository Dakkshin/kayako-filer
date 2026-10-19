# SPEC: kayako-filer — an owner change up to the send (v1), and new tickets (v2)

Sole source for what is built. `docs/PROTOCOL.md` holds the protocol notes behind it (what was observed on the wire).

## Objective

Stop clicking through the Kayako staff UI to hand a ticket to someone else. `kayako_filer.py plan <ticket> --to "<name>"` shows the ticket's current owner and the resolved new one. `kayako_filer.py log <ticket> --to "<name>"` logs in, fetches the ticket view fresh and prints the exact Update (General) submit it would send with the owner changed. Nothing is sent: the owner-change send waits for one real submit capture that confirms the payload.

v2: create a new ticket from a small file. `ticket-plan FILE` resolves every name against the live New Ticket form, `ticket-log FILE` prints the exact body, and `file FILE --send --confirm CODE` creates the ticket. The by-hand capture that decision 1 required is replaced for new tickets by the tool's own first send: a TEST ticket to your own mailbox, with your explicit yes. The owner-change send is unchanged and still not built.

## Scope

In: config, credentials, login and logout, the staff list, ticket-view parsing, `login`, `staff`, `view`, `plan`, `log`, docs and a capture checklist. v2: the ticket file, the New Ticket dialog and form parsers, `ticket-plan`, `ticket-log`, `file`, the wire-level guard and the recipient allowlist.
Out: a send of the owner change (Update), Assign, Forward, replies, bulk changes on the grid, attachments and recurrence on new tickets, `tickettype=user` (filing on a user's behalf).

## Decisions

1. **No submit, with one exception.** No code requests `GeneralSubmit`, `ReplySubmit`, `Dispatch`, `FollowUpSubmit`, `SaveAsDraft` or any other submit route. A test proves `log`, `ticket-plan` and `ticket-log` issue none. The one exception (v2) is `POST /Tickets/Ticket/NewTicketSubmit/sendmail`, requested only by `file --send` under decision 8.
2. **GET before POST.** A body's `csrfhash` and `isajax` come from the same run's GET of the same form. They are never stored.
3. **No stored session.** Log in per run and log out at the end. `SWIFT_sessionid20` never touches disk.
4. **Names, not ids.** The new owner is resolved from the ticket view's own owner select, which lists the ticket department's staff. `Base/AJAX/OnlineStaff` is a cross-check only, since it may list online staff alone.
5. **Modules for parallel lanes.** `kayako_filer.py` holds the CLI, config, credentials and auth; `kayako_parse.py` the parsers; `kayako_payload.py` the body; `kayako_guard.py` the wire-level guard; `kayako_ticket.py` the ticket file.
6. **Offline tests.** pytest with synthetic fixtures in the documented shape and a fake session. No test touches the network.
7. **The guard sits on the wire.** A transport adapter mounted on every real session refuses, before any I/O, a request to another origin, a URL that names a submit word once decoded and normalized, and any write that is not a POST to an exact URL on an allowlist (login and the two New Ticket form fetches); PUT, DELETE and PATCH are refused everywhere. It sees the final prepared request, so encoded, dot-segment, mixed-case and direct-send routes all fail the same way.
8. **A send needs four things.** (a) `--send`; (b) `--confirm CODE`, the code a dry run of this exact ticket printed, which changes with any field the send carries; (c) every recipient (`to` and `cc`) on `[send] allow_to` in `kayako.toml`, which is empty by default and so refuses every send; (d) the guard armed for the one send URL. A dry run is the default. A send never retries: a network failure after the request is exit 3, "outcome unknown", and you check the Tickets grid before trying again.

## Contracts

### C1. Config (`kayako.toml`)

`[server] base_url = "https://helpdesk.example.test/support/staff/index.php?"`, `verify_tls = true`. A route such as `/Core/Default/Login` joins by plain concatenation, never `urljoin`, because the base ends in a query string.

### C2. Credentials

`KAYAKO_USER` / `KAYAKO_PASS`, then `./.env`, then `~/.config/kayako/creds` (warn unless mode 600). The password is never printed.

### C3. Login and logout

`POST /Core/Default/Login` form-urlencoded: `username`, `password`, `submitbutton=Login`, `remember=0`, `languagecode=en-us`, `_ca=login`, `_redirectAction=`. Success is HTTP 200 with a redirect to `/Base/Home/Index` in the body and no login form. A 200 that still shows the login form is a failure. Logout is `GET /Core/Default/Logout`, always run at the end.

### C4. Parsers (`kayako_parse.py`)

- `parse_online_staff(text)` → `{full name: id}` from `onlineusersarray`.
- `parse_ticket_view(html)` → the `View_Ticketform` action, and every field it would submit as-is: hidden and text inputs with their values, each select's selected value, checked checkboxes, textareas. Plus the option lists of `gendepartmentid` and `genownerstaffid` as `{name: id}` with the selected ones.
- `is_login_form(html)` → true when the page is the login form.

### C5. Update body (`kayako_payload.py`)

`build_general_body(view, new_owner_id)` returns every field of C4 in form order, with `genownerstaffid` set to the new owner and `csrfhash` and `isajax` from the same fetch. Sent as `multipart/form-data` once `file` exists. Which fields the real Update actually carries is unconfirmed: `log` prints all of them.

### C6. Commands

- `login` — log in, report OK or FAILED, log out.
- `staff [--grep NAME]` — the OnlineStaff names and ids.
- `view TICKET [--save]` — the ticket's department, owner, type, status, priority and the owner choices. `--save` writes the HTML to `out/` with `csrfhash` masked.
- `plan TICKET --to NAME [--view-file FILE]` — current owner → resolved new owner. Exit 1 when the name is unknown, ambiguous, or already the owner. `--view-file` works offline.
- `log TICKET --to NAME` — log in, GET the view, build C5, print it with `csrfhash` masked, write `out/general_body_<ticket>_<stamp>.json` (masked), log out. Sends nothing.

- `ticket-plan FILE [--dialog-file F --form-file F]` — C10.
- `ticket-log FILE [--dialog-file F --form-file F]` — C10.
- `file FILE [--send --confirm CODE] [--dialog-file F --form-file F]` — C10.

Exit codes: 0 done, 1 invalid or refused, 2 usage or config, 3 a send's outcome is unknown.

### C7. Ticket file (`kayako_ticket.py`)

One TOML file per ticket. Required: `department`, `owner`, `to` (an address or a list), `subject` (one line, at most 255 characters), `body`. Optional: `type`, `status`, `priority`, `cc`, `send_email`. Department, owner, type, status and priority are names, never ids. An unknown key is an error, so a typo cannot drop a field. A bad address, a missing key or a non-bool `send_email` is an error, exit 2. `tickets/example.toml` is the committed template; every other file in `tickets/` is git-ignored.

### C8. New Ticket parsers (`kayako_parse.py`)

- `parse_new_ticket_dialog(html)` reads form `newticketdialogform`: the `csrfhash`, the checked `tickettype` radio and the `departmentid` options as `{label: id}`. Raises `ValueError` if the form, the select or the `csrfhash` is missing.
- `parse_new_ticket_form(html)` reads form `newticketform`: every field a send would carry in form order (hidden and text inputs, each select's selected or first value, checked checkboxes **and checked radios**, textareas; file inputs left out), plus every select as `{"options": {label: id}, "selected": id}`.
- Labels are whitespace-normalized (the server uses non-breaking spaces). `resolve(name, options, what)` matches an exact label first, then a run of whole words inside a label (`Jane Doe` finds `Sales Team; Jane Doe (JD)`), and refuses several matches with each named.

### C9. New Ticket body (`kayako_payload.py`)

`build_new_ticket_body(form, resolved)` returns every field of C8 in form order with the ticket applied: `newticketsubject`, `newticketcontents` (CRLF), `newticketownerstaffid`, the type, status and priority ids when the file names them, `optnewticket_sendemail` when `send_email` is set, and the recipients as `taginputcheck_newticketto[]` and `containertaginput_newticketto[]` (and `...cc[]`), repeated per address. `csrfhash`, `isajax` and `newticketdepartmentid` come from the same fetch; a form for another department, or one missing a needed field, is refused by name. A tag input still holding its placeholder text is blanked. The recipient encoding was inferred from the other forms' tag controls and the first real sends confirmed it. Sent as `multipart/form-data` with text parts (no filename).

### C10. New ticket commands

- `ticket-plan` — log in, `POST /Tickets/Ticket/NewTicket/` (the dialog), resolve the department, `POST /Tickets/Ticket/NewTicketForm` for it, resolve every other name, print department, owner, type, status, priority, recipients, email flag, subject, body size and the confirm code. Form defaults show as `(form default)`. Exit 1 for an unknown or ambiguous name, with close matches. `--dialog-file` and `--form-file` (given together) read saved copies and touch no network and no credentials.
- `ticket-log` — `ticket-plan` plus every field of the C9 body, `csrfhash` and any session id masked, and `out/new_ticket_body_<stamp>.json`. Sends nothing.
- `file` — `ticket-log`, then, without `--send`, "dry run: nothing sent" and the exact command to send. With `--send --confirm CODE`: refuse before logging in unless every recipient is on `allow_to`; refuse if the code does not match; otherwise `POST` the body to `/Tickets/Ticket/NewTicketSubmit/sendmail` once, without following redirects, and print the HTTP status, any redirect and a short masked excerpt of the response, saved masked to `out/new_ticket_response_<stamp>.txt`. It states that a success status is not proof a ticket exists. `--send` with saved forms is a usage error.

### C11. Guard (`kayako_guard.py`)

`GuardAdapter` (a `requests` transport adapter) is mounted on every real session. Refusals raise `SendRefused` before any I/O. See decision 7. `file --send` alone arms the one send URL; the match is exact string equality on the prepared URL and the POST method.

## Success criteria

- [x] `plan` against a saved view prints current → new and exits 0; an unknown or ambiguous name exits 1 with close matches.
- [x] `log` against a fake session prints every form field with the new owner, masks `csrfhash`, logs out, and the session records no request to any submit route.
- [x] No test or command writes a session cookie to disk.
- [x] `pytest` and `ruff check .` are green; the README documents the commands.
- [x] v2: `ticket-plan` and `ticket-log` against saved fixtures and a fake session print the resolved ticket and the full masked body, exit 1 with close matches on an unknown name, and the session records no submit route.
- [x] v2: `file` without `--send` posts no submit; with `--send` it refuses a wrong code, a missing or non-listed recipient and a saved-forms run, and otherwise sends exactly one multipart POST to the one URL.
- [x] v2: on a real session with the guard mounted, every submit-route variant is refused on the wire and the send URL passes only when armed.
- [x] v2: real sends have created tickets on a live Kayako 4.69.0 staff panel (two so far), and their saved responses were compared with this spec (see the answered questions below).

## Open questions

Update (owner change), which needs a capture of the real submit:

1. Which fields the real Update submit carries, and whether it is multipart as the form declares.
2. What a successful and a refused submit return (HTTP 200 either way; the body tells).
3. Whether changing the owner alone is accepted, or the department has to move with it.

New ticket, from the first real sends:

4. Answered: the recipient encoding in C9 is what the server reads, since the created tickets show the recipient.
5. Partly answered: a success is an HTTP 200 whose body is the new ticket's own view page, and the id is in its `_incomingRequestHistoryChunk = "/Tickets/Ticket/View/<id>/..."` line and its `TICKET ID` block. The tool does not extract the id yet. What a refusal looks like is still unknown.
6. Partly answered: the form's default type (`SELECT TYPE`, id 1) was accepted. Whether a blank tag input or an unset `newticketfrom` matter is still unknown.
7. Still unknown: whether `send_email = false` creates the ticket without emailing anyone, which would make a TEST send safer.
