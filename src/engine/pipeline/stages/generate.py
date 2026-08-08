"""Generate stage -- turn selected stories into publishable text.

The matrix is brand voice x channel x language. One story selected for a
workspace with two languages and three channels is six assets, each addressable
on its own so a bad LinkedIn post can be regenerated without touching the
Arabic caption that came out fine.

Four decisions worth knowing about:

* **Only selected, only enriched.** Selection already refused anything without
  a primary source read first-hand; this stage re-checks rather than trusting,
  because generating from a newsletter's summary is the one failure that would
  make the engine a republisher.
* **Regeneration appends a revision.** An existing asset for the same slot is
  not duplicated and not overwritten -- it gains revision *n+1* and its pointer
  moves. That is what preserves the generated-versus-edited difference the
  revision chain exists for (ADR 0013).
* **Committed per asset.** Each one costs a model call. Batching the write
  meant a single interruption threw away every generation that had already
  succeeded, which is a lesson enrichment already paid for.
* **Over-length output is a failure, not a truncation.** A post cut off at the
  character limit reads as broken; refusing it and saying so is honest, and the
  fix is a prompt or a model, not a substring.
"""

from __future__ import annotations

from typing import Final

from engine.adapters.generation.prompts import (
    PROMPT_VERSION,
    GeneratedContent,
    build_cacheable_prefix,
    build_prompt,
    build_system,
    first_sentence,
)
from engine.core.domain.content import BrandProfile, ContentAsset, RevisionOrigin
from engine.core.domain.enums import StoryStatus
from engine.core.domain.language import language_name
from engine.core.domain.ledger import TokenUsage
from engine.core.domain.pack import ChannelDef, DomainPack, ProductDef
from engine.core.domain.story import Story
from engine.core.ports.llm import LLMError, LLMPort, LLMRequest, LLMResult
from engine.observability.logging import get_logger
from engine.pipeline.context import RunContext
from engine.pipeline.stages.base import StageResult

logger = get_logger(__name__)

#: Model output is attributed to this actor. Revisions record who wrote them,
#: and "the system" has to be a real, stable value for the edit-delta dataset
#: to be able to tell a machine draft from a human rewrite.
SYSTEM_ACTOR: Final = "actor_system"

#: Asset meta keys, kept here so the stage and the CLI agree on spelling.
HOOK_KEY: Final = "hook"
HASHTAGS_KEY: Final = "hashtags"
MODEL_KEY: Final = "model"
COST_KEY: Final = "cost_usd"

#: Stable failure codes counted in the ledger.
CODE_NO_SOURCE: Final = "no_primary_source"
CODE_NO_BRAND: Final = "no_brand_profile"
CODE_TOO_LONG: Final = "over_channel_limit"
CODE_MODEL_FAILED: Final = "model_failed"


class GenerationTarget:
    """One cell of the matrix: a channel, a product and a language.

    A class rather than a tuple because the stage passes it through four
    functions, and positional tuples are how a channel and a language end up
    swapped without the type checker noticing.
    """

    __slots__ = ("brand", "channel", "product")

    def __init__(self, *, channel: ChannelDef, product: ProductDef, brand: BrandProfile) -> None:
        self.channel = channel
        self.product = product
        self.brand = brand

    @property
    def label(self) -> str:
        return f"{self.channel.key}/{self.brand.language}"


