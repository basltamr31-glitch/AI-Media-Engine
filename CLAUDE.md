# Working policy for this repository

This is a **development repository**. It contains no production deployment, no
live customers and no remote database. Treat it accordingly.

---

## Default autonomy: HIGH

**Routine project operations: ALWAYS proceed.**
**Permission requests: ONLY for genuinely destructive, irreversible, external,
financial, production, credential, or requirement-changing actions.**

Claude Code is authorised to autonomously inspect, modify, test, refactor,
document and validate this project. The user should not have to supervise
routine commands one by one.

### Proceed without asking

- Reading, searching and inspecting anything in the repository.
- Creating and modifying source files, tests, documentation, configuration,
  migrations and development artifacts required by the approved roadmap.
- Running tests, linters, formatters, type checkers, static analysis, import
  contracts and local CLI commands.
- Running the local pipeline against the local SQLite database.
- Running the local model (Ollama) — it is free and offline.
- Read-only git: `status`, `log`, `diff`, `show`, `blame`, `check-ignore`,
  `ls-files`, `rev-parse`, `fsck`.
- `git add` and `git stash` — both preserve work rather than destroying it.
- Installing Python dependencies already declared in `pyproject.toml`.

### Stop and ask first

- `git push`, tags, releases, anything touching `origin`.
- `git reset --hard`, `git checkout -- <file>`, `git restore <file>`,
  `git clean`, or deleting untracked files — see **Git safety** below.
- Deleting or overwriting files that are not regenerable.
- Anything that spends money, including paid model providers.
- Anything that contacts a person, publishes content or deploys.
- Printing, echoing or committing secrets: `.env`, `credentials.json`,
  `token.json`, API keys.
- Changing fundamental product requirements or the roadmap's scope.

### How to decide

- Prefer the safest reasonable implementation automatically.
- When several implementations exist, choose the most conservative one that
  preserves the existing architecture and requirements.
- Explain the choice **afterwards**, in the report — do not stop beforehand to
  ask which to pick.
- Do not stop merely to ask "should I proceed?" when the next action is an
  obvious continuation of an approved task.
- If a decision genuinely changes the product, keep auditing everything else
  and raise that one decision at the end.

---

## Git safety

Uncommitted work in this repository has real value: for long stretches, entire
phases have existed only in the working tree.

**Never use a destructive git command to clean up your own mistake.** If you
break something:

1. **Stop.**
2. Preserve the current state (`git stash` or a copy) — do not overwrite it.
3. Then recover.

`git checkout -- README.md` once destroyed several phases of uncommitted
documentation in this repository. `git status` beforehand would have prevented
it. Run it first, every time, before anything that can discard changes.

---

## Architecture rules

Dependencies point inward only, and it is machine-enforced:

```
engine.cli            composition root, user interface
engine.pipeline       stage orchestration, run ledger, cost ceiling
engine.adapters       persistence, config, sources, LLM, rendering — all I/O
engine.observability  structured logging
engine.settings       configuration
engine.core           pure domain: entities, ports, policies (zero I/O)
```

- `engine.core` imports no infrastructure. Pure algorithms belong there
  (`matching`, `scoring`, `evaluation`); anything touching a library or the
  network belongs in `adapters`.
- Every new capability is a **stage** implementing `Stage`, not a new pipeline.
  Stages get the ledger, cost accounting and observability for free.
- Provider SDK types never escape their adapter package.
- Repository methods are workspace-scoped by signature, so tenant isolation
  cannot be forgotten.
- Vocabulary (channels, products, taxonomy, signals) is **pack data**, not
  enums (ADR 0015). Adding a channel is a YAML edit.

---

## Verification

Run all of these before reporting work complete:

```bash
pytest                       # full suite
pytest -m "not integration"  # unit only, fast
ruff check src tests
ruff format --check src tests
mypy                         # strict, covers src and tests
lint-imports                 # layer contracts
```

`lint-imports` is not on `PATH` as a module; invoke the console script in
`.venv/Scripts/` (Windows) or `.venv/bin/` (POSIX).

After changing a SQLAlchemy model, generate the migration **and** re-run
autogenerate to confirm it comes back empty — a non-empty probe means the model
and schema disagree. Delete the probe afterwards.

Alembic quirk in this repo: autogenerate emits `astext_type=Text()` unqualified
for JSONB variants, which raises `NameError`. Correct it to `sa.Text()`.
Adding a `NOT NULL` column to a populated table needs a `server_default`, and
that default must also be declared on the model or every later autogenerate
reports phantom drift.

---

## Conventions

- **Comments explain *why*, never *what*.** Match the density of the
  surrounding file — this codebase documents reasoning heavily, especially
  where a decision looks arbitrary.
- **Test names are behavioural sentences** (`test_a_missing_model_says_how_to_install_it`).
  Prefer a docstring giving the reason over a comment.
- Tests assert on stable **codes**, never on message text.
- Failures are counted and reported with actionable next commands, not
  swallowed.
- Editorial rule, enforced in code: **newsletters are tips, not text**
  (ADR 0003). Generation reads the extracted primary source, never a
  newsletter's summary.
- Cost rule: **never design something that scales linearly in LLM cost if an
  algorithm can solve it.** The eval suite is deliberately algorithmic for this
  reason.

### Windows specifics

- Do **not** write non-ASCII files with PowerShell `Set-Content`/`Out-File`;
  PowerShell 5.1 double-encodes them. Use the Edit/Write tools, which preserve
  UTF-8. This corrupted `README.md` once.
- PowerShell has no `&&`. Use `;` with `if ($?)`, or run the command in bash.
- Redirecting a native command's stderr (`2>&1`) makes PowerShell report a
  non-zero exit even on success. Do not chase those.

---

## Honesty in documentation

Phase status must distinguish four different things:

1. capability implemented
2. capability tested
3. quality bar achieved
4. production-ready

Marking a phase complete because the code exists is wrong. Where a bar is not
met, say so plainly in both `README.md` and `docs/ROADMAP.md` — the Phase 3
caveat about the local model failing its own golden set is deliberate and must
not be silently upgraded.

When finishing a phase, updating the README status table and the roadmap
heading is **part of the work**, not tidying afterwards.
