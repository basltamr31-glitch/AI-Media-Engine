"""Prompt construction for generation.

Three things are assembled here, and the split between them is the whole
design:

* **The stable prefix** -- brand voice and channel rules. Identical for every
  story generated for the same channel and language in a run, which is exactly
  what :attr:`LLMRequest.cacheable_prefix` is for. Cache reads bill at ~10% of
  input, so putting anything story-specific in front of this boundary would
  quietly cost real money.
* **The instruction** -- what to produce, once.
* **The per-story part** -- the primary source text and its provenance.

The prompt is *versioned*. Every generated revision records which version wrote
it, because "output got worse last Tuesday" is unanswerable without knowing
which prompt was live -- and a style regression that cannot be attributed
cannot be fixed.

Nothing here is AI-news-specific. The voice comes from a ``BrandProfile``, the
constraints from a pack's ``ChannelDef``; a cybersecurity pack supplies its own
of each and this module does not change.
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import BaseModel, Field

from engine.core.domain.content import BrandProfile
from engine.core.domain.language import language_name
from engine.core.domain.pack import ChannelDef, ProductDef
from engine.core.domain.story import Story

#: Bump on any change to the text below. Recorded on every generated revision.
#:
#: gen-2: name the language in words. gen-1 said "in ar", and a small model
#: read a BCP-47 tag as decoration and wrote the Arabic post in English --
#: silently, since the asset was correctly labelled ``ar`` either way.
#: gen-3: restate the language *last*, after the source text, and say outright
#: not to copy from the source. gen-2 still drifted to English and began
#: reproducing the abstract verbatim: both are recency effects, and a long
#: English prefix followed by a long English article is the worst possible
#: context for an instruction sitting at the top.
#: gen-4: drop the ``hook`` field and compute it instead. The golden set showed
#: the model returning hashtags in that field on every single case -- a field
#: the schema could not have got wrong, because it should never have been asked
#: for. The first check the eval suite paid for.
#: gen-5: restate the length limit last, next to the language, and give it as a
#: word count as well as characters. The limit was stated once, in the cached
#: prefix, thousands of tokens before the model started writing -- the same
#: recency failure gen-3 fixed for language, left in place for length because
#: only language had been caught at the time. Words as well as characters
#: because a model has no character counter: it cannot check "under 280
#: characters" while writing, and it can approximately check "about 45 words".
#:
#: gen-6 was attempted twice and reverted both times. It made the copying
#: rule countable -- "never reuse more than 6 words in a row" -- on the
#: theory that worked for length in gen-5: a model cannot obey a rule it
#: cannot check itself against. It does not transfer.
#:
#: In the closing line, beside the length rule, it *regressed* to 0.886.
#: The longer closing pushed the language reminder further from the end and
#: the Arabic post came back 0% Arabic -- the failure gen-3 had fixed. The
#: tail of a prompt is a scarce resource, and adding a rule there evicted
#: one rather than adding one.
#:
#: Moved into the system text, where it competes with nothing, it scored
#: 0.973 against gen-5's 0.976 -- indistinguishable, with copying unchanged
#: at 55/173 runs versus 47/164. Two placements, no effect: the remaining
#: copying is a limit of a 1.7B model, not of the instruction. Shipping
#: instruction text that demonstrably changes nothing only costs tokens, so
#: it is recorded here instead of carried in the prompt.
PROMPT_VERSION: Final = "gen-5"

#: Average characters per word, used to turn a channel's character limit into a
#: word budget. Six is deliberately generous for English (~5.7 with the space)
#: and close for Arabic, so the derived budget lands *under* the real limit in
#: both -- a post that comes in short is publishable, and one that comes in long
#: is refused by the stage.
_CHARS_PER_WORD: Final = 6.0

#: Fraction of the limit to aim for. The budget is a target the model will miss
#: in both directions; aiming at the ceiling means half the misses are refusals.
_LENGTH_SAFETY: Final = 0.9


def word_budget(max_characters: int) -> int:
    """Roughly how many words fit in a channel's character limit.

    Approximate on purpose. Its job is to give the model a quantity it can
    actually track while writing, not to be arithmetically defensible: the
    real limit is still enforced exactly, in characters, after generation.
    """
    return max(15, int(max_characters * _LENGTH_SAFETY / _CHARS_PER_WORD))


#: Tokens allowed per character of a channel's limit. Deliberately generous:
#: Arabic tokenises far less efficiently than English -- frequently near one
#: token per character -- and truncating a valid post is worse than letting a
#: bad one run a little longer.
_TOKENS_PER_CHARACTER: Final = 1.5

#: Headroom for the JSON envelope, hashtags and the source URL, none of which
#: are part of the body the channel limit governs.
_TOKEN_OVERHEAD: Final = 256

#: Never go below this, whatever the channel says. A ceiling tight enough to
#: truncate would convert a working generation into a failure.
_MIN_TOKENS: Final = 512


def token_ceiling(max_characters: int, configured: int) -> int:
    """How many tokens one post may take, given the channel it is for.

    The configured ceiling is sized for the *longest* channel, so every short
    one inherits it: a 280-character post was allowed 4096 tokens, and a model
    that rambles used all of them before the result was refused for length.
    The refusal was correct and the cost of reaching it was not -- on a free
    local model that is 80 minutes of wall clock, and on a metered one it is
    the same waste with an invoice.

    Never raises the configured value, only lowers it where it is absurd.
    """
    room = int(max_characters * _TOKENS_PER_CHARACTER) + _TOKEN_OVERHEAD
    return min(configured, max(_MIN_TOKENS, room))


#: How much primary-source text one prompt carries. Enough for the model to
#: have something to say, bounded so a long-read cannot blow the context window
#: -- silent truncation of the *end* of a prompt is how a system starts writing
#: about text it never saw.
MAX_SOURCE_CHARS: Final = 6000


class GeneratedContent(BaseModel):
    """What generation must return.

    ``hashtags`` is a separate field because every channel treats tags as a
    separate object, and asking a model to mark them up inside prose is a
    parsing problem invented for no reason.

    The hook is **not** here. It was, and the model reliably returned hashtags
    or a question about the post instead of the opening sentence. The hook is
    definitionally the first sentence of the body, so asking a model to copy it
    was asking for an error where :func:`first_sentence` cannot make one.
    """

    body: str = Field(description="The post itself, ready to publish as written.")
    hashtags: list[str] = Field(
        default_factory=list,
        description=(
            "Topic tags, each a single word with no leading '#'. "
            "An empty list when the channel does not use hashtags."
        ),
    )
    source_url: str = Field(description="The primary source this was written from, copied exactly.")


#: Sentence terminators across the scripts this engine writes, including the
#: Arabic full stop and question mark, which are distinct code points.
_SENTENCE_END: Final = re.compile(
    r"(?<=[.!?۔؟])\s"  # noqa: RUF001 -- Arabic full stop and question mark, deliberately
)


def first_sentence(body: str, *, max_characters: int = 200) -> str:
    """The post's opening sentence -- what decides whether the rest is read.

    Computed rather than requested. See :class:`GeneratedContent`.
    """
    cleaned = body.strip()
    if not cleaned:
        return ""
    first = _SENTENCE_END.split(cleaned, maxsplit=1)[0].strip()
    return first[:max_characters]


def _numbered(rules: list[str]) -> str:
    return "\n".join(f"- {rule}" for rule in rules)


def build_cacheable_prefix(
    *,
    brand: BrandProfile,
    channel: ChannelDef,
    product: ProductDef,
) -> str:
    """The stable half: who is speaking, and what the channel allows.

    Ordered voice-then-channel because voice is the part a customer is paying
    for, and the model weights the opening of a long instruction most heavily.
    """
    language = language_name(brand.language)
    sections = [
        f"You write {product.name.lower()}s for {channel.name}.",
        "",
        f"# Language: {language}",
        f"Write the body entirely in {language}. Every sentence, without "
        f"exception. Technical terms and product names keep their original "
        f"spelling; everything around them is {language}.",
        "",
        "# Voice",
        f"You are: {brand.name}.",
        f"You are writing for: {brand.audience.strip()}",
    ]

    if brand.tone_rules:
        sections += ["", "## Tone", _numbered(brand.tone_rules)]
    if brand.style_rules:
        sections += ["", "## Style", _numbered(brand.style_rules)]
    if brand.signature_phrases:
        sections += [
            "",
            "## Phrases that sound like us",
            _numbered(brand.signature_phrases),
        ]
    if brand.preferred_terms:
        # Matters most in Arabic, where a wrong technical term reads as
        # machine-translated regardless of how good the sentence is.
        pairs = [f"{wrong} -> {right}" for wrong, right in brand.preferred_terms.items()]
        sections += ["", "## Preferred terms", _numbered(pairs)]
    if brand.forbidden_topics:
        sections += ["", "## Never write about", _numbered(brand.forbidden_topics)]
    if brand.example_posts:
        # Few-shot exemplars are the strongest single lever on voice fidelity,
        # so they sit inside the cached prefix where they cost almost nothing
        # to repeat on every story.
        examples = "\n\n---\n\n".join(brand.example_posts)
        sections += ["", "## Posts we have published", examples]

    limits = [
        f"Hard limit: {channel.max_characters} characters. Going over means it cannot be posted.",
    ]
    if channel.supports_hashtags:
        limits.append(f"At most {channel.max_hashtags} hashtags.")
    else:
        limits.append("This channel does not use hashtags. Return none.")
    if not channel.supports_links:
        limits.append(
            "Links are not clickable here. Name the source in the text instead of pasting a URL."
        )
    sections += ["", f"# {channel.name} rules", _numbered(limits + channel.format_notes)]

    return "\n".join(sections)


def build_system() -> str:
    """The instruction, held separate from the voice so it can change alone."""
    return (
        "Write from the primary source you are given, and from nothing else.\n"
        "Every factual claim must be supported by that text. Do not add context "
        "you happen to know, do not estimate numbers, and do not describe "
        "significance the source does not state.\n"
        "If the source is too thin to write from honestly, say so in the body "
        "rather than padding it.\n"
        "Return the post exactly as it should appear -- no preamble, no "
        "explanation, no markdown headings."
    )


def build_prompt(
    story: Story,
    *,
    language: str,
    max_characters: int | None = None,
    max_source_chars: int = MAX_SOURCE_CHARS,
) -> str:
    """The per-story half, placed after the cache boundary.

    Uses the *extracted primary source*, never a newsletter's summary of it.
    That is the same rule enrichment exists to enforce (ADR 0003), applied at
    the point where breaking it would actually produce republished text.

    The language is restated at the very end, which looks redundant against the
    prefix and is not. The prefix and the source text are both long and both in
    English; a small model reads the last instruction most strongly, and stating
    the requirement only at the top produced English posts on an Arabic profile.
    """
    enrichment = story.enrichment
    if enrichment is None or not enrichment.text.strip():
        msg = f"story {story.id!r} has no extracted primary source to write from"
        raise ValueError(msg)

    parts = [f"Headline as reported: {story.canonical_headline}"]
    if enrichment.title:
        parts.append(f"Title of the source: {enrichment.title}")
    if enrichment.author:
        parts.append(f"Author: {enrichment.author}")
    if enrichment.published_at:
        parts.append(f"Published: {enrichment.published_at.date().isoformat()}")
    parts.append(f"Source URL: {enrichment.url}")
    parts += ["", "Primary source text:", enrichment.text[:max_source_chars].strip()]

    closing = (
        f"Now write the post. Do not copy sentences from the source above -- "
        f"it is your material, not your draft. Write it in {language}."
    )
    if max_characters is not None:
        # Last, for the same reason the language is: this is the instruction the
        # model is most likely to still be holding when it starts writing.
        closing += (
            f" Keep it under {max_characters} characters -- about "
            f"{word_budget(max_characters)} words. Going over means it cannot "
            f"be posted at all."
        )
    parts += ["", closing]
    return "\n".join(parts)
