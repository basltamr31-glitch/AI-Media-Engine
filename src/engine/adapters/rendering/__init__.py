"""Rendering: a day's output as files on disk.

Two artifacts, and the relationship between them is the design.
``package.json`` is canonical -- self-contained, deterministic, versioned, and
what another program reads. ``post.md`` is a *view* of it for a person deciding
what to publish. Because the Markdown is derived rather than assembled
separately, the two cannot drift apart.
"""

from engine.adapters.rendering.markdown import is_rtl, render_body, render_markdown
from engine.adapters.rendering.package import SCHEMA_VERSION, build_package

__all__ = [
    "SCHEMA_VERSION",
    "build_package",
    "is_rtl",
    "render_body",
    "render_markdown",
]
