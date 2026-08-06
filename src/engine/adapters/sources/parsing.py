"""Shared parsing helpers for ingestion adapters.

Two problems every source format presents, solved once:

* **Dates arrive in every conceivable format.** ISO 8601, RFC 2822, Unix epoch,
  and each with its own ideas about timezones. A naive datetime that slips
  through here corrupts recency ranking silently, so parsing is centralised and
  always returns UTC-aware values or ``None``.
* **Payload shapes differ wildly.** Hacker News nests items under ``hits``,
  Reddit under ``data.children`` with each field beneath another ``data``, and
  GitHub returns a bare array. Dot-path extraction turns those differences into
  configuration instead of code.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Final

#: Plausible range for a Unix timestamp in seconds: 1990-01-01 to 2100-01-01.
#: Used to tell seconds from milliseconds, since APIs disagree about which.
_EPOCH_SECONDS_MIN: Final = 631_152_000
_EPOCH_SECONDS_MAX: Final = 4_102_444_800

_TRAILING_Z: Final = re.compile(r"[Zz]$")


def parse_datetime(value: Any) -> datetime | None:
    """Best-effort conversion of ``value`` into a UTC-aware datetime.

    Accepts ISO 8601 strings (with or without ``Z``), RFC 2822 strings as used
    by RSS and email, Unix epoch numbers in seconds or milliseconds, and
    ``datetime`` objects. Returns ``None`` rather than raising: a story with an
    unparseable date is still a story, and losing the whole item over a
    malformed field would be a worse failure than losing the timestamp.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        return _as_utc(value)

    if isinstance(value, int | float) and not isinstance(value, bool):
        return _from_epoch(float(value))

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return _from_string(text)

    return None


def _as_utc(moment: datetime) -> datetime:
    """Attach UTC to a naive datetime, or convert an aware one."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _from_epoch(raw: float) -> datetime | None:
    """Interpret a number as epoch seconds, tolerating milliseconds."""
    seconds = raw / 1000.0 if raw > _EPOCH_SECONDS_MAX else raw
    if not (_EPOCH_SECONDS_MIN <= seconds <= _EPOCH_SECONDS_MAX):
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _from_string(text: str) -> datetime | None:
    """Try ISO 8601, then RFC 2822, then a numeric string."""
    try:
        return _as_utc(datetime.fromisoformat(_TRAILING_Z.sub("+00:00", text)))
    except ValueError:
        pass

    try:
        return _as_utc(parsedate_to_datetime(text))
    except (TypeError, ValueError):
        pass

    try:
        return _from_epoch(float(text))
    except ValueError:
        return None


def get_path(data: Any, path: str, default: Any = None) -> Any:
    """Read a nested value using a dot-separated ``path``.

    Supports dict keys and list indices, e.g. ``"data.children.0.data.title"``.
    An empty path returns ``data`` unchanged, which is how a payload that is
    already a bare array is expressed in configuration.

    Returns ``default`` when any segment is missing rather than raising, because
    real feeds omit fields on individual items constantly.
    """
    if not path:
        return data

    current = data
    for segment in path.split("."):
        if isinstance(current, dict):
            if segment not in current:
                return default
            current = current[segment]
        elif isinstance(current, list):
            try:
                current = current[int(segment)]
            except (ValueError, IndexError):
                return default
        else:
            return default
    return current


def coerce_text(value: Any) -> str | None:
    """Normalise a mapped field into a trimmed string, or ``None``.

    Field maps point at JSON that may hold numbers (Reddit IDs), booleans or
    nulls. Everything downstream expects text, so coercion happens once here.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float):
        return str(value)
    return None


def truncate(value: str | None, limit: int) -> str | None:
    """Trim ``value`` to ``limit`` characters to respect column widths.

    Titles and URLs from the wild routinely exceed sane lengths. Truncating at
    the boundary beats a database error that fails an entire ingest run.
    """
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit]
