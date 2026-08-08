"""Golden-set loading and the eval runner.

The shipped golden set is loaded here rather than a fixture one, so a malformed
case file fails in the suite instead of the first time someone runs an eval.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.adapters.evaluation.golden import GoldenSet, GoldenSetError
from engine.adapters.evaluation.runner import EvalRunner
from engine.adapters.generation.prompts import PROMPT_VERSION, GeneratedContent
from engine.cli.container import find_project_root
from tests.fakes import FakeLLM

pytestmark = pytest.mark.integration

GOLDEN_DIR = find_project_root() / "evals" / "golden"


def answering(body: str) -> FakeLLM:
    return FakeLLM(
        value=GeneratedContent(body=body, hashtags=[], source_url="https://arxiv.org/abs/1")
    )


class TestGoldenSet:
    def test_the_shipped_set_loads(self):
        """A malformed case must fail here, not in front of an operator."""
        cases = GoldenSet(GOLDEN_DIR).load_all()

        assert cases
        assert all(case.source_text.strip() for case in cases)

    def test_it_covers_both_languages(self):
        """Language compliance is the regression the set exists to catch."""
        languages = {case.language for case in GoldenSet(GOLDEN_DIR).load_all()}

        assert {"ar", "en"} <= languages

    def test_it_covers_a_tight_character_limit(self):
        """X's 280 is where a small model fails; a set of only long-form cases
        would score well and prove nothing."""
        limits = {case.max_characters for case in GoldenSet(GOLDEN_DIR).load_all()}

        assert min(limits) <= 280

    def test_cases_are_returned_in_a_stable_order(self):
        first = [case.key for case in GoldenSet(GOLDEN_DIR).load_all()]
        second = [case.key for case in GoldenSet(GOLDEN_DIR).load_all()]

        assert first == second == sorted(first)

    def test_a_missing_directory_is_reported(self, tmp_path: Path):
        with pytest.raises(GoldenSetError, match="no golden set"):
            GoldenSet(tmp_path / "absent").load_all()

    def test_an_empty_directory_is_reported(self, tmp_path: Path):
        with pytest.raises(GoldenSetError, match="no golden cases"):
            GoldenSet(tmp_path).load_all()

    def test_a_malformed_case_names_the_file(self, tmp_path: Path):
        (tmp_path / "broken.yaml").write_text("key: broken\n", encoding="utf-8")

        with pytest.raises(GoldenSetError, match=r"broken\.yaml"):
            GoldenSet(tmp_path).load_all()

    def test_duplicate_keys_are_refused(self, tmp_path: Path):
        """Two cases sharing a key would overwrite each other in the baseline,
        which is exactly where a regression would hide."""
        body = (
            "headline: H\nsource_url: https://x.test\nsource_text: T\n"
            "language: en\nchannel_key: x\nmax_characters: 280\n"
        )
        (tmp_path / "a.yaml").write_text(f"key: same\n{body}", encoding="utf-8")
        (tmp_path / "b.yaml").write_text(f"key: same\n{body}", encoding="utf-8")

        with pytest.raises(GoldenSetError, match="duplicate"):
            GoldenSet(tmp_path).load_all()


class TestEvalRunner:
    def test_a_good_post_passes_every_case(self):
        cases = [case for case in GoldenSet(GOLDEN_DIR).load_all() if case.language == "en"]
        good = (
            "MatrAIx simulates many different users so products can be checked "
            "against people who do not all behave the same way. That matters because "
            "offline testing usually flattens exactly that variation."
        )

        report = EvalRunner(llm=answering(good), model="test-model").run(cases)

        assert report.passed, [
            (o.key, [c.detail for c in o.failures]) for o in report.outcomes if not o.passed
        ]
        assert report.score == pytest.approx(1.0)

    def test_it_records_which_prompt_produced_the_scores(self):
        """A score without its prompt version cannot be compared to anything."""
        cases = GoldenSet(GOLDEN_DIR).load_all()[:1]

        report = EvalRunner(llm=answering("A short post about it."), model="m").run(cases)

        assert report.prompt_version == PROMPT_VERSION

    def test_an_english_post_fails_the_arabic_case(self):
        """The regression that shipped silently in gen-1 and gen-2."""
        cases = [case for case in GoldenSet(GOLDEN_DIR).load_all() if case.language == "ar"]
        assert cases

        report = EvalRunner(
            llm=answering("MatrAIx introduces a simulated-user evaluation infrastructure."),
            model="m",
        ).run(cases)

        assert not report.passed
        assert "language" in [check.name for check in report.outcomes[0].failures]

    def test_repeating_samples_each_case_and_reports_the_sampling(self):
        """One sample of a non-deterministic model is a coin toss, not a score."""
        cases = GoldenSet(GOLDEN_DIR).load_all()[:1]
        llm = answering("A short factual post about the work.")

        report = EvalRunner(llm=llm, model="m", repeat=3).run(cases)

        assert len(llm.requests) == 3
        assert report.repeat == 3

    def test_a_provider_failure_scores_zero_rather_than_being_skipped(self):
        """'The model was down' and 'the model wrote nothing usable' are both
        regressions from the reader's point of view."""
        cases = GoldenSet(GOLDEN_DIR).load_all()[:1]

        report = EvalRunner(llm=FakeLLM(fails_with="connection refused"), model="m").run(cases)

        assert not report.passed
        assert report.score == 0.0
        assert report.outcomes[0].error is not None
