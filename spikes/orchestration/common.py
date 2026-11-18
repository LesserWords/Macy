"""Shared contracts and fake steps for the M0-09 orchestration spike."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ActiveRunExistsError(Exception):
    """Raised when an active run already exists for a campaign."""


class StepExecutionError(Exception):
    """Raised when a pipeline step fails for a given item."""


@dataclass(frozen=True)
class PipelineItem:
    item_id: str
    campaign_id: str
    raw_text: str


@dataclass(frozen=True)
class ResearchOutput:
    item_id: str
    summary: str
    key_findings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "summary": self.summary,
            "key_findings": self.key_findings,
        }


@dataclass(frozen=True)
class ScoreOutput:
    item_id: str
    score: float
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "score": self.score,
            "status": self.status,
        }


def fake_research_step(
    item_id: str,
    raw_text: str,
    *,
    should_fail: bool = False,
) -> ResearchOutput:
    """Simulate stage 1: research and extraction."""
    if should_fail:
        raise StepExecutionError(f"Research failed for item {item_id}")
    return ResearchOutput(
        item_id=item_id,
        summary=f"Summary for {raw_text[:20]}",
        key_findings=[f"Finding 1 for {item_id}", f"Finding 2 for {item_id}"],
    )


def fake_score_step(
    item_id: str,
    summary: str,
    *,
    should_fail: bool = False,
) -> ScoreOutput:
    """Simulate stage 2: scoring."""
    if should_fail:
        raise StepExecutionError(f"Scoring failed for item {item_id}")
    score = round(0.5 + (len(summary) % 50) / 100.0, 2)
    return ScoreOutput(
        item_id=item_id,
        score=score,
        status="scored",
    )
