"""Primary-source extraction port.

The editorial rule this exists to enforce: **newsletters are tips, not text**
(ADR 0003). A newsletter tells us *what* to look at; what we actually write
from is the announcement, paper or release itself. Everything downstream --
generation, evidence, the fair-use posture -- depends on that distinction being
real rather than aspirational.

The port is narrow on purpose. Extraction is the part most likely to be
replaced (a better library, a paid API, a model that reads screenshots), and a
one-method interface makes that a swap rather than a rewrite.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.core.domain.story import EnrichedSource


class ExtractionError(RuntimeError):
    """Raised when a document cannot be turned into usable text.

    A single exception type, like :class:`~engine.core.ports.http.HttpError`:
    the stage isolates the failure per story rather than branching on the cause.

    It carries a short, stable ``code`` alongside the human-readable message.
    Callers that need to *count* failures use the code; deriving one by
    slicing the message is how "only 320 characters extracted" and "only 91
    characters extracted" become two different categories.
    """

    def __init__(self, url: str, message: str, *, code: str = "extraction_failed") -> None:
        self.url = url
        self.code = code
        super().__init__(f"{message} (url={url})")


@runtime_checkable
class ContentExtractor(Protocol):
    """Turns a fetched page into the text a story is actually about."""

    def extract(self, html: str, *, url: str) -> EnrichedSource:
        """Extract the main content of one page.

        Implementations must strip navigation, comments and boilerplate: the
        text is fed to generation later, and a sidebar quoted as if it were the
        article is both an accuracy problem and a copyright one.

        Raises:
            ExtractionError: If no usable content could be recovered.
        """
        ...
