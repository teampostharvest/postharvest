"""Parse the Netscape HTTP Cookie File (``cookies.txt``) format.

The format is what browser extensions such as "Get cookies.txt LOCALLY"
export: one cookie per line, fields tab-separated::

    # Netscape HTTP Cookie File
    # https://curl.se/docs/http-cookies.html

    .facebook.com\tTRUE\t/\tFALSE\t0\twd\t...
    #HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t1863317412\tc_user\t1000...

Rules honoured here:

* full-line comments (``# ...``) and blank lines are ignored — except lines
  beginning with the ``#HttpOnly_`` prefix, which are real cookie rows (the
  prefix marks the cookie as HttpOnly; the remainder is the domain);
* a row must have at least 7 tab-separated fields
  (domain, includeSubdomains, path, secure, expiry, name, value);
* ``expiry`` is a Unix timestamp in seconds; ``0`` means a session cookie
  and maps to Playwright's ``-1``;
* malformed rows are skipped and counted (surfacing the count in the log so
  a sloppy export still yields its valid cookies).

The parser returns the internal jar shape — the same Playwright browser
cookie dicts that every other capture path stores (``save_cookies``), so a
cookies.txt import is byte-compatible with a live session capture.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_HTTPONLY_PREFIX = "#HttpOnly_"
_MAX_COOKIES = 500


class InvalidCookiesTxt(ValueError):
    """The provided cookies.txt could not be turned into a usable jar."""


def parse_cookies_txt(text: str) -> list[dict]:
    """Parse Netscape cookies.txt content into internal cookie dicts.

    Raises :class:`InvalidCookiesTxt` when the input is empty or yields no
    valid cookies at all. Malformed individual lines are skipped.
    """
    if not text or not text.strip():
        raise InvalidCookiesTxt("The cookies.txt content is empty")

    cookies: list[dict] = []
    skipped = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        http_only = False
        if line.startswith(_HTTPONLY_PREFIX):
            http_only = True
            line = line[len(_HTTPONLY_PREFIX) :].lstrip()
        elif line.startswith("#"):
            continue  # header/comment line

        parts = line.split("\t")
        if len(parts) < 7:
            skipped += 1
            continue
        domain, _include_subdomains, path, secure, expiry, name, value = parts[:7]
        if not domain or not name:
            skipped += 1
            continue

        try:
            expires = int(expiry)
        except ValueError:
            expires = 0

        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path or "/",
                # Netscape "0" (session cookie) -> Playwright "-1".
                "expires": -1 if expires == 0 else expires,
                "httpOnly": http_only,
                "secure": secure.strip().upper() == "TRUE",
                "sameSite": "Lax",
            }
        )
        if len(cookies) >= _MAX_COOKIES:
            break

    if not cookies:
        raise InvalidCookiesTxt(
            "No cookies could be parsed from the provided file — expected the "
            'tab-separated Netscape "cookies.txt" format'
        )
    if skipped:
        logger.info("Skipped %d malformed cookies.txt row(s)", skipped)
    return cookies


def require_facebook_session(cookies: list[dict]) -> None:
    """Guard that a jar actually unlocks a Facebook session.

    Mirrors the live-capture gate (browser_scraper.py:1004-1005): a Facebook
    session needs both the ``c_user`` and ``xs`` cookies on a facebook.com
    domain. Rejects anything else with a clear message.
    """
    names = {
        c["name"]
        for c in cookies
        if "facebook.com" in c.get("domain", "")
    }
    missing = sorted(name for name in ("c_user", "xs") if name not in names)
    if missing:
        raise InvalidCookiesTxt(
            "This cookies.txt does not contain a Facebook session — missing "
            f"{', '.join(missing)} cookie(s) for facebook.com. Export cookies "
            "from an authenticated Facebook tab and try again."
        )