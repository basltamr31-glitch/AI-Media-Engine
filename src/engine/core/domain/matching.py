"""Keys and similarity used to decide that two mentions are one story.

Pure functions over strings, deliberately: clustering is the step most likely
to be revisited, and it has to be arguable from test cases rather than from a
running database.

Two mentions are the same story when they point at the same place. Everything
else -- headline wording, which newsletter carried it, when it arrived -- is
weaker evidence, and this module keeps the strong signal and the weak one
separate rather than blending them into one opaque score.
"""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import urlsplit

#: Tokens carrying no distinguishing information. A short list on purpose:
#: aggressive stopword removal makes unrelated headlines look alike, which is
#: the failure mode that matters here.
_STOPWORDS: Final = frozenset(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "on",
        "for",
        "and",
        "or",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "at",
        "by",
        "from",
        "as",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "new",
        "now",
        "how",
        "why",
        "what",
        "when",
        "who",
        "will",
        "can",
        "could",
        "may",
        "might",
    ]
)

_WORD: Final = re.compile(r"[a-z0-9]+")

#: Host prefixes that are decoration rather than identity.
_HOST_PREFIXES: Final = ("www.", "m.", "amp.")

#: Path suffixes some publishers append to the same article.
_PATH_SUFFIXES: Final = ("/amp", "/index.html", ".html", ".htm")

#: Two, not three. "AI", "ML", "OS", "5G" and "VR" are among the most
#: informative tokens this engine will ever see, and a three-character floor
#: silently deletes every one of them -- which for an AI-news workspace means
#: the topic "AI agents" matches on "agents" alone. Single characters stay
#: excluded: they are initials and list markers, never subjects.
_MIN_TOKEN_LENGTH: Final = 2

#: Below this many shared distinguishing tokens, a high ratio is arithmetic
#: rather than evidence: two three-word headlines sharing two words score 0.5
#: without being about the same thing.
MIN_SHARED_TOKENS: Final = 3


def url_key(url: str | None) -> str | None:
    """Identity of the thing a link points at.

    Host and path only. Query strings and fragments are where campaign tags,
    session identifiers and anchor links live -- all of which differ between
    two mentions of the same article.

    Returns None when there is no usable link, which callers must treat as
    "unknown", never as a key that can match another unknown.
    """
    if not url or not url.strip():
        return None
    parts = urlsplit(url.strip())
    if not parts.netloc:
        return None

    host = parts.netloc.casefold()
    for prefix in _HOST_PREFIXES:
        host = host.removeprefix(prefix)
    host = host.split(":", 1)[0]

    path = parts.path.casefold().rstrip("/")
    for suffix in _PATH_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break

    key = f"{host}{path}"
    return key or None


def title_key(headline: str | None) -> str | None:
    """Normalised headline, for matching two mentions with no usable link.

    Case, punctuation and spacing differ between a newsletter's rendering of a
    headline and the publisher's own. What survives normalisation is the
    wording, which must match exactly -- this is the strict fallback, not the
    fuzzy one.
    """
    if not headline:
        return None
    normalised = " ".join(_WORD.findall(headline.casefold()))
    return normalised or None


def title_tokens(headline: str | None) -> frozenset[str]:
    """Distinguishing words of a headline."""
    if not headline:
        return frozenset()
    return frozenset(
        word
        for word in _WORD.findall(headline.casefold())
        if len(word) >= _MIN_TOKEN_LENGTH and word not in _STOPWORDS
    )


def similarity(left: str | None, right: str | None) -> float:
    """Jaccard overlap of two headlines' distinguishing tokens, in 0..1.

    Returns 0.0 when the two share fewer than :data:`MIN_SHARED_TOKENS`
    words. Without that floor, short headlines produce high ratios from
    coincidence -- and a wrong merge is worse than a missed one, because two
    stories fused into one cannot be separated again downstream.
    """
    first, second = title_tokens(left), title_tokens(right)
    if not first or not second:
        return 0.0
    shared = first & second
    if len(shared) < MIN_SHARED_TOKENS:
        return 0.0
    return len(shared) / len(first | second)
