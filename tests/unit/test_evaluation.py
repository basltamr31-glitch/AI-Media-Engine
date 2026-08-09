"""Tests for the algorithmic quality checks.

Every case here is drawn from a failure that actually happened while the
generation prompt was being written, which is the only honest way to know a
check is worth its runtime.
"""

from __future__ import annotations

import pytest

from engine.core.domain.evaluation import (
    EvalCase,
    check_hook,
    check_language,
    check_length,
    check_must_include,
    check_no_fused_words,
    check_not_copied,
    check_numbers_supported,
    evaluate,
    overall_score,
)

SOURCE = (
    "MatrAIx is a population-scale simulated-user evaluation infrastructure. "
    "Persona 8B contains 8.3 billion persona records represented by 1,290 "
    "categorical dimensions. We release a coreset of approximately 1 million "
    "personas, comprising 599,847 human-grounded and 400,000 synthetic records. "
    "We conducted 18,189 evaluation trials across eight representative tasks. "
    "The declared behavior was expressed or correctly suppressed in 366 trials (91.5%)."
)

ARABIC_POST = "يقدم MatrAIx بنية تحتية لتقييم أنظمة الذكاء الاصطناعي باستخدام 8.3 مليار سجل."


class TestLanguage:
    def test_an_arabic_post_passes(self):
        assert check_language(ARABIC_POST, "ar").passed

    def test_an_english_post_on_an_arabic_profile_fails(self):
        """The regression that shipped silently: the asset was labelled `ar`
        either way, so nothing downstream noticed."""
        result = check_language("MatrAIx introduces a simulated-user infrastructure.", "ar")

        assert not result.passed
        assert result.score == 0.0

    def test_latin_product_names_do_not_sink_an_arabic_post(self):
        """A correct Arabic post still says MatrAIx and 8.3, in Latin."""
        assert check_language(ARABIC_POST, "ar").score < 1.0
        assert check_language(ARABIC_POST, "ar").passed

    def test_english_is_checked_too(self):
        assert check_language("A short English post about a model.", "en").passed
        assert not check_language(ARABIC_POST, "en").passed

    def test_an_unknown_language_is_not_guessed_at(self):
        """A wrong expectation would fail every correct post."""
        result = check_language("Kwalunkwe test bil-Malti.", "mt")

        assert result.passed
        assert "not checked" in result.detail

    def test_a_post_with_no_letters_fails(self):
        assert not check_language("8.3 --- 1,290 (91.5%)", "en").passed


class TestNotCopied:
    def test_original_writing_passes(self):
        post = (
            "A new evaluation harness simulates users at population scale. "
            "It ships with a filtered subset and a set of tasks across many domains, "
            "which makes broad testing practical for the first time."
        )

        assert check_not_copied(post, SOURCE).passed

    def test_a_lifted_sentence_is_caught(self):
        """The model fell into this when it ran out of things to say."""
        result = check_not_copied(
            "Persona 8B contains 8.3 billion persona records represented by 1,290 "
            "categorical dimensions.",
            SOURCE,
        )

        assert not result.passed
        assert "verbatim" in result.detail

    def test_a_very_short_post_is_not_judged(self):
        assert check_not_copied("Too short.", SOURCE).passed


class TestNumbersSupported:
    def test_figures_copied_from_the_source_pass(self):
        post = "The set covers 8.3 billion records and 599,847 human-grounded personas."

        assert check_numbers_supported(post, SOURCE).passed

    def test_grouping_and_decimals_do_not_cause_false_alarms(self):
        """1,290 must match 1290, and 91.5% must match 91.5."""
        post = "Across 1290 dimensions, 91.5% of trials behaved as declared."

        assert check_numbers_supported(post, SOURCE).passed

    def test_an_invented_figure_is_caught(self):
        """A real hallucination from a live run: the source says 1,290
        dimensions and ~1 million personas, never 1.29 million personas."""
        result = check_numbers_supported("A dataset of 1.29 million personas.", SOURCE)

        assert not result.passed
        assert "1.29" in result.detail

    def test_a_post_with_no_figures_passes(self):
        assert check_numbers_supported("A qualitative summary.", SOURCE).passed


