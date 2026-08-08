"""Language tags.

ADR 0015. A two-member enum could not express a German finance customer without
a migration, so languages are validated BCP-47 tags rather than enumerated.

Validated, not free-form: ``"english"``, ``"EN-gb"`` and ``"en_GB"`` must not all
be storable, or every per-language query silently fragments.

The grammar accepted is the subset that matters here -- ``language``,
``language-Script``, ``language-REGION``, ``language-Script-REGION``. Extensions,
private-use subtags and grandfathered tags are rejected; nothing in this system
needs them, and accepting them would mean carrying a full BCP-47 parser.
"""

from __future__ import annotations

import re
from typing import Annotated, Final

from pydantic import AfterValidator

_TAG_PATTERN: Final = re.compile(
    r"^(?P<language>[A-Za-z]{2,3})"
    r"(?:-(?P<script>[A-Za-z]{4}))?"
    r"(?:-(?P<region>[A-Za-z]{2}|\d{3}))?$"
)

#: Convenience constants for the languages this install currently produces.
#: These are values, not a closed set -- any valid tag is accepted.
ARABIC: Final = "ar"
ENGLISH: Final = "en"


def normalise_language_tag(value: str) -> str:
    """Validate and canonicalise a BCP-47 tag.

    Canonical casing follows the standard: lowercase language, titlecase script,
    uppercase region. ``en-gb`` and ``EN-GB`` both become ``en-GB``.

    Raises:
        ValueError: If the tag is malformed.
    """
    candidate = value.strip()
    match = _TAG_PATTERN.match(candidate)
    if match is None:
        msg = (
            f"{value!r} is not a valid language tag. Expected BCP-47, "
            "e.g. 'ar', 'en', 'en-GB', 'zh-Hans', 'pt-BR'."
        )
        raise ValueError(msg)

    parts = [match.group("language").lower()]
    if script := match.group("script"):
        parts.append(script.title())
    if region := match.group("region"):
        parts.append(region.upper())
    return "-".join(parts)


#: A validated, canonicalised BCP-47 language tag.
LanguageTag = Annotated[str, AfterValidator(normalise_language_tag)]


def base_language(tag: str) -> str:
    """The primary subtag: ``en-GB`` -> ``en``.

    Used when matching a brand profile to a workspace language, so a profile
    written for ``en`` still serves a workspace configured for ``en-GB``.
    """
    return tag.split("-")[0].lower()


#: Names for the tags this install produces. Deliberately a short map with a
#: fallback rather than a dependency on a full CLDR table: the only consumer is
#: prompt construction, and one wrong entry there is a mistranslated post.
_LANGUAGE_NAMES: Final[dict[str, str]] = {
    "ar": "Arabic",
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "tr": "Turkish",
    "ur": "Urdu",
    "hi": "Hindi",
    "zh": "Chinese",
}


def language_name(tag: str) -> str:
    """A language's name in words, for addressing a model.

    Models follow "write in Arabic" far more reliably than "write in ar" --
    a tag is an identifier, and asking for prose in one is asking the model to
    guess. Unknown tags fall back to the tag itself, which is still better than
    silently writing in the wrong language.
    """
    return _LANGUAGE_NAMES.get(base_language(tag), tag)
