"""Loading the golden set.

Cases are YAML files rather than code so that adding one is an editorial act,
not a development task -- the people who can tell whether a post is good are
not necessarily the people who can write a test.

The source text is stored verbatim rather than fetched at eval time. An eval
that re-fetches is an eval whose inputs change under it, and a suite that fails
because a page was edited teaches everyone to ignore failures.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from engine.core.domain.evaluation import EvalCase


class GoldenSetError(RuntimeError):
    """Raised when the golden set cannot be read or is malformed."""


class GoldenSet:
    """The golden cases on disk."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    @property
    def directory(self) -> Path:
        return self._directory

    def load_all(self) -> list[EvalCase]:
        """Every case, in a stable order so reports are comparable run to run."""
        if not self._directory.is_dir():
            msg = f"no golden set at {self._directory}"
            raise GoldenSetError(msg)

        cases = [self._load(path) for path in sorted(self._directory.glob("*.yaml"))]
        if not cases:
            msg = f"no golden cases found in {self._directory}"
            raise GoldenSetError(msg)

        keys = [case.key for case in cases]
        duplicates = {key for key in keys if keys.count(key) > 1}
        if duplicates:
            # Two cases sharing a key would silently overwrite each other in
            # the baseline, which is where a regression would go to hide.
            msg = f"duplicate golden case key(s): {', '.join(sorted(duplicates))}"
            raise GoldenSetError(msg)
        return cases

    def _load(self, path: Path) -> EvalCase:
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            msg = f"cannot read golden case {path.name}: {exc}"
            raise GoldenSetError(msg) from exc
        if not isinstance(payload, dict):
            msg = f"golden case {path.name} is not a mapping"
            raise GoldenSetError(msg)
        try:
            return EvalCase.model_validate(payload)
        except ValidationError as exc:
            msg = f"invalid golden case {path.name}: {exc}"
            raise GoldenSetError(msg) from exc