class TestLength:
    def test_a_post_within_the_limit_passes(self):
        assert check_length("Short.", 280).passed

    def test_a_post_over_the_limit_fails(self):
        """X's 280 characters is where a small model fails most often."""
        result = check_length("x" * 500, 280)

        assert not result.passed
        assert "500 of 280" in result.detail


class TestHook:
    def test_the_opening_sentence_passes(self):
        body = "The lab shipped open weights today. More detail follows."

        assert check_hook("The lab shipped open weights today.", body).passed

    def test_a_question_about_the_post_fails(self):
        """Observed live: the model answered the field description instead of
        following it."""
        body = "The lab shipped open weights today."

        assert not check_hook("What is the primary use case here?", body).passed

    def test_hashtags_in_the_hook_field_fail(self):
        assert not check_hook("#AI #Evaluation", "The lab shipped open weights.").passed

    def test_an_empty_hook_fails(self):
        assert not check_hook("   ", "A post.").passed


class TestMustInclude:
    def test_present_terms_pass(self):
        assert check_must_include("MatrAIx simulates users.", ["MatrAIx"]).passed

    def test_a_missing_term_is_named(self):
        result = check_must_include("A simulated-user harness.", ["MatrAIx", "Persona 8B"])

        assert not result.passed
        assert "MatrAIx" in result.detail

    def test_no_requirement_passes(self):
        assert check_must_include("Anything.", []).passed


class TestEvaluate:
    def a_case(self, **overrides) -> EvalCase:
        defaults = {
            "key": "matraix",
            "headline": "MatrAIx",
            "source_url": "https://arxiv.org/abs/2608.04205",
            "source_text": SOURCE,
            "language": "en",
            "channel_key": "linkedin",
            "max_characters": 3000,
            "must_include": ["MatrAIx"],
        }
        return EvalCase(**(defaults | overrides))

    def test_a_good_post_passes_every_check(self):
        body = (
            "MatrAIx simulates users at population scale so products can be tested "
            "against people who are not all the same. Its released subset covers "
            "599,847 human-grounded profiles, and behaviour held in 91.5% of trials."
        )
        results = evaluate(body=body, hook=body.split(".")[0] + ".", case=self.a_case())

        assert all(result.passed for result in results), [
            (r.name, r.detail) for r in results if not r.passed
        ]
        assert overall_score(results) == pytest.approx(1.0)

    def test_the_arabic_regression_is_caught_end_to_end(self):
        """gen-1 and gen-2 both produced this; gen-3 fixed it."""
        body = "MatrAIx introduces a simulated-user evaluation infrastructure."
        results = evaluate(body=body, hook=body, case=self.a_case(language="ar"))

        failed = [result.name for result in results if not result.passed]
        assert "language" in failed
        assert overall_score(results) < 1.0


class TestNoFusedWords:
    """Found by reading real output, not by design.

    A live Arabic post welded the Arabic word for "art" onto the tail of
    "financial". Every other check passed it: 78% Arabic script, nothing
    copied, no unsupported number, length fine. Unreadable to an Arabic
    reader and invisible to everyone else -- the same shape as the RTL bug.
    """

    def test_a_fused_word_is_caught(self):
        result = check_no_fused_words("القطاع الفنancial يعتمد على معايير")

        assert not result.passed
        assert "الفنancial" in result.detail

    def test_a_latin_product_name_in_arabic_is_fine(self):
        """The prompt asks for product names in their original spelling."""
        assert check_no_fused_words("نموذج MatrAIx الجديد يقدم أداء أفضل").passed

    def test_clean_arabic_passes(self):
        assert check_no_fused_words("نص عربي سليم تماما بدون أي مشاكل").passed

    def test_clean_english_passes(self):
        assert check_no_fused_words("A normal English post about AI models.").passed

    def test_empty_text_passes(self):
        assert check_no_fused_words("").passed

    def test_more_fusion_scores_worse(self):
        one = check_no_fused_words("الفنancial كلمة عادية أخرى هنا تماما")
        two = check_no_fused_words("الفنancial والعلمtechnical كلمة أخرى هنا")

        assert two.score < one.score

    def test_it_is_part_of_the_suite(self):
        """A check that exists but never runs catches nothing."""
        case = TestEvaluate().a_case(language="ar")

        names = {c.name for c in evaluate(body="x", hook="x", case=case)}

        assert "no_fused_words" in names
