"""Paid SERP RankTrackerPort adapters (Serper / DataForSEO) with ledger caps (M6-03 D4)."""

from __future__ import annotations

import base64
import urllib.parse
import uuid
from decimal import Decimal
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, ConfigDict

from engine import budget, db, safe_http

SERPER_ENDPOINT = "https://google.serper.dev/search"
DATAFORSEO_SERP_ENDPOINT = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"


def compute_delta_status(previous: int | None, current: int | None) -> str:
    """Classify SERP ranking movement cleanly across nullable top-100 boundaries."""
    if previous is None and current is not None:
        return "entered_top_100"
    if previous is not None and current is None:
        return "dropped_out"
    if previous is None or current is None:
        return "unranked"
    if current < previous:
        return "improved"
    return "declined" if current > previous else "unchanged"


def _domain_matches(candidate_url: str, domain: str) -> bool:
    host = urllib.parse.urlparse(candidate_url).netloc.lower().removeprefix("www.")
    target = domain.strip().lower().removeprefix("www.")
    return host == target or host.endswith(f".{target}")


def _extract_serper_rank(payload: dict[str, Any], domain: str) -> tuple[int | None, str | None]:
    for item in payload.get("organic", []):
        link = str(item.get("link", ""))
        if link and _domain_matches(link, domain):
            pos = item.get("position")
            return (int(pos) if pos is not None else None), link
    return None, None


def _extract_dataforseo_rank(payload: dict[str, Any], domain: str) -> tuple[int | None, str | None]:
    tasks = payload.get("tasks", [])
    items = tasks[0].get("result", [{}])[0].get("items", []) if tasks else []
    for item in items:
        if item.get("type") != "organic":
            continue
        link = str(item.get("url", ""))
        if link and _domain_matches(link, domain):
            pos = item.get("rank_absolute") or item.get("rank_group")
            return (int(pos) if pos is not None else None), link
    return None, None


class SerpRankTracker:
    """Paid SERP rank tracker gated behind rank_tracking_enabled and usage_ledger caps."""

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")

        provider: Literal["serper", "dataforseo"] = "serper"
        api_key: str = ""
        api_login: str = ""
        rank_tracking_enabled: bool = False
        cost_per_check_usd: Decimal = Decimal("0.002000")

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()

    def _require_enabled(self) -> None:
        if not self.settings.rank_tracking_enabled:
            msg = "SERP rank tracking disabled for workspace plan (rank_tracking_enabled=False)."
            raise PermissionError(msg)

    def healthcheck(self) -> None:
        """Verify paid feature flag and credentials."""
        self._require_enabled()
        if not self.settings.api_key.strip():
            msg = f"{self.settings.provider} API key is required for rank tracking."
            raise ValueError(msg)

    def check_rank(
        self,
        *,
        keyword: str,
        domain: str,
        locale: str = "pt-BR",
        market: str = "BR",
    ) -> dict[str, Any]:
        """Query SERP provider for keyword/domain organic rank position."""
        self._require_enabled()
        gl = market.strip().lower() or "br"
        hl = locale.split("-")[0].strip().lower() or "pt"

        if self.settings.provider == "serper":
            resp = safe_http.safe_request(
                "POST",
                SERPER_ENDPOINT,
                headers={"X-API-KEY": self.settings.api_key},
                json_data={"q": keyword, "gl": gl, "hl": hl, "num": 100},
                timeout=10.0,
            )
            payload = dict(resp.json()) if resp.status_code == 200 else {}
            pos, url = _extract_serper_rank(payload, domain)
        else:
            creds = base64.b64encode(
                f"{self.settings.api_login}:{self.settings.api_key}".encode()
            ).decode("ascii")
            resp = safe_http.safe_request(
                "POST",
                DATAFORSEO_SERP_ENDPOINT,
                headers={"Authorization": f"Basic {creds}"},
                json_data=[{"keyword": keyword, "language_code": hl, "location_name": market}],
                timeout=10.0,
            )
            payload = dict(resp.json()) if resp.status_code == 200 else {}
            pos, url = _extract_dataforseo_rank(payload, domain)

        return {
            "keyword": keyword,
            "domain": domain,
            "locale": locale,
            "market": market,
            "position": pos,
            "ranking_url": url,
            "provider": self.settings.provider,
        }

    def track_keyword(  # noqa: PLR0913
        self,
        conn: psycopg.Connection[Any],
        *,
        workspace_id: uuid.UUID,
        keyword: str,
        domain: str,
        locale: str = "pt-BR",
        market: str = "BR",
        monthly_cap_usd: Decimal = Decimal("50.00"),
        run_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Enforce plan toggle and monthly cap, query SERP rank, record spend and history."""
        self._require_enabled()
        budget.check_monthly_budget(
            conn, workspace_id=workspace_id, monthly_cap_usd=monthly_cap_usd
        )

        prev_rows = db.list_keyword_rankings(
            conn, workspace_id=workspace_id, domain=domain, keyword=keyword, limit=1
        )
        prev_pos = int(prev_rows[0]["position"]) if prev_rows and prev_rows[0]["position"] else None

        res = self.check_rank(keyword=keyword, domain=domain, locale=locale, market=market)
        curr_pos: int | None = res["position"]
        delta_status = compute_delta_status(prev_pos, curr_pos)

        db.record_usage(
            conn,
            workspace_id=workspace_id,
            run_id=run_id,
            stage="seo_monitor",
            kind="api",
            vendor=self.settings.provider,
            cost_usd=self.settings.cost_per_check_usd,
        )
        ranking_id = db.insert_keyword_ranking(
            conn,
            workspace_id=workspace_id,
            keyword=keyword,
            domain=domain,
            locale=locale,
            market=market,
            position=curr_pos,
            previous_position=prev_pos,
            ranking_url=res["ranking_url"],
            provider=self.settings.provider,
        )
        return {
            **res,
            "ranking_id": ranking_id,
            "previous_position": prev_pos,
            "delta_status": delta_status,
            "cost_usd": self.settings.cost_per_check_usd,
        }
