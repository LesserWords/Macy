"""Tests for MCP spend preflight, usage ledger metering, and runtime claim verification."""

from __future__ import annotations

import ast
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest

from engine import budget, db, safe_http, services
from engine.mcp.errors import MCPErrorCode, format_exception_error
from engine.mcp.mock_store import DEMO_RUN_ID, DEMO_WS_ID, MockStore
from engine.safe_http import SafeResponse
from tests.test_mcp_auth import _seed_workspace_fixture


def _seed_spend_workspace(
    conn: psycopg.Connection[Any],
    *,
    tag: str,
    monthly_cap: Decimal = Decimal("20.00"),
    run_cap: Decimal = Decimal("5.00"),
) -> dict[str, Any]:
    email = f"spend-{tag}-{uuid.uuid4().hex[:6]}@example.com"
    ws = _seed_workspace_fixture(conn, tag=tag, owner_email=email, with_campaign=False)
    camp = services.save_campaign(
        conn,
        workspace_id=ws["workspace_id"],
        user_id=ws["owner_id"],
        locale="pt-BR",
        market="BR",
        articles_per_week=2,
        schedule_cron="0 9 * * 1",
        monthly_cap_usd=monthly_cap,
        run_cap_usd=run_cap,
        name=f"Spend Campaign {tag}",
    )
    now = datetime.now(UTC)
    run_id = db.create_run(
        conn,
        workspace_id=ws["workspace_id"],
        campaign_id=camp["id"],
        trigger="manual",
        window_start=now - timedelta(days=7),
        window_end=now,
        run_cap_usd=run_cap,
        code_version="v1",
        pack_version="p1",
        created_by=ws["owner_id"],
    )
    return {**ws, "campaign_id": camp["id"], "run_id": run_id}


