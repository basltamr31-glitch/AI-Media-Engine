"""Alert sinks: the log, a file, or several at once.

No webhook implementation ships here, and that is deliberate rather than an
omission. Posting to an external endpoint is the one alerting channel that can
leak run detail off the machine, and it should be added by whoever knows where
it is pointing. The port makes it a small class; this module is what works with
no configuration at all.

Every sink swallows its own failures. An alerting channel that raises turns one
failure into two, and the second has nowhere to be reported.
"""

from __future__ import annotations

import json
from pathlib import Path

from engine.core.ports.alerts import Alert, AlertSeverity, AlertSink
from engine.observability.logging import get_logger

logger = get_logger(__name__)


class NullAlertSink:
    """Discards alerts. For dry runs and tests that are not about alerting."""

    def send(self, alert: Alert) -> None:
        return None


class LoggingAlertSink:
    """Emits the alert as a structured log event.

    The baseline that always works: wherever the process's output already goes
    -- a terminal, a service manager's journal, a log shipper -- an alert now
    goes too, with no configuration.
    """

    def send(self, alert: Alert) -> None:
        bound = logger.bind(
            alert_code=alert.code,
            workspace=alert.workspace_slug,
            **alert.context,
        )
        if alert.is_critical:
            bound.error("alert.raised", summary=alert.summary, detail=alert.detail)
        else:
            bound.warning("alert.raised", summary=alert.summary, detail=alert.detail)


class FileAlertSink:
    """Appends alerts to a JSON Lines file.

    A file rather than a table: the thing being reported on is frequently the
    database or the run that writes to it, and an alert store that shares a
    failure domain with what it monitors is silent exactly when it matters.

    JSON Lines because appending is atomic enough for one writer, the file
    stays readable with ``tail``, and a truncated final line costs one alert
    rather than the whole history.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def send(self, alert: Alert) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(alert.model_dump(mode="json"), ensure_ascii=False) + "\n")
        except OSError as exc:
            # Reported through the other channel rather than raised: a full
            # disk must not also destroy the run that was trying to say so.
            logger.error("alert.write_failed", path=str(self._path), error=str(exc))

    def read_recent(self, limit: int = 20) -> list[Alert]:
        """The most recent alerts, newest first.

        Skips lines that will not parse instead of failing. A corrupt line is
        one lost alert; refusing to read the file loses all of them.
        """
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []

        alerts: list[Alert] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                alerts.append(Alert.model_validate_json(line))
            except ValueError:
                continue
            if len(alerts) >= limit:
                break
        return alerts


class CompositeAlertSink:
    """Sends to several sinks, and keeps going if one fails."""

    def __init__(self, *sinks: AlertSink) -> None:
        self._sinks = sinks

    def send(self, alert: Alert) -> None:
        for sink in self._sinks:
            try:
                sink.send(alert)
            # Broad by intent: a sink must never break the run it reports on,
            # and there is no useful narrower type for 'whatever a webhook
            # library raises'.
            except Exception as exc:
                logger.error(
                    "alert.sink_failed",
                    sink=type(sink).__name__,
                    error=f"{type(exc).__name__}: {exc}",
                )


def alert_from_failure(
    *,
    code: str,
    summary: str,
    detail: str = "",
    workspace_slug: str = "",
    critical: bool = True,
    **context: object,
) -> Alert:
    """Build an alert without every caller repeating the severity mapping."""
    return Alert(
        code=code,
        severity=AlertSeverity.CRITICAL if critical else AlertSeverity.WARNING,
        summary=summary,
        detail=detail,
        workspace_slug=workspace_slug,
        context=dict(context),
    )
