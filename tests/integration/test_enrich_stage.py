"""Integration tests for the enrich stage, against a real database.

This is the only stage that touches the open web, so the tests concentrate on
what it does when the web misbehaves: dead links, paywalls, PDFs, servers that
refuse a non-browser client. Each of those is permanent, and a stage that
retried them forever would spend every morning's budget on the same failures.
"""

from __future__ import annotations

import pytest

from engine.core.domain.enums import RunStatus, StoryStatus
from engine.core.domain.story import Story
from engine.core.ports.enrichment import ExtractionError
from engine.core.ports.http import HttpError
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.enrich import ATTEMPTS_KEY, ERROR_KEY, EnrichStage
from tests.fakes import FakeExtractor, FakeHttpClient

pytestmark = pytest.mark.integration

ARTICLE = "<html><body><article><p>The announcement in full.</p></article></body></html>"


def store_story(uow, workspace, clock, *, url: str | None = "https://example.com/post") -> Story:
    story = Story(
        workspace_id=workspace.id,
        canonical_headline="A story worth reading",
        primary_url=url,
        status=StoryStatus.RESOLVED,
        first_seen_at=clock.now(),
        last_seen_at=clock.now(),
    )
    with uow as active:
        active.stories.add(story)
        active.commit()
    return story


def run_stage(uow, clock, settings, workspace_id, stage, *, dry_run: bool = False):
    runner = PipelineRunner(uow=uow, clock=clock, settings=settings)
    return runner.run(workspace_id=workspace_id, pipeline="enrich", stages=[stage], dry_run=dry_run)


def build_stage(
    responses: dict[str, str] | None = None,
    *,
    error: Exception | None = None,
    content_type: str = "text/html",
    extractor: FakeExtractor | None = None,
    **kwargs,
) -> EnrichStage:
    http = FakeHttpClient(responses or {}, error=error, content_type=content_type)
    return EnrichStage(http=http, extractor=extractor or FakeExtractor(), **kwargs)


def reload_story(uow, workspace_id, story_id) -> Story:
    with uow as active:
        found: Story | None = active.stories.get(workspace_id, story_id)
    assert found is not None
    return found


class TestHappyPath:
    def test_a_story_gains_its_primary_source(self, uow, clock, settings, persisted_workspace):
        story = store_story(uow, persisted_workspace, clock)

        ledger = run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage({"https://example.com/post": ARTICLE}),
        )

        assert ledger.status is RunStatus.SUCCEEDED
        enriched = reload_story(uow, persisted_workspace.id, story.id)
        assert enriched.enrichment is not None
        assert enriched.enrichment.text

    def test_it_becomes_publishable(self, uow, clock, settings, persisted_workspace):
        """The editorial contract: we do not write about second-hand reports."""
        story = store_story(uow, persisted_workspace, clock)
        assert not story.is_publishable

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage({"https://example.com/post": ARTICLE}),
        )

        enriched = reload_story(uow, persisted_workspace.id, story.id)
        assert enriched.status is StoryStatus.ENRICHED
        assert enriched.is_publishable

    def test_an_enriched_story_is_not_fetched_again(
        self, uow, clock, settings, persisted_workspace
    ):
        store_story(uow, persisted_workspace, clock)
        http = FakeHttpClient({"https://example.com/post": ARTICLE}, content_type="text/html")
        stage = EnrichStage(http=http, extractor=FakeExtractor())

        run_stage(uow, clock, settings, persisted_workspace.id, stage)
        run_stage(uow, clock, settings, persisted_workspace.id, stage)

        assert len(http.calls) == 1

    def test_the_ledger_counts_what_happened(self, uow, clock, settings, persisted_workspace):
        store_story(uow, persisted_workspace, clock)

        ledger = run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage({"https://example.com/post": ARTICLE}),
        )

        with uow as active:
            record = next(
                r
                for r in active.runs.list_stages(persisted_workspace.id, ledger.id)
                if r.stage == "enrich"
            )
        assert record.meta["enriched"] == 1
        assert record.meta["failed"] == 0

    def test_a_dry_run_stores_nothing(self, uow, clock, settings, persisted_workspace):
        story = store_story(uow, persisted_workspace, clock)

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage({"https://example.com/post": ARTICLE}),
            dry_run=True,
        )

        assert reload_story(uow, persisted_workspace.id, story.id).enrichment is None

    def test_one_story_can_be_targeted(self, uow, clock, settings, persisted_workspace):
        wanted = store_story(uow, persisted_workspace, clock)
        store_story(uow, persisted_workspace, clock, url="https://example.com/other")

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage({"https://example.com/post": ARTICLE}, only_story=wanted.id),
        )

        assert reload_story(uow, persisted_workspace.id, wanted.id).enrichment is not None


