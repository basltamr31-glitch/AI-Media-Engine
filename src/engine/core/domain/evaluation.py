"""Quality checks for generated content.

Every check here is **algorithmic**: a regex, a set intersection, a ratio.
Nothing calls a model. That is a cost decision before it is a design one --
an LLM judge costs a model call per generation per run, which is exactly the
shape of spend that makes an eval suite too expensive to run often, and an eval
suite nobody runs is worse than none because it looks like coverage.

The checks were not invented in the abstract. Each one catches a failure that
actually happened while the generation prompt was being written:

* An Arabic brand profile produced an English post -- silently, because the
  asset was correctly labelled ``ar`` either way.
* The model began reproducing the source abstract near-verbatim instead of
  writing about it.
* It wrote "1.29 million personas" where the source said 1,290 dimensions and
  8.3 billion records. A plausible number that is not in the source is the most
  dangerous output this system can produce.
* It wrote past a channel's hard character limit.
* It returned a question, or a list of hashtags, where the opening sentence
  was asked for.

Each is a *score* in 0..1 with a threshold, not a bare boolean, so a regression
is visible as movement before it becomes a failure.
"""

from __future__ import annotations

import re
from typing import Final

from pydantic import Field

from engine.core.domain.base import DomainModel, ImmutableModel
from engine.core.domain.language import base_language

#: Unicode ranges sufficient to tell the scripts this engine writes apart.
#: Not a general script detector -- it answers "is this the alphabet we asked
#: for?", which is a much easier question than "what language is this?".
_SCRIPTS: Final[dict[str, re.Pattern[str]]] = {
    "arabic": re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]"),
    "latin": re.compile(r"[A-Za-zÀ-ɏ]"),
}

#: Which script each language is written in. Unknown languages are not checked
#: rather than guessed -- a wrong expectation would fail every correct post.
_LANGUAGE_SCRIPT: Final[dict[str, str]] = {
    "ar": "arabic",
    "ur": "arabic",
    "en": "latin",
    "fr": "latin",
    "de": "latin",
    "es": "latin",
    "pt": "latin",
    "tr": "latin",
}

#: Words long enough that sharing a run of them indicates copying rather than
#: coincidence. Eight is empirical: shorter windows fire on stock phrasing
#: ("the model was trained on"), longer ones miss a copied sentence.
_COPY_WINDOW: Final = 8

_WORD = re.compile(r"\w+", re.UNICODE)
#: Numbers, including grouped and decimal forms: 8.3, 1,290, 599847, 91.5%.
_NUMBER = re.compile(r"\d[\d,.]*")


class CheckResult(ImmutableModel):
    """One check against one generated post."""

    name: str
    score: float = Field(ge=0.0, le=1.0, description="1.0 is perfect; the threshold decides pass.")
    threshold: float = Field(ge=0.0, le=1.0)
    detail: str = Field(default="", description="What the score means, in words.")

    @property
    def passed(self) -> bool:
        return self.score >= self.threshold


class EvalCase(DomainModel):
    """One golden input: a real source, and what should come out of it."""

    key: str
    headline: str
    source_url: str
    source_text: str
    language: str
    channel_key: str
    max_characters: int = Field(gt=0)
    must_include: list[str] = Field(
        default_factory=list,
        description="Terms a correct post about this source cannot omit.",
    )


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def check_language(text: str, language: str, *, threshold: float = 0.5) -> CheckResult:
    """Is the post written in the alphabet the brand profile asked for?

    Measured as a ratio over letters only, ignoring digits, punctuation and
    whitespace. A correct Arabic post still contains Latin product names and
    figures, so the bar is "mostly", not "entirely" -- an English post scores
    near zero and a real Arabic one scores well clear of it.
    """
    script = _LANGUAGE_SCRIPT.get(base_language(language))
    if script is None:
        return CheckResult(
            name="language",
            score=1.0,
            threshold=0.0,
            detail=f"no script expectation recorded for {language!r}; not checked",
        )

    expected = len(_SCRIPTS[script].findall(text))
    letters = sum(len(pattern.findall(text)) for pattern in _SCRIPTS.values())
    if letters == 0:
        return CheckResult(
            name="language", score=0.0, threshold=threshold, detail="no letters in the post"
        )

    ratio = expected / letters
    return CheckResult(
        name="language",
        score=ratio,
        threshold=threshold,
        detail=f"{ratio:.0%} of letters are {script} (expected for {language})",
    )


def check_not_copied(text: str, source: str, *, threshold: float = 0.8) -> CheckResult:
    """Is this written *about* the source, or lifted *from* it?

    Scored as the proportion of the post's word-windows that do **not** appear
    verbatim in the source. Copying is both a quality failure and a copyright
    one, and it is the failure mode a model falls into when it cannot think of
    anything to say.
    """
    post_words = _words(text)
    source_words = _words(source)
    if len(post_words) < _COPY_WINDOW or len(source_words) < _COPY_WINDOW:
        return CheckResult(
            name="not_copied", score=1.0, threshold=threshold, detail="too short to assess"
        )

    source_windows = {
        tuple(source_words[index : index + _COPY_WINDOW])
        for index in range(len(source_words) - _COPY_WINDOW + 1)
    }
    windows = [
        tuple(post_words[index : index + _COPY_WINDOW])
        for index in range(len(post_words) - _COPY_WINDOW + 1)
    ]
    copied = sum(1 for window in windows if window in source_windows)
    original = 1.0 - (copied / len(windows))
    return CheckResult(
        name="not_copied",
        score=original,
        threshold=threshold,
        detail=f"{copied}/{len(windows)} {_COPY_WINDOW}-word runs appear verbatim in the source",
    )


