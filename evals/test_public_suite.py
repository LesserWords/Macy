"""Public suite loader tests with fixture rows (no network)."""

import json
from pathlib import Path

import pytest

from evals.public_suite.loader import (
    TIERS,
    PublicSuiteError,
    Sample,
    load_cached,
    map_rows,
    stratified_sample,
    write_cache,
)


def _samples(n_per_label: int) -> list[Sample]:
    return [
        Sample(f"id{i:03d}", "pt-BR", "source", f"claim {i}", supported=i % 2 == 0, subset="all")
        for i in range(2 * n_per_label)
    ]


def test_stratified_sample_is_balanced_and_repeatable() -> None:
    rows = _samples(20)

    first = stratified_sample(rows, size=10, seed=7)
    second = stratified_sample(list(reversed(rows)), size=10, seed=7)

    assert [s.id for s in first] == [s.id for s in second]
    assert sum(s.supported for s in first) == 5


def test_stratified_sample_rejects_too_few_rows() -> None:
    with pytest.raises(PublicSuiteError, match="only"):
        stratified_sample(_samples(2), size=10, seed=7)


def test_assin2_rows_map_entailment_to_supported() -> None:
    rows = [
        {"sentence_pair_id": 1, "premise": "P1", "hypothesis": "H1", "entailment_judgment": 1},
        {"sentence_pair_id": 2, "premise": "P2", "hypothesis": "H2", "entailment_judgment": 0},
    ]

    samples = map_rows(TIERS["b"], rows)

    assert samples == [
        Sample("assin2-1", "pt-BR", "P1", "H1", supported=True, subset="assin2"),
        Sample("assin2-2", "pt-BR", "P2", "H2", supported=False, subset="assin2"),
    ]


def test_aggrefact_rows_keep_only_requested_subsets() -> None:
    rows = [
        {"dataset": "RAGTruth", "doc": "D1", "claim": "C1", "label": 1},
        {"dataset": "Reveal", "doc": "D2", "claim": "C2", "label": 0},
        {"dataset": "ExpertQA", "doc": "D3", "claim": "C3", "label": 1},
    ]

    samples = map_rows(TIERS["a"], rows)

    assert [(s.subset, s.source, s.claim, s.supported) for s in samples] == [
        ("RAGTruth", "D1", "C1", True),
        ("Reveal", "D2", "C2", False),
    ]
    assert samples[0].id != samples[1].id


def test_gated_tier_without_token_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    from evals.public_suite.loader import fetch_tier

    monkeypatch.delenv("HF_TOKEN", raising=False)

    with pytest.raises(PublicSuiteError, match="HF_TOKEN"):
        fetch_tier("a")


def test_cache_must_match_manifest(tmp_path: Path) -> None:
    cache = tmp_path / "tier_b.jsonl"
    manifest = tmp_path / "manifest.json"
    samples = _samples(2)
    write_cache("b", samples, cache=cache, manifest=manifest, seed=7)

    assert load_cached("b", cache=cache, manifest=manifest) == samples
    assert json.loads(manifest.read_text(encoding="utf-8"))["b"]["ids"] == [s.id for s in samples]

    cache.write_text(
        cache.read_text(encoding="utf-8").replace("claim 0", "claim X"), encoding="utf-8"
    )
    with pytest.raises(PublicSuiteError, match="SHA-256"):
        load_cached("b", cache=cache, manifest=manifest)


def test_public_samples_become_one_sentence_articles() -> None:
    from evals.claim_benchmark.harness import PlantedError, public_variants

    supported, unsupported = _samples(1)

    variants = public_variants([supported, unsupported])

    assert [(v.id, v.sentences, v.source, v.error) for v in variants] == [
        ("id000", ("claim 0",), "source", None),
        ("id001", ("claim 1",), "source", PlantedError("unsupported", 0)),
    ]
