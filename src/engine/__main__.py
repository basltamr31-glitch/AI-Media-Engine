"""Allow ``python -m engine`` as an alternative to the ``engine`` script."""

from __future__ import annotations

from engine.cli.main import app

if __name__ == "__main__":
    app()
