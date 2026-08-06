"""Workspace commands.

``seed`` reconciles the database with the YAML definition, which is deliberately
idempotent: the config file stays the source of truth and the database is a
materialised view of it. That property is what makes tenant provisioning a
config change rather than a manual data-entry exercise -- the mechanism that
onboards customer #50 is the same one used for workspace #1 today.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from engine.adapters.config.policy_loader import PolicyLoadError
from engine.adapters.config.schema import WorkspaceConfig
from engine.cli.container import Container, build_container
from engine.core.domain.source import Source
from engine.core.domain.workspace import Workspace

console = Console()
app = typer.Typer(help="Workspace (tenant) management.")


def _sync_sources(
    container: Container,
    workspace: Workspace,
    config: WorkspaceConfig,
) -> tuple[int, int, int]:
    """Reconcile stored sources with the config file.

    Matching is by **name**, not by identifier. A source's URL is a mutable
    property of it -- arXiv moving from ``http`` to ``https``, or an API gaining
    a query parameter, should update the existing source and keep its ingested
    history. Matching on identifier instead silently forks a second source and
    the old one keeps being polled forever.

    Anything in the database whose name is absent from the config is
    **deactivated, never deleted**: the config is the source of truth for what
    we fetch, but documents already ingested are evidence and are never
    discarded on a config edit.

    Returns:
        ``(created, updated, deactivated)``.
    """
    created = updated = deactivated = 0
    configured_names = {source_config.name for source_config in config.sources}

    with container.unit_of_work() as uow:
        stored = uow.sources.list_for_workspace(workspace.id, only_active=False)
        by_name: dict[str, list[Source]] = {}
        for source in stored:
            by_name.setdefault(source.name, []).append(source)

        for source_config in config.sources:
            candidates = by_name.get(source_config.name, [])
            if not candidates:
                uow.sources.add(
                    Source(
                        workspace_id=workspace.id,
                        kind=source_config.kind,
                        name=source_config.name,
                        identifier=source_config.identifier,
                        parser_key=source_config.parser_key,
                        trust_weight=source_config.trust_weight,
                        is_active=source_config.is_active,
                        config=source_config.config,
                    )
                )
                created += 1
                continue

            # Keep whichever row actually carries ingestion history, then break
            # ties toward the oldest (ULIDs sort by creation time). Preferring
            # an identifier match instead would orphan a source's entire
            # document history the moment its URL changed -- which is precisely
            # the case this reconciliation exists to handle.
            primary = min(
                candidates,
                key=lambda s: (-uow.documents.count_for_source(workspace.id, s.id), s.id),
            )
            primary.kind = source_config.kind
            primary.identifier = source_config.identifier
            primary.parser_key = source_config.parser_key
            primary.trust_weight = source_config.trust_weight
            primary.is_active = source_config.is_active
            primary.config = source_config.config
            primary.updated_at = container.clock.now()
            uow.sources.update(primary)
            updated += 1

            # Collapse any historical duplicates of this name.
            for duplicate in candidates:
                if duplicate.id != primary.id and duplicate.is_active:
                    duplicate.is_active = False
                    duplicate.updated_at = container.clock.now()
                    uow.sources.update(duplicate)
                    deactivated += 1

        for source in stored:
            if source.name not in configured_names and source.is_active:
                source.is_active = False
                source.updated_at = container.clock.now()
                uow.sources.update(source)
                deactivated += 1

        uow.commit()
    return created, updated, deactivated


@app.command("seed")
def seed(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
) -> None:
    """Create or update a workspace and its sources from config/workspaces/<slug>.yaml."""
    container = build_container()
    target_slug = slug or container.settings.default_workspace_slug

    try:
        config = container.policy_loader.load_workspace(target_slug)
    except PolicyLoadError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(config.slug)
        if workspace is None:
            workspace = Workspace(
                slug=config.slug,
                name=config.name,
                timezone=config.timezone,
                languages=config.languages,
                meta=config.meta,
            )
            uow.workspaces.add(workspace)
            action = "created"
        else:
            workspace.name = config.name
            workspace.timezone = config.timezone
            workspace.languages = config.languages
            workspace.meta = config.meta
            workspace.touch()
            uow.workspaces.update(workspace)
            action = "updated"
        uow.commit()

    created, updated, deactivated = _sync_sources(container, workspace, config)

    console.print(f"[green]Workspace {action}:[/green] {workspace.slug} ({workspace.id})")
    console.print(
        f"Sources: [cyan]{created}[/cyan] created, [cyan]{updated}[/cyan] updated, "
        f"[cyan]{deactivated}[/cyan] deactivated"
    )

    # Validate the referenced policy files now, so a broken brand profile is a
    # seeding error rather than a generation-time surprise in Phase 3.
    for key in config.brand_profiles:
        try:
            profile = container.policy_loader.load_brand_profile(key, workspace.id)
        except PolicyLoadError as exc:
            console.print(f"[red]brand profile {key!r} is invalid: {exc}[/red]")
            raise typer.Exit(code=1) from exc
        console.print(f"  brand profile ok: [cyan]{profile.key}[/cyan] ({profile.language})")


@app.command("list")
def list_workspaces(
    include_inactive: bool = typer.Option(False, "--all", help="Include inactive workspaces."),
) -> None:
    """List workspaces in the database."""
    container = build_container()
    with container.unit_of_work() as uow:
        workspaces = uow.workspaces.list_all(include_inactive=include_inactive)

    if not workspaces:
        console.print("[yellow]No workspaces found.[/yellow] Run: engine workspace seed")
        return

    table = Table(title="Workspaces")
    table.add_column("Slug", style="cyan")
    table.add_column("Name")
    table.add_column("Languages")
    table.add_column("Timezone")
    table.add_column("Active")
    table.add_column("ID", style="dim")
    for workspace in workspaces:
        table.add_row(
            workspace.slug,
            workspace.name,
            ", ".join(language.value for language in workspace.languages),
            workspace.timezone,
            "yes" if workspace.is_active else "no",
            workspace.id,
        )
    console.print(table)


@app.command("sources")
def list_sources(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
    include_inactive: bool = typer.Option(False, "--all", help="Include inactive sources."),
) -> None:
    """List configured sources for a workspace."""
    container = build_container()
    target_slug = slug or container.settings.default_workspace_slug

    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target_slug)
        if workspace is None:
            console.print(
                f"[red]Workspace {target_slug!r} not found.[/red] Run: engine workspace seed"
            )
            raise typer.Exit(code=1)
        sources = uow.sources.list_for_workspace(workspace.id, only_active=not include_inactive)

    if not sources:
        console.print("[yellow]No sources configured.[/yellow]")
        return

    table = Table(title=f"Sources for {workspace.slug}")
    table.add_column("Name", style="cyan")
    table.add_column("Kind")
    table.add_column("Parser")
    table.add_column("Trust", justify="right")
    table.add_column("Active")
    table.add_column("Identifier", style="dim", overflow="fold")
    for source in sources:
        table.add_row(
            source.name,
            source.kind.value,
            source.parser_key or "-",
            f"{source.trust_weight:.2f}",
            "yes" if source.is_active else "no",
            source.identifier,
        )
    console.print(table)
