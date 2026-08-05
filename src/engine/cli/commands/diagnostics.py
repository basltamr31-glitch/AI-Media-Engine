"""Diagnostics: ``doctor`` and ``config show``.

A single command that answers "is this environment correctly set up?" is worth
far more than a setup document, because it stays true as the project changes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final, NamedTuple

import typer
from rich.console import Console
from rich.table import Table

from engine import __version__
from engine.adapters.config.policy_loader import PolicyLoadError
from engine.cli.container import build_container

console = Console()
app = typer.Typer(help="Environment diagnostics and configuration inspection.")

_OK = "[green]OK[/green]"
_WARN = "[yellow]WARN[/yellow]"
_FAIL = "[red]FAIL[/red]"

#: Kept in sync with ``requires-python`` in pyproject.toml.
MINIMUM_PYTHON: Final[tuple[int, int]] = (3, 12)


class Check(NamedTuple):
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == _FAIL


def _check_python() -> Check:
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info[:2] < MINIMUM_PYTHON:
        required = ".".join(str(part) for part in MINIMUM_PYTHON)
        return Check("Python", _FAIL, f"{version} ({required}+ required)")
    return Check("Python", _OK, version)


def _check_directory(label: str, path: Path) -> Check:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check(label, _FAIL, f"{path}: {exc}")
    return Check(label, _OK, str(path))


def _check_database(database_url: str, project_root: Path) -> list[Check]:
    from engine.cli.commands.db import current_revision, head_revision

    checks: list[Check] = []
    try:
        applied = current_revision(database_url, project_root)
    except Exception as exc:  # doctor reports failures, never raises them
        return [Check("Database", _FAIL, f"cannot connect: {exc}")]

    checks.append(Check("Database", _OK, database_url))

    try:
        head = head_revision(project_root, database_url)
    except Exception as exc:  # doctor reports failures, never raises them
        checks.append(Check("Migrations", _FAIL, f"cannot read migrations: {exc}"))
        return checks

    if applied is None:
        checks.append(Check("Migrations", _WARN, "not initialised -- run: engine db upgrade"))
    elif applied != head:
        checks.append(
            Check("Migrations", _WARN, f"applied {applied}, head {head} -- run: engine db upgrade")
        )
    else:
        checks.append(Check("Migrations", _OK, f"up to date ({applied})"))
    return checks


def _check_optional_dependency(label: str, module: str, phase: str) -> Check:
    from importlib.util import find_spec

    if find_spec(module) is None:
        return Check(label, _WARN, f"not installed (needed in {phase})")
    return Check(label, _OK, "installed")


@app.command("doctor")
def doctor() -> None:
    """Verify the environment is correctly configured."""
    container = build_container()
    settings = container.settings

    checks: list[Check] = [_check_python()]
    checks.append(Check("Project root", _OK, str(container.project_root)))
    checks.append(_check_directory("Data dir", settings.data_dir))
    checks.append(_check_directory("Output dir", settings.output_dir))
    checks.extend(_check_database(settings.database_url, container.project_root))

    loader = container.policy_loader
    if not loader.workspaces_dir.is_dir():
        checks.append(Check("Config", _FAIL, f"missing {loader.workspaces_dir}"))
    else:
        slugs = loader.list_workspace_slugs()
        if not slugs:
            checks.append(Check("Config", _WARN, "no workspace definitions found"))
        else:
            try:
                for slug in slugs:
                    loader.load_workspace(slug)
            except PolicyLoadError as exc:
                checks.append(Check("Config", _FAIL, str(exc)))
            else:
                detail = f"{len(slugs)} workspace(s): {', '.join(slugs)}"
                checks.append(Check("Config", _OK, detail))

    try:
        specs = loader.load_all_channel_specs()
    except PolicyLoadError as exc:
        checks.append(Check("Channel specs", _FAIL, str(exc)))
    else:
        checks.append(Check("Channel specs", _OK, f"{len(specs)} loaded"))

    checks.append(_check_optional_dependency("Anthropic SDK", "anthropic", "Phase 3"))
    checks.append(_check_optional_dependency("HTTP client", "httpx", "Phase 1"))

    if settings.anthropic_api_key is None:
        checks.append(Check("LLM credentials", _WARN, "ENGINE_ANTHROPIC_API_KEY unset (Phase 3)"))
    else:
        checks.append(Check("LLM credentials", _OK, "configured"))

    table = Table(title=f"engine doctor  (v{__version__}, env={settings.environment})")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")
    for check in checks:
        table.add_row(check.name, check.status, check.detail)
    console.print(table)

    if any(check.failed for check in checks):
        console.print("[red]One or more checks failed.[/red]")
        raise typer.Exit(code=1)
    console.print("[green]Environment looks healthy.[/green]")


@app.command("show")
def show_config() -> None:
    """Print effective settings. Secrets are redacted."""
    container = build_container()
    settings = container.settings

    table = Table(title="Effective configuration")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", overflow="fold")

    for field_name in type(settings).model_fields:
        value = getattr(settings, field_name)
        if field_name == "anthropic_api_key":
            rendered = "<set>" if value is not None else "<unset>"
        else:
            rendered = str(value)
        table.add_row(field_name, rendered)

    console.print(table)
    console.print("[dim]Override any value with an ENGINE_-prefixed env var or .env entry.[/dim]")
