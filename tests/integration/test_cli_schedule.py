"""End-to-end tests for unattended running.

What has to be true for a scheduler to be trusted with this: it must not start
a run on top of a running one, it must distinguish "skipped because busy" from
"failed" in a way a scheduler can branch on, and asking whether it has been
working must not require reading a log.

The pipeline itself is stubbed out here. These tests are about the scheduling
wrapper -- lock, exit codes, trigger attribution -- and running eight real
stages would test the stages instead, slowly, for the third time.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from engine.adapters.locking import LockOwner
from engine.cli.commands import schedule as schedule_module
from engine.cli.main import app
from engine.core.domain.enums import RunStatus
from engine.core.domain.ledger import RunLedger
from engine.settings import get_settings

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("ENGINE_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path / 'cli.db'}")
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
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    assert runner.invoke(app, ["workspace", "seed"]).exit_code == 0
    return cli_env


@pytest.fixture
def stub_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Replace the pipeline with one that records a ledger of a chosen status."""

    def install(status: RunStatus = RunStatus.SUCCEEDED, *, error: str | None = None):
        recorded: dict[str, object] = {}

        def fake_execute(container, workspace, *, dry_run: bool) -> RunLedger:
            recorded["dry_run"] = dry_run
            ledger = RunLedger(
                workspace_id=workspace.id,
                pipeline="daily_content",
                status=status,
                trigger="schedule",
                started_at=container.clock.now(),
                finished_at=container.clock.now(),
                error=error,
            )
            with container.unit_of_work() as uow:
                uow.runs.add(ledger)
                uow.commit()
            recorded["ledger"] = ledger
            return ledger

        monkeypatch.setattr(schedule_module, "_execute", fake_execute)
        return recorded

    return install


def lock_file(root: Path, slug: str = "default") -> Path:
    return root / "data" / f"{slug}.daily.lock"


class TestUnattendedRun:
    def test_a_successful_run_exits_zero(self, seeded: Path, stub_pipeline) -> None:
        stub_pipeline()

        result = runner.invoke(app, ["schedule", "run"])

        assert result.exit_code == 0, result.stdout
        assert "succeeded" in result.stdout

    def test_a_failed_run_exits_nonzero(self, seeded: Path, stub_pipeline) -> None:
        """A scheduler only learns about failure through the exit code."""
        stub_pipeline(RunStatus.FAILED, error="the model was unreachable")

        result = runner.invoke(app, ["schedule", "run"])

        assert result.exit_code == schedule_module.EXIT_FAILED
        assert "unreachable" in result.stdout

    def test_the_run_is_attributed_to_the_schedule(self, seeded: Path, stub_pipeline) -> None:
        """Unattended runs fail differently; they must be separable afterwards."""
        recorded = stub_pipeline()

        runner.invoke(app, ["schedule", "run"])

        ledger = recorded["ledger"]
        assert isinstance(ledger, RunLedger)
        assert ledger.trigger == "schedule"

    def test_dry_run_is_passed_through(self, seeded: Path, stub_pipeline) -> None:
        recorded = stub_pipeline()

        runner.invoke(app, ["schedule", "run", "--dry-run"])

        assert recorded["dry_run"] is True

    def test_the_lock_is_released_afterwards(self, seeded: Path, stub_pipeline) -> None:
        stub_pipeline()

        runner.invoke(app, ["schedule", "run"])

        assert not lock_file(seeded).exists()

    def test_the_lock_is_released_even_when_the_run_fails(
        self, seeded: Path, stub_pipeline
    ) -> None:
        """Otherwise one bad night blocks every morning that follows."""
        stub_pipeline(RunStatus.FAILED, error="boom")

        runner.invoke(app, ["schedule", "run"])

        assert not lock_file(seeded).exists()


class TestOverlappingRuns:
    def _hold_lock(self, root: Path) -> None:
        """Plant a lock owned by this (live) process."""
        path = lock_file(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            LockOwner(pid=os.getpid(), acquired_at=datetime.now(UTC)).to_json(),
            encoding="utf-8",
        )

    def test_a_second_run_is_skipped_not_failed(self, seeded: Path, stub_pipeline) -> None:
        """A pipeline that overran its interval is normal, not an incident.

        The exit code is distinct so a scheduler can stay quiet about this
        while still alerting on real failures -- one code for both would mean
        either noisy alerts or silent breakage.
        """
        stub_pipeline()
        self._hold_lock(seeded)

        result = runner.invoke(app, ["schedule", "run"])

        assert result.exit_code == schedule_module.EXIT_ALREADY_RUNNING
        assert result.exit_code != schedule_module.EXIT_FAILED
        assert "in progress" in result.stdout

    def test_the_pipeline_does_not_run_when_locked_out(self, seeded: Path, stub_pipeline) -> None:
        recorded = stub_pipeline()
        self._hold_lock(seeded)

        runner.invoke(app, ["schedule", "run"])

        assert "ledger" not in recorded

    def test_force_overrides_the_lock(self, seeded: Path, stub_pipeline) -> None:
        recorded = stub_pipeline()
        self._hold_lock(seeded)

        result = runner.invoke(app, ["schedule", "run", "--force"])

        assert result.exit_code == 0
        assert "ledger" in recorded


