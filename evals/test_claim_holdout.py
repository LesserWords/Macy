"""Holdout composition and multi-error variant tests (no network)."""

from pathlib import Path

import pytest

from evals.claim_benchmark import BenchmarkDataError
from evals.claim_benchmark.harness import (
    MethodResult,
    PlantedError,
    build_variants,
    composition_problems,
    dataset_sha256,
    load_dataset,
    score_results,
)

CASE = {
    "id": "x",
    "locale": "en-US",
    "title": "T",
    "source": "Sales rose 10%. Stores opened in May.",
    "sentences": [
        {
            "text": "Sales rose 10%.",
            "errors": [{"type": "number", "find": "10%", "replace": "20%"}],
        },
        {
            "text": "Stores opened in May.",
            "errors": [{"type": "date", "find": "May", "replace": "June"}],
        },
        {"text": "Worth watching."},
    ],
    "combinations": [["s0e0", "s1e0"]],
}


def test_combination_variant_plants_every_listed_error() -> None:
    combo = build_variants(CASE)[-1]

    assert combo.id == "x/combo0"
    assert combo.sentences == ("Sales rose 20%.", "Stores opened in June.", "Worth watching.")
    assert combo.errors == (PlantedError("number", 0), PlantedError("date", 1))


def test_combination_on_one_sentence_twice_is_rejected() -> None:
    with pytest.raises(BenchmarkDataError, match="combination"):
        build_variants({**CASE, "combinations": [["s0e0", "s0e0"]]})


def test_multi_error_article_scores_each_error() -> None:
    combo = build_variants(CASE)[-1]
    result = MethodResult(publish=False, flagged=["Sales rose 20%.", "Worth watching."])

    score = score_results([(combo, result)])

    assert (score.errors_total, score.errors_caught) == (2, 1)
    assert (score.dirty_articles, score.false_passes) == (1, 0)
    assert (score.clean_sentences, score.false_alarms) == (1, 1)


def test_composition_problems_names_every_shortfall() -> None:
    problems = composition_problems(build_variants(CASE))

    assert [p.split(":")[0] for p in problems] == [
        "articles",
        "en-US articles",
        "pt-BR articles",
        "planted errors",
        "clean articles",
        "median source words",
    ]


def test_holdout_with_matching_hash_but_too_small_is_refused(tmp_path: Path) -> None:
    import yaml

    holdout = tmp_path / "cases.yaml"
    holdout.write_text(yaml.safe_dump([CASE]), encoding="utf-8")

    with pytest.raises(BenchmarkDataError, match="composition"):
        load_dataset("holdout", path=holdout, expected_sha256=dataset_sha256(holdout))