class TestFailures:
    def test_a_dead_link_is_recorded_not_raised(self, uow, clock, settings, persisted_workspace):
        story = store_story(uow, persisted_workspace, clock)

        ledger = run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage(error=HttpError("https://example.com/post", "404", status_code=404)),
        )

        assert ledger.status is RunStatus.SUCCEEDED
        failed = reload_story(uow, persisted_workspace.id, story.id)
        assert failed.enrichment is None
        assert failed.status is StoryStatus.RESOLVED
        assert "fetch failed" in failed.meta[ERROR_KEY]

    def test_a_paywall_is_recorded_with_its_reason(self, uow, clock, settings, persisted_workspace):
        story = store_story(uow, persisted_workspace, clock)

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage(
                {"https://example.com/post": ARTICLE},
                extractor=FakeExtractor(fails_with="only 90 characters extracted"),
            ),
        )

        assert (
            "90 characters" in reload_story(uow, persisted_workspace.id, story.id).meta[ERROR_KEY]
        )

    def test_a_pdf_is_skipped_rather_than_mangled(self, uow, clock, settings, persisted_workspace):
        """Recorded, so a PDF extractor later has a list of what it unlocks."""
        story = store_story(uow, persisted_workspace, clock)

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage({"https://example.com/post": "%PDF-1.7"}, content_type="application/pdf"),
        )

        assert (
            "unsupported content type"
            in reload_story(uow, persisted_workspace.id, story.id).meta[ERROR_KEY]
        )

    def test_a_story_with_no_link_cannot_be_enriched(
        self, uow, clock, settings, persisted_workspace
    ):
        story = store_story(uow, persisted_workspace, clock, url=None)

        run_stage(uow, clock, settings, persisted_workspace.id, build_stage(only_story=story.id))

        assert reload_story(uow, persisted_workspace.id, story.id).meta[ERROR_KEY] == (
            "no primary url"
        )

    def test_one_failure_does_not_stop_the_others(self, uow, clock, settings, persisted_workspace):
        good = store_story(uow, persisted_workspace, clock, url="https://example.com/good")
        bad = store_story(uow, persisted_workspace, clock, url="https://example.com/bad")

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage({"https://example.com/good": ARTICLE}),
        )

        assert reload_story(uow, persisted_workspace.id, good.id).enrichment is not None
        assert reload_story(uow, persisted_workspace.id, bad.id).enrichment is None


class TestRetryBudget:
    def test_attempts_are_counted(self, uow, clock, settings, persisted_workspace):
        story = store_story(uow, persisted_workspace, clock)
        stage = build_stage(error=HttpError("https://example.com/post", "boom"))

        run_stage(uow, clock, settings, persisted_workspace.id, stage)
        run_stage(uow, clock, settings, persisted_workspace.id, stage)

        assert reload_story(uow, persisted_workspace.id, story.id).meta[ATTEMPTS_KEY] == 2

    def test_a_hopeless_url_is_eventually_left_alone(
        self, uow, clock, settings, persisted_workspace
    ):
        """Otherwise every morning is spent re-fetching the same dead links."""
        store_story(uow, persisted_workspace, clock)
        http = FakeHttpClient(
            {}, error=HttpError("https://example.com/post", "gone"), content_type="text/html"
        )
        stage = EnrichStage(http=http, extractor=FakeExtractor(), max_attempts=2)

        for _ in range(4):
            run_stage(uow, clock, settings, persisted_workspace.id, stage)

        assert len(http.calls) == 2

    def test_a_success_clears_the_failure_record(self, uow, clock, settings, persisted_workspace):
        story = store_story(uow, persisted_workspace, clock)

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage(error=HttpError("https://example.com/post", "temporary")),
        )
        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage({"https://example.com/post": ARTICLE}),
        )

        enriched = reload_story(uow, persisted_workspace.id, story.id)
        assert enriched.enrichment is not None
        assert ERROR_KEY not in enriched.meta
        assert ATTEMPTS_KEY not in enriched.meta


class TestSelection:
    def test_nothing_to_enrich_is_a_skip(self, uow, clock, settings, persisted_workspace):
        ledger = run_stage(uow, clock, settings, persisted_workspace.id, build_stage())
        assert ledger.status is RunStatus.SUCCEEDED

    def test_the_per_run_ceiling_is_respected(self, uow, clock, settings, persisted_workspace):
        """Every fetch is a request to somebody else's server."""
        for index in range(5):
            store_story(uow, persisted_workspace, clock, url=f"https://example.com/{index}")
        http = FakeHttpClient(
            {f"https://example.com/{i}": ARTICLE for i in range(5)}, content_type="text/html"
        )

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            EnrichStage(http=http, extractor=FakeExtractor(), max_stories=2),
        )

        assert len(http.calls) == 2

    def test_an_unknown_story_id_is_a_skip(self, uow, clock, settings, persisted_workspace):
        ledger = run_stage(
            uow, clock, settings, persisted_workspace.id, build_stage(only_story="sty_nope")
        )
        assert ledger.status is RunStatus.SUCCEEDED


