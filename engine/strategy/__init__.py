"""Strategy module for opportunity scoring, brief drafting, and report generation."""

from engine.strategy.brief import InvalidEvidenceReferenceError, brief, synthesize_brief_with_prompt

__all__ = [
    "InvalidEvidenceReferenceError",
    "brief",
    "synthesize_brief_with_prompt",
]
