"""Running the golden set.

Generates through the **real** prompt path -- the same
``build_cacheable_prefix`` / ``build_system`` / ``build_prompt`` the generate
stage uses -- because an eval that assembles its own prompt tests the eval,
not the product.

Nothing is persisted. A golden case is not a story, produces no
:class:`ContentAsset`, and belongs to no workspace: it exists to answer
"did quality change?", and writing its output into the corpus would pollute
exactly the data the question is about.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.adapters.generation.prompts import (
    PROMPT_VERSION,
    GeneratedContent,
    build_cacheable_prefix,
    build_prompt,
    build_system,
    first_sentence,
)
from engine.core.domain.content import BrandProfile
from engine.core.domain.evaluation import CheckResult, EvalCase, evaluate, overall_score
from engine.core.domain.language import language_name
from engine.core.domain.pack import ChannelDef, ProductDef, ProductKind
from engine.core.domain.story import EnrichedSource, Story
from engine.core.ports.llm import LLMError, LLMPort, LLMRequest
from engine.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class CaseOutcome:
    """What one golden case produced, and how it scored."""

    key: str
    checks: list[CheckResult] = field(default_factory=list)
    body: str = ""
    error: str | None = None
    cost_usd: float = 0.0
    output_tokens: int = 0

    @property
    def score(self) -> float:
        # A case that could not be generated scores zero rather than being
        # skipped: "the model was down" and "the model wrote nothing usable"
        # are both regressions from the reader's point of view.
        return 0.0 if self.error else overall_score(self.checks)

    @property
    def passed(self) -> bool:
        return self.error is None and all(check.passed for check in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        return [check for check in self.checks if not check.passed]


@dataclass(slots=True)
class EvalReport:
    """The whole run."""

    outcomes: list[CaseOutcome]
    model: str
    prompt_version: str = PROMPT_VERSION
    repeat: int = 1

    @property
    def score(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(outcome.score for outcome in self.outcomes) / len(self.outcomes)

    @property
    def passed(self) -> bool:
        return all(outcome.passed for outcome in self.outcomes)

    @property
    def cost_usd(self) -> float:
        return sum(outcome.cost_usd for outcome in self.outcomes)


class EvalRunner:
    """Generates each golden case and scores the result."""

    def __init__(
        self,
        *,
        llm: LLMPort,
        model: str,
        max_tokens: int = 4096,
        repeat: int = 1,
    ) -> None:
        self._llm = llm
        self._model = model
        self._max_tokens = max_tokens
        # Generation is not deterministic, and on a small model the spread
        # between two runs of the same case can exceed any sane regression
        # threshold. One sample is a data point; the mean of several is a
        # measurement. Left at 1 by default because most runs are a quick
        # "did I break it?", and raised when recording a baseline.
        self._repeat = max(1, repeat)

    def run(self, cases: list[EvalCase]) -> EvalReport:
        return EvalReport(
            outcomes=[self._run_case(case) for case in cases],
            model=self._model,
            repeat=self._repeat,
        )

    def _run_case(self, case: EvalCase) -> CaseOutcome:
        """Sample the case ``repeat`` times and keep the median outcome.

        Median rather than mean: a single failed call would drag an average
        down far enough to look like a collapse, and the question being asked
        is "what does this case typically score?".
        """
        if self._repeat == 1:
            return self._run_one(case)
        samples = sorted(
            (self._run_one(case) for _ in range(self._repeat)),
            key=lambda outcome: outcome.score,
        )
        return samples[len(samples) // 2]

    def _run_one(self, case: EvalCase) -> CaseOutcome:
        bound = logger.bind(case=case.key)
        request = LLMRequest(
            system=build_system(),
            prompt=build_prompt(
                _story_like(case),
                language=language_name(case.language),
                max_characters=case.max_characters,
            ),
            model=self._model,
            max_tokens=self._max_tokens,
            cacheable_prefix=build_cacheable_prefix(
                brand=_brand_for(case), channel=_channel_for(case), product=_PRODUCT
            ),
        )

        try:
            result = self._llm.generate_structured(request, GeneratedContent)
        except LLMError as exc:
            bound.warning("eval.generation_failed", code=exc.code)
            return CaseOutcome(key=case.key, error=f"{exc.code}: {exc}")

        body = result.value.body.strip()
        checks = evaluate(body=body, hook=first_sentence(body), case=case)
        bound.info(
            "eval.case_scored",
            score=round(overall_score(checks), 4),
            failed=[check.name for check in checks if not check.passed],
        )
        return CaseOutcome(
            key=case.key,
            checks=checks,
            body=body,
            cost_usd=result.cost_usd,
            output_tokens=result.usage.output_tokens,
        )


#: Evals exercise one product shape; varying it would measure the pack rather
#: than the prompt.
_PRODUCT = ProductDef(key="social_post", name="Social post", kind=ProductKind.POST)


def _channel_for(case: EvalCase) -> ChannelDef:
    """A channel definition carrying the case's own hard limit."""
    return ChannelDef(
        key=case.channel_key,
        name=case.channel_key.title(),
        max_characters=case.max_characters,
        max_hashtags=3,
        preferred_products=[_PRODUCT.key],
    )


def _brand_for(case: EvalCase) -> BrandProfile:
    """A deliberately plain voice.

    The golden set measures whether the *prompt* holds up, so the voice is held
    constant and minimal. Testing a real brand profile here would mean every
    edit to that profile registered as a quality regression.
    """
    return BrandProfile(
        workspace_id="ws_eval",
        key=f"eval_{case.language}",
        name="Evaluation voice",
        language=case.language,
        audience=(
            "Engineers who already read AI news and want the implication, not the press release."
        ),
        tone_rules=["Direct and specific. Lead with what changed."],
        style_rules=["First sentence carries the news.", "Concrete numbers over adjectives."],
    )


def _story_like(case: EvalCase) -> Story:
    """Adapt a golden case to what ``build_prompt`` reads off a story.

    Built rather than stored: a golden case owns no workspace, status or
    resolution history, and inventing them would make the eval depend on
    pipeline state it is supposed to be independent of.
    """
    story = Story(
        workspace_id="ws_eval",
        canonical_headline=case.headline,
        primary_url=case.source_url,
    )
    story.enrichment = EnrichedSource(url=case.source_url, text=case.source_text)
    return story
