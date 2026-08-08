"""Shared helpers for reading HTML documents.

Newsletter HTML is machine-generated email markup: nested tables, inline
styles, invisible spacer characters. The helpers here exist so each parser
worries about *its* newsletter's structure rather than re-solving whitespace
normalisation and link cleaning.
"""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

#: Zero-width and layout-only characters that email templates use as spacers.
#: Left in place they corrupt every headline comparison downstream.
_INVISIBLE: Final = re.compile(r"[​‌‍⁠­͏]")

_WHITESPACE: Final = re.compile(r"\s+")

#: Query parameters stripped from outbound links. Campaign tags make the same
#: article look like two different URLs, which would defeat cross-source
#: clustering; ``email`` appears in newsletter footer links and is the
#: subscriber's own address, which must never be stored.
_STRIPPED_PARAMS: Final = frozenset({"email", "e", "recipient"})
_STRIPPED_PREFIXES: Final = ("utm_",)


def clean_text(value: str | None) -> str:
    """Collapse whitespace and drop invisible layout characters."""
    if not value:
        return ""
    return _WHITESPACE.sub(" ", _INVISIBLE.sub("", value)).strip()


def parse_html(markup: str) -> BeautifulSoup:
    """Parse with the stdlib backend and drop non-content elements.

    ``html.parser`` rather than lxml: it is stdlib, tolerant of the malformed
    markup email templates produce, and one fewer binary dependency.
    """
    soup = BeautifulSoup(markup, "html.parser")
    for tag in soup(["style", "script", "head", "title"]):
        tag.decompose()
    return soup


def element_text(element: Tag | None) -> str:
    """Normalised visible text of one element."""
    if element is None:
        return ""
    return clean_text(element.get_text(" "))


def href_of(element: Tag) -> str:
    """The ``href`` of an anchor, as a string."""
    value = element.get("href")
    if isinstance(value, list):  # bs4 types multi-valued attributes as lists
        value = value[0] if value else ""
    return str(value or "").strip()


def canonical_url(url: str) -> str:
    """Strip tracking parameters from an outbound link.

    Two newsletters linking the same article with different campaign tags must
    produce the same URL, or cross-source corroboration -- the whole point of
    reading more than one source -- silently fails to detect it.
    """
    if not url:
        return ""
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in _STRIPPED_PARAMS
        and not key.casefold().startswith(_STRIPPED_PREFIXES)
    ]
    return urlunsplit(parts._replace(query=urlencode(kept)))
