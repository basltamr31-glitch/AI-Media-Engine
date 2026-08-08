"""Export stage -- approved posts as files ready to paste into a channel.

This is the hand-off. ``render`` writes the edition a human reads; this writes
what actually gets published, one file per approved asset, formatted to the
channel's rules from the pack.

Driven by **approval state, not by a time window**, which is the difference
between this and ``render``. A post approved on Friday and published on Monday
is normal; filing exports by when they were generated would scatter one
publishing session across three folders. The edition date names the folder
because an operator needs to find "the batch I did this morning", not because
the assets belong to that date.

Nothing is deleted and nothing is marked as published here. Writing a file is
not evidence that anyone posted it -- ``engine publish record`` is where a
human states that, and only they know whether it is true.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from engine import __version__
from engine.adapters.rendering.export import ChannelExport, format_for_channel
from engine.core.domain.enums import ContentState
from engine.core.domain.pack import DomainPack
from engine.observability.logging import get_logger
from engine.pipeline.context import RunContext
from engine.pipeline.stages.base import StageResult

logger = get_logger(__name__)

EXPORT_DIRNAME: Final = "export"
MANIFEST_NAME: Final = "manifest.json"

#: Manifest shape version. Bumped when a consumer would have to change; a
#: publisher reading these files needs to be able to tell.
SCHEMA_VERSION: Final = 1


class ExportStage:
    """Writes one export folder for everything currently approved."""

    def __init__(
        self,
        *,
        output_dir: Path,
        workspace_slug: str,
        timezone: str,
        pack: DomainPack,
        edition: str | None = None,
        limit: int = 200,
    ) -> None:
        self._output_dir = output_dir
        self._workspace_slug = workspace_slug
        self._timezone = timezone
        self._pack = pack
        self._edition = edition
        self._limit = limit

    @property
    def name(self) -> str:
        return "export"

    def run(self, context: RunContext) -> StageResult:
        now = context.clock.now()
        edition = self._edition or self._edition_date(now)

        exports = self._gather(context)
        if not exports:
            return StageResult.skip("nothing approved to export")

        directory = self._output_dir / self._workspace_slug / edition / EXPORT_DIRNAME
        if not context.dry_run:
            self._write(directory, exports, edition=edition, run_id=context.run_id, now=now)

        unpublishable = [export for export in exports if not export.is_publishable]
        logger.info(
            "export.finished",
            edition=edition,
            posts=len(exports),
            with_warnings=len(unpublishable),
            directory=str(directory),
        )
        return StageResult(
            items_in=len(exports),
            items_out=len(exports),
            meta={
                "edition": edition,
                "directory": str(directory),
                "posts": len(exports),
                "with_warnings": len(unpublishable),
                "channels": sorted({export.channel_key for export in exports}),
                "dry_run": context.dry_run,
            },
        )

    # -- internals ---------------------------------------------------------

    def _gather(self, context: RunContext) -> list[ChannelExport]:
        """Format every approved asset, newest first."""
        channels = {channel.key: channel for channel in self._pack.channels}
        with context.uow as uow:
            assets = [
                asset
                for asset in uow.assets.list_recent(context.workspace_id, limit=self._limit)
                if asset.state is ContentState.APPROVED
            ]
            return [
                format_for_channel(
                    asset,
                    uow.assets.get_revision(context.workspace_id, asset.current_revision_id)
                    if asset.current_revision_id
                    else None,
                    channels.get(asset.channel_key or ""),
                    uow.stories.get(context.workspace_id, asset.story_id),
                )
                for asset in assets
            ]

    def _write(
        self,
        directory: Path,
        exports: list[ChannelExport],
        *,
        edition: str,
        run_id: str,
        now: datetime,
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for export in exports:
            # A trailing newline: these are text files, and a publisher that
            # strips whitespace is far more common than one that keeps it.
            (directory / export.filename).write_text(export.text + "\n", encoding="utf-8")
        (directory / MANIFEST_NAME).write_text(
            json.dumps(
                self._manifest(exports, edition=edition, run_id=run_id, now=now),
                indent=2,
                sort_keys=True,
                # Arabic readable in the file rather than escaped to \uXXXX.
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    def _manifest(
        self,
        exports: list[ChannelExport],
        *,
        edition: str,
        run_id: str,
        now: datetime,
    ) -> dict[str, Any]:
        """What was exported, so a publisher never has to parse filenames."""
        return {
            "schema_version": SCHEMA_VERSION,
            "engine_version": __version__,
            "workspace": self._workspace_slug,
            "edition": edition,
            "run_id": run_id,
            "generated_at": now.isoformat(),
            "posts": [
                {
                    "asset_id": export.asset_id,
                    "channel": export.channel_key,
                    "language": export.language,
                    "file": export.filename,
                    "characters": export.characters,
                    "headline": export.headline,
                    "publishable": export.is_publishable,
                    "warnings": export.warnings,
                }
                # Sorted for byte-stability, so re-exporting an unchanged batch
                # produces an identical manifest and a diff means something.
                for export in sorted(exports, key=lambda item: item.filename)
            ],
        }

    def _edition_date(self, now: datetime) -> str:
        """The publishing day, in the workspace's own timezone."""
        try:
            zone = ZoneInfo(self._timezone)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("export.unknown_timezone", timezone=self._timezone)
            return now.date().isoformat()
        return now.astimezone(zone).date().isoformat()