class GenerateStage:
    """Writes one asset per selected story, channel and language."""

    def __init__(
        self,
        *,
        llm: LLMPort,
        pack: DomainPack,
        brand_profiles: list[BrandProfile],
        model: str,
        max_stories: int = 5,
        channels: list[str] | None = None,
        only_story: str | None = None,
        max_tokens: int = 2048,
        max_cost_usd: float | None = None,
    ) -> None:
        self._llm = llm
        self._pack = pack
        self._brands = brand_profiles
        self._model = model
        self._max_stories = max_stories
        self._channels = channels
        self._only_story = only_story
        self._max_tokens = max_tokens
        # None disables the in-stage ceiling. Meaningful for a free local
        # provider, where every call is priced at zero and a budget check can
        # never fire; the runner's per-run ceiling still applies either way.
        self._max_cost_usd = max_cost_usd

    @property
    def name(self) -> str:
        return "generate"

    def run(self, context: RunContext) -> StageResult:
        stories = self._stories(context)
        if not stories:
            return StageResult.skip("no selected stories awaiting generation")

        targets = self._targets()
        if not targets:
            return StageResult.skip("no channel and brand-profile pairing to generate for")

        usage = TokenUsage()
        cost = 0.0
        written = 0
        failures: dict[str, int] = {}
        halted: str | None = None

        for story in stories:
            for target in targets:
                # Checked *before* the call, not after. The runner's ceiling
                # only applies between stages, so one generate run over a large
                # matrix could spend the whole budget several times over and
                # only be noticed once it had finished doing so.
                if self._max_cost_usd is not None and cost >= self._max_cost_usd:
                    halted = (
                        f"stopped at ${cost:.4f}, at or over the ${self._max_cost_usd:.2f} ceiling"
                    )
                    break

                outcome = self._generate_one(story, target, context)
                if outcome.code is not None:
                    failures[outcome.code] = failures.get(outcome.code, 0) + 1
                else:
                    written += 1
                usage = usage + outcome.usage
                cost += outcome.cost_usd
            if halted:
                break

        if halted:
            # Not an exception: everything already generated is committed and
            # worth keeping, and a partial batch that says so is more useful
            # than a failed run that discards the work it paid for.
            logger.warning("generate.budget_exhausted", cost_usd=round(cost, 6), written=written)

        return StageResult(
            items_in=len(stories) * len(targets),
            items_out=written,
            usage=usage,
            cost_usd=cost,
            meta={
                "stories": len(stories),
                "targets": [target.label for target in targets],
                "generated": written,
                "failures": failures,
                "prompt_version": PROMPT_VERSION,
                "model": self._model,
                "cost_ceiling_usd": self._max_cost_usd,
                "halted": halted,
                "dry_run": context.dry_run,
            },
        )

    # -- internals ---------------------------------------------------------

    def _stories(self, context: RunContext) -> list[Story]:
        with context.uow as uow:
            if self._only_story:
                one = uow.stories.get(context.workspace_id, self._only_story)
                # A story nobody selected has not passed the editorial gates,
                # and generating from it by id would route around them.
                if one is None or one.status is not StoryStatus.SELECTED:
                    return []
                return [one]
            return uow.stories.list_selected(context.workspace_id, limit=self._max_stories)

    def _targets(self) -> list[GenerationTarget]:
        """Every channel x language pairing the workspace can actually write.

        A channel with no brand profile in its language is skipped silently
        rather than generating in the wrong voice, which is the one output
        nobody wants.
        """
        targets: list[GenerationTarget] = []
        for channel in self._pack.channels:
            if self._channels and channel.key not in self._channels:
                continue
            product = self._product_for(channel)
            if product is None:
                continue
            for brand in self._brands:
                targets.append(GenerationTarget(channel=channel, product=product, brand=brand))
        return targets

    def _product_for(self, channel: ChannelDef) -> ProductDef | None:
        """What this channel wants written, in the channel's own order.

        The pack states each channel's preferences, so Instagram asking for a
        caption while LinkedIn asks for a post is pack data rather than a
        branch in this file.
        """
        for key in channel.preferred_products:
            product = self._pack.product(key)
            if product is not None:
                return product
        return None

    def _generate_one(
        self,
        story: Story,
        target: GenerationTarget,
        context: RunContext,
    ) -> _Outcome:
        bound = logger.bind(story_id=story.id, target=target.label)

        if story.enrichment is None or not story.enrichment.text.strip():
            bound.warning("generate.no_primary_source")
            return _Outcome(code=CODE_NO_SOURCE)

        request = LLMRequest(
            system=build_system(),
            prompt=build_prompt(story, language=language_name(target.brand.language)),
            model=self._model,
            max_tokens=self._max_tokens,
            cacheable_prefix=build_cacheable_prefix(
                brand=target.brand, channel=target.channel, product=target.product
            ),
            metadata={"story_id": story.id, "channel": target.channel.key},
        )

        try:
            result = self._llm.generate_structured(request, GeneratedContent)
        except LLMError as exc:
            bound.warning("generate.model_failed", code=exc.code, error=str(exc))
            return _Outcome(code=CODE_MODEL_FAILED)

        body = result.value.body.strip()
        if len(body) > target.channel.max_characters:
            # Truncating here would ship a sentence that stops mid-word. The
            # asset is refused so the run reports it, and the fix stays a
            # prompt or model change rather than a silent edit.
            bound.warning(
                "generate.over_limit",
                characters=len(body),
                limit=target.channel.max_characters,
            )
            return _Outcome(code=CODE_TOO_LONG, usage=result.usage, cost_usd=result.cost_usd)

        if not context.dry_run:
            self._persist(story, target, result, context)

        bound.info(
            "generate.written",
            characters=len(body),
            model=result.model,
            output_tokens=result.usage.output_tokens,
        )
        return _Outcome(usage=result.usage, cost_usd=result.cost_usd)

    def _persist(
        self,
        story: Story,
        target: GenerationTarget,
        result: LLMResult[GeneratedContent],
        context: RunContext,
    ) -> None:
        """Write the asset and its revision, committing this one alone."""
        content = result.value
        body = content.body.strip()
        hook = first_sentence(body)
        with context.uow as uow:
            asset = uow.assets.find_slot(
                context.workspace_id,
                story_id=story.id,
                product_key=target.product.key,
                channel_key=target.channel.key,
                language=str(target.brand.language),
            )
            previous = None
            if asset is None:
                asset = ContentAsset(
                    workspace_id=context.workspace_id,
                    story_id=story.id,
                    run_id=context.run_id,
                    product_key=target.product.key,
                    channel_key=target.channel.key,
                    language=target.brand.language,
                    brand_profile_key=target.brand.key,
                    evidence_ids=[
                        evidence.id
                        for evidence in uow.evidence.list_for_story(context.workspace_id, story.id)
                    ],
                )
                uow.assets.add(asset)
            else:
                previous = uow.assets.latest_revision(context.workspace_id, asset.id)

            revision = asset.next_revision(
                body,
                origin=RevisionOrigin.GENERATED,
                actor_id=SYSTEM_ACTOR,
                previous=previous,
                prompt_version=PROMPT_VERSION,
                model=result.model,
                # Priced now, at the rates that were in force. A later price
                # change must not rewrite what last month actually cost.
                usage=result.usage,
                cost_usd=result.cost_usd,
                meta={HOOK_KEY: hook, HASHTAGS_KEY: content.hashtags},
            )
            uow.assets.add_revision(revision)

            # Only now, with the revision durable in this transaction, does the
            # asset point at it.
            asset.current_revision_id = revision.id
            asset.run_id = context.run_id
            asset.meta.update(
                {
                    HOOK_KEY: hook,
                    HASHTAGS_KEY: content.hashtags,
                    MODEL_KEY: result.model,
                    COST_KEY: round(result.cost_usd, 6),
                }
            )
            asset.updated_at = context.clock.now()
            uow.assets.update(asset)
            uow.commit()


class _Outcome:
    """What one generation attempt produced. Internal to this stage."""

    __slots__ = ("code", "cost_usd", "usage")

    def __init__(
        self,
        *,
        code: str | None = None,
        usage: TokenUsage | None = None,
        cost_usd: float = 0.0,
    ) -> None:
        self.code = code
        self.usage = usage or TokenUsage()
        self.cost_usd = cost_usd
