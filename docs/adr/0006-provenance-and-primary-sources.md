# ADR 0006: Newsletters are tips, not text

- **Status:** Accepted
- **Date:** Phase 0

## Context

The original concept was: read AI newsletters, extract stories, rewrite them as
social content. That approach has three linked problems.

**Legal.** Facts are not copyrightable; expression is. A newsletter's selection
and arrangement of stories can be protected as a compilation. Paraphrasing a
newsletter's prose and republishing it is a derivative work and a genuine risk.

**Quality.** Content derived from a summary is a summary of a summary. It cannot
contain anything the newsletter did not, which caps the product's ceiling below
its own source.

**Strategic.** A pipeline whose substance comes from one newsletter inherits that
newsletter's editorial bias, is always a day late, and dies when the format
changes. It is also a commodity: anyone can point an LLM at the same input.

These are the same problem viewed from three angles, so one decision resolves
all three.

## Decision

**Newsletters are tips, not text.** Aggregators tell us *what happened*; the
primary source tells us *what to say*.

Enforced structurally, not by convention:

1. `EvidenceKind` distinguishes `PRIMARY`, `SECONDARY` and `AGGREGATOR`. An
   aggregator reference may justify attention; only a primary source may justify
   a factual claim.
2. `Story.enrichment` holds content fetched from the primary source.
   `Story.is_publishable` is `False` without it.
3. `EditorialPolicy.require_primary_source` defaults to `True`, and a test asserts
   the shipped config keeps it enabled — so the rule cannot regress silently.
4. `Evidence` binds each claim to the source supporting it, forming the provenance
   ledger.
5. `Evidence.quote` rejects anything over 25 words (`MAX_QUOTE_WORDS`) at
   validation time. The fair-use ceiling cannot be forgotten under deadline
   pressure.
6. `Evidence` and `SourceDocument` are immutable — a record that can be edited is
   not an audit trail.

Editorial rules that follow: cite the primary source rather than the newsletter
that surfaced it; add value the original does not contain (Arabic
contextualisation, synthesis across sources, regional relevance, opinion); and
apply the test *if the reader saw the source, would our version still be worth
reading?*

## Alternatives considered

**Summarise newsletters directly.** Faster to build, cheaper to run. Rejected on
all three grounds above. The engineering saving is real; it buys a product with
no ceiling and a lawsuit surface.

**Primary sources only, no aggregators.** Clean, but discards the genuinely
valuable signal aggregators provide: human editors have already filtered for
importance. Using them as *signal* while sourcing substance elsewhere gets both.

## Consequences

**Positive**

- Removes derivative-work risk at the architectural level.
- Raises the quality ceiling above any single source.
- Breaks single-source dependency.
- Provenance becomes a visible trust feature, not just a legal shield.
- Debugging gains a precise answer to "where did this sentence come from?".

**Negative**

- Real engineering cost in Phase 2: fetching and extracting arbitrary web pages
  is messy, and some primary sources are paywalled or hostile to fetching.
- Higher latency and cost per story.
- Some stories will be unpublishable because the primary source cannot be
  retrieved. That is the correct outcome, not a bug to work around.
