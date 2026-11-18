"""Frozen benchmark rules (OpenSpec change 2026-11-10-claim-judge-jev-cascade, BM-1).

Change these only through a new OpenSpec change; runs made before a change do not count.
"""

from typing import Final

# Worth key results, as fractions (0.10 = 10 percentage points).
W1_MIN_IMPROVEMENT: Final = 0.10
W1_MAX_REGRESSION: Final = 0.03
W2_MIN_CATCH: Final = 0.90
W2_MAX_FALSE_PASS: Final = 0.10
W3_MAX_CLEAN_REJECT: Final = 0.25
W4_MAX_COST_RATIO: Final = 0.50
W5_MAX_GAP: Final = 0.03
W6_MIN_ACCEPT_PRECISION: Final = 0.97
W7_MIN_EVIDENCE_CORRECT: Final = 0.90
W7_AUDIT_CLAIMS: Final = 50
W8_MAX_LANGUAGE_GAP: Final = 0.05
W9_MAX_RUN_RANGE: Final = 0.05
RUNS_PER_MEASUREMENT: Final = 3

# Holdout composition (D4).
D4_MIN_ARTICLES: Final = 30
D4_MIN_PER_LANGUAGE: Final = 15
D4_MIN_ERRORS: Final = 120
D4_MIN_CLEAN: Final = 30
D4_MIN_MULTI_ERROR_SHARE: Final = 0.15
D4_MIN_MEDIAN_SOURCE_WORDS: Final = 800

THRESHOLD_GRID: Final = (0.70, 0.75, 0.80, 0.85, 0.90, 0.95)
JEV_MODEL: Final = "typesafe/jev-1.13"
# Outcome candidates (amended by OpenSpec change 2026-11-11-claim-judge-add-jev-judge).
JEV_CANDIDATES: Final = ("jev_judge", "jev_cascade")
MAX_STATE_BYTES: Final = 30_000
BUDGET_USD: Final = 5.0

# SHA-256 of each dataset file with CRLF normalized to LF.
DEV_SHA256: Final = "559e9ed50e979dfa6a04c857f6cd258d9a01d24f3ed814b0918ed6e3d0af645c"
HOLDOUT_SHA256: Final[str | None] = (
    "95881bce0c70e501c9705ce11b2ce626f19de331c266baf421dc7d9ee7182519"
)
