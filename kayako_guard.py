"""Wire-level send guard (SPEC decision 1, AD-1, A4).

A transport adapter mounted on the session. It is the last stop before the
network, so it sees the final prepared request, whatever route string the
calling code started from. It refuses, before any I/O:

- any request to another origin than the configured base URL;
- any request whose URL, read the way the server may read it (percent-decoded
  until stable, lowercased, every non-alphanumeric dropped), contains a submit
  word: ``submit``, ``dispatch``, ``savedraft`` or ``saveasdraft``. Encoded,
  dot-segment, mixed-case, backslash and separator variants all collapse to
  the same text, so none of them gets past;
- any write (anything but GET or HEAD) that is not a POST to an exact URL on
  the write allowlist.

The one exception is a single send URL, handed in only by the ``file``
command with ``--send``: a POST to exactly that URL passes, nothing else
containing a submit word does.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from requests.adapters import HTTPAdapter

FORBIDDEN_WORDS = ("submit", "dispatch", "savedraft", "saveasdraft")


class SendRefused(Exception):
    """A request was refused before it left the machine (AD-1)."""


def routing_text(url):
    """The URL as the server may read it: decoded until stable, lowercase, letters and digits only."""
    text = str(url)
    while True:
        decoded = unquote(text)
        if decoded == text:
            break
        text = decoded
    return re.sub(r"[^0-9a-z]+", "", text.lower())


def _origin(url):
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return None
    port = parts.port or {"http": 80, "https": 443}.get((parts.scheme or "").lower())
    return ((parts.scheme or "").lower(), (parts.hostname or "").lower(), port)


class GuardAdapter(HTTPAdapter):
    """Refuse a request that must not leave; pass every other one on unchanged.

    ``base_url`` pins the origin. ``allowed_writes`` is the exact set of URLs
    a POST may target; PUT, DELETE and PATCH are refused everywhere. ``send_url`` is the one submit URL a POST
    may reach, or ``None`` to refuse every submit route. ``inner_send`` lets
    a test stand in for the network.
    """

    def __init__(self, base_url, allowed_writes, send_url=None, inner_send=None):
        super().__init__()
        self._origin = _origin(base_url)
        self._writes = frozenset(allowed_writes)
        self._send_url = send_url
        self._inner_send = inner_send

    def refusal(self, method, url):
        """Why this request is refused, or ``None`` when it may go."""
        method = (method or "").upper()
        url = str(url)
        if _origin(url) != self._origin:
            return f"{method} {url} refused: not the configured server"
        if self._send_url is not None and method == "POST" and url == self._send_url:
            return None
        if any(word in routing_text(url) for word in FORBIDDEN_WORDS):
            return f"{method} {url} refused: submit route (AD-1)"
        if method not in ("GET", "HEAD") and not (method == "POST" and url in self._writes):
            return f"{method} {url} refused: not on the write allowlist"
        return None

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        why = self.refusal(request.method, request.url)
        if why:
            raise SendRefused(why)
        kwargs = {"stream": stream, "timeout": timeout, "verify": verify, "cert": cert, "proxies": proxies}
        if self._inner_send is not None:
            return self._inner_send(request, **kwargs)
        return super().send(request, **kwargs)


def arm(session, base_url, allowed_writes, send_url=None, inner_send=None):
    """Mount the guard on ``session`` for http and https; return it.

    A session without ``mount`` (a test's fake, which never touches the
    network) is left alone and ``None`` is returned.
    """
    mount = getattr(session, "mount", None)
    if mount is None:
        return None
    adapter = GuardAdapter(base_url, allowed_writes, send_url=send_url, inner_send=inner_send)
    mount("https://", adapter)
    mount("http://", adapter)
    return adapter
