"""Detection metrics: TPR by error count, TNR, Rogan-Gladen (no network)."""

import pytest

from evals.claim_benchmark.harness import (
    MethodResult,
    PlantedError,
    Variant,
    detection_rates,
    rogan_gladen,
    score_results,
)

SENTENCES = ("First one.", "Second one.", "Third one.")


def _variant(name: str, *errors: PlantedError) -> Variant:
    first = errors[0] if errors else None
    return Variant(name, "c", "en-US", "T", "S", SENTENCES, first, tuple(errors[1:]))


def test_detection_rates_hand_worked_example() -> None:
    rows = [
        (_variant("a"), MethodResult(publish=True, flagged=[])),
        (_variant("b"), MethodResult(publish=False, flagged=["First one."])),
        (
            _variant("c", PlantedError("number", 1)),
            MethodResult(publish=False, flagged=["Second one."]),
        ),
        (_variant("d", PlantedError("number", 1)), MethodResult(publish=True, flagged=[])),
        (
            _variant("e", PlantedError("number", 1), PlantedError("date", 2)),
            MethodResult(publish=False, flagged=["Second one."]),
        ),
    ]

    rates = detection_rates(score_results(rows))

    assert rates["tpr_single"] == 0.5
    assert rates["tpr_multi"] == 1.0
    assert rates["tnr_clean_articles"] == 0.5
    assert rates["tnr_clean_sentences"] == pytest.approx(10 / 11)
    # p = 3 of 5 flagged, TPR = 2/3, TNR = 1/2: (0.6 + 0.5 - 1) / (2/3 + 0.5 - 1) = 0.6
    assert rates["rogan_gladen"] == pytest.approx(0.6)


def test_rogan_gladen_formula_and_undefined_case() -> None:
    assert rogan_gladen(observed=0.3, tpr=0.9, tnr=0.8) == pytest.approx(0.1 / 0.7)
    assert rogan_gladen(observed=0.3, tpr=0.5, tnr=0.5) is None
    assert rogan_gladen(observed=0.0, tpr=0.9, tnr=0.8) == 0.0  # clipped from below


def test_detection_rates_without_multi_error_articles() -> None:
    rows = [
        (
            _variant("c", PlantedError("number", 1)),
            MethodResult(publish=False, flagged=["Second one."]),
        )
    ]

    assert detection_rates(score_results(rows))["tpr_multi"] is None
