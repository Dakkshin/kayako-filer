# kayako-filer

A small command-line tool that files tickets in a Kayako SWIFT staff panel from a text file, without clicking through the web UI. It sends the same requests the panel's own pages send, and it puts a dry run and a wire-level guard in front of every send.

> **Unofficial.** This project is not affiliated with or endorsed by Kayako. It talks to the staff control panel the way a browser does, and it was built and tested against **Kayako SWIFT 4.69.0**. Other versions may differ. Use it only on a helpdesk you are authorised to use, and read [Safety](#safety) before you send anything.

## Status

- **Read commands work.** `login` signs you in and out. `staff` lists staff. `view` shows one ticket. `plan` and `log` preview an owner change, which is a move of one ticket from its current owner to a new one. The owner-change send is **not built**; those two commands never send.
- **New tickets work.** `ticket-plan` shows what would be created. `ticket-log` shows the exact body. `file` does a dry run by default, and it sends only with `--send --confirm CODE`. Real sends have created tickets on a live 4.69.0 panel.
- The build detail is in [`SPEC.md`](SPEC.md). What was observed on the wire is in [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## Install

You need Python 3.11 or newer.

```sh
git clone https://github.com/Dakkshin/kayako-filer.git
cd kayako-filer
pip install -r requirements.txt
cp kayako.example.toml kayako.toml      # then set base_url to your own staff panel
python3 kayako_filer.py selftest        # offline checks, no network
```

`kayako.toml` is git-ignored. `base_url` is the staff control panel address ending in `index.php?`, for example `https://helpdesk.example.com/support/staff/index.php?`.

## Credentials

The tool reads credentials in this order. First it reads `KAYAKO_USER` and `KAYAKO_PASS` from the environment. Then it reads `./.env`. Then it reads `~/.config/kayako/creds`.

Keep that last file owner-read-only (mode 600). `.env` is git-ignored. Never commit a password. The tool never prints the password and never stores a session: it logs in at the start of a run and out at the end.

## New tickets

One ticket per TOML file, a small text file that names the department, the owner and the message. Copy the template and fill in your own. Your own files in `tickets/` stay untracked.

```toml
department = "Sales & Support - Accounts"
owner = "Jane Doe"
to = "jane.doe@example.test"
subject = "..."
body = """..."""
```

Names match what the live Kayako New Ticket form shows. `Jane Doe` finds `Sales Team; Jane Doe (JD)`, and a typo prints close matches. The optional keys are `type`, `status`, `priority`, `cc` and `send_email`.

Preview what would be created. `ticket-plan` signs in, opens the dialog and the form, and signs out. It submits nothing.

```sh
python3 kayako_filer.py ticket-plan tickets/my-ticket.toml
```

Inspect the exact body. `ticket-log` prints every field the send would carry, with secrets masked.

```sh
python3 kayako_filer.py ticket-log tickets/my-ticket.toml
```

Dry run the ticket. `file` without flags prints the confirm code for this exact ticket.

```sh
python3 kayako_filer.py file tickets/my-ticket.toml
```

Create the ticket. `file` with the code the dry run printed.

```sh
python3 kayako_filer.py file tickets/my-ticket.toml --send --confirm <code>
```

Pass `--dialog-file` together with `--form-file` to run a preview off saved copies of the two forms. That pair runs offline and touches no network. A send with saved copies is refused.

## Safety

A send is refused unless all four of these hold.

- You pass `--send`.
- You pass `--confirm CODE`, the code the dry run of this exact ticket printed. Any change to the ticket changes the code.
- Every `to` and `cc` address is listed in `[send] allow_to` in `kayako.toml`. The list starts empty, which refuses every send until you add an address.
- The live form was fetched. Saved forms never send.

On top of that, a guard sits on the wire. It is a transport adapter mounted on every real session, and it inspects the final prepared request before any I/O. It refuses a request to any other origin, any URL that names a submit route once decoded and normalised (`submit`, `dispatch`, `savedraft`), and any write that is not a POST to a short exact-URL allowlist. Only `file --send` arms the one send URL.

A send never retries. A network failure after the request exits 3, which means the outcome is unknown. Look in the Tickets grid for the subject before you try again.

**Your first send should be a test.** Put only your own address in `allow_to`, start the subject with `TEST`, and read the saved response in `out/`. Whether `send_email = false` suppresses the notification email is not confirmed, so assume the email goes out.

Exit codes: 0 done, 1 invalid or refused, 2 usage or config, 3 a send whose outcome is unknown.

## Owner change preview

Preview an owner move. `plan` shows the current owner and the resolved new one. `log` prints the Update body it would send. Neither sends.

```sh
python3 kayako_filer.py plan 1234567 --to "Jane Doe"
python3 kayako_filer.py log 1234567 --to "Jane Doe"
```

`staff`, `view` and `login` only read. Pass `--view-file FILE` to run `plan` off a saved copy of a ticket view.

## Development

```sh
pip install pytest ruff
python3 -m pytest        # offline: synthetic fixtures and a fake session, no test touches the network
ruff check .
```

Tests run against `kayako.example.toml`, never against your `kayako.toml`. Fixtures under `tests/fixtures/` are synthetic. If you add one, build it from a real page by replacing every name, address, id and token, and never commit a capture that holds real data, a password, a session cookie or a `csrfhash`.

## License

[MIT](LICENSE).
