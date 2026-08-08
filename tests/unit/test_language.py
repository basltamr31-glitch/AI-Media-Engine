"""Tests for BCP-47 language tags (ADR 0015).

The point of validating rather than enumerating: any language is expressible,
but ``"english"`` and ``"EN-gb"`` are not both storable. Uncanonicalised tags
silently fragment every per-language query.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from engine.core.domain.language import (
    ARABIC,
    ENGLISH,
    LanguageTag,
    base_language,
    normalise_language_tag,
)


class Model(BaseModel):
    language: LanguageTag


class TestNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ar", "ar"),
            ("en", "en"),
            ("EN", "en"),
            ("  fr  ", "fr"),
            ("en-gb", "en-GB"),
            ("EN-GB", "en-GB"),
            ("pt-br", "pt-BR"),
            ("zh-hans", "zh-Hans"),
            ("zh-HANS-cn", "zh-Hans-CN"),
            ("es-419", "es-419"),
            ("fil", "fil"),
        ],
    )
    def test_canonical_casing(self, raw: str, expected: str):
        assert normalise_language_tag(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", "english", "e", "toolong", "en_GB", "en-", "-en", "en-GB-extra-bits", "123"],
    )
    def test_malformed_tags_are_rejected(self, raw: str):
        with pytest.raises(ValueError, match="not a valid language tag"):
            normalise_language_tag(raw)

    def test_the_error_shows_valid_examples(self):
        with pytest.raises(ValueError, match="en-GB"):
            normalise_language_tag("english")


class TestPydanticIntegration:
    def test_tags_are_canonicalised_on_construction(self):
        assert Model(language="EN-gb").language == "en-GB"

    def test_invalid_tags_fail_validation(self):
        with pytest.raises(ValidationError):
            Model(language="english")

    def test_any_language_is_expressible(self):
        """The whole reason for dropping the enum: a German customer needs no migration."""
        for tag in ("de", "ja", "ko", "hi", "sw", "tr", "pt-BR", "nb"):
            assert Model(language=tag).language == tag


class TestBaseLanguage:
    @pytest.mark.parametrize(
        ("tag", "expected"),
        [("en", "en"), ("en-GB", "en"), ("zh-Hans-CN", "zh"), ("pt-BR", "pt")],
    )
    def test_primary_subtag(self, tag: str, expected: str):
        assert base_language(tag) == expected

    def test_a_regional_workspace_still_matches_a_generic_profile(self):
        assert base_language("en-GB") == base_language(ENGLISH)


class TestConstants:
    def test_shipped_constants_are_valid(self):
        assert normalise_language_tag(ARABIC) == "ar"
        assert normalise_language_tag(ENGLISH) == "en"
