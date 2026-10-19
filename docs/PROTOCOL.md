# Protocol notes: the Kayako SWIFT staff panel

What `kayako-filer` relies on, as observed on the wire. This is evidence from browser captures of one Kayako SWIFT staff control panel, product `case`, version `4.69.0`, served by Apache over HTTP/2. Other versions may differ. `SPEC.md` says what the tool does with it.

Everything below uses the placeholder base `https://helpdesk.example.test/support/staff/index.php?`. Routes are the query string after `index.php?`, in the form `/App/Controller/Action`. `swiftpath` is `https://helpdesk.example.test/support/` and `cparea` is `staff`.

## Auth

Login is a classic form POST, and it does not answer with an HTTP redirect.

- `POST index.php?/Core/Default/Login`, `Content-Type: application/x-www-form-urlencoded`.
- Fields: `username`, `password`, `submitbutton=Login`, `remember` (`0` or `1`), `languagecode=en-us`, `_ca=login`, `_redirectAction` (empty).
- The login form carries no CSRF field.
- Success is **HTTP 200** plus JavaScript: a `window.location.href` to `/Base/Home/Index`, with a 2-second `setTimeout` fallback. A script has to read that body. It must not wait for a 302.
- A 200 that still shows the login form is a failure.
- The session cookie is `SWIFT_sessionid20`, `path=/`, **httponly**. `SWIFT_rememberusername20` and `SWIFT_rememberpassword20` are deleted on a login with `remember=0`.
- Logout is `GET index.php?/Core/Default/Logout`. It returns the login form ("Logged out successfully") and expires those three `SWIFT_*` cookies.

Other cookies on the page (`documentheight`, `jqCookieJar_options`) are client UI state, not the session.

## Ticket writes carry a token

Unlike login, every ticket form carries a hidden `csrfhash` field plus `isajax=1`. That holds for the manage grid, the ticket view form and both New Ticket dialog steps. A client must `GET` the exact form first and submit the fresh pair back. A captured value does not replay.

Resolve departments, staff and statuses by name on every run. The numeric ids differ per installation and change over time.

## Manage grid

`GET index.php?/Tickets/Manage/Index` renders the ticket table plus the mass-action panel in the same HTML.

- The grid form posts to `.../Tickets/Manage/Index//` with `onsubmit return false`, so submissions go through the grid JavaScript rather than a plain submit.
- The mass panel selects are `departmentid` (move), `staffid` (assign), `tickettypeid`, `ticketstatusid`, `ticketpriorityid`, `bayescategoryid` (spam training), `ticketlinktypeid` and `ticketflagid`, plus add and remove tag controls (`/Base/Tags/QuickSearch`).
- Row actions are JavaScript calls: Spam, Watch and Merge go through `GridMassAction` with per-action hashes, and Reply opens `UICreateWindowGrid` (the "Mass Reply" dialog).
- `GET index.php?/Tickets/Manage/Preview/<id>` returns the ticket's body HTML, which is a cheap way to re-read a ticket.

## Ticket view

`GET index.php?/Tickets/Ticket/View/<id>/inbox/-1/-1/-1` (as an XHR) returns the full view: the sidebar, the ticket form and the first post.

- The form is `View_Ticketform`, `method=post`, `enctype=multipart/form-data`. Its default action is the General submit: `.../Tickets/Ticket/GeneralSubmit/<id>/inbox/-1/-1/-1/0`.
- General fields: `gendepartmentid`, `genownerstaffid`, `gentickettypeid`, `genticketstatusid`, `genticketpriorityid`, plus tags.
- Hidden on the form: `isajax=1` and `csrfhash`.

Toolbar routes follow the same id and context-path pattern:

| Button | Route |
|---|---|
| Update | submits the form as-is (General) |
| Add Note | `.../Tickets/Ticket/AddNote/<id>` (dialog) |
| Surrender | `.../Tickets/Ticket/Surrender/<id>/...` |
| Assign | `.../Tickets/Ticket/Dispatch/<id>/...` ("Assign Ticket" dialog) |
| Duplicate | `.../Tickets/Ticket/DuplicateTicket/<id>` (dialog) |
| Watch | `.../Tickets/Ticket/Watch/<id>/...` |
| Flag | `.../Tickets/Ticket/Flag/<id>/<n>/...` (`n` is 1 to 6) |
| Prev / Next | `.../Tickets/Ticket/Jump/previous\|next/<id>/...` |

