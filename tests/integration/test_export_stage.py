"""Integration tests for the export stage.

Two properties matter beyond "it writes files". Export is driven by approval
state rather than by a time window -- that is what separates it from ``render``
-- and it never claims anything was published, because writing a file is not
evidence that a human posted it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from engine.cli import lifecycle
from engine.core.domain.account import OWNER_ACTOR_KEY, Actor
from engine.core.domain.content import ContentAsset, RevisionOrigin
from engine.core.domain.enums import ContentState, StoryStatus
from engine.core.domain.ledger import RunLedger
from engine.core.domain.pack import ChannelDef, DomainPack, ProductDef, ProductKind
from engine.core.domain.story import Story
from engine.core.domain.workspace import Workspace
from engine.pipeline.runner import PipelineRunner
from engine.pipeline.stages.export import EXPORT_DIRNAME, MANIFEST_NAME, ExportStage

pytestmark = pytest.mark.integration

BODY = "The lab published the training recipe, so small teams can reproduce the result."


def a_pack() -> DomainPack:
    return DomainPack(
        key="test-pack",
        version=1,
        name="Test pack",
        products=[ProductDef(key="social_post", name="Social post", kind=ProductKind.POST)],
        channels=[
            ChannelDef(
                key="linkedin",
                name="LinkedIn",
                max_characters=3000,
                max_hashtags=3,
                preferred_products=["social_post"],
            ),
            ChannelDef(
                key="x",
                name="X",
                max_characters=280,
                max_hashtags=2,
                preferred_products=["social_post"],
            ),
        ],
    )


@pytest.fixture
def owner(uow, persisted_account) -> Actor:
    with uow as active:
        actor: Actor | None = active.actors.get_by_key(persisted_account.id, OWNER_ACTOR_KEY)
    assert actor is not None
    return actor


@pytest.fixture
def story(uow, persisted_workspace, clock) -> Story:
    record = Story(
        workspace_id=persisted_workspace.id,
        canonical_headline="An open model was released",
        primary_url="https://example.test/model",
        status=StoryStatus.SELECTED,
        first_seen_at=clock.now(),
        last_seen_at=clock.now(),
    )
    with uow as active:
        active.stories.add(record)
        active.commit()
    return record


def make_asset(
    uow,
    workspace,
    story,
    system_actor,
    *,
    channel: str = "linkedin",
    language: str = "en",
    body: str = BODY,
    hashtags: list[str] | None = None,
) -> ContentAsset:
    asset = ContentAsset(
        workspace_id=workspace.id,
        story_id=story.id,
        run_id="run_test",
        product_key="social_post",
        channel_key=channel,
        language=language,
        brand_profile_key=f"house_{language}",
        meta={"hashtags": hashtags if hashtags is not None else ["ai"]},
    )
    revision = asset.next_revision(body, origin=RevisionOrigin.GENERATED, actor_id=system_actor.id)
    asset.current_revision_id = revision.id
    with uow as active:
        active.assets.add(asset)
        active.assets.add_revision(revision)
        active.commit()
    return asset


def approve(uow, workspace, asset, owner, clock) -> None:
    with uow as active:
        lifecycle.approve(active, workspace.id, asset.id, actor=owner, clock=clock)
        active.commit()


def run_export(uow, clock, settings, workspace, tmp_path, *, dry_run: bool = False) -> RunLedger:
    stage = ExportStage(
        output_dir=tmp_path,
        workspace_slug=workspace.slug,
        timezone=workspace.timezone,
        pack=a_pack(),
        edition="2026-08-08",
    )
    runner = PipelineRunner(uow=uow, clock=clock, settings=settings)
    return runner.run(workspace_id=workspace.id, pipeline="export", stages=[stage], dry_run=dry_run)


def export_dir(tmp_path: Path, workspace: Workspace) -> Path:
    return tmp_path / workspace.slug / "2026-08-08" / EXPORT_DIRNAME


def manifest_of(tmp_path: Path, workspace: Workspace) -> dict[str, Any]:
    raw = (export_dir(tmp_path, workspace) / MANIFEST_NAME).read_text(encoding="utf-8")
    manifest: dict[str, Any] = json.loads(raw)
    return manifest


class TestWhatGetsExported:
    def test_only_approved_content(
        self, uow, clock, settings, persisted_workspace, story, system_actor, owner, tmp_path
    ):
        """The difference between this and render: state, not a time window."""
        approved = make_asset(uow, persisted_workspace, story, system_actor)
        make_asset(uow, persisted_workspace, story, system_actor, channel="x")
        approve(uow, persisted_workspace, approved, owner, clock)

        run_export(uow, clock, settings, persisted_workspace, tmp_path)

        posts = manifest_of(tmp_path, persisted_workspace)["posts"]
        assert [post["asset_id"] for post in posts] == [approved.id]

    def test_nothing_approved_is_a_skip_not_a_failure(
        self, uow, clock, settings, persisted_workspace, story, system_actor, tmp_path
    ):
        make_asset(uow, persisted_workspace, story, system_actor)

        ledger = run_export(uow, clock, settings, persisted_workspace, tmp_path)

        with uow as active:
            record = next(r for r in active.runs.list_stages(persisted_workspace.id, ledger.id))
        assert record.meta["skip_reason"] == "nothing approved to export"
        assert not export_dir(tmp_path, persisted_workspace).exists()

    def test_the_file_holds_exactly_what_gets_pasted(
        self, uow, clock, settings, persisted_workspace, story, system_actor, owner, tmp_path
    ):
        asset = make_asset(uow, persisted_workspace, story, system_actor)
        approve(uow, persisted_workspace, asset, owner, clock)

        run_export(uow, clock, settings, persisted_workspace, tmp_path)

        post = manifest_of(tmp_path, persisted_workspace)["posts"][0]
        text = (export_dir(tmp_path, persisted_workspace) / post["file"]).read_text(
            encoding="utf-8"
        )
        assert text == f"{BODY}\n\n#ai\n\nhttps://example.test/model\n"

    def test_arabic_is_written_readable_not_escaped(
        self, uow, clock, settings, persisted_workspace, story, system_actor, owner, tmp_path
    ):
        """\\uXXXX soup in the manifest would make it unreviewable."""
        arabic = "أطلق المختبر نموذجًا مفتوح الأوزان ونشر وصفة التدريب."
        asset = make_asset(
            uow, persisted_workspace, story, system_actor, language="ar", body=arabic
        )
        approve(uow, persisted_workspace, asset, owner, clock)

        run_export(uow, clock, settings, persisted_workspace, tmp_path)

        post = manifest_of(tmp_path, persisted_workspace)["posts"][0]
        text = (export_dir(tmp_path, persisted_workspace) / post["file"]).read_text(
            encoding="utf-8"
        )
        assert arabic in text
        raw = (export_dir(tmp_path, persisted_workspace) / MANIFEST_NAME).read_text(
            encoding="utf-8"
        )
        assert "\\u" not in raw

    def test_two_channels_produce_two_files(
        self, uow, clock, settings, persisted_workspace, story, system_actor, owner, tmp_path
    ):
        for channel in ("linkedin", "x"):
            asset = make_asset(
                uow, persisted_workspace, story, system_actor, channel=channel, body="Short."
            )
            approve(uow, persisted_workspace, asset, owner, clock)

        run_export(uow, clock, settings, persisted_workspace, tmp_path)

        posts = manifest_of(tmp_path, persisted_workspace)["posts"]
        assert {post["channel"] for post in posts} == {"linkedin", "x"}
        assert len(list(export_dir(tmp_path, persisted_workspace).glob("*.txt"))) == 2


class TestWarnings:
    def test_an_over_limit_post_is_exported_and_flagged(
        self, uow, clock, settings, persisted_workspace, story, system_actor, owner, tmp_path
    ):
        """An operator with no file and no explanation is worse off."""
        asset = make_asset(
            uow, persisted_workspace, story, system_actor, channel="x", body="x" * 400
        )
        approve(uow, persisted_workspace, asset, owner, clock)

        run_export(uow, clock, settings, persisted_workspace, tmp_path)

        post = manifest_of(tmp_path, persisted_workspace)["posts"][0]
        assert post["publishable"] is False
        assert any("allowed on X" in warning for warning in post["warnings"])
        assert (export_dir(tmp_path, persisted_workspace) / post["file"]).is_file()

    def test_the_stage_counts_what_needs_attention(
        self, uow, clock, settings, persisted_workspace, story, system_actor, owner, tmp_path
    ):
        good = make_asset(uow, persisted_workspace, story, system_actor, body="Short and fine.")
        bad = make_asset(uow, persisted_workspace, story, system_actor, channel="x", body="x" * 400)
        for asset in (good, bad):
            approve(uow, persisted_workspace, asset, owner, clock)

        ledger = run_export(uow, clock, settings, persisted_workspace, tmp_path)

        with uow as active:
            meta = next(r for r in active.runs.list_stages(persisted_workspace.id, ledger.id)).meta
        assert meta["posts"] == 2
        assert meta["with_warnings"] == 1


class TestTheManifest:
    def test_it_is_byte_stable_across_reruns(
        self, uow, clock, settings, persisted_workspace, story, system_actor, owner, tmp_path
    ):
        """A diff should mean something changed, not that the order moved."""
        for channel in ("x", "linkedin"):
            asset = make_asset(
                uow, persisted_workspace, story, system_actor, channel=channel, body="Short."
            )
            approve(uow, persisted_workspace, asset, owner, clock)

        run_export(uow, clock, settings, persisted_workspace, tmp_path)
        first = manifest_of(tmp_path, persisted_workspace)
        run_export(uow, clock, settings, persisted_workspace, tmp_path)
        second = manifest_of(tmp_path, persisted_workspace)

        assert [post["file"] for post in first["posts"]] == [
            post["file"] for post in second["posts"]
        ]
        assert first["posts"] == second["posts"]

    def test_it_declares_a_schema_version(
        self, uow, clock, settings, persisted_workspace, story, system_actor, owner, tmp_path
    ):
        asset = make_asset(uow, persisted_workspace, story, system_actor)
        approve(uow, persisted_workspace, asset, owner, clock)

        run_export(uow, clock, settings, persisted_workspace, tmp_path)

        manifest = manifest_of(tmp_path, persisted_workspace)
        assert manifest["schema_version"] == 1
        assert manifest["edition"] == "2026-08-08"
        assert manifest["workspace"] == persisted_workspace.slug


class TestExportChangesNothing:
    def test_it_does_not_mark_anything_published(
        self, uow, clock, settings, persisted_workspace, story, system_actor, owner, tmp_path
    ):
        """Writing a file is not evidence that a human posted it."""
        asset = make_asset(uow, persisted_workspace, story, system_actor)
        approve(uow, persisted_workspace, asset, owner, clock)

        run_export(uow, clock, settings, persisted_workspace, tmp_path)

        with uow as active:
            stored = active.assets.get(persisted_workspace.id, asset.id)
            publications = active.publications.list_for_asset(persisted_workspace.id, asset.id)
        assert stored is not None and stored.state is ContentState.APPROVED
        assert publications == []

    def test_a_dry_run_writes_nothing(
        self, uow, clock, settings, persisted_workspace, story, system_actor, owner, tmp_path
    ):
        asset = make_asset(uow, persisted_workspace, story, system_actor)
        approve(uow, persisted_workspace, asset, owner, clock)

        ledger = run_export(uow, clock, settings, persisted_workspace, tmp_path, dry_run=True)

        assert not export_dir(tmp_path, persisted_workspace).exists()
        with uow as active:
            meta = next(r for r in active.runs.list_stages(persisted_workspace.id, ledger.id)).meta
        assert meta["posts"] == 1
        assert meta["dry_run"] is True
