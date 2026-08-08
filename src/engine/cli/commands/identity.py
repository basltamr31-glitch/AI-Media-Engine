"""Account, actor and signal inspection commands.

``signals summary`` is the first piece of *product* analytics in the system, as
distinct from the operational telemetry the run ledger already provides. The run
ledger answers "did the pipeline work?"; the signal log answers "is the product
working?" -- and the second question is the one that predicts churn.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from engine.cli.container import build_container

console = Console()

account_app = typer.Typer(help="Accounts, actors and memberships.")
signals_app = typer.Typer(help="The feedback log.")


@account_app.command("list")
def list_accounts(
    include_inactive: bool = typer.Option(False, "--all", help="Include inactive accounts."),
) -> None:
    """List accounts and their workspace counts."""
    container = build_container()
    with container.unit_of_work() as uow:
        accounts = uow.accounts.list_all(include_inactive=include_inactive)
        rows = [
            (
                account,
                [
                    w
                    for w in uow.workspaces.list_all(include_inactive=True)
                    if w.account_id == account.id
                ],
            )
            for account in accounts
        ]

    if not rows:
        console.print("[yellow]No accounts.[/yellow] Run: engine workspace seed")
        return

    table = Table(title="Accounts")
    table.add_column("Slug", style="cyan")
    table.add_column("Name")
    table.add_column("Plan")
    table.add_column("Workspaces", justify="right")
    table.add_column("Active")
    for account, workspaces in rows:
        table.add_row(
            account.slug,
            account.name,
            account.plan.value,
            str(len(workspaces)),
            "yes" if account.is_active else "[dim]no[/dim]",
        )
    console.print(table)


@account_app.command("show")
def show_account(
    slug: str = typer.Argument(None, help="Account slug (default: the configured one)."),
) -> None:
    """Show one account, its quota, its actors and its workspaces."""
    container = build_container()
    target = slug or container.settings.default_account_slug

    with container.unit_of_work() as uow:
        account = uow.accounts.get_by_slug(target)
        if account is None:
            console.print(f"[red]Account {target!r} not found.[/red]")
            raise typer.Exit(code=1)
        actors = uow.actors.list_for_account(account.id, only_active=False)
        workspaces = [
            w for w in uow.workspaces.list_all(include_inactive=True) if w.account_id == account.id
        ]

    console.print(f"[green]{account.name}[/green] ([cyan]{account.slug}[/cyan]) — {account.id}")
    console.print(f"Plan: {account.plan.value}")

    quota = Table(title="Quota")
    quota.add_column("Limit", style="cyan")
    quota.add_column("Value", justify="right")
    for field_name, value in account.quota.model_dump().items():
        quota.add_row(field_name, str(value))
    console.print(quota)

    people = Table(title="Actors")
    people.add_column("Key", style="cyan")
    people.add_column("Name")
    people.add_column("Kind")
    people.add_column("Active")
    for actor in actors:
        people.add_row(
            actor.key,
            actor.display_name,
            actor.kind.value,
            "yes" if actor.is_active else "[dim]no[/dim]",
        )
    console.print(people)

    spaces = Table(title="Workspaces")
    spaces.add_column("Slug", style="cyan")
    spaces.add_column("Name")
    spaces.add_column("Languages")
    for workspace in workspaces:
        spaces.add_row(
            workspace.slug,
            workspace.name,
            ", ".join(workspace.languages),
        )
    console.print(spaces)


@signals_app.command("summary")
def signal_summary(
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
) -> None:
    """Show how many signals of each type have been recorded.

    An empty table is expected until Phase 2 begins emitting. The command exists
    now so that the moment emission starts, it is observable.
    """
    container = build_container()
    target = slug or container.settings.default_workspace_slug

    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target)
        if workspace is None:
            console.print(f"[red]Workspace {target!r} not found.[/red]")
            raise typer.Exit(code=1)
        counts = uow.signals.count_by_type(workspace.id)

    if not counts:
        console.print(
            f"[yellow]No signals recorded for {workspace.slug} yet.[/yellow]\n"
            "[dim]Emission begins in Phase 2. The log is append-only: every human "
            "decision and measured outcome lands here.[/dim]"
        )
        return

    table = Table(title=f"Signals for {workspace.slug}")
    table.add_column("Signal type", style="cyan")
    table.add_column("Count", justify="right")
    for signal_type, count in counts.items():
        table.add_row(signal_type, str(count))
    console.print(table)
    console.print(f"[dim]Total: {sum(counts.values())}[/dim]")


@signals_app.command("show")
def show_signals(
    subject_id: str = typer.Argument(..., help="Story, asset, source or publication id."),
    subject_type: str = typer.Option("story", "--type", help="Subject type."),
    slug: str = typer.Option(None, "--slug", help="Workspace slug (default: configured one)."),
) -> None:
    """Show everything that ever happened to one subject."""
    from engine.core.domain.enums import SubjectType

    container = build_container()
    target = slug or container.settings.default_workspace_slug

    try:
        parsed_type = SubjectType(subject_type)
    except ValueError:
        valid = ", ".join(member.value for member in SubjectType)
        console.print(f"[red]Unknown subject type {subject_type!r}.[/red] Valid: {valid}")
        raise typer.Exit(code=1) from None

    with container.unit_of_work() as uow:
        workspace = uow.workspaces.get_by_slug(target)
        if workspace is None:
            console.print(f"[red]Workspace {target!r} not found.[/red]")
            raise typer.Exit(code=1)
        signals = uow.signals.for_subject(workspace.id, parsed_type, subject_id)

    if not signals:
        console.print(f"[yellow]No signals for {subject_type} {subject_id}.[/yellow]")
        return

    table = Table(title=f"History of {subject_type} {subject_id}")
    table.add_column("Occurred (UTC)")
    table.add_column("Signal", style="cyan")
    table.add_column("Polarity")
    table.add_column("Value", justify="right")
    table.add_column("Label")
    for signal in signals:
        table.add_row(
            signal.occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
            signal.signal_type,
            signal.polarity.value,
            f"{signal.value:.4g}" if signal.value is not None else "-",
            signal.label or "-",
        )
    console.print(table)
