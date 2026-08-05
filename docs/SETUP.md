# Setup

## Requirements

- **Python 3.12+** (`python --version`)
- No database server, no API keys, no external accounts. Phase 0 runs entirely
  offline — that is a deliberate property: a foundation you cannot run without
  credentials is a foundation you cannot test.

---

## Install

```bash
cd "AI-News agent"

python -m venv .venv
.venv\Scripts\activate           # Windows PowerShell / cmd
# source .venv/bin/activate      # macOS / Linux

python -m pip install --upgrade pip
pip install -e ".[dev]"
```

`-e` (editable) means source changes take effect without reinstalling.
`[dev]` adds pytest, ruff, mypy and import-linter.

### Optional extras

Declared but **not installed**, so the dependency graph documents the roadmap
and nothing gets used before its phase:

```bash
pip install -e ".[dev,sources]"   # Phase 1: httpx, feedparser, bs4, lxml, trafilatura
pip install -e ".[dev,google]"    # Phase 1: Gmail API client
pip install -e ".[dev,llm]"       # Phase 3: anthropic SDK
pip install -e ".[dev,postgres]"  # Phase 6: psycopg
pip install -e ".[dev,api]"       # Phase 7: fastapi, uvicorn
```

---

## Configure

Defaults work with no configuration. To customise:

```bash
copy .env.example .env      # Windows
# cp .env.example .env      # macOS / Linux
```

Every setting is an `ENGINE_`-prefixed environment variable. `.env` is
gitignored and must never be committed.

Check what is actually in effect (secrets are redacted):

```bash
engine config show
```

---

## Initialise

```bash
engine db upgrade        # create the schema at data/engine.db
engine workspace seed    # load config/workspaces/default.yaml into the database
engine doctor            # verify everything
```

`engine doctor` checks Python version, writable directories, database
connectivity, migration status, every policy file, and which optional
dependencies are present. Green across the board means the environment is
correct.

---

## Run

```bash
engine pipeline run --smoke     # end-to-end run with a no-op stage
engine pipeline runs            # history with duration and cost
engine pipeline stages <run-id> # per-stage breakdown
```

`engine pipeline run` without `--smoke` executes zero stages — correct for Phase 0.
Real stages arrive in Phase 1.

Inspect what was seeded:

```bash
engine workspace list
engine workspace sources
```

---

## Development workflow

```bash
pytest                        # full suite
pytest -m "not integration"   # unit tests only (fast)
pytest --cov                  # with coverage

ruff check .                  # lint
ruff format .                 # format
mypy                          # type check (strict)
lint-imports                  # verify layer boundaries
```

Run all four before committing. `lint-imports` is the one people forget and the
one that protects the architecture.

### Changing the database schema

```bash
# 1. edit src/engine/adapters/persistence/models.py
engine db revision -m "add stories table"   # 2. generate the migration
#    3. READ the generated file in migrations/versions/ before trusting it
engine db upgrade                            # 4. apply
```

Autogenerate is a starting point, not an oracle — always review the diff.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `engine: command not found` | Virtualenv not activated, or `pip install -e .` not run. |
| `Database is not initialised` | Run `engine db upgrade`. |
| `Workspace 'default' not found` | Run `engine workspace seed`. |
| `alembic.ini not found` | Run from the project root (any subdirectory also works — the CLI walks up to find `pyproject.toml`). |
| `Readme file does not exist` during install | `README.md` is missing; it is referenced by `pyproject.toml`. |
| Import errors after moving files | `pip install -e ".[dev]"` again to refresh the editable install. |
| `lint-imports` fails | A layer boundary was crossed. Read the reported contract — the fix is almost always to move the code, not to relax the rule. |

---

## Project layout

```
AI-News agent/
├── pyproject.toml         dependencies, tooling config, entry point
├── alembic.ini            migration config (URL resolved from settings)
├── .importlinter          machine-enforced architecture
├── .env.example           documented configuration surface
├── config/                policy as data (workspaces, brand voices, channels)
├── migrations/            Alembic environment and versions
├── docs/                  architecture, setup, ADRs
├── src/engine/
│   ├── core/              domain: entities, ports, policies — zero I/O
│   ├── adapters/          persistence, config, sources — all the I/O
│   ├── pipeline/          orchestration, stages, run ledger
│   ├── observability/     structured logging
│   ├── cli/               commands and composition root
│   └── settings.py        configuration
├── tests/                 unit and integration
├── data/                  SQLite database (gitignored, regenerable)
└── output/                generated content (gitignored)
```
