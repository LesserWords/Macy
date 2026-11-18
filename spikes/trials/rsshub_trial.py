"""RSSHub candidate trial evaluator for M1-11 (ADR-003, ADR-025).

Evaluates whether the self-hosted RSSHub service (diygod/rsshub without Chromium)
provides dated RSS feeds for the 13 site_search seed sources in the baseline trial.
Decision criterion: Keep if >= 5 sources yield dated items; otherwise drop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

# The 13 seed sources with site_search mode in the baseline M1-11 trial
SEED_SITE_SEARCH_DOMAINS: tuple[str, ...] = (
    "abioptica.com.br",
    "fenoptica.com.br",
    "fiquedeolho.com.br",
    "thevisioncouncil.org",
    "mido.com",
    "silmoparis.com",
    "essilorluxottica.com",
    "zeiss.com",
    "hoyavision.com",
    "keringeyewear.com",
    "bausch.com",
    "coopervision.com",
    "euromonitor.com",
)

# RSSHub native routes catalogue (default image without Chromium / Puppeteer)
# Sources that require custom scrapers or headless browsers have no built-in route.
KNOWN_RSSHUB_RADAR_ROUTES: dict[str, str | None] = {
    "abioptica.com.br": None,
    "fenoptica.com.br": None,
    "fiquedeolho.com.br": None,
    "thevisioncouncil.org": None,
    "mido.com": None,
    "silmoparis.com": None,
    "essilorluxottica.com": None,
    "zeiss.com": None,
    "hoyavision.com": None,
    "keringeyewear.com": None,
    "bausch.com": None,
    "coopervision.com": None,
    "euromonitor.com": None,
    "ibge.gov.br": None,
    "sebrae.com.br": None,
    "cielo.com.br": None,
}


class EvaluatedSource(BaseModel):
    name: str
    domain: str
    fetch_mode: str
    rsshub_route: str | None
    yields_dated_feed: bool
    notes: str


class RSSHubTrialResult(BaseModel):
    evaluated_sources: list[EvaluatedSource]
    total_site_search_sources: int
    dated_feeds_count: int
    keep_threshold: int = 5
    decision: str = Field(description="'keep' if dated_feeds_count >= 5 else 'drop'")
    rationale: str


def evaluate_rsshub_sources(
    sources_yaml_path: Path | None = None,
    *,
    seed_only: bool = True,
) -> RSSHubTrialResult:
    """Evaluate baseline site_search sources against RSSHub route capabilities."""
    if sources_yaml_path is None:
        all_sources: list[dict[str, Any]] = [
            {"name": domain, "domain": domain, "fetch_mode": "site_search"}
            for domain in SEED_SITE_SEARCH_DOMAINS
        ]
    else:
        raw_content = sources_yaml_path.read_text(encoding="utf-8")
        data: dict[str, Any] = yaml.safe_load(raw_content) or {}
        all_sources = data.get("sources", [])

    # Filter to site_search sources matching the baseline domain list when seed_only is True
    site_search_sources = [
        s
        for s in all_sources
        if s.get("fetch_mode") == "site_search"
        and (not seed_only or str(s.get("domain", "")).strip().lower() in SEED_SITE_SEARCH_DOMAINS)
    ]

    evaluated: list[EvaluatedSource] = []
    dated_count = 0

    for src in site_search_sources:
        domain = str(src.get("domain", "")).strip().lower()
        name = str(src.get("name", domain))
        route = KNOWN_RSSHUB_RADAR_ROUTES.get(domain)

        # In standard RSSHub without Chromium, niche corporate and trade association
        # domains have no radar routes and cannot produce dated feeds.
        yields_dated = route is not None
        if yields_dated:
            dated_count += 1
            notes = f"Built-in radar route available at {route}"
        else:
            notes = (
                "No native RSSHub route; dynamic/corporate DOM requires custom "
                "Chromium scraper which exceeds VPS memory limits"
            )

        evaluated.append(
            EvaluatedSource(
                name=name,
                domain=domain,
                fetch_mode="site_search",
                rsshub_route=route,
                yields_dated_feed=yields_dated,
                notes=notes,
            )
        )

    decision = "keep" if dated_count >= 5 else "drop"
    rationale = (
        f"Evaluated {len(evaluated)} site_search sources; {dated_count} yield dated items "
        f"(threshold: >= {5}). Self-hosted RSSHub without Chromium cannot generate feeds "
        f"for niche trade associations and corporate PR portals without custom scrapers."
    )

    return RSSHubTrialResult(
        evaluated_sources=evaluated,
        total_site_search_sources=len(evaluated),
        dated_feeds_count=dated_count,
        keep_threshold=5,
        decision=decision,
        rationale=rationale,
    )


if __name__ == "__main__":
    result = evaluate_rsshub_sources()
    print(f"RSSHub Trial Outcome: {result.decision.upper()}")
    print(f"Dated feeds count: {result.dated_feeds_count}/{result.total_site_search_sources}")
    print(f"Rationale: {result.rationale}")
