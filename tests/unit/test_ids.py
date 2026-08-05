"""Identifier generation and parsing."""

from __future__ import annotations

import pytest

from engine.core.domain.ids import (
    IdPrefix,
    is_valid_id,
    new_id,
    timestamp_of,
    ulid,
)


def test_ulid_has_canonical_length():
    assert len(ulid()) == 26


def test_new_id_is_prefixed():
    identifier = new_id(IdPrefix.STORY)
    assert identifier.startswith("sty_")
    assert is_valid_id(identifier, IdPrefix.STORY)


def test_ids_are_unique_across_many_generations():
    generated = {new_id(IdPrefix.ASSET) for _ in range(5_000)}
    assert len(generated) == 5_000


def test_ids_sort_chronologically():
    """Lexicographic order must match creation order -- this is why we use ULIDs."""
    earlier = new_id(IdPrefix.RUN, timestamp_ms=1_700_000_000_000)
    later = new_id(IdPrefix.RUN, timestamp_ms=1_700_000_001_000)
    assert earlier < later


def test_timestamp_round_trips():
    moment = 1_762_000_000_123
    assert timestamp_of(new_id(IdPrefix.DOCUMENT, timestamp_ms=moment)) == moment


def test_prefix_mismatch_is_rejected():
    assert not is_valid_id(new_id(IdPrefix.STORY), IdPrefix.WORKSPACE)


@pytest.mark.parametrize(
    "value",
    ["", "nope", "sty_", "sty_TOOSHORT", "sty_" + "I" * 26, "no-underscore"],
)
def test_malformed_ids_are_rejected(value: str):
    assert not is_valid_id(value)


def test_timestamp_of_rejects_malformed_input():
    with pytest.raises(ValueError, match="not a valid engine identifier"):
        timestamp_of("garbage")