class TestInterruption:
    """Enrichment spends real network I/O per story; losing it is expensive."""

    def test_completed_work_survives_an_interruption(
        self, uow, clock, settings, persisted_workspace
    ):
        """Ctrl-C on story three must not discard stories one and two."""
        first = store_story(uow, persisted_workspace, clock, url="https://example.com/0")
        second = store_story(uow, persisted_workspace, clock, url="https://example.com/1")
        store_story(uow, persisted_workspace, clock, url="https://example.com/2")

        class Interrupting(FakeExtractor):
            def __init__(self) -> None:
                super().__init__()
                self._seen = 0

            def extract(self, html, *, url):
                self._seen += 1
                if self._seen > 2:
                    raise KeyboardInterrupt
                return super().extract(html, url=url)

        http = FakeHttpClient(
            {f"https://example.com/{index}": ARTICLE for index in range(3)},
            content_type="text/html",
        )
        stage = EnrichStage(http=http, extractor=Interrupting())

        with pytest.raises(KeyboardInterrupt):
            run_stage(uow, clock, settings, persisted_workspace.id, stage)

        assert reload_story(uow, persisted_workspace.id, first.id).enrichment is not None
        assert reload_story(uow, persisted_workspace.id, second.id).enrichment is not None

    def test_an_unforeseen_extractor_error_costs_only_that_story(
        self, uow, clock, settings, persisted_workspace
    ):
        """The module promises fault isolation, so it has to isolate anything."""
        exploding = store_story(uow, persisted_workspace, clock, url="https://example.com/0")
        fine = store_story(uow, persisted_workspace, clock, url="https://example.com/1")

        class Exploding(FakeExtractor):
            def extract(self, html, *, url):
                if url.endswith("/0"):
                    raise RecursionError("maximum recursion depth exceeded")
                return super().extract(html, url=url)

        stage = EnrichStage(
            http=FakeHttpClient(
                {f"https://example.com/{index}": ARTICLE for index in range(2)},
                content_type="text/html",
            ),
            extractor=Exploding(),
        )

        ledger = run_stage(uow, clock, settings, persisted_workspace.id, stage)

        assert ledger.status is RunStatus.SUCCEEDED
        assert reload_story(uow, persisted_workspace.id, fine.id).enrichment is not None
        broken = reload_story(uow, persisted_workspace.id, exploding.id)
        assert "extractor error" in broken.meta[ERROR_KEY]
        assert "RecursionError" in broken.meta[ERROR_KEY]


class TestFailureCodes:
    """The ledger counts codes, so they must not vary with the page."""

    def _reasons(self, uow, workspace_id, ledger) -> dict[str, int]:
        with uow as active:
            record = next(
                r for r in active.runs.list_stages(workspace_id, ledger.id) if r.stage == "enrich"
            )
        reasons: dict[str, int] = record.meta["failure_reasons"]
        return reasons

    def test_two_short_pages_of_different_lengths_are_one_category(
        self, uow, clock, settings, persisted_workspace
    ):
        """Regression: the code was sliced off the message, so "only 320
        characters" and "only 91 characters" counted as separate failures."""
        store_story(uow, persisted_workspace, clock, url="https://example.com/0")
        store_story(uow, persisted_workspace, clock, url="https://example.com/1")

        class Stubby(FakeExtractor):
            def extract(self, html, *, url):
                length = 320 if url.endswith("/0") else 91
                raise ExtractionError(
                    url, f"only {length} characters extracted", code="text_too_short"
                )

        ledger = run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            EnrichStage(
                http=FakeHttpClient(
                    {f"https://example.com/{i}": ARTICLE for i in range(2)},
                    content_type="text/html",
                ),
                extractor=Stubby(),
            ),
        )

        assert self._reasons(uow, persisted_workspace.id, ledger) == {"text_too_short": 2}

    def test_a_url_containing_a_colon_does_not_split_the_code(
        self, uow, clock, settings, persisted_workspace
    ):
        """Regression: `https://` split the label in half."""
        store_story(uow, persisted_workspace, clock, url="https://example.com/0")

        ledger = run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            EnrichStage(
                http=FakeHttpClient({"https://example.com/0": ARTICLE}, content_type="text/html"),
                extractor=FakeExtractor(fails_with="no article text found"),
            ),
        )

        codes = self._reasons(uow, persisted_workspace.id, ledger)
        assert all("https" not in code for code in codes)

    def test_the_http_status_is_part_of_the_category(
        self, uow, clock, settings, persisted_workspace
    ):
        """403 (blocked) and 401 (paywall) need different responses."""
        store_story(uow, persisted_workspace, clock, url="https://example.com/0")

        ledger = run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage(error=HttpError("https://example.com/0", "denied", status_code=403)),
        )

        assert self._reasons(uow, persisted_workspace.id, ledger) == {"fetch_failed_403": 1}

    def test_the_story_still_keeps_the_readable_message(
        self, uow, clock, settings, persisted_workspace
    ):
        """Codes are for counting; an operator debugging one link needs detail."""
        story = store_story(uow, persisted_workspace, clock, url="https://example.com/0")

        run_stage(
            uow,
            clock,
            settings,
            persisted_workspace.id,
            build_stage(error=HttpError("https://example.com/0", "denied", status_code=403)),
        )

        detail = reload_story(uow, persisted_workspace.id, story.id).meta[ERROR_KEY]
        assert "403" in detail
        assert "example.com/0" in detail
