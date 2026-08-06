"""Source authentication and inspection commands.

Authentication is a separate command from ingestion on purpose. It is
interactive, needs a browser, and runs roughly once per machine -- properties
that have no business inside a scheduled pipeline. A cron job that pops open a
browser is a broken cron job.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from engine.adapters.sources.email.oauth import GoogleAuthError
from engine.cli.container import build_container

console = Console()
app = typer.Typer(help="Source credentials and connectivity.")


@app.command("auth")
def auth(
    force: bool = typer.Option(
        False, "--force", help="Discard the cached token and re-run the consent flow."
    ),
    port: int = typer.Option(
        0, "--port", help="Local redirect port. 0 picks a free one automatically."
    ),
) -> None:
    """Authorise Gmail access and cache the token locally.

    Opens a browser for Google's consent screen. The resulting token is written
    to the configured token file and refreshed automatically from then on.
    """
    container = build_container()
    store = container.credential_store

    if not store.has_client_secrets():
        console.print(f"[red]OAuth client file not found:[/red] {store.credentials_file}")
        console.print(
            "\nCreate it in Google Cloud Console:\n"
            "  1. APIs & Services -> Credentials\n"
            "  2. Create Credentials -> OAuth client ID\n"
            "  3. Application type: [cyan]Desktop app[/cyan]\n"
            f"  4. Download the JSON and save it as [cyan]{store.credentials_file}[/cyan]"
        )
        raise typer.Exit(code=1)

    if force and store.revoke_local():
        console.print("[dim]Removed the cached token.[/dim]")

    if store.has_token() and not force:
        try:
            store.load()
        except GoogleAuthError as exc:
            console.print(f"[yellow]Cached token unusable:[/yellow] {exc}")
        else:
            console.print(
                f"[green]Already authenticated.[/green] Token: {store.token_file}\n"
                "[dim]Use --force to re-authorise with a different account.[/dim]"
            )
            return

    console.print("Opening your browser for Google consent...")
    console.print(f"[dim]Requesting scope: {', '.join(store.scopes)}[/dim]")

    try:
        store.authorise(port=port)
    except GoogleAuthError as exc:
        console.print(f"[red]Authentication failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Authenticated.[/green] Token cached at {store.token_file}")
    console.print("[dim]Next: engine ingest run[/dim]")


@app.command("logout")
def logout() -> None:
    """Delete the locally cached Google token."""
    container = build_container()
    store = container.credential_store

    if store.revoke_local():
        console.print(f"[green]Removed[/green] {store.token_file}")
    else:
        console.print("[yellow]No cached token to remove.[/yellow]")

    # Deleting the file ends our access; it does not withdraw the grant on
    # Google's side. Saying so avoids a false sense of revocation.
    console.print(
        "[dim]This only clears local credentials. To revoke the grant itself, "
        "visit myaccount.google.com/permissions[/dim]"
    )


@app.command("status")
def status() -> None:
    """Show credential and fetcher availability."""
    container = build_container()
    store = container.credential_store

    table = Table(title="Source readiness")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Detail", style="dim")

    table.add_row(
        "OAuth client",
        "[green]found[/green]" if store.has_client_secrets() else "[red]missing[/red]",
        str(store.credentials_file),
    )

    if not store.has_token():
        token_status, token_detail = "[yellow]not authorised[/yellow]", "run: engine sources auth"
    else:
        try:
            store.load()
            token_status, token_detail = "[green]valid[/green]", str(store.token_file)
        except GoogleAuthError as exc:
            token_status, token_detail = "[red]invalid[/red]", str(exc)[:80]
    table.add_row("Google token", token_status, token_detail)

    table.add_row(
        "Registered fetchers",
        "[green]" + str(len(container.fetchers)) + "[/green]",
        ", ".join(container.fetchers.keys()),
    )
    console.print(table)
