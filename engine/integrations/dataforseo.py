"""DataForSEO Keywords Data API plugin implementing KeywordProviderPort (F11, ADR-005, ADR-015)."""

from __future__ import annotations

import base64
import json
import os
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from engine import budget, db
from engine.contracts import KeywordData
from engine.integrations.registry import UnsupportedLocale
from engine.safe_http import safe_request, validate_safe_url

if TYPE_CHECKING:
    import uuid

    import psycopg

DATAFORSEO_LOCALE_MAP: dict[tuple[str, str], tuple[int, str]] = {
    ("pt-BR", "br"): (2076, "pt"),
    ("en-US", "us"): (2840, "en"),
}


def map_dataforseo_locale(locale: str, market: str) -> tuple[int, str]:
    """Map campaign (locale, market) to DataForSEO (location_code, language_code)."""
    norm_locale = locale.strip()
    norm_market = market.strip().lower()
    mapped = DATAFORSEO_LOCALE_MAP.get((norm_locale, norm_market))
    if mapped is not None:
        return mapped
    if norm_locale not in ("pt-BR", "en-US"):
        raise UnsupportedLocale("dataforseo", locale)
    raise UnsupportedLocale("dataforseo", locale, market)


class DataForSEOKeywordProvider:
    """DataForSEO Google Ads search volume provider."""

    class Settings(BaseModel):
        model_config = ConfigDict(extra="forbid")
        api_login: str = Field(default="", description="DataForSEO API login")
        api_password: SecretStr = Field(
            default_factory=lambda: SecretStr(""),
            description="DataForSEO API password",
        )
        base_url: str = Field(
            default="https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live",
            description="DataForSEO live search volume endpoint URL",
        )
        unit_cost_usd: Decimal = Field(
            default=Decimal("0.000500"),
            description="Cost per keyword query in USD",
        )
        timeout_seconds: float = Field(default=15.0, description="HTTP timeout in seconds")

    settings: Settings

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or self.Settings()

    def _raw_credentials(self) -> tuple[str, str]:
        login = self.settings.api_login or os.environ.get("DATAFORSEO_LOGIN", "")
        pw = (
            self.settings.api_password.get_secret_value()
            if isinstance(self.settings.api_password, SecretStr)
            else str(self.settings.api_password or "")
        )
        password = pw or os.environ.get("DATAFORSEO_PASSWORD", "")
        return login.strip(), password.strip()

    def healthcheck(self) -> None:
        """Verify endpoint URL safety."""
        validate_safe_url(self.settings.base_url)

    def lookup(  # noqa: PLR0913
        self,
        terms: list[str],
        locale: str,
        market: str,
        *,
        conn: psycopg.Connection[Any] | None = None,
        workspace_id: uuid.UUID | None = None,
        run_id: uuid.UUID | None = None,
        stage: str | None = None,
        **kwargs: Any,
    ) -> list[KeywordData]:
        """Lookup keyword metrics from DataForSEO, verifying budget and recording usage."""
        if not terms:
            return []

        location_code, language_code = map_dataforseo_locale(locale, market)
        active_conn = conn
        active_ws = workspace_id
        active_run = run_id
        active_stage = stage or "keywords"

        total_cost = self.settings.unit_cost_usd * Decimal(len(terms))

        if active_conn is not None and active_ws is not None:
            _enforce_budget_guards(active_conn, active_ws, active_run, total_cost)

        login, password = self._raw_credentials()
        auth_bytes = f"{login}:{password}".encode()
        auth_header = f"Basic {base64.b64encode(auth_bytes).decode('ascii')}"

        request_body = [
            {
                "keywords": terms,
                "location_code": location_code,
                "language_code": language_code,
            }
        ]

        start_time = time.perf_counter()
        resp = safe_request(
            "POST",
            self.settings.base_url,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/json",
            },
            json_data=request_body,
            timeout=self.settings.timeout_seconds,
        )
        latency_ms = int((time.perf_counter() - start_time) * 1000)

        if resp.status_code >= 400:
            msg = f"DataForSEO API request failed with HTTP {resp.status_code}"
            raise ValueError(msg)

        if active_conn is not None and active_ws is not None:
            db.record_usage(
                active_conn,
                workspace_id=active_ws,
                run_id=active_run,
                stage=active_stage,
                kind="api",
                vendor="dataforseo",
                model="google_ads_search_volume",
                units=Decimal(len(terms)),
                cost_usd=total_cost,
                latency_ms=latency_ms,
            )

        return _parse_dataforseo_response(resp.text, terms)


def _enforce_budget_guards(
    conn: psycopg.Connection[Any],
    workspace_id: uuid.UUID,
    run_id: uuid.UUID | None,
    total_cost: Decimal,
) -> None:
    if run_id is not None:
        run_row = db.get_run(conn, workspace_id=workspace_id, run_id=run_id)
        if run_row is not None:
            run_cap = Decimal(str(run_row["run_cap_usd"]))
            budget.check_run_budget(
                conn,
                workspace_id=workspace_id,
                run_id=run_id,
                run_cap_usd=run_cap,
                estimated_cost=total_cost,
            )
            campaign = db.get_campaign(
                conn,
                workspace_id=workspace_id,
                campaign_id=run_row["campaign_id"],
            )
            if campaign is not None and "monthly_cap_usd" in campaign:
                budget.check_monthly_budget(
                    conn,
                    workspace_id=workspace_id,
                    monthly_cap_usd=Decimal(str(campaign["monthly_cap_usd"])),
                )
    else:
        campaigns = db.list_campaigns(conn, workspace_id=workspace_id)
        if campaigns:
            camp = campaigns[0]
            if "monthly_cap_usd" in camp:
                budget.check_monthly_budget(
                    conn,
                    workspace_id=workspace_id,
                    monthly_cap_usd=Decimal(str(camp["monthly_cap_usd"])),
                )


def _extract_results_by_keyword(tasks: list[Any]) -> dict[str, dict[str, Any]]:
    results_by_keyword: dict[str, dict[str, Any]] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        items = task.get("result", []) or []
        for item in items:
            if isinstance(item, dict) and "keyword" in item:
                kw = str(item["keyword"]).strip().lower()
                results_by_keyword[kw] = item
    return results_by_keyword


def _item_to_keyword_data(term: str, item: dict[str, Any] | None) -> KeywordData:
    if item is not None and item.get("search_volume") is not None:
        intent_info = item.get("search_intent_info", {})
        intent = (
            intent_info.get("main_intent", "informational")
            if isinstance(intent_info, dict)
            else "informational"
        )
        diff = float(item.get("competition_index") or item.get("keyword_difficulty") or 0.0)
        cpc = float(item.get("cpc") or 0.0)
        vol = int(item["search_volume"])
        return KeywordData(
            keyword=term,
            search_volume=vol,
            difficulty=diff,
            cpc_usd=cpc,
            intent=intent,
            is_estimate=False,
        )
    return KeywordData(
        keyword=term,
        search_volume=0,
        difficulty=0.0,
        cpc_usd=0.0,
        intent="informational",
        is_estimate=True,
    )


def _parse_dataforseo_response(response_text: str, requested_terms: list[str]) -> list[KeywordData]:
    """Parse DataForSEO JSON response into KeywordData models."""
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError:
        return [_item_to_keyword_data(t, None) for t in requested_terms]

    tasks = payload.get("tasks", []) if isinstance(payload, dict) else []
    results_by_keyword = _extract_results_by_keyword(tasks)
    return [
        _item_to_keyword_data(term, results_by_keyword.get(term.strip().lower()))
        for term in requested_terms
    ]
