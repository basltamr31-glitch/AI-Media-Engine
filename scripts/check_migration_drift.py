"""Fail if the ORM models and the migrations disagree.

The mistake this catches is quiet and common: a column is added to a model, the
tests pass because the test database is built from the model metadata, and the
migration is never written. Nothing breaks until a real deployment runs against
a real database that was built from migrations.

CLAUDE.md already requires this check by hand -- generate a probe migration,
confirm it comes back empty, delete it. Doing it by hand means doing it when
you remember to. This is the same procedure with the remembering removed.

Run it against a database already at head, e.g. after ``engine db upgrade``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSIONS_DIR = PROJECT_ROOT / "migrations" / "versions"
PROBE_MESSAGE = "drift probe"


def existing_revisions() -> set[Path]:
    return set(VERSIONS_DIR.glob("*.py"))


def probe_body(path: Path) -> str:
    """The generated ``upgrade()`` body, stripped of Alembic's boilerplate."""
    text = path.read_text(encoding="utf-8")
    body = text.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    lines = [
        line.strip()
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("#") and line.strip() != "-> None:"
    ]
    return "\n".join(line for line in lines if line != "pass")


def main() -> int:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    before = existing_revisions()

    command.revision(config, message=PROBE_MESSAGE, autogenerate=True)

    generated = existing_revisions() - before
    if not generated:
        print("ERROR: autogenerate produced no file; the check could not run.")
        return 1

    probe = generated.pop()
    try:
        drift = probe_body(probe)
    finally:
        # Deleted whatever the outcome. A probe left behind becomes a real
        # migration on the next commit, which is a worse problem than the one
        # this script exists to find.
        probe.unlink(missing_ok=True)

    if drift:
        print("ERROR: the models and the migrations disagree.\n")
        print(drift)
        print("\nWrite the migration:  engine db revision -m '<what changed>'")
        return 1

    print("OK: migrations match the models.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
