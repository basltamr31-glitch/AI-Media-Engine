"""Tests for alert sinks.

One rule dominates: a sink must never raise. An alerting channel that breaks
the run it was reporting on turns one failure into two, and the second has
nowhere to be reported.
"""

from __future__ import annotations

from pathlib import Path

from engine.adapters.alerts import (
    CompositeAlertSink,
    FileAlertSink,
    LoggingAlertSink,
    NullAlertSink,
    alert_from_failure,
)
from engine.core.ports.alerts import Alert, AlertSeverity


class ExplodingSink:
    def send(self, alert: Alert) -> None:
        raise RuntimeError("the pager is on fire")


class RecordingSink:
    def __init__(self) -> None:
        self.received: list[Alert] = []

    def send(self, alert: Alert) -> None:
        self.received.append(alert)


def an_alert(code: str = "run_failed") -> Alert:
    return alert_from_failure(
        code=code,
        summary="Scheduled run failed",
        detail="the model was unreachable",
        workspace_slug="default",
        run_id="run_123",
    )


class TestAlertConstruction:
    def test_a_failure_is_critical_by_default(self):
        assert an_alert().severity is AlertSeverity.CRITICAL

    def test_it_can_be_downgraded(self):
        alert = alert_from_failure(code="x", summary="y", critical=False)

        assert alert.severity is AlertSeverity.WARNING
        assert alert.is_critical is False

    def test_context_is_carried_through(self):
        assert an_alert().context["run_id"] == "run_123"


class TestFileSink:
    def test_an_alert_is_appended(self, tmp_path: Path):
        sink = FileAlertSink(tmp_path / "alerts.jsonl")

        sink.send(an_alert())

        assert sink.path.read_text(encoding="utf-8").count("\n") == 1

    def test_the_directory_is_created(self, tmp_path: Path):
        sink = FileAlertSink(tmp_path / "nested" / "alerts.jsonl")

        sink.send(an_alert())

        assert sink.path.exists()

    def test_alerts_accumulate_rather_than_overwrite(self, tmp_path: Path):
        sink = FileAlertSink(tmp_path / "alerts.jsonl")

        sink.send(an_alert("first"))
        sink.send(an_alert("second"))

        assert len(sink.read_recent()) == 2

    def test_the_newest_alert_is_first(self, tmp_path: Path):
        sink = FileAlertSink(tmp_path / "alerts.jsonl")
        sink.send(an_alert("older"))
        sink.send(an_alert("newer"))

        assert sink.read_recent()[0].code == "newer"

    def test_a_corrupt_line_costs_one_alert_not_the_history(self, tmp_path: Path):
        """A truncated final write must not make the whole file unreadable."""
        path = tmp_path / "alerts.jsonl"
        sink = FileAlertSink(path)
        sink.send(an_alert("good"))
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"truncated": \n')

        recovered = sink.read_recent()

        assert [alert.code for alert in recovered] == ["good"]

    def test_reading_a_missing_file_is_empty_not_an_error(self, tmp_path: Path):
        assert FileAlertSink(tmp_path / "nothing.jsonl").read_recent() == []

    def test_non_ascii_detail_survives_the_round_trip(self, tmp_path: Path):
        """Arabic is the point of the product; it must survive the alert path."""
        sink = FileAlertSink(tmp_path / "alerts.jsonl")
        sink.send(alert_from_failure(code="x", summary="فشل التشغيل"))

        assert sink.read_recent()[0].summary == "فشل التشغيل"

    def test_an_unwritable_path_does_not_raise(self, tmp_path: Path):
        """A full disk must not also destroy the run that was reporting it."""
        blocker = tmp_path / "blocked"
        blocker.write_text("i am a file, not a directory", encoding="utf-8")

        FileAlertSink(blocker / "alerts.jsonl").send(an_alert())  # must not raise


class TestCompositeSink:
    def test_every_sink_receives_the_alert(self):
        first, second = RecordingSink(), RecordingSink()

        CompositeAlertSink(first, second).send(an_alert())

        assert len(first.received) == 1
        assert len(second.received) == 1

    def test_one_broken_sink_does_not_stop_the_others(self):
        """The whole reason a composite exists rather than a list comprehension."""
        survivor = RecordingSink()

        CompositeAlertSink(ExplodingSink(), survivor).send(an_alert())

        assert len(survivor.received) == 1

    def test_a_broken_sink_does_not_raise(self):
        CompositeAlertSink(ExplodingSink()).send(an_alert())  # must not raise


class TestOtherSinks:
    def test_the_null_sink_accepts_and_discards(self):
        NullAlertSink().send(an_alert())  # must not raise

    def test_the_logging_sink_accepts_both_severities(self):
        sink = LoggingAlertSink()

        sink.send(an_alert())
        sink.send(alert_from_failure(code="x", summary="y", critical=False))
