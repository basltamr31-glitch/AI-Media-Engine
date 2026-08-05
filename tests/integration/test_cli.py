"""End-to-end CLI tests.

These exist because two real defects — a `slots=True` dataclass breaking
`cached_property`, and a structlog logger factory incompatible with the
`add_logger_name` processor — passed the entire unit and integration suite and
only appeared when a command was actually run.

Composition-root wiring and logging configuration are exactly the code that unit
tests skip, so the CLI gets exercised for real: migrate, seed, run, inspect.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.cli.container import build_container
from engine.cli.main import app
from engine.settings import get_settings

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the CLI at an isolated database and directories."""
    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENGINE_OUTPUT_DIR", str(tmp_path / "output"))
    # Rich falls back to 80 columns when stdout is not a terminal, which elides
    # table cells and makes assertions on them meaningless.
    monkeypatch.setenv("COLUMNS", "240")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_container_builds(cli_env: None) -> None:
    """Directly guards the composition root against wiring regressions."""
    container = build_container()
    assert container.policy_loader.workspaces_dir.name == "workspaces"
    assert container.clock.now().tzinfo is not None
    assert container.unit_of_work() is not None


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "ai-media-engine" in result.stdout


def test_no_args_shows_help():
    result = runner.invoke(app, [])
    assert "Usage" in result.stdout


class TestFullLifecycle:
    """migrate -> seed -> run -> inspect, in order, against a fresh database."""

    def test_lifecycle(self, cli_env: None):
        assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0

        current = runner.invoke(app, ["db", "current"])
        assert current.exit_code == 0
        assert "up to date" in current.stdout

        seed = runner.invoke(app, ["workspace", "seed"])
        assert seed.exit_code == 0, seed.output
        assert "Workspace created" in seed.stdout

        # Seeding is idempotent: the config file stays the source of truth.
        reseed = runner.invoke(app, ["workspace", "seed"])
        assert reseed.exit_code == 0
        assert "Workspace updated" in reseed.stdout
        assert "0 created" in reseed.stdout

        listing = runner.invoke(app, ["workspace", "list"])
        assert listing.exit_code == 0
        assert "default" in listing.stdout

        sources = runner.invoke(app, ["workspace", "sources"])
        assert sources.exit_code == 0
        # Three transports from day one -- never a single-source dependency.
        for expected in ("email", "rss", "api"):
            assert expected in sources.stdout

        run = runner.invoke(app, ["pipeline", "run", "--smoke"])
        assert run.exit_code == 0, run.output
        assert "Run succeeded" in run.stdout

        history = runner.invoke(app, ["pipeline", "runs"])
        assert history.exit_code == 0
        assert "daily_content" in history.stdout

        doctor = runner.invoke(app, ["doctor"])
        assert doctor.exit_code == 0, doctor.output
        assert "Environment looks healthy" in doctor.stdout


class TestGuardRails:
    def test_pipeline_run_without_seed_fails_clearly(self, cli_env: None):
        runner.invoke(app, ["db", "upgrade"])
        result = runner.invoke(app, ["pipeline", "run"])
        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_seed_rejects_unknown_workspace(self, cli_env: None):
        runner.invoke(app, ["db", "upgrade"])
        result = runner.invoke(app, ["workspace", "seed", "--slug", "no-such-tenant"])
        assert result.exit_code == 1
        assert "not found" in result.stdout


def test_config_show_redacts_secrets(cli_env: None, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENGINE_ANTHROPIC_API_KEY", "sk-ant-should-never-appear")
    get_settings.cache_clear()
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "sk-ant-should-never-appear" not in result.stdout
    assert "<set>" in result.stdout
