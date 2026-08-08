"""The canonical machine output.

``package.json`` is what another program reads: a scheduler, a publishing
integration, a static-site build. Everything a downstream consumer needs is in
here, which means the Markdown beside it is a *view* rather than a source, and
the two can never disagree about what was produced.

Three properties it holds deliberately:

* **Self-contained.** A package carries the post text, the story it came from,
  the primary source, the score that selected it and the evidence behind it.
  Reading it must not require the database, because the thing consuming it a
  year from now probably will not have one.
* **Deterministic.** Keys are ordered, collections are sorted, floats are
  rounded. Re-rendering an unchanged day produces a byte-identical file except
  for ``generated_at``, so a diff shows editorial change rather than churn.
* **Versioned.** ``schema_version`` is the promise to whoever parses this. It
  is the first field for a reason.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from engine.core.domain.content import ContentAsset, ContentRevision, normalise_hashtags
from engine.core.domain.story import EditorialScore, Evidence, Story

#: Bump on any breaking change to the shape below. Consumers key off this.
SCHEMA_VERSION: Final = 1

_MONEY_PLACES: Final = 6
_SCORE_PLACES: Final = 4


def _score_payload(score: EditorialScore | None) -> dict[str, Any] | None:
    """Why this story was chosen, in the shape it was chosen with."""
    if score is None:
        return None
    return {
        "total": round(score.total, _SCORE_PLACES),
        # Sorted so two packages differ only where the scoring differed.
        "components": {
            key: round(value, _SCORE_PLACES) for key, value in sorted(score.components.items())
        },
        "scored_at": score.scored_at.isoformat(),
    }


def _evidence_payload(records: list[Evidence]) -> list[dict[str, Any]]:
    """The provenance ledger, flattened.

    Carried into the package rather than referenced by id: a publisher checking
    a claim needs the URL, not a foreign key into a database it cannot reach.
    """
    return [
        {
            "claim": record.claim,
            "kind": record.kind.value,
            "source_url": record.source_url,
            "source_title": record.source_title,
        }
        for record in sorted(records, key=lambda item: (item.kind.value, item.source_url))
    ]


def _asset_payload(asset: ContentAsset, revision: ContentRevision | None) -> dict[str, Any]:
    body = revision.body if revision else ""
    return {
        "id": asset.id,
        "product": asset.product_key,
        "channel": asset.channel_key,
        "language": str(asset.language),
        "brand_profile": asset.brand_profile_key,
        "state": asset.state.value,
        "variant": asset.variant,
        "body": body,
        # Counted here so a publisher does not have to agree with us about what
        # a character is before it can check a channel limit.
        "characters": len(body),
        "hashtags": normalise_hashtags(asset.meta.get("hashtags")),
        "hook": asset.meta.get("hook") or "",
        "revision": {
            "number": revision.revision_number if revision else 0,
            "origin": revision.origin.value if revision else None,
            "prompt_version": revision.prompt_version if revision else None,
            "model": revision.model if revision else None,
            "created_at": revision.created_at.isoformat() if revision else None,
        },
        "cost_usd": round(revision.cost_usd, _MONEY_PLACES) if revision else 0.0,
    }


def _story_payload(
    story: Story,
    *,
    score: EditorialScore | None,
    evidence: list[Evidence],
    assets: list[tuple[ContentAsset, ContentRevision | None]],
) -> dict[str, Any]:
    enrichment = story.enrichment
    return {
        "id": story.id,
        "headline": story.canonical_headline,
        "primary_url": story.primary_url,
        "selected_at": story.selected_at.isoformat() if story.selected_at else None,
        "source": {
            "url": enrichment.url if enrichment else None,
            "title": enrichment.title if enrichment else None,
            "author": enrichment.author if enrichment else None,
            "published_at": (
                enrichment.published_at.isoformat()
                if enrichment and enrichment.published_at
                else None
            ),
            # The extracted text itself is deliberately absent: it is somebody
            # else's copyright, and a publishing package is the wrong place to
            # redistribute it. The URL is what a reader should follow.
            "characters_extracted": len(enrichment.text) if enrichment else 0,
        },
        "score": _score_payload(score),
        "evidence": _evidence_payload(evidence),
        "assets": [
            _asset_payload(asset, revision)
            for asset, revision in sorted(
                assets,
                key=lambda pair: (pair[0].channel_key or "", str(pair[0].language)),
            )
        ],
    }


def build_package(
    *,
    workspace_slug: str,
    edition_date: str,
    generated_at: datetime,
    run_id: str,
    engine_version: str,
    stories: list[
        tuple[
            Story,
            EditorialScore | None,
            list[Evidence],
            list[tuple[ContentAsset, ContentRevision | None]],
        ]
    ],
) -> dict[str, Any]:
    """Assemble one day's package."""
    payloads = [
        _story_payload(story, score=score, evidence=evidence, assets=assets)
        for story, score, evidence, assets in stories
    ]
    # Highest-scoring first: the ordering is editorial, and a consumer that
    # takes the first N should get the best N.
    payloads.sort(
        key=lambda item: (-(item["score"] or {}).get("total", 0.0), item["headline"]),
    )

    asset_count = sum(len(item["assets"]) for item in payloads)
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": workspace_slug,
        "edition": edition_date,
        "generated_at": generated_at.isoformat(),
        "run_id": run_id,
        "engine_version": engine_version,
        "counts": {"stories": len(payloads), "assets": asset_count},
        "cost_usd": round(
            sum(asset["cost_usd"] for item in payloads for asset in item["assets"]),
            _MONEY_PLACES,
        ),
        "stories": payloads,
    }
