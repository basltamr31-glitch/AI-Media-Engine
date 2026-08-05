"""Adapters: every implementation that touches the outside world.

Databases, filesystems, HTTP, email and LLM providers all live here. Each one
implements a port defined in :mod:`engine.core.ports`, so any of them can be
replaced -- SQLite for Postgres, one LLM vendor for another, CLI for API --
without the domain noticing.
"""

from __future__ import annotations
