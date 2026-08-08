"""Tests for the edit delta -- the measurement the moat dataset is made of.

The revision chain keeps the model's draft and the human's rewrite. These
numbers are what makes that pair queryable: "is the generator improving?" is a
question about the average of this measurement over time, and nothing else in
the system answers it.
"""

from __future__ import annotations

import pytest

from engine.core.domain.editing import REWRITE_THRESHOLD, EditDelta, measure_edit


class TestMeasuringAnEdit:
    def test_identical_text_scores_one(self):
        delta = measure_edit("A short factual post.", "A short factual post.")

        assert delta.similarity == 1.0
        assert delta.is_unchanged
        assert delta.characters_changed == 0

    def test_a_small_correction_stays_similar(self):
        delta = measure_edit(
            "The lab released an open-weights model.",
            "The lab released an open weights model.",
        )

        assert delta.similarity > 0.9
        assert not delta.is_rewrite

    def test_a_replacement_scores_low(self):
        delta = measure_edit(
            "The lab released an open-weights model.",
            "Nobody expected this, and the numbers explain why.",
        )

        assert delta.is_rewrite

    def test_growth_and_shrinkage_are_signed(self):
        assert measure_edit("abc", "abcdef").characters_changed == 3
        assert measure_edit("abcdef", "abc").characters_changed == -3

    def test_two_empty_strings_do_not_divide_by_zero(self):
        """The degenerate case has to have an answer, not an exception."""
        delta = measure_edit("", "")

        assert delta.similarity == 1.0
        assert delta.is_unchanged

    def test_deleting_everything_scores_zero(self):
        assert measure_edit("some text", "").similarity == 0.0


class TestArabicIsMeasuredFairly:
    """Why the comparison is character-level rather than word-level.

    Arabic marks agreement and definiteness with affixes, so a one-letter
    correction changes the inside of a word. A word-level ratio would score
    that as a whole-token replacement and report a rewrite where an editor
    would say a typo was fixed -- and the resulting number would say the
    generator is failing when it is not.
    """

    def test_an_affix_correction_is_not_a_rewrite(self):
        before = "أطلق المختبر نموذجا مفتوح الأوزان"
        after = "أطلق المختبر نموذجًا مفتوح الأوزان"

        delta = measure_edit(before, after)

        assert delta.similarity > 0.9
        assert not delta.is_rewrite

    def test_the_same_change_measured_by_words_would_look_like_a_rewrite(self):
        """Pins the reason for the design, not just its result."""
        before = "أطلق المختبر نموذجا مفتوح الأوزان"
        after = "أطلق المختبر نموذجًا مفتوح الأوزان"

        word_overlap = len(set(before.split()) & set(after.split())) / len(set(after.split()))

        assert word_overlap < measure_edit(before, after).similarity


class TestTheRewriteBoundary:
    @pytest.mark.parametrize(
        ("similarity", "expected"),
        [(0.0, True), (REWRITE_THRESHOLD - 0.01, True), (REWRITE_THRESHOLD, False), (1.0, False)],
    )
    def test_is_rewrite_uses_the_threshold(self, similarity: float, expected: bool):
        delta = EditDelta(characters_before=10, characters_after=10, similarity=similarity)

        assert delta.is_rewrite is expected


class TestTheSignalPayload:
    def test_the_context_is_flat_and_rounded(self):
        """Flat because these get averaged across thousands of rows."""
        context = measure_edit("hello world", "hello there world").as_context()

        assert set(context) == {
            "characters_before",
            "characters_after",
            "characters_changed",
            "similarity",
            "is_rewrite",
        }
        assert all(not isinstance(value, dict | list) for value in context.values())
        assert context["similarity"] == round(float(context["similarity"]), 4)

    def test_a_similarity_outside_zero_to_one_is_rejected(self):
        with pytest.raises(ValueError, match="less than or equal to 1"):
            EditDelta(characters_before=1, characters_after=1, similarity=1.5)
