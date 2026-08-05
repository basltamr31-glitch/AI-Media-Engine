"""Database migration commands.

Wrapping Alembic in the CLI (rather than documenting raw ``alembic`` calls)
guarantees migrations always run against the same URL the application uses.
Divergence between the two is a classic production incident.
"""

from __future__ import annotations

from pathlib import Path

import typer
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from rich.console import Console

from engine.cli.container import build_container

console = Console()
app = typer.Typer(help="Database schema management.")


def alembic_config(project_root: Path, database_url: str) -> Config:
    """Build an Alembic config pointed at the application's database."""
    ini_path = project_root / "alembic.ini"
    if not ini_path.is_file():
        console.print(f"[red]alembic.ini not found at {ini_path}[/red]")
        raise typer.Exit(code=1)
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def current_revision(database_url: str, project_root: Path) -> str | None:
    """Return the revision currently applied to the database, if any."""
    from engine.adapters.persistence.session import create_db_engine
    from engine.settings import Settings

    probe_settings = Settings(database_url=database_url)
    db_engine = create_db_engine(probe_settings)
    try:
        with db_engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        db_engine.dispose()


def head_revision(project_root: Path, database_url: str) -> str | None:
    """Return the newest revision available in the migrations directory."""
    script = ScriptDirectory.from_config(alembic_config(project_root, database_url))
    return script.get_current_head()


@app.command("upgrade")
def upgrade(
    revision: str = typer.Argument("head", help="Target revision."),
) -> None:
    """Apply migrations up to REVISION (default: head)."""
    container = build_container()
    config = alembic_config(container.project_root, container.settings.database_url)
    command.upgrade(config, revision)
    console.print(f"[green]Database upgraded to[/green] {revision}")


@app.command("downgrade")
def downgrade(
    revision: str = typer.Argument(..., help="Target revision, e.g. -1."),
) -> None:
    """Roll back migrations to REVISION."""
    container = build_container()
    config = alembic_config(container.project_root, container.settings.database_url)
    command.downgrade(config, revision)
    console.print(f"[yellow]Database downgraded to[/yellow] {revision}")


@app.command("current")
def current() -> None:
    """Show the applied revision and whether it is up to date."""
    container = build_container()
    applied = current_revision(container.settings.database_url, container.project_root)
    head = head_revision(container.project_root, container.settings.database_url)

    console.print(f"applied: [cyan]{applied or '<none>'}[/cyan]")
    console.print(f"head:    [cyan]{head or '<none>'}[/cyan]")
    if applied == head:
        console.print("[green]Database is up to date.[/green]")
    else:
        console.print("[yellow]Pending migrations. Run:[/yellow] engine db upgrade")


@app.command("revision")
def revision(
    message: str = typer.Option(..., "--message", "-m", help="Description of the change."),
    autogenerate: bool = typer.Option(
        True, "--autogenerate/--empty", help="Diff models against the database."
    ),
) -> None:
    """Create a new migration script."""
    container = build_container()
    config = alembic_config(container.project_root, container.settings.database_url)
    command.revision(config, message=message, autogenerate=autogenerate)
    console.print("[green]Revision created.[/green] Review it before committing.")


@app.command("history")
def history() -> None:
    """Show the migration history."""
    container = build_container()
    config = alembic_config(container.project_root, container.settings.database_url)
    command.history(config, verbose=False)