def test_preflight_spend_cap_aborts_before_llm_and_records_usage(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preflight halts before paid LLM/Jev calls on budget cap breach and meters usage_ledger."""
    ws = _seed_spend_workspace(db_conn, tag="cap")
    ws_id: uuid.UUID = ws["workspace_id"]
    owner_id: uuid.UUID = ws["owner_id"]
    run_id: uuid.UUID = ws["run_id"]

    db.record_usage(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="research",
        kind="llm",
        vendor="openrouter",
        cost_usd=Decimal("4.800000"),
        model="anthropic/claude-haiku-5.5",
    )

    with pytest.raises(budget.BudgetCapReached):
        services.preflight_mcp_spend(
            db_conn,
            workspace_id=ws_id,
            run_id=run_id,
            estimated_cost=Decimal("0.30"),
        )

    assert (
        format_exception_error(budget.BudgetCapReached(run_id, Decimal("5"), Decimal("5.1")))[
            "error"
        ]
        == MCPErrorCode.BUDGET_CAP_EXCEEDED.value
    )

    with pytest.raises(budget.MonthlyCapReached):
        services.preflight_mcp_spend(
            db_conn,
            workspace_id=ws_id,
            run_id=None,
            estimated_cost=Decimal("16.00"),
        )

    db.record_usage(
        db_conn,
        workspace_id=ws_id,
        run_id=run_id,
        stage="score",
        kind="llm",
        vendor="openrouter",
        cost_usd=Decimal("15.200000"),
        model="anthropic/claude-haiku-5.5",
    )

    upstream_calls = 0

    def _forbidden_safe_request(*args: Any, **kwargs: Any) -> SafeResponse:
        nonlocal upstream_calls
        upstream_calls += 1
        raise AssertionError("Paid upstream API should never run when cap is reached")

    monkeypatch.setattr(safe_http, "safe_request", _forbidden_safe_request)

    with pytest.raises(budget.MonthlyCapReached):
        services.verify_claims(
            db_conn,
            workspace_id=ws_id,
            user_id=owner_id,
            claims=["Optical sales grew 18% in 2026."],
            passages=["In 2026, optical sales grew 18% nationwide."],
            jev_api_key="sk-or-never-called",
        )
    assert upstream_calls == 0

    ws_ok = _seed_spend_workspace(
        db_conn,
        tag="meter",
        monthly_cap=Decimal("50.00"),
        run_cap=Decimal("10.00"),
    )

    def _fake_jev_post(method: str, url: str, **kwargs: Any) -> SafeResponse:
        assert method == "POST"
        assert "alpha/decisions" in url
        body = json.loads(json.dumps(kwargs["json_data"]))
        assert body["provider"]["data_collection"] == "deny"
        payload = {
            "model": "typesafe/jev-1.13",
            "usage": {"cost": 0.00059},
            "answers": {
                "claim_0": {
                    "type": "choice",
                    "choice": "supported",
                    "confidence": 0.94,
                    "probabilities": {"supported": 0.94},
                }
            },
        }
        return SafeResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            content=json.dumps(payload).encode("utf-8"),
        )

    monkeypatch.setattr(safe_http, "safe_request", _fake_jev_post)
    verified = services.verify_claims(
        db_conn,
        workspace_id=ws_ok["workspace_id"],
        user_id=ws_ok["owner_id"],
        claims=["Optical sales grew 18% in 2026."],
        passages=["In 2026, optical sales grew 18% nationwide."],
        jev_api_key="sk-or-metered-test-key",
    )
    assert verified["results"][0]["verdict"] == "supported"
    assert Decimal(str(verified["total_cost_usd"])) == Decimal("0.000590")

    usage_rows = db.list_usage(db_conn, workspace_id=ws_ok["workspace_id"])
    verify_rows = [row for row in usage_rows if row["stage"] == "verify_claims"]
    assert len(verify_rows) == 1
    assert verify_rows[0]["cost_usd"] == Decimal("0.000590")
    assert verify_rows[0]["vendor"] == "openrouter"

    managed_spend = services.preflight_mcp_spend(workspace_id=ws_ok["workspace_id"])
    assert managed_spend["workspace_id"] == ws_ok["workspace_id"]

    mock_store = MockStore.create_default({"APP_ENV": "development"})
    mock_store._runs[DEMO_RUN_ID]["cost_usd"] = Decimal("99.000000")
    with pytest.raises(budget.BudgetCapReached):
        mock_store.preflight_spend(workspace_id=DEMO_WS_ID, run_id=DEMO_RUN_ID)


def _assert_no_evals_or_httpx2_imports(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".", 1)[0]
                assert top not in {"evals", "httpx2"}, f"Forbidden import {alias.name} in {path}"
        elif isinstance(node, ast.ImportFrom) and node.module:
            top = node.module.split(".", 1)[0]
            assert top not in {"evals", "httpx2"}, f"Forbidden import {node.module} in {path}"


def test_runtime_claims_without_evals_or_httpx2(
    db_conn: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """engine/quality/claims.py runs without evals or httpx2 and enforces numeric/quote guards."""
    ws = _seed_spend_workspace(db_conn, tag="no-llm")
    monkeypatch.setitem(sys.modules, "evals", None)
    monkeypatch.setitem(sys.modules, "evals.claim_benchmark", None)
    monkeypatch.setitem(sys.modules, "evals.claim_benchmark.jev", None)
    monkeypatch.setitem(sys.modules, "httpx2", None)

    from engine.quality import claims as runtime_claims

    _assert_no_evals_or_httpx2_imports(Path(runtime_claims.__file__))

    det_supported = runtime_claims.verify_claim_deterministic(
        "Revenue reached 1,234.50 BRL across 12 stores.",
        "Annual audit showed revenue reached 1.234,50 BRL across 12 stores.",
    )
    assert det_supported.numbers_ok is True
    assert det_supported.preliminary_verdict == "supported"
    assert "1.234,50" in det_supported.evidence_quote

    det_refuted = runtime_claims.verify_claim_deterministic(
        "Optical stores grew revenue by 95% in 2026.",
        "Optical stores grew revenue by 18% in 2026.",
    )
    assert det_refuted.numbers_ok is False
    assert det_refuted.preliminary_verdict == "refuted"

    class _EscalationLLM:
        def healthcheck(self) -> None:
            return None

        def complete(self, prompt: str, schema: type[Any] | None = None) -> dict[str, Any]:
            assert "CLAIM:" in prompt
            return {
                "verdict": "supported",
                "confidence": 0.88,
                "evidence_quote": "Stores reported a 14% conversion lift after training.",
            }

    def _jev_mixed(method: str, url: str, **kwargs: Any) -> SafeResponse:
        assert method == "POST"
        assert url == runtime_claims.DECISIONS_URL
        payload = {
            "model": runtime_claims.JEV_MODEL,
            "usage": {"cost": 0.0003},
            "answers": {
                "claim_0": {"type": "choice", "choice": "supported", "confidence": 0.91},
                "claim_1": {"type": "choice", "choice": "contradicted", "confidence": 0.93},
                "claim_2": {"type": "choice", "choice": "supported", "confidence": 0.89},
                "claim_3": {"type": "choice", "choice": "supported", "confidence": 0.40},
            },
        }
        return SafeResponse(status_code=200, headers={}, content=json.dumps(payload).encode())

    monkeypatch.setattr(safe_http, "safe_request", _jev_mixed)
    source = (
        "Stores reported a 14% conversion lift after training. "
        "Multifocal lenses accounted for 40% of unit volume in Q3."
    )
    results = runtime_claims.verify_claims_against_sources(
        [
            "Multifocal lenses accounted for 40% of unit volume in Q3.",
            "Multifocal lenses accounted for 85% of unit volume in Q3.",
            "Staff reduced wait time by 99 minutes per customer.",
            "Training improved store conversion by 14%.",
        ],
        [source],
        llm=_EscalationLLM(),
        jev_api_key="sk-or-test",
    )
    assert results[0].verdict == "supported"
    assert "40%" in results[0].evidence_quote
    assert results[1].verdict == "refuted"
    # Numeric code guard blocks Jev's hallucinated 'supported' verdict for 99 minutes:
    assert results[2].verdict == "unverifiable"
    # Low-confidence Jev decision escalates to injected LLMPort:
    assert results[3].verdict == "supported"

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(PermissionError):
        services.verify_claims(
            db_conn,
            workspace_id=ws["workspace_id"],
            user_id=ws["owner_id"],
            claims=["Optical sales grew."],
            passages=["Optical sales grew in 2026."],
            offline=True,
        )
    monkeypatch.setenv("APP_ENV", "development")
    with pytest.raises(ValueError, match="Missing required integration 'llm'"):
        services.verify_claims(
            db_conn,
            workspace_id=ws["workspace_id"],
            user_id=ws["owner_id"],
            claims=["Optical sales grew."],
            passages=["Optical sales grew in 2026."],
            offline=False,
        )

    assert runtime_claims.verify_claims_against_sources([], ["source"]) == []
    assert runtime_claims.normalize_verdict("unknown-status") == "unverifiable"
    fmt_check = runtime_claims.verify_claim_deterministic(
        "Taxas de 12,5% e 1.200 unidades em 1,2.3,4.",
        "Em 1.200 casos, taxas de 12.5% e 1,2.3,4 ocorreram.",
    )
    assert fmt_check.numbers_ok is True
    assert fmt_check.preliminary_verdict == "supported"

    monkeypatch.setattr(
        safe_http,
        "safe_request",
        lambda *_a, **_kw: SafeResponse(status_code=502, headers={}, content=b"bad gateway"),
    )
    with pytest.raises(RuntimeError, match="HTTP 502"):
        runtime_claims.verify_claims_against_sources(
            ["Optical sales grew 18%."], ["Optical sales grew 18%."], jev_api_key="sk-or-test"
        )

    def _jev_partial(*_args: Any, **_kwargs: Any) -> SafeResponse:
        payload = {
            "usage": {"cost": 0.0001},
            "answers": {
                "claim_0": {"type": "other"},
                "claim_1": {"type": "choice", "choice": "supported", "confidence": 0.4},
            },
        }
        return SafeResponse(status_code=200, headers={}, content=json.dumps(payload).encode())

    monkeypatch.setattr(safe_http, "safe_request", _jev_partial)
    partial_res = runtime_claims.verify_claims_against_sources(
        ["Conversion lifted 14%.", "Optical sales grew 18%."],
        ["Conversion lifted 14% and optical sales grew 18%."],
        llm=None,
        jev_api_key="sk-or-test",
    )
    assert [r.judge for r in partial_res] == ["deterministic", "jev"]
    assert all(r.verdict == "supported" for r in partial_res)
