"""Generation: turning a selected story into publishable text.

The model is reached through :class:`~engine.core.ports.llm.LLMPort`, so which
one writes is configuration. What lives here is everything that is *not* the
model: how a brand voice and a channel's constraints become a prompt, what
shape the output must take, and which prompt version produced a given revision.
"""

from engine.adapters.generation.prompts import (
    MAX_SOURCE_CHARS,
    PROMPT_VERSION,
    GeneratedContent,
    build_cacheable_prefix,
    build_prompt,
    build_system,
)

__all__ = [
    "MAX_SOURCE_CHARS",
    "PROMPT_VERSION",
    "GeneratedContent",
    "build_cacheable_prefix",
    "build_prompt",
    "build_system",
]
