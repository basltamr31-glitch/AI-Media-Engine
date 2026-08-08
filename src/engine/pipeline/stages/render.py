"""Render stage -- a day's output as a folder someone can publish from.

Writes ``package.json`` (canonical, machine-read) and ``post.md`` (the human
view of it) into a dated directory per workspace.

Three decisions worth knowing:

* **The date is local, not UTC.** An edition is a publishing day, and a
  workspace in Asia/Riyadh rolls over three hours before UTC does. Using UTC
  would file the late-evening batch under tomorrow, which is exactly the kind
  of bug nobody notices until a customer asks where Tuesday went.
* **Output is deterministic.** Same inputs, same bytes -- apart from
  ``generated_at``. That makes an edition diffable, so "what changed since I
  last looked?" is answerable with ``git diff`` rather than by reading.
* **Re-rendering overwrites.** An edition is derived data: it can always be
  rebuilt from the database, so keeping stale copies would only create two
  answers to the same question. The revision chain, which is *not*
  reconstructible, is never touched.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from engine import __version__
from engine.adapters.rendering.markdown import render_markdown
from engine.adapters.rendering.package import build_package
from engine.core.domain.content import ContentAsset, ContentRevision
from engine.core.domain.story import EditorialScore, Evidence, Story
from engine.core.ports.repositories import UnitOfWork
from engine.observability.logging import get_logger
from engine.pipeline.context import RunContext
from engine.pipeline.stages.base import StageResult

logger = get_logger(__name__)

PACKAGE_NAME: Final = "package.json"
POST_NAME: Final = "post.md"


class RenderStage:
    """Writes one edition folder."""

    def __init__(
        self,
        *,
        output_dir: Path,
        workspace_slug: str,
        timezone: str,
        window_hours: int = 24,
        edition: str | None = None,
    ) -> None:
        self._output_dir = output_dir
        self._workspace_slug = workspace_slug
        self._timezone = timezone
        self._window_hours = window_hours
        self._edition = edition

    @property
    def name(self) -> str:
        return "render"

    def run(self, context: RunContext) -> StageResult:
        now = context.clock.now()
        edition = self._edition or self._edition_date(now)

        with context.uow as uow:
            stories = uow.stories.selected_since(
                context.workspace_id, since=now - timedelta(hours=self._window_hours)
            )
            gathered = [self._gather(uow, context.workspace_id, story) for story in stories]

        package = build_package(
            workspace_slug=self._workspace_slug,
            edition_date=edition,
            generated_at=now,
            run_id=context.run_id,
            engine_version=__version__,
            stories=gathered,
        )
        markdown = render_markdown(package)

        directory = self._output_dir / self._workspace_slug / edition
        if not context.dry_run:
            directory.mkdir(parents=True, exist_ok=True)
            # sort_keys for byte-stability, ensure_ascii off so Arabic is
            # readable in the file rather than escaped into \uXXXX soup.
            (directory / PACKAGE_NAME).write_text(
                json.dumps(package, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (directory / POST_NAME).write_text(markdown, encoding="utf-8")

        asset_count = package["counts"]["assets"]
        logger.info(
            "render.finished",
            edition=edition,
            stories=package["counts"]["stories"],
            assets=asset_count,
            directory=str(directory),
        )
        return StageResult(
            items_in=len(gathered),
            items_out=asset_count,
            meta={
                "edition": edition,
                "directory": str(directory),
                "stories": package["counts"]["stories"],
                "assets": asset_count,
                "cost_usd": package["cost_usd"],
                "window_hours": self._window_hours,
                "dry_run": context.dry_run,
            },
        )

    # -- internals ---------------------------------------------------------

    def _edition_date(self, now: datetime) -> str:
        """The publishing day, in the workspace's own timezone."""
        try:
            zone = ZoneInfo(self._timezone)
        except (ZoneInfoNotFoundError, ValueError):
            # A bad timezone must not stop an edition going out; UTC is a
            # defensible fallback and the warning says what to fix.
            logger.warning("render.unknown_timezone", timezone=self._timezone)
            return now.date().isoformat()
        return now.astimezone(zone).date().isoformat()

    def _gather(
        self, uow: UnitOfWork, workspace_id: str, story: Story
    ) -> tuple[
        Story,
        EditorialScore | None,
        list[Evidence],
        list[tuple[ContentAsset, ContentRevision | None]],
    ]:
        """Everything one story needs to be publishable, in one place."""
        score = uow.scores.latest_for_story(workspace_id, story.id)
        evidence = uow.evidence.list_for_story(workspace_id, story.id)
        pairs = [
            (
                asset,
                uow.assets.get_revision(workspace_id, asset.current_revision_id)
                if asset.current_revision_id
                else None,
            )
            for asset in uow.assets.list_for_story(workspace_id, story.id)
        ]
        return story, score, evidence, pairs
