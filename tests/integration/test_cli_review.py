"""End-to-end CLI tests for the review, export, publish and metrics commands.

These drive the whole Phase 5 loop the way an operator does -- through the
command line, against a real database and real files -- so the tests cover
argument handling, exit codes and the wiring between commands, none of which
the use-case tests touch.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.cli.container import build_container
from engine.core.domain.content import ContentAsset, RevisionOrigin
from engine.core.domain.enums import ContentState, StoryStatus
from engine.core.domain.story import EnrichedSource, Story
from engine.settings import get_settings

pytestmark = pytest.mark.integration

runner = CliRunner()

BODY = "The lab published the training recipe, so small teams can reproduce the result."


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'review.db'}")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENGINE_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("ENGINE_GOOGLE_CREDENTIALS_FILE", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("ENGINE_GOOGLE_TOKEN_FILE", str(tmp_path / "token.json"))
    monkeypatch.setenv("COLUMNS", "240")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def seeded(cli_env: Path) -> Path:
    # The app module is imported lazily so each test picks up its own settings.
    from engine.cli.main import app

    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0
    return cli_env


@pytest.fixture
def app_module():
    from engine.cli.main import app

    return app


def add_draft(body: str = BODY, *, language: str = "en", channel: str = "linkedin") -> str:
    """Seed one generated draft and return its asset id."""
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug("default")
        assert workspace is not None
        actor = uow.actors.get_by_key(workspace.account_id, "system")
        assert actor is not None

        story = Story(
            workspace_id=workspace.id,
            canonical_headline="Open weights released",
            primary_url="https://arxiv.org/abs/1",
            status=StoryStatus.SELECTED,
            first_seen_at=container.clock.now(),
            last_seen_at=container.clock.now(),
            selected_at=container.clock.now(),
        )
        story.enrichment = EnrichedSource(
            url="https://arxiv.org/abs/1", text="The abstract.", fetched_at=container.clock.now()
        )
        uow.stories.add(story)

        asset = ContentAsset(
            workspace_id=workspace.id,
            story_id=story.id,
            run_id="run_seed",
            product_key="social_post",
            channel_key=channel,
            language=language,
            brand_profile_key=f"default_{language}",
            meta={"hashtags": ["ai"]},
        )
        revision = asset.next_revision(
            body, origin=RevisionOrigin.GENERATED, actor_id=actor.id, model="test-model"
        )
        asset.current_revision_id = revision.id
        uow.assets.add(asset)
        uow.assets.add_revision(revision)
        uow.commit()
        return asset.id


def state_of(asset_id: str) -> ContentState:
    container = build_container()
    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug("default")
        assert workspace is not None
        asset = uow.assets.get(workspace.id, asset_id)
    assert asset is not None
    return asset.state


class TestReviewQueue:
    def test_an_empty_queue_says_what_to_do_next(self, seeded, app_module):
        result = runner.invoke(app_module, ["review", "queue"])

        assert result.exit_code == 0
        assert "Nothing waiting" in result.stdout
        assert "engine generate run" in result.stdout

    def test_it_lists_a_draft(self, seeded, app_module):
        asset_id = add_draft()

        result = runner.invoke(app_module, ["review", "queue"])

        assert result.exit_code == 0
        assert asset_id in result.stdout
        assert "draft" in result.stdout

    def test_an_unknown_state_lists_the_valid_ones(self, seeded, app_module):
        result = runner.invoke(app_module, ["review", "queue", "--state", "pending"])

        assert result.exit_code == 1
        assert "approved" in result.stdout

    def test_showing_an_unknown_asset_exits_nonzero(self, seeded, app_module):
        result = runner.invoke(app_module, ["review", "show", "ast_missing"])

        assert result.exit_code == 1
        assert "not found" in result.stdout


class TestReviewDecisions:
    def test_approving_moves_the_asset(self, seeded, app_module):
        asset_id = add_draft()

        result = runner.invoke(app_module, ["review", "approve", asset_id])

        assert result.exit_code == 0
        assert state_of(asset_id) is ContentState.APPROVED

    def test_rejecting_requires_a_reason(self, seeded, app_module):
        asset_id = add_draft()

        result = runner.invoke(app_module, ["review", "reject", asset_id])

        assert result.exit_code != 0
        assert state_of(asset_id) is ContentState.DRAFT

    def test_rejecting_with_a_reason_records_it(self, seeded, app_module):
        asset_id = add_draft()

        result = runner.invoke(
            app_module, ["review", "reject", asset_id, "--reason", "reads like the abstract"]
        )

        assert result.exit_code == 0
        assert state_of(asset_id) is ContentState.REJECTED

    def test_an_unknown_actor_is_refused_rather_than_invented(self, seeded, app_module):
        asset_id = add_draft()

        result = runner.invoke(app_module, ["review", "approve", asset_id, "--as", "ghost"])

        assert result.exit_code == 1
        assert "no actor 'ghost'" in result.stdout
        assert state_of(asset_id) is ContentState.DRAFT


class TestEditing:
    def test_editing_from_a_file(self, seeded, app_module, tmp_path):
        asset_id = add_draft()
        replacement = tmp_path / "edited.txt"
        replacement.write_text("Small teams can reproduce this now.", encoding="utf-8")

        result = runner.invoke(app_module, ["review", "edit", asset_id, "--file", str(replacement)])

        assert result.exit_code == 0
        assert "revision 2" in result.stdout

    def test_editing_arabic_from_a_file(self, seeded, app_module, tmp_path):
        """The wedge language, read through an explicit UTF-8 decode."""
        asset_id = add_draft(language="ar")
        arabic = "أطلق المختبر نموذجًا مفتوح الأوزان ونشر وصفة التدريب كاملة."
        replacement = tmp_path / "edited_ar.txt"
        replacement.write_text(arabic, encoding="utf-8")

        result = runner.invoke(app_module, ["review", "edit", asset_id, "--file", str(replacement)])

        assert result.exit_code == 0
        container = build_container()
        with container.unit_of_work() as uow:
            workspace = uow.workspaces.get_by_slug("default")
            assert workspace is not None
            revisions = uow.assets.list_revisions(workspace.id, asset_id)
        assert revisions[-1].body == arabic

    def test_both_file_and_body_is_refused(self, seeded, app_module, tmp_path):
        asset_id = add_draft()
        replacement = tmp_path / "edited.txt"
        replacement.write_text("Text.", encoding="utf-8")

        result = runner.invoke(
            app_module,
            ["review", "edit", asset_id, "--file", str(replacement), "--body", "Text."],
        )

        assert result.exit_code == 1
        assert "exactly one" in result.stdout

    def test_neither_file_nor_body_is_refused(self, seeded, app_module):
        result = runner.invoke(app_module, ["review", "edit", add_draft()])

        assert result.exit_code == 1

    def test_a_missing_file_is_reported(self, seeded, app_module, tmp_path):
        result = runner.invoke(
            app_module, ["review", "edit", add_draft(), "--file", str(tmp_path / "gone.txt")]
        )

        assert result.exit_code == 1
        assert "No such file" in result.stdout


class TestExport:
    def test_nothing_approved_says_so(self, seeded, app_module):
        add_draft()

        result = runner.invoke(app_module, ["export", "run"])

        assert result.exit_code == 0
        assert "Nothing exported" in result.stdout
        assert "engine review queue" in result.stdout

    def test_it_writes_a_file_per_approved_post(self, seeded, app_module, cli_env):
        asset_id = add_draft()
        runner.invoke(app_module, ["review", "approve", asset_id])

        result = runner.invoke(app_module, ["export", "run"])

        assert result.exit_code == 0
        written = list((cli_env / "output" / "default").rglob("*.txt"))
        assert len(written) == 1
        assert BODY in written[0].read_text(encoding="utf-8")

    def test_show_reads_the_latest_export(self, seeded, app_module):
        runner.invoke(app_module, ["review", "approve", add_draft()])
        runner.invoke(app_module, ["export", "run"])

        result = runner.invoke(app_module, ["export", "show"])

        assert result.exit_code == 0
        assert "linkedin" in result.stdout

    def test_show_without_an_export_says_what_to_run(self, seeded, app_module):
        result = runner.invoke(app_module, ["export", "show"])

        assert result.exit_code == 1
        assert "engine export run" in result.stdout


class TestPublishAndMeasure:
    def _publication_id(self, app_module) -> str:
        container = build_container()
        with container.unit_of_work() as uow:
            workspace = uow.workspaces.get_by_slug("default")
            assert workspace is not None
            publications = uow.publications.list_recent(workspace.id)
        return publications[0].id

    def test_recording_an_unapproved_asset_is_refused(self, seeded, app_module):
        result = runner.invoke(app_module, ["publish", "record", add_draft()])

        assert result.exit_code == 1
        assert "not approved" in result.stdout

    def test_the_whole_loop_through_the_cli(self, seeded, app_module):
        """Draft to measured publication, exactly as an operator does it."""
        asset_id = add_draft()

        assert runner.invoke(app_module, ["review", "approve", asset_id]).exit_code == 0
        assert runner.invoke(app_module, ["export", "run"]).exit_code == 0
        published = runner.invoke(
            app_module,
            ["publish", "record", asset_id, "--url", "https://linkedin.test/posts/1"],
        )
        assert published.exit_code == 0
        assert state_of(asset_id) is ContentState.PUBLISHED

        publication_id = self._publication_id(app_module)
        measured = runner.invoke(
            app_module,
            ["metrics", "add", publication_id, "-m", "impressions", "-v", "4210"],
        )
        assert measured.exit_code == 0

        report = runner.invoke(app_module, ["metrics", "report"])
        assert report.exit_code == 0
        assert "impressions" in report.stdout
        assert "4210" in report.stdout

    def test_an_unreadable_date_is_reported(self, seeded, app_module):
        asset_id = add_draft()
        runner.invoke(app_module, ["review", "approve", asset_id])

        result = runner.invoke(app_module, ["publish", "record", asset_id, "--at", "last Tuesday"])

        assert result.exit_code == 1
        assert "Cannot read" in result.stdout

    def test_a_future_capture_time_is_reported_not_crashed(self, seeded, app_module):
        asset_id = add_draft()
        runner.invoke(app_module, ["review", "approve", asset_id])
        runner.invoke(app_module, ["publish", "record", asset_id])
        publication_id = self._publication_id(app_module)

        result = runner.invoke(
            app_module,
            ["metrics", "add", publication_id, "-m", "likes", "-v", "1", "--at", "2099-01-01"],
        )

        assert result.exit_code == 1
        assert "in the future" in result.stdout

    def test_the_report_names_what_has_not_been_measured(self, seeded, app_module):
        asset_id = add_draft()
        runner.invoke(app_module, ["review", "approve", asset_id])
        runner.invoke(app_module, ["publish", "record", asset_id])

        result = runner.invoke(app_module, ["metrics", "report"])

        assert result.exit_code == 0
        assert "no readings yet" in result.stdout

    def test_an_empty_report_says_what_to_do(self, seeded, app_module):
        result = runner.invoke(app_module, ["metrics", "report"])

        assert result.exit_code == 0
        assert "Nothing published yet" in result.stdout

    def test_publish_list_is_empty_at_first(self, seeded, app_module):
        result = runner.invoke(app_module, ["publish", "list"])

        assert result.exit_code == 0
        assert "Nothing published yet" in result.stdout