def check_numbers_supported(text: str, source: str, *, threshold: float = 1.0) -> CheckResult:
    """Does every figure in the post appear in the source?

    The highest-value check in the file. A model that invents a plausible
    number produces output that reads perfectly and is false, which is the one
    failure a human reviewer is least likely to catch.

    Compared on digits alone, so ``1,290`` matches ``1290`` and ``91.5%``
    matches ``91.5``. Deliberately strict: a figure the post *derived* rather
    than copied will fail, and that is the right default -- the prompt tells the
    model not to compute.
    """
    found = _NUMBER.findall(text)
    if not found:
        return CheckResult(
            name="numbers_supported", score=1.0, threshold=threshold, detail="no figures used"
        )

    source_digits = re.sub(r"[,\s]", "", source)
    unsupported = [
        number for number in found if re.sub(r"[,\s]", "", number).rstrip(".") not in source_digits
    ]
    supported = 1.0 - (len(unsupported) / len(found))
    detail = (
        f"all {len(found)} figure(s) appear in the source"
        if not unsupported
        else f"not in the source: {', '.join(sorted(set(unsupported))[:5])}"
    )
    return CheckResult(
        name="numbers_supported", score=supported, threshold=threshold, detail=detail
    )


def check_length(text: str, max_characters: int) -> CheckResult:
    """Does it fit the channel? A post that cannot be posted is not a post."""
    length = len(text)
    return CheckResult(
        name="length",
        score=1.0 if length <= max_characters else 0.0,
        threshold=1.0,
        detail=f"{length} of {max_characters} characters",
    )


def check_hook(hook: str, body: str, *, threshold: float = 1.0) -> CheckResult:
    """Is the hook actually the post's opening?

    This check is why the hook is no longer asked of the model. It failed on
    every golden case -- the model returned hashtags or a question about the
    post -- and the fix was to compute the opening sentence rather than request
    it. The check stays because it is now a guard on that computation: an empty
    hook means an empty or unpunctuated post, which is still worth catching.
    """
    cleaned = hook.strip()
    if not cleaned:
        return CheckResult(name="hook", score=0.0, threshold=threshold, detail="empty")
    opens_with = body.strip().lower().startswith(cleaned.lower()[: len(cleaned)])
    return CheckResult(
        name="hook",
        score=1.0 if opens_with else 0.0,
        threshold=threshold,
        detail="matches the opening" if opens_with else f"does not open the post: {cleaned[:60]!r}",
    )


def check_must_include(text: str, terms: list[str], *, threshold: float = 1.0) -> CheckResult:
    """Did it mention what a correct post about this source cannot omit?

    The one place a golden case asserts *content* rather than shape. Kept to
    terms that are unarguable -- a product name, a headline figure -- because
    anything debatable belongs to a human reviewer, not a threshold.
    """
    if not terms:
        return CheckResult(
            name="must_include", score=1.0, threshold=threshold, detail="none required"
        )
    lowered = text.lower()
    missing = [term for term in terms if term.lower() not in lowered]
    present = 1.0 - (len(missing) / len(terms))
    return CheckResult(
        name="must_include",
        score=present,
        threshold=threshold,
        detail="all present" if not missing else f"missing: {', '.join(missing)}",
    )


def check_no_fused_words(text: str, *, threshold: float = 1.0) -> CheckResult:
    """Are any words half one script and half another?

    Found by reading real output rather than by design. A live Arabic post
    welded the Arabic word for "art" onto the tail of the English "financial",
    producing one token that is half each -- and every other check passed it:
    the script ratio was 78% Arabic, nothing was copied, no number was
    unsupported, the length was fine. It is unreadable to an Arabic speaker and
    invisible to everyone else, which is the same shape of failure the RTL bug
    had.

    A single word mixing Arabic and Latin letters with no separator is always a
    generation artifact. Product names stay clean because they are wholly
    Latin; the check only fires when the scripts are fused *inside* one token.

    Threshold 1.0: one fused word is one too many. This does not detect bad
    grammar or clumsy translation -- those need a human, and this file does not
    pretend to replace one.
    """
    fused = [word for word in re.findall(r"\S+", text) if _is_fused(word)]
    if not fused:
        return CheckResult(
            name="no_fused_words", score=1.0, threshold=threshold, detail="none found"
        )
    total = len(_words(text)) or 1
    return CheckResult(
        name="no_fused_words",
        score=max(0.0, 1.0 - len(fused) / total),
        threshold=threshold,
        detail=f"{len(fused)} word(s) mix scripts: {', '.join(fused[:3])}",
    )


def _is_fused(word: str) -> bool:
    """Whether one token contains letters from two of the tracked scripts."""
    return sum(1 for pattern in _SCRIPTS.values() if pattern.search(word)) > 1


def evaluate(
    *,
    body: str,
    hook: str,
    case: EvalCase,
) -> list[CheckResult]:
    """Run every check against one generated post."""
    return [
        check_language(body, case.language),
        check_not_copied(body, case.source_text),
        check_numbers_supported(body, case.source_text),
        check_length(body, case.max_characters),
        check_hook(hook, body),
        check_must_include(body, case.must_include),
        check_no_fused_words(body),
    ]


def overall_score(results: list[CheckResult]) -> float:
    """The mean, so one check collapsing is visible without hiding the others."""
    return sum(result.score for result in results) / len(results) if results else 0.0
