"""Fetch the HTML of final URLs with a phone browser's user agent and report what matters for landing
page experience. Only the HTML is read: no JavaScript runs and nothing is rendered.

Only https URLs on the allowed domains (builder.toml `domains`) are opened, only while their host resolves
to public internet addresses (or to a VPN's fake-IP range, see FAKE_IP), and a redirect is followed only
while it stays on them. Each page gets
TIMEOUT seconds and each call TOTAL_SECONDS. Changes nothing in Google Ads. Never raises: a URL that
can't be checked gets an `error` instead."""

from __future__ import annotations

import ipaddress
import socket
import time
import unicodedata
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

from .config import _BAD_URL_CHAR

TIMEOUT = 20  # seconds per page: every request and all the reading, redirects included
TOTAL_SECONDS = 120  # per call, across all its URLs
MAX_BYTES = 2 * 1024 * 1024
CHUNK = 64 * 1024
MAX_REDIRECTS = 5
MAX_URLS = 25
MAX_KEYWORDS = 50
MAX_SHOWN = 500  # characters of a URL in a result
REDIRECTS = (301, 302, 303, 307, 308)
USER_AGENT = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
NOT_ALLOWED = "not opened: only https URLs on the allowed domains in builder.toml are checked"
TOO_SLOW = f"too slow: stopped at the time limit ({TIMEOUT} seconds per page, {TOTAL_SECONDS} per call)"
OUT_OF_TIME = f"not opened: time budget used up ({TOTAL_SECONDS} seconds per call); check it in another call"
OFF_DOMAIN = "redirects to a URL that is not on an allowed domain; not followed"
# A VPN client in fake-IP (TUN) mode answers DNS from the benchmarking range and proxies the traffic to the
# real host. Allowed, with a note: the real address can't be seen from here, but only the allowed domains
# are ever opened. Every other non-public address is still refused.
FAKE_IP = ipaddress.ip_network("198.18.0.0/15")
FAKE_IP_NOTE = "resolved through the VPN's fake-IP DNS (198.18.x.x)"
_URL_SAFE = "/:?#[]@!$&'()*+,;=%~"  # kept as they are when a URL is percent-encoded for the request


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # check_url follows redirects itself, after checking where they go


_OPENER = urllib.request.build_opener(_NoRedirects)


def _open(request, timeout):
    """The only place a URL is opened (tests replace it)."""
    return _OPENER.open(request, timeout=timeout)


def _resolve(host: str) -> list[str]:
    """Every address `host` resolves to (tests replace it)."""
    return [info[4][0] for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]


def _now() -> float:
    return time.monotonic()  # tests replace it with a clock they move


def _address_problem(host: str) -> tuple[str | None, str | None]:
    """(why `host` must not be opened, a note). The problem is None when every address it resolves to is
    on the public internet (no private, loopback, link-local or other reserved address: nothing on this
    Mac or its network) or in the VPN's FAKE_IP range; the note is FAKE_IP_NOTE when one of them is."""
    if not host:
        return "not opened: the URL has no host name", None
    try:
        addresses = _resolve(host)
    except OSError:
        return f"not opened: could not look up {host}", None
    if not addresses:
        return f"not opened: could not look up {host}", None
    fake_ip = False
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError:
            ip = None
        if ip is not None and ip in FAKE_IP:  # IPv4 only: an IPv6 address is never "in" an IPv4 network
            fake_ip = True
        elif ip is None or not ip.is_global:
            return f"not opened: {host} resolves to {_shown(address)}, which is not a public internet address", None
    return None, FAKE_IP_NOTE if fake_ip else None


def _encoded(url: str, encoding: str = "utf-8") -> str:
    """Percent-encoded for the request line; ASCII and already-encoded URLs stay as they are."""
    try:
        return quote(url, safe=_URL_SAFE, encoding=encoding)
    except UnicodeEncodeError:
        return quote(url, safe=_URL_SAFE)


def _shown(url: str) -> str:
    """A URL as a result shows it: no control, bidi or other invisible characters, at most MAX_SHOWN long."""
    text = "".join(ch for ch in _BAD_URL_CHAR.sub("", url) if unicodedata.category(ch)[0] != "C")
    return text if len(text) <= MAX_SHOWN else text[:MAX_SHOWN - 1] + "…"


def _host_of(url: str) -> str:
    """Only the host name of a URL that is not followed, with nothing but letters, digits and . - _ :"""
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        host = ""
    return "".join(ch for ch in host if ch.isalnum() or ch in ".-_:")[:253]


