"""Pipeline stages.

Phase 0 ships the contract and a no-op stage only. Concrete stages arrive with
the phase that needs them:

    Phase 1  ingest      fetch documents from configured sources
    Phase 2  extract     documents -> story candidates
             resolve     cluster candidates, enrich from the primary source
    Phase 3  editorial   score and select
             generate    brand voice x channel -> content assets
    Phase 4  render      Markdown and JSON output
    Phase 5  distribute  export for publishing
             measure     ingest performance data
"""

from __future__ import annotations

from engine.pipeline.stages.base import Stage, StageError, StageResult
from engine.pipeline.stages.noop import NoOpStage

__all__ = ["NoOpStage", "Stage", "StageError", "StageResult"]