class TestStatus:
    def test_silence_is_reported_as_a_state(self, seeded: Path) -> None:
        """A scheduler that stopped firing produces no failures to notice."""
        result = runner.invoke(app, ["schedule", "status"])

        assert result.exit_code == 0
        assert "No scheduled runs" in result.stdout

    def test_a_recent_run_is_summarised(self, seeded: Path, stub_pipeline) -> None:
        stub_pipeline()
        runner.invoke(app, ["schedule", "run"])

        result = runner.invoke(app, ["schedule", "status"])

        assert "Last scheduled run succeeded" in result.stdout

    def test_manual_runs_are_not_counted_as_scheduled(self, seeded: Path) -> None:
        """Otherwise a developer running it by hand hides a dead scheduler."""
        assert runner.invoke(app, ["pipeline", "run", "--smoke"]).exit_code == 0

        result = runner.invoke(app, ["schedule", "status"])

        assert "No scheduled runs" in result.stdout

    def test_a_run_in_progress_is_reported(self, seeded: Path) -> None:
        path = lock_file(seeded)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            LockOwner(
                pid=os.getpid(), acquired_at=datetime.now(UTC) - timedelta(minutes=5)
            ).to_json(),
            encoding="utf-8",
        )

        result = runner.invoke(app, ["schedule", "status"])

        assert "in progress" in result.stdout


class TestAlerting:
    def alerts_file(self, root: Path) -> Path:
        return root / "data" / "alerts.jsonl"

    def test_a_failed_run_raises_an_alert(self, seeded: Path, stub_pipeline) -> None:
        """Nobody is watching the terminal; the failure has to persist somewhere."""
        stub_pipeline(RunStatus.FAILED, error="the model was unreachable")

        runner.invoke(app, ["schedule", "run"])

        written = self.alerts_file(seeded).read_text(encoding="utf-8")
        assert "run_failed" in written
        assert "unreachable" in written

    def test_a_successful_run_raises_nothing(self, seeded: Path, stub_pipeline) -> None:
        """An alert channel that fires on success is one that gets muted."""
        stub_pipeline()

        runner.invoke(app, ["schedule", "run"])

        assert not self.alerts_file(seeded).exists()

    def test_being_skipped_is_not_an_alert(self, seeded: Path, stub_pipeline) -> None:
        """An overrunning pipeline is normal operation, not an incident."""
        stub_pipeline()
        path = lock_file(seeded)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            LockOwner(pid=os.getpid(), acquired_at=datetime.now(UTC)).to_json(),
            encoding="utf-8",
        )

        runner.invoke(app, ["schedule", "run"])

        assert not self.alerts_file(seeded).exists()


class TestHealth:
    def test_a_fresh_install_is_unknown_and_exits_zero(self, seeded: Path) -> None:
        """Day one is not an incident."""
        result = runner.invoke(app, ["schedule", "health"])

        assert result.exit_code == 0
        assert "UNKNOWN" in result.stdout

    def test_a_healthy_engine_exits_zero(self, seeded: Path, stub_pipeline) -> None:
        stub_pipeline()
        runner.invoke(app, ["schedule", "run"])

        result = runner.invoke(app, ["schedule", "health"])

        assert result.exit_code == 0
        assert "HEALTHY" in result.stdout

    def test_a_failing_engine_exits_nonzero(self, seeded: Path, stub_pipeline) -> None:
        """This is what a monitor branches on."""
        stub_pipeline(RunStatus.FAILED, error="boom")
        runner.invoke(app, ["schedule", "run"])

        result = runner.invoke(app, ["schedule", "health"])

        assert result.exit_code == schedule_module.EXIT_FAILED
        assert "FAILING" in result.stdout

    def test_a_stale_schedule_is_caught_although_nothing_failed(
        self, seeded: Path, stub_pipeline
    ) -> None:
        """The failure that produces no failed runs.

        Every recorded run succeeded; the scheduler simply stopped firing.
        Judged with a zero-hour staleness threshold so the just-recorded run
        already counts as too old.
        """
        stub_pipeline()
        runner.invoke(app, ["schedule", "run"])

        result = runner.invoke(app, ["schedule", "health", "--max-age-hours", "0"])

        assert result.exit_code == schedule_module.EXIT_FAILED
        assert "STALE" in result.stdout

    def test_health_can_raise_an_alert_too(self, seeded: Path, stub_pipeline) -> None:
        stub_pipeline(RunStatus.FAILED, error="boom")
        runner.invoke(app, ["schedule", "run"])
        (seeded / "data" / "alerts.jsonl").unlink()

        runner.invoke(app, ["schedule", "health", "--alert"])

        assert "health_last_run_failed" in (seeded / "data" / "alerts.jsonl").read_text(
            encoding="utf-8"
        )

    def test_health_stays_quiet_without_the_flag(self, seeded: Path, stub_pipeline) -> None:
        """Checking is not the same as reporting; a monitor polls this often."""
        stub_pipeline(RunStatus.FAILED, error="boom")
        runner.invoke(app, ["schedule", "run"])
        (seeded / "data" / "alerts.jsonl").unlink()

        runner.invoke(app, ["schedule", "health"])

        assert not (seeded / "data" / "alerts.jsonl").exists()