class _Page(HTMLParser):
    """The document's title, whether a mobile viewport is declared, and the visible text."""

    HIDDEN = {"head", "script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.viewport, self.text = "", False, []
        self._hidden, self._svg, self._in_title, self._title_done = 0, 0, False, False

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self._hidden = self._svg = 0  # a page may leave <head> open
        if tag == "meta":
            a = {k: (v or "") for k, v in attrs}
            content = a.get("content", "").replace(" ", "").lower()
            if a.get("name", "").lower() == "viewport" and "width=device-width" in content:
                self.viewport = True
        elif tag == "title":
            # an <svg> has titles of its own; only the document's first <title> counts
            self._in_title = not self._svg and not self._title_done
        elif tag in self.HIDDEN:
            self._hidden += 1
            self._svg += tag == "svg"

    def handle_endtag(self, tag):
        if tag == "title":
            if self._in_title:
                self._in_title, self._title_done = False, True
        elif tag in self.HIDDEN and self._hidden:
            self._hidden -= 1
            if tag == "svg" and self._svg:
                self._svg -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._hidden:
            self.text.append(data)


# Arabic-script spelling variants that mean the same word: tatweel (U+0640) and harakat (U+064B-U+065F,
# U+0670) are dropped, Persian yeh and keheh become Arabic yeh and kaf, and ZWNJ is a space.
_FOLD = str.maketrans({0x0640: None, **dict.fromkeys(range(0x064B, 0x0660)), 0x0670: None,
                       0x06CC: "ي", 0x06A9: "ك", 0x200C: " "})


def _norm(text: str) -> str:
    """How keywords, page text and search terms are compared: NFKC, case-folded, Arabic-script variants
    made equal (see _FOLD) and whitespace collapsed. Used by insights_search too."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().translate(_FOLD).split())


def _read(response, deadline: float) -> bytes | None:
    """At most MAX_BYTES + 1 bytes, one chunk at a time; None if the deadline passes before the end."""
    read = getattr(response, "read1", None) or response.read
    chunks, size = [], 0
    while size <= MAX_BYTES:
        if _now() > deadline:
            return None
        chunk = read(min(CHUNK, MAX_BYTES + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def _fetch(url: str, cfg, out: dict, deadline: float):
    """Follows allowed redirects until `deadline`. Returns (status, final URL, body, charset), or None
    after writing an error into out."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if _now() > deadline:
            out.update(final_url=_shown(current), error=TOO_SLOW)
            return None
        problem, note = _address_problem(urlsplit(current).hostname or "")
        if note:
            out["note"] = note
        if problem:
            out.update(final_url=_shown(current), error=problem)
            return None
        request = urllib.request.Request(_encoded(current), headers={"User-Agent": USER_AGENT,
                                                                     "Accept": "text/html,*/*;q=0.8"})
        try:
            response = _open(request, max(1.0, min(TIMEOUT, deadline - _now())))
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location") if e.headers is not None else None
            e.close()
            if e.code in REDIRECTS and location:
                # http.client reads headers as Latin-1: encoding back to Latin-1 restores the server's bytes
                target = urljoin(current, _encoded(location, "iso-8859-1"))
                if not cfg.allows_url(target):
                    out.update(status=e.code, final_url=_shown(current), redirect_host=_host_of(target),
                               error=OFF_DOMAIN)
                    return None
                current = target
                continue
            out.update(status=e.code, final_url=_shown(current), error=f"HTTP {e.code}")
            return None
        with response:
            status = response.status
            charset = response.headers.get_content_charset() or "utf-8"
            body = _read(response, deadline)
        if body is None:
            out.update(status=status, final_url=_shown(current), error=TOO_SLOW)
            return None
        return status, current, body, charset
    out.update(final_url=_shown(current), error=f"more than {MAX_REDIRECTS} redirects")
    return None


def check_url(url, keywords: list[str], cfg, until: float | None = None) -> dict:
    """One URL, within TIMEOUT seconds and before `until` (a _now() time) if given."""
    out = {"url": _shown(url) if isinstance(url, str) else url}
    try:
        if not isinstance(url, str) or not cfg.allows_url(url):
            out["error"] = NOT_ALLOWED
            return out
        started = _now()
        deadline = started + TIMEOUT if until is None else min(started + TIMEOUT, until)
        fetched = _fetch(url, cfg, out, deadline)
        load_seconds = round(_now() - started, 2)  # the page's HTML only, before any parsing
        if fetched is None:
            return out
        status, final_url, body, charset = fetched
        try:
            html = body[:MAX_BYTES].decode(charset, errors="replace")
        except LookupError:  # an unknown charset name
            html = body[:MAX_BYTES].decode("utf-8", errors="replace")
        page = _Page()
        page.feed(html)
        page.close()
        text = _norm(" ".join(page.text))
        found = [k for k in keywords if (key := _norm(k)) and key in text]
        out.update(
            status=status,
            final_url=_shown(final_url),
            load_seconds=load_seconds,
            mobile_viewport=page.viewport,
            title=" ".join(page.title.split())[:200],
            keywords_found=found,
            keywords_missing=[k for k in keywords if k not in found],
            truncated=len(body) > MAX_BYTES,
        )
    except Exception as e:  # never raise: one bad page must not hide the others
        out["error"] = f"{type(e).__name__}: {e}"[:300]
    return out


def check_landing_pages(urls, keywords, cfg) -> list[dict]:
    urls = urls if isinstance(urls, list) else [urls]
    words = [k for k in keywords if isinstance(k, str) and k.strip()] if isinstance(keywords, list) else []
    note = None
    if len(words) > MAX_KEYWORDS:
        note = f"only the first {MAX_KEYWORDS} keywords were checked; check the rest in another call"
        words = words[:MAX_KEYWORDS]
    until = _now() + TOTAL_SECONDS
    results = []
    for i, url in enumerate(urls):
        shown = _shown(url) if isinstance(url, str) else url
        if i >= MAX_URLS:
            res = {"url": shown, "error": f"not opened: at most {MAX_URLS} URLs per call"}
        elif _now() >= until:
            res = {"url": shown, "error": OUT_OF_TIME}
        else:
            res = check_url(url, words, cfg, until)
        if note and "keywords_found" in res:
            res["note"] = f"{res['note']}; {note}" if "note" in res else note
        results.append(res)
    return results
