"""Prefixed, time-sortable identifiers.

Every entity gets an ID of the form ``<prefix>_<ULID>``, e.g. ``sty_01JBRX...``.

Why not integers or bare UUIDs:

* **Prefixes** make IDs self-describing in logs, URLs and support tickets. A
  stray ``ast_...`` in a bug report is instantly recognisable as a content asset.
* **ULIDs** are lexicographically sortable by creation time, which gives cheap
  chronological ordering and keyset pagination without a separate sort column --
  and, unlike auto-increment integers, they can be generated client-side and
  never collide across shards or tenants.
* **No database round-trip** is needed to know an entity's identity, so objects
  can be fully constructed in the domain layer before persistence exists.
"""

from __future__ import annotations

import secrets
import time
from typing import Final

# Crockford base32: no I, L, O or U, so IDs survive being read aloud or retyped.
_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TIME_CHARS: Final = 10  # 50 bits, covers timestamps to the year 10889
_RANDOM_CHARS: Final = 16  # 80 bits of entropy per millisecond
_ULID_LENGTH: Final = _TIME_CHARS + _RANDOM_CHARS


class IdPrefix:
    """Canonical entity prefixes. Values are stable and must never be reused."""

    ACCOUNT: Final = "acc"
    ACTOR: Final = "act"
    MEMBERSHIP: Final = "mbr"
    SIGNAL: Final = "sig"
    REVISION: Final = "rev"
    WORKSPACE: Final = "ws"
    SOURCE: Final = "src"
    DOCUMENT: Final = "doc"
    CANDIDATE: Final = "cnd"
    STORY: Final = "sty"
    EVIDENCE: Final = "evd"
    SCORE: Final = "scr"
    BRAND: Final = "brd"
    ASSET: Final = "ast"
    PUBLICATION: Final = "pub"
    METRIC: Final = "met"
    RUN: Final = "run"
    STAGE: Final = "stg"


def _encode(value: int, length: int) -> str:
    """Encode ``value`` as ``length`` Crockford base32 characters, big-endian."""
    chars = ["0"] * length
    for index in range(length - 1, -1, -1):
        chars[index] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def ulid(timestamp_ms: int | None = None) -> str:
    """Generate a 26-character ULID.

    Args:
        timestamp_ms: Millisecond epoch to embed. Defaults to now. Injectable so
            tests can produce deterministic, ordered identifiers.
    """
    moment = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    return _encode(moment, _TIME_CHARS) + _encode(secrets.randbits(80), _RANDOM_CHARS)


def new_id(prefix: str, timestamp_ms: int | None = None) -> str:
    """Generate a prefixed identifier, e.g. ``new_id(IdPrefix.STORY)``."""
    return f"{prefix}_{ulid(timestamp_ms)}"


def is_valid_id(value: str, prefix: str | None = None) -> bool:
    """Return True if ``value`` is a well-formed identifier.

    Args:
        value: The candidate identifier.
        prefix: If given, also require this exact prefix.
    """
    head, separator, tail = value.partition("_")
    if not separator or len(tail) != _ULID_LENGTH:
        return False
    if prefix is not None and head != prefix:
        return False
    return all(character in _ALPHABET for character in tail)


def timestamp_of(value: str) -> int:
    """Recover the millisecond timestamp embedded in an identifier.

    Raises:
        ValueError: If ``value`` is not a well-formed identifier.
    """
    if not is_valid_id(value):
        msg = f"not a valid engine identifier: {value!r}"
        raise ValueError(msg)
    _, _, tail = value.partition("_")
    moment = 0
    for character in tail[:_TIME_CHARS]:
        moment = (moment << 5) | _ALPHABET.index(character)
    return moment
