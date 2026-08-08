# Running it unattended

Phase 6's target is a system that runs for two weeks without intervention. That
is less about the pipeline working and more about the three things that go
wrong when nobody is watching: it runs twice, it fails silently, or it stops
running and nothing says so.

---

## The one command

```bash
engine schedule run
```

Idempotent by construction. Every stage skips what it has already done, so
running it more often than there is news costs almost nothing, and running it
twice does not duplicate anything.

It runs **ingest → parse → resolve → enrich → evidence → score → select →
generate**, and stops there. Rendering is not included: an edition produced
every morning would imply the day's content was finished when nothing had been
reviewed. Unattended work ends by filling the review queue.

### Exit codes

| Code | Meaning | What a scheduler should do |
|---|---|---|
| `0` | The run succeeded | Nothing |
| `1` | The run failed | Alert |
| `2` | Skipped — a previous run is still going | Nothing |

`2` is distinct from `1` on purpose. A pipeline that overruns its interval is
normal operation; a scheduler that cannot tell the two apart either alerts on
healthy skips until the alerts are muted, or stays quiet about real failures.

---

## Setting up a scheduler

There is deliberately no daemon in this project. Every operating system already
ships something that runs a command on a timer, restarts it after a reboot and
captures its output, and none of them fall over silently at 3am.

### Windows — Task Scheduler

```powershell
schtasks /create /tn "AI Media Engine" /tr "C:\path\to\.venv\Scripts\engine.exe schedule run" /sc daily /st 06:00
```

### Linux / macOS — cron

```bash
0 6 * * * cd /path/to/repo && .venv/bin/engine schedule run >> logs/schedule.log 2>&1
```

### systemd timer

A `.service` calling `engine schedule run` with `Type=oneshot`, plus a `.timer`
with `OnCalendar=daily`. Preferable to cron where available: the journal keeps
the output and `systemctl status` answers what happened last.

---

## Knowing it is working

The failure that ends an unattended stretch is rarely a crash. It is a
scheduler that quietly stopped firing, which produces **no failed runs to
notice**. Absence has to be checked for.

```bash
engine schedule status     # has it been running, and did those runs pass?
engine schedule health     # the same question as an exit code, for a monitor
```

`health` exits non-zero when the engine is unhealthy, so it can be polled on
its own timer:

| Status | Meaning |
|---|---|
| `HEALTHY` | Recent scheduled runs, none failed |
| `UNKNOWN` | Nothing has ever run. Day one is not an incident |
| `DEGRADED` | Some runs failed, but the most recent passed |
| `FAILING` | The last run failed, or most recent runs did |
| `STALE` | No run for longer than expected — the scheduler may have stopped |

`STALE` outranks everything else. A perfect success rate from last week says
nothing about whether anything ran this morning.

Add `--alert` to have an unhealthy verdict also raise an alert. Without it,
`health` only reports — a monitor polls this often, and checking is not the
same as reporting.

---

## Alerts

Failures go to two places, with no configuration:

- **The log**, as a structured `alert.raised` event, wherever the process's
  output already goes.
- **`data/alerts.jsonl`**, one JSON object per line, which survives a terminal
  nobody was watching.

```bash
tail -5 data/alerts.jsonl
```

Alerts are raised when a scheduled run fails or crashes, and by
`engine schedule health --alert`. They are **not** raised when a run is skipped
because another is in progress — an alert channel that fires on normal
operation is one that gets muted.

Sending alerts elsewhere — a webhook, a paging service — is a small class
implementing `AlertSink` and one line in the container. None ships here because
posting run detail to an external endpoint should be set up by whoever knows
where it is pointing.

---

## Being a good citizen

Running unattended means the engine fetches other people's pages with nobody
supervising it. Two protections apply to every outbound request, and neither is
a stage's responsibility ([ADR 0019](adr/0019-politeness-as-transport-policy.md)):

- **`robots.txt` is obeyed.** Cached per host for 24 hours. A refusal is
  recorded as `robots_disallowed`, kept separate from fetch failures because
  retrying will not help and nothing is broken.
- **Requests to one host are spaced**, by one second or by the site's own
  `Crawl-delay`, whichever is longer.

| Setting | Default | Purpose |
|---|---|---|
| `ENGINE_HTTP_RESPECT_ROBOTS` | `true` | Obey `robots.txt` |
| `ENGINE_HTTP_MIN_INTERVAL_SECONDS` | `1.0` | Minimum gap per host |
| `ENGINE_HTTP_ROBOTS_TTL_SECONDS` | `86400` | How long a `robots.txt` is trusted |

Turning off robots compliance is supported for fetching **your own** sites,
where the rules are yours to set. It is a setting rather than a command-line
flag so that doing it to somebody else's site is not convenient.

---

## Cost while unattended

The per-run ceiling still applies and is checked **before each model call**, not
between stages:

```bash
engine cost report
```

A run that hits the ceiling stops and reports it rather than failing, so the
work already done is kept.

---

## The daily loop in practice

```bash
engine schedule run        # unattended, on a timer
engine review queue        # a human reads what it wrote
engine review approve <id>
engine export run          # publish-ready files per channel
engine publish record <id> --url https://...
engine metrics add <publication-id> --metric likes --value 42
engine tune weights        # what the outcomes say about the ranking
```

The first line is automated. Everything below it is deliberately not
([ADR 0018](adr/0018-record-publication-do-not-perform-it.md),
[ADR 0020](adr/0020-weight-tuning-proposes-never-applies.md)).
