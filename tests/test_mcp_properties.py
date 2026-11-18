"""Property-based tests for MCP spend preflight, gate normalization, claims, and redaction."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine import budget, db, events, services
from engine.mcp.errors import MCPError, MCPErrorCode, format_exception_error
from engine.mcp.mock_store import DEMO_WS_ID, MockStore
from engine.quality import claims as claims_mod

_ALNUM_SECRET = st.from_regex(r"[A-Za-z0-9]{10,24}", fullmatch=True)


class _AlwaysPassLLM:
    def healthcheck(self) -> None:
        return None

    def complete(self, prompt: str, schema: type[Any] | None = None) -> dict[str, Any]:
        del prompt, schema
        return {"verdict": "supported", "confidence": 0.99, "evidence_quote": ""}


@given(
    monthly_spend=st.decimals(min_value=Decimal("0"), max_value=Decimal("100"), places=6),
    monthly_cap=st.decimals(min_value=Decimal("1"), max_value=Decimal("100"), places=2),
    run_spend=st.decimals(min_value=Decimal("0"), max_value=Decimal("25"), places=6),
    run_cap=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("25"), places=2),
    est_cost=st.decimals(min_value=Decimal("0"), max_value=Decimal("10"), places=6),
)
def test_preflight_spend_monotonicity_property(
    monthly_spend: Decimal,
    monthly_cap: Decimal,
    run_spend: Decimal,
    run_cap: Decimal,
    est_cost: Decimal,
) -> None:
    """Preflight always aborts with BUDGET_CAP_EXCEEDED semantics at cap boundaries."""
    ws_id = uuid.uuid4()
    run_id = uuid.uuid4()
    mp = pytest.MonkeyPatch()
    mp.setattr(
        db,
        "list_campaigns",
        lambda conn, *, workspace_id: [{"id": uuid.uuid4(), "monthly_cap_usd": monthly_cap}],
    )
    mp.setattr(
        db,
        "get_monthly_spend",
        lambda conn, *, workspace_id, reference_date=None: monthly_spend,
    )
    mp.setattr(
        db,
        "get_run",
        lambda conn, *, workspace_id, run_id: {"id": run_id, "run_cap_usd": run_cap},
    )
    mp.setattr(db, "get_run_total_cost", lambda conn, *, workspace_id, run_id: run_spend)
    mp.setattr(db, "update_run_status", lambda *args, **kwargs: True)
    mp.setattr(events, "emit", lambda *args, **kwargs: uuid.uuid4())
    dummy_conn: Any = object()
    try:
        monthly_blocked = monthly_spend >= monthly_cap or (monthly_spend + est_cost > monthly_cap)
        eff_est = est_cost if est_cost > Decimal("0") else Decimal("0.000001")
        run_blocked = run_spend + eff_est > run_cap
        if monthly_blocked:
            with pytest.raises(budget.MonthlyCapReached):
                services.preflight_mcp_spend(
                    dummy_conn, workspace_id=ws_id, run_id=run_id, estimated_cost=est_cost
                )
        elif run_blocked:
            with pytest.raises(budget.BudgetCapReached):
                services.preflight_mcp_spend(
                    dummy_conn, workspace_id=ws_id, run_id=run_id, estimated_cost=est_cost
                )
        else:
            res = services.preflight_mcp_spend(
                dummy_conn, workspace_id=ws_id, run_id=run_id, estimated_cost=est_cost
            )
            assert res["monthly_spend_usd"] == monthly_spend
    finally:
        mp.undo()


@given(
    monthly_spend=st.decimals(min_value=Decimal("0"), max_value=Decimal("200"), places=6),
    est_cost=st.decimals(min_value=Decimal("0"), max_value=Decimal("50"), places=6),
)
def test_mock_store_spend_preflight_parity_property(
    monthly_spend: Decimal,
    est_cost: Decimal,
) -> None:
    """MockStore enforces identical monthly spend cap boundaries as services."""
    store = MockStore.create_default({"APP_ENV": "development"})
    ws = store._workspaces[DEMO_WS_ID]
    ws["monthly_spend_usd"] = monthly_spend
    cap = Decimal(str(ws["monthly_cap_usd"]))
    if monthly_spend >= cap or monthly_spend + est_cost > cap:
        with pytest.raises(budget.MonthlyCapReached):
            store.preflight_spend(workspace_id=DEMO_WS_ID, estimated_cost=est_cost)
    else:
        assert (
            store.preflight_spend(workspace_id=DEMO_WS_ID, estimated_cost=est_cost) == monthly_spend
        )


@given(raw=st.text(max_size=30))
def test_gate_decision_normalization_property(raw: str) -> None:
    """Gate 1 and Gate 2 normalizers strictly allow only their canonical decision sets."""
    norm = raw.strip().lower()
    if norm in {"approve", "approved"}:
        assert services._normalize_topic_decision(raw) == "approved"
        assert services._normalize_draft_decision(raw) == "approved"
    elif norm in {"reject", "rejected"}:
        assert services._normalize_topic_decision(raw) == "rejected"
        with pytest.raises(ValueError, match="Invalid decision"):
            services._normalize_draft_decision(raw)
    elif norm in {"decline", "declined"}:
        with pytest.raises(ValueError, match="Invalid decision"):
            services._normalize_topic_decision(raw)
        assert services._normalize_draft_decision(raw) == "declined"
    else:
        with pytest.raises(ValueError, match="Invalid decision"):
            services._normalize_topic_decision(raw)
        with pytest.raises(ValueError, match="Invalid decision"):
            services._normalize_draft_decision(raw)


@given(
    claim=st.text(max_size=240),
    source=st.text(max_size=400),
    number=st.integers(min_value=10, max_value=999),
)
def test_claim_verifier_invariants_property(claim: str, source: str, number: int) -> None:
    """Deterministic checker handles Unicode cleanly and numeric guard blocks hallucination."""
    res = claims_mod.verify_claim_deterministic(claim, source)
    assert res.preliminary_verdict in {"supported", "refuted", "unverifiable"}
    assert 0.0 <= res.confidence <= 1.0
    assert len(res.evidence_quote) <= 400
    if res.evidence_quote:
        assert res.evidence_quote in source

    pt_claim = f"A rede optica elevou o giro em {number}% no varejo."
    contradicting_source = f"Estudo apontou variacao de {number + 77}% no varejo em 2026."
    guarded = claims_mod.verify_claims_against_sources(
        [pt_claim], [contradicting_source], llm=_AlwaysPassLLM()
    )
    assert len(guarded) == 1
    assert guarded[0].verdict != "supported"


@given(bearer_secret=_ALNUM_SECRET, dsn_pass=_ALNUM_SECRET)
def test_mcp_error_redaction_property(bearer_secret: str, dsn_pass: str) -> None:
    """MCPError and format_exception_error never leak Bearer tokens or postgres DSN passwords."""
    msg = f"Failed Bearer {bearer_secret} connecting postgresql://app:{dsn_pass}@db:5432/macy"
    err = MCPError(MCPErrorCode.INTERNAL_ERROR, msg).to_dict()
    formatted = format_exception_error(RuntimeError(msg), mcp_tool="macy_test")
    for out in (err, formatted):
        assert bearer_secret not in out["message"]
        assert dsn_pass not in out["message"]
        assert "Bearer [REDACTED]" in out["message"]
