"""Pipeline stages.

Phase 0 ships the contract and a no-op stage only. Concrete stages arrive with
the phase that needs them:

    Phase 1  ingest      fetch documents from configured sources
    Phase 2  parse       documents -> story candidates
             resolve     cluster candidates into canonical stories
             enrich      fetch and extract each story's primary source
             evidence    record which sources support which claim
    Phase 3  score       rank stories against the workspace profile
             select      choose what to produce
             generate    brand voice x channel -> content assets
    Phase 4  render      Markdown and JSON output
    Phase 5  export      approved assets -> per-channel publishable files

Phase 5's other two capabilities -- recording that content was published, and
recording what it achieved -- are deliberately *not* stages. A stage is
unattended batch work; both of those are one human stating one fact, and the
reason a fact was recorded has nowhere to live inside a batch. They are use
cases in ``engine.cli.lifecycle`` instead (ADR 0018).
"""

from __future__ import annotations

from engine.pipeline.stages.base import Stage, StageError, StageResult
from engine.pipeline.stages.noop import NoOpStage

__all__ = ["NoOpStage", "Stage", "StageError", "StageResult"]