The Reply tab rewrites the same form's action in JavaScript: Send goes to `.../Tickets/Ticket/ReplySubmit/<id>/...` and Save as Draft to `.../Tickets/Ticket/SaveAsDraft/<id>/...`. Its fields are `replyfrom`, To (fixed), a CC tag control (`/Tickets/Ajax/SearchEmail`), `replydepartmentid`, `replyownerstaffid`, `replytickettypeid`, `replyticketstatusid`, `replyticketpriorityid`, `replycontents`, the checkboxes `optreply_sendemail`, `optreply_private`, `optreply_addkb` and `optreply_watch`, billing time fields, notes, BCC, due dates and attachments (`replyattachments[]`).

Forward and Follow-up are separate tabs with their own URLs, which end in an opaque per-ticket token segment (`.../Forward/<id>/.../0/<token>`, `.../FollowUp/<id>/.../0/0/<token>`). The Follow-up fragment's Update button posts the view form to `.../Tickets/Ticket/FollowUpSubmit/<id>/...`, with `fufollowuptype` (`minutes|hours|days|weeks|months|custom`) and `fufollowupvalue`.

## New ticket

Two fetch steps build the form, and the send is a third request.

1. `POST .../Tickets/Ticket/NewTicket/` with an empty body (as an XHR) returns a dialog form with `tickettype` (`sendmail` means you author the ticket yourself, `user` means you file it on a user's behalf) and a short `departmentid` list.
2. `POST .../Tickets/Ticket/NewTicketForm` with `tickettype`, `departmentid` and `csrfhash` returns the full form. The send rewrites its action to `.../Tickets/Ticket/NewTicketSubmit/sendmail`.
3. The send is a `multipart/form-data` POST to that URL. A success is an HTTP 200 whose body is the new ticket's own view page. The new id appears in its `_incomingRequestHistoryChunk = "/Tickets/Ticket/View/<id>/..."` line and in its `TICKET ID` block.

The New Ticket form's fields mirror the reply tab: `newticketfrom`, `newticketsubject`, the To and CC tag controls, `newticketownerstaffid`, type, status and priority selects, `newticketcontents`, the same checkbox set, notes, BCC, due dates and attachments. It adds a hidden `newticketdepartmentid` and a recurrence block, which defaults to not recurring.

Recipients travel as paired fields, `taginputcheck_newticketto[]` and `containertaginput_newticketto[]` (and `...cc[]`), repeated per address.

## Staff lookup

`GET index.php?/Base/AJAX/OnlineStaff` returns JSON with an `onlineusersarray` of staff id to full name. It may list only staff who are online, so the owner select on the ticket view is the authoritative list for a department. `GET .../Tickets/Macro/GetMenu/<deptid>` returns a department's macro list.

## Noise to ignore

`GET /support/cron/index.php?/Base/CronManager/Execute` returns a 1x1 gif. Compressor CSS and JavaScript, menu JavaScript, theme images and the favicon are page chrome and are not part of any write.

## Gotchas

- **Fetch the form, then post it.** Scrape a fresh `csrfhash` and `isajax` from the exact form you are about to submit (view, dialog or grid), in the same run.
- **Reuse the links the view returns.** Forward and Follow-up URLs end in a per-ticket token. Fetch the view first and use its links. Never build one.
- **`multipart/form-data`.** The view and New Ticket forms declare multipart, because of attachments. Encode a replay accordingly, even with no file attached.
- **Login success is not a 302.** Treat a 200 that still shows the login form as a failure.
- **The session cookie is httponly.** Keep the cookie jar. The value is not readable from page JavaScript.
- **Keep department and owner consistent.** Changing the department refreshes the status, type and owner selects (`UpdateTicket*Div`). Submit an owner id from the same department's list.
- **Permission and state checks exist.** A ticket may refuse a change by status, department or role, and the response can be a 200 either way. Read the body before you call a write a success.
- **Never log secrets.** Raw captures contain a live password, a live `SWIFT_sessionid20` and live `csrfhash` values. Keep none of them.

## Provenance

These notes come from browser (HAR) captures of the logout page and the login POST, the manage grid, a ticket view with its reply and follow-up fragments, a preview, the two New Ticket form-fetch requests and the OnlineStaff JSON, plus the saved responses of real New Ticket sends made by this tool. The raw captures are not kept, because they contain credentials.
