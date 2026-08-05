"""AI Media Engine.

A layered content pipeline: ingest -> extract -> resolve -> editorial ->
generate -> review -> distribute -> measure.

Layer map (dependencies point inward only, enforced by ``.importlinter``)::

    engine.cli            composition root / user interface
    engine.pipeline       stage orchestration, run ledger
    engine.adapters       everything that touches the outside world
    engine.observability  structured logging
    engine.settings       configuration
    engine.core           pure domain: entities, ports, policies (no I/O)
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
