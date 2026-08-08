"""The signal computers the AI-news pack declares.

Each answers one question in its own units and knows nothing about weights,
other signals, or what the total will be used for. They are registered through
entry points like every other plugin (ADR 0017), so a vertical that needs
``cvss_severity`` or ``trial_phase`` ships it in a package beside its pack.

All five are pure arithmetic over data the stage already loaded. Ranking runs
across every story on every run; a computer that cost a network call would make
the cheapest stage the most expensive one.
"""

from __future__ import annotations

from typing import Final

from engine.adapters.plugins.context import PluginContext
from engine.core.domain.matching import title_tokens
from engine.core.domain.story import Story
from engine.core.ports.scoring import SignalContext

#: Where the newsletter placed it. Position 0 is the lead item; anything at or
#: below the ceiling scores zero.
_DEFAULT_PROMINENCE_CEILING: Final = 10.0

#: Community attention lives in candidate metadata under these keys, written by
#: the parser that saw it. Named here rather than guessed at per computer.
_ATTENTION_KEYS: Final = ("points", "comments")


class CorroborationComputer:
    """How many *distinct* sources reported this story.

    The strongest honest signal that something matters, and the hardest to
    game: one source repeating itself all week still counts once, which is why
    this counts sources rather than mentions.
    """

    @property
    def key(self) -> str:
        return "corroboration"

    def compute(self, story: Story, context: SignalContext) -> float | None:
        return float(story.source_count)


class SourceAuthorityComputer:
    """How much the reporting sources are trusted.

    The best source that carried it, not the average. A story broken by a
    primary source is not made less reliable by an aggregator also mentioning
    it, and averaging would penalise exactly the corroboration we want.
    """

    @property
    def key(self) -> str:
        return "source_authority"

    def compute(self, story: Story, context: SignalContext) -> float | None:
        weights = [
            context.sources[mention.source_id].trust_weight
            for mention in context.mentions
            if mention.source_id in context.sources
        ]
        if not weights:
            return None
        return max(weights)


class RecencyComputer:
    """Age in hours, for the decay normaliser.

    Measured from when the story was *first* seen rather than last: a story
    that keeps being re-mentioned is not thereby new again, and scoring it as
    new is how an aggregator's re-run becomes today's lead item.
    """

    @property
    def key(self) -> str:
        return "recency"

    def compute(self, story: Story, context: SignalContext) -> float | None:
        age = (context.now - story.first_seen_at).total_seconds() / 3600.0
        # A story timestamped in the future is a clock or feed problem, not a
        # reason to score it above everything else.
        return max(0.0, age)


class ProminenceComputer:
    """How high the source itself placed the story.

    Two forms of the same editorial judgement, freely given: a newsletter's
    ordering, and a community's votes. Whichever is available scores higher
    wins, because they are alternative expressions of "this one matters",
    not independent signals to be summed.
    """

    @property
    def key(self) -> str:
        return "prominence"

    def compute(self, story: Story, context: SignalContext) -> float | None:
        if not context.mentions:
            return None
        ceiling = _ceiling_of(context)

        # Position: the lead item scores the ceiling, decreasing by one per
        # place. Inverted here rather than in the normaliser, which only knows
        # that larger is better.
        #
        # Only counted where the source actually made a choice. An arXiv entry
        # is position 0 because its feed item contains one paper, not because
        # anyone judged it the day's most important -- and scoring that as a
        # lead item hands every single-item feed full marks for free.
        by_position: float | None = None
        ranked = [
            mention
            for mention in context.mentions
            if int(mention.meta.get("document_items", 0)) > 1
        ]
        if ranked:
            best_position = min(mention.ordinal for mention in ranked)
            by_position = max(0.0, ceiling - float(best_position))

        # Attention: raw counts are already "larger is better", but they live
        # on a different scale, so they are mapped onto the same ceiling.
        attention = _best_attention(context)
        by_attention = (
            None if attention is None else min(ceiling, attention / _attention_scale(ceiling))
        )

        measured = [value for value in (by_position, by_attention) if value is not None]
        # Nothing said anything about prominence: not measurable, which the
        # stage treats as "exclude", not as "zero".
        return max(measured) if measured else None


class AudienceFitComputer:
    """How well the story matches what this workspace says it cares about.

    Deliberately blunt: token overlap with the configured priority topics, and
    zero for anything touching a blocked topic. Editorial *intent* expressed as
    configuration, not a model's opinion of relevance -- which would cost a
    call per story and be unarguable when wrong.
    """

    @property
    def key(self) -> str:
        return "audience_fit"

    def compute(self, story: Story, context: SignalContext) -> float | None:
        policy = context.policy
        if not policy.priority_topics and not policy.blocked_topics:
            return None

        subject = title_tokens(story.canonical_headline) | title_tokens(
            story.canonical_summary or ""
        )
        if not subject:
            return None

        for blocked in policy.blocked_topics:
            if title_tokens(blocked) and title_tokens(blocked) <= subject:
                return 0.0

        if not policy.priority_topics:
            return None

        # Best-matching topic, not the sum: a story squarely about one priority
        # is a hit, and matching two loosely is not better than matching one
        # exactly.
        best = 0.0
        for topic in policy.priority_topics:
            tokens = title_tokens(topic)
            if not tokens:
                continue
            best = max(best, len(tokens & subject) / len(tokens))
        return best


def _ceiling_of(context: SignalContext) -> float:
    raw = context.parameters.get("ceiling", _DEFAULT_PROMINENCE_CEILING)
    try:
        ceiling = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_PROMINENCE_CEILING
    return ceiling if ceiling > 0 else _DEFAULT_PROMINENCE_CEILING


def _attention_scale(ceiling: float) -> float:
    """Points per unit of prominence.

    100 points on Hacker News is a front-page story; the scale maps that onto
    the ceiling so votes and newsletter position end up commensurable.
    """
    return max(1.0, 100.0 / ceiling)


def _best_attention(context: SignalContext) -> float | None:
    values = [
        float(mention.meta[key])
        for mention in context.mentions
        for key in _ATTENTION_KEYS
        if isinstance(mention.meta.get(key), int | float)
    ]
    return max(values) if values else None


def build_corroboration(context: PluginContext) -> CorroborationComputer:
    """Entry-point factory (group ``engine.signals``, name ``corroboration``)."""
    return CorroborationComputer()


def build_source_authority(context: PluginContext) -> SourceAuthorityComputer:
    """Entry-point factory (group ``engine.signals``, name ``source_authority``)."""
    return SourceAuthorityComputer()


def build_recency(context: PluginContext) -> RecencyComputer:
    """Entry-point factory (group ``engine.signals``, name ``recency``)."""
    return RecencyComputer()


def build_prominence(context: PluginContext) -> ProminenceComputer:
    """Entry-point factory (group ``engine.signals``, name ``prominence``)."""
    return ProminenceComputer()


def build_audience_fit(context: PluginContext) -> AudienceFitComputer:
    """Entry-point factory (group ``engine.signals``, name ``audience_fit``)."""
    return AudienceFitComputer()
