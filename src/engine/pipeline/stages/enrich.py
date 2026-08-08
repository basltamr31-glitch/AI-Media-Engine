"""Enrich stage -- fetch what the story is actually about.

This is where "newsletters are tips, not text" stops being a slogan. Until a
story carries content from its primary source it is second-hand, and
:attr:`Story.is_publishable` is False, so the editorial stage in Phase 3 cannot
select it however well it scores.

Unlike every stage before it, this one talks to the open web, which brings
failure modes the others do not have:

* **Most failures are permanent.** A paywall, a dead link or a JavaScript-only
  page will fail again tomorrow. Attempts are counted on the story so a
  hopeless URL is retried a few times and then left alone, instead of consuming
  the run budget every morning.
* **Someone else's server is involved.** One story per request, a per-run
  ceiling, and the engine's identifying user-agent. Concurrency would be easy
  and would make us a nuisance.
* **Fetching is not the same as trusting.** Content type and length are checked
  before extraction, and extraction quality is checked before storage.
"""

from __future__ import annotations

from typing import Any, Final

from engine.core.domain.enums import StoryStatus
from engine.core.domain.story import Story
from engine.core.ports.enrichment import ContentExtractor, ExtractionError
from engine.core.ports.http import HttpClient, HttpError
from engine.observability.logging import get_logger
from engine.pipeline.context import RunContext
from engine.pipeline.stages.base import StageResult

logger = get_logger(__name__)

#: After this many failed attempts a URL is presumed permanently unreachable.
#: Three is enough to survive a deploy or a rate limit and few enough that a
#: dead link stops costing anything.
DEFAULT_MAX_ATTEMPTS: Final = 3

#: Content types worth handing to an HTML extractor.
_HTML_TYPES: Final = ("text/html", "application/xhtml")

#: Story meta keys. Kept here so the stage and the CLI agree on spelling.
ATTEMPTS_KEY: Final = "enrichment_attempts"
ERROR_KEY: Final = "enrichment_error"

#: Stable failure codes for the ledger. The story keeps the full message; the
#: ledger counts these, so they must not embed anything that varies per page --
#: a character count or a URL turns one category into hundreds.
CODE_NO_URL: Final = "no_primary_url"
CODE_FETCH_FAILED: Final = "fetch_failed"
CODE_UNSUPPORTED_TYPE: Final = "unsupported_content_type"
CODE_EXTRACTOR_ERROR: Final = "extractor_error"


class EnrichStage:
    """Fetches and extracts the primary source behind each resolved story."""

    def __init__(
        self,
        *,
        http: HttpClient,
        extractor: ContentExtractor,
        max_stories: int = 25,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        only_story: str | None = None,
    ) -> None:
        self._http = http
        self._extractor = extractor
        self._max_stories = max_stories
        self._max_attempts = max_attempts
        self._only_story = only_story

    @property
    def name(self) -> str:
        return "enrich"

    def run(self, context: RunContext) -> StageResult:
        with context.uow as uow:
            if self._only_story:
                one = uow.stories.get(context.workspace_id, self._only_story)
                if one is None:
                    return StageResult.skip(f"story {self._only_story!r} not found")
                stories = [one]
            else:
                stories = uow.stories.list_needing_enrichment(
                    context.workspace_id,
                    limit=self._max_stories,
                    max_attempts=self._max_attempts,
                )

        if not stories:
            return StageResult.skip("no stories awaiting enrichment")

        enriched: list[Story] = []
        failed: list[Story] = []
        reasons: dict[str, int] = {}

        for story in stories:
            outcome = self._enrich_one(story)
            if outcome is None:
                enriched.append(story)
            else:
                reasons[outcome] = reasons.get(outcome, 0) + 1
                failed.append(story)

            # Committed per story, not per run. Each one costs a request to
            # somebody else's server, and batching the write meant a single
            # interruption -- a Ctrl-C, a dropped connection on story 18 --
            # threw away every fetch that had already succeeded.
            if not context.dry_run:
                with context.uow as uow:
                    uow.stories.update(story)
                    uow.commit()

        return StageResult(
            items_in=len(stories),
            items_out=len(enriched),
            meta={
                "considered": len(stories),
                "enriched": len(enriched),
                "failed": len(failed),
                "failure_reasons": reasons,
                "max_attempts": self._max_attempts,
                "dry_run": context.dry_run,
            },
        )

    def _enrich_one(self, story: Story) -> str | None:
        """Enrich one story in place. Returns a stable failure code, or None."""
        story_logger = logger.bind(story_id=story.id, url=story.primary_url)
        url = story.primary_url or ""
        if not url:
            return self._fail(story, "no primary url", CODE_NO_URL, story_logger)

        try:
            response = self._http.get(url)
        except HttpError as exc:
            # The status is part of the category: 403 (blocked), 401 (paywall)
            # and 404 (gone) call for different responses from an operator.
            code = (
                f"{CODE_FETCH_FAILED}_{exc.status_code}" if exc.status_code else CODE_FETCH_FAILED
            )
            return self._fail(story, f"fetch failed: {exc}", code, story_logger)

        content_type = _content_type_of(response.headers)
        if content_type and not content_type.startswith(_HTML_TYPES):
            # A PDF or an image is not a failure of the link, but this
            # extractor cannot read it. Recorded so a PDF extractor later has a
            # list of exactly what it would unlock.
            return self._fail(
                story,
                f"unsupported content type {content_type!r}",
                CODE_UNSUPPORTED_TYPE,
                story_logger,
            )

        try:
            enrichment = self._extractor.extract(response.text, url=response.url or url)
        except ExtractionError as exc:
            return self._fail(story, str(exc), exc.code, story_logger)
        except Exception as exc:
            # The module promises per-story fault isolation; an extractor that
            # raises something unforeseen on one malformed page must cost that
            # page rather than the run.
            return self._fail(
                story,
                f"extractor error: {type(exc).__name__}: {exc}",
                CODE_EXTRACTOR_ERROR,
                story_logger,
            )

        story.enrichment = enrichment
        story.status = StoryStatus.ENRICHED
        story.meta.pop(ERROR_KEY, None)
        story.meta.pop(ATTEMPTS_KEY, None)
        story_logger.info(
            "enrich.succeeded",
            characters=len(enrichment.text),
            extracted_title=enrichment.title,
        )
        return None

    def _fail(self, story: Story, reason: str, code: str, story_logger: Any) -> str:
        """Record a failure on the story and return its ledger code.

        The story keeps the full message -- an operator debugging one link
        wants the URL and the status. The ledger gets the code, which stays the
        same across pages so counts mean something.
        """
        attempts = int(story.meta.get(ATTEMPTS_KEY, 0)) + 1
        story.meta[ATTEMPTS_KEY] = attempts
        story.meta[ERROR_KEY] = reason
        story_logger.warning("enrich.failed", reason=reason, code=code, attempts=attempts)
        return code


def _content_type_of(headers: dict[str, str]) -> str:
    for key, value in headers.items():
        if key.casefold() == "content-type":
            return value.split(";")[0].strip().casefold()
    return ""
