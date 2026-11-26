"""In-memory non-production mock store seeded with general demo state (ADR-027)."""

from __future__ import annotations

import os
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from engine import budget, capabilities, safe_http
from engine.audit import audit_url as _run_url_audit_core
from engine.contracts import (
    BrandProfile,
    ContentDraft,
    CrawlConfig,
    DraftClaim,
    Opportunity,
    PublishRequest,
    ScoringWeights,
)
from engine.crawl import crawl_site as _run_site_crawl_core
from engine.integrations.fakes import (
    FakeExtractor,
    FakeFeedSource,
    FakeKeywordProvider,
    FakeLLM,
    FakeNewsSearch,
    FakePublisher,
)
from engine.integrations.registry import IntegrationMissing
from engine.mcp.auth import (
    AdaptDraftRequest,
    BriefRequest,
    DraftDecisionRequest,
    EvaluateDraftRequest,
    GenerateDraftRequest,
    ImportDraftRequest,
    IngestResearchRequest,
    PublishDraftRequest,
    ResearchRequest,
    VerifyClaimsRequest,
    is_non_local_environment,
    is_staging_environment,
)
from engine.mcp.errors import MockForbiddenError
from engine.quality import claims as claims_mod

DEMO_OWNER_ID = uuid.UUID("01900000-0000-7000-8000-000000000001")
LUMINA_OWNER_ID = uuid.UUID("01900000-0000-7000-8000-000000000002")
DEMO_COOWNER_ID = uuid.UUID("01900000-0000-7000-8000-000000000003")
DEMO_MEMBER_ID = uuid.UUID("01900000-0000-7000-8000-000000000004")
DEMO_WS_ID = uuid.UUID("01900000-0000-7000-8000-000000000101")
LUMINA_WS_ID = uuid.UUID("01900000-0000-7000-8000-000000000201")
DEMO_CAMPAIGN_ID = uuid.UUID("01900000-0000-7000-8000-000000000301")
DEMO_RUN_ID = uuid.UUID("01900000-0000-7000-8000-000000000401")
DEMO_PREV_RUN_ID = uuid.UUID("01900000-0000-7000-8000-000000000402")
DEMO_DOC_ID = uuid.UUID("01900000-0000-7000-8000-000000000501")
DEMO_APPROVED_OPP_ID = uuid.UUID("01900000-0000-7000-8000-000000000605")
DEMO_BRIEF_ID = uuid.UUID("01900000-0000-7000-8000-000000000701")
DEMO_PASS_DRAFT_ID = uuid.UUID("01900000-0000-7000-8000-000000000801")

_DEMO_OPPS_SEED: tuple[tuple[str, str, str, str], ...] = (
    (
        "01900000-0000-7000-8000-000000000601",
        "Controle de Estoque de Lentes Multifocais em Redes Regionais",
        "https://abióptica.example.com/2026/11/05/estoque-lentes",
        "Padronizar giro por grau reduz ruptura no balcao",
    ),
    (
        "01900000-0000-7000-8000-000000000602",
        "Miopia Infantil e Recomendacoes Clinicas para Oticas",
        "https://cbo.example.com/2026/11/04/miopia-infantil",
        "Conexao consultiva entre receituario pediatrico e estoque",
    ),
    (
        "01900000-0000-7000-8000-000000000603",
        "CRM para Pos-Venda de Armacoes e Retorno em 12 Meses",
        "https://varejopt.example.com/2026/11/03/crm-pos-venda",
        "Recompra previsivel reduz CAC em lojas de shopping",
    ),
    (
        "01900000-0000-7000-8000-000000000604",
        "Tendencias de Tratamentos Antirreflexo no Varejo BR",
        "https://opticanews.example.com/2026/11/02/antirreflexo",
        "Comparativo de ticket medio com upgrade de lente",
    ),
)

_SEEDED_DOC_TEXT = (
    "Redes regionais de oticas reduziram a ruptura de lentes multifocais em 18% "
    "no terceiro trimestre de 2026 ao padronizar a reposicao semanal."
)
_SEEDED_FACT_TEXT = "Redes regionais reduziram a ruptura de lentes multifocais em 18% em 2026."


def is_mock_mode_requested(mock_flag: bool, env: Mapping[str, str] | None = None) -> bool:
    """Return True if --mock flag or MACY_MCP_STANDALONE=1 requests offline demo mode."""
    if mock_flag:
        return True
    source = os.environ if env is None else env
    standalone = source.get("MACY_MCP_STANDALONE", "").strip().lower()
    return standalone in ("1", "true", "yes")


def validate_mock_policy(mock_flag: bool, env: Mapping[str, str] | None = None) -> bool:
    """Enforce that mock/standalone mode is refused in staging or production."""
    requested = is_mock_mode_requested(mock_flag, env=env)
    if requested and is_non_local_environment(env):
        msg = "Mock or standalone MCP mode is forbidden in staging or production"
        raise MockForbiddenError(msg)
    return requested


def _mock_rubric_scores() -> tuple[dict[str, Any], float]:
    opp = Opportunity.model_validate(
        {
            "title": "Demo Optical Topic",
            "angle": "Practical retail benchmark",
            "funnel_stage": "mofu",
            "confidence": "high",
            "novelty": {"score": 8.0, "evidence": "Verified source", "is_estimate": False},
            "audience_fit": {"score": 8.5, "evidence": "Core ICP pain", "is_estimate": False},
            "brand_fit": {"score": 8.0, "evidence": "Aligned voice", "is_estimate": False},
            "competitor_gap": {"score": 7.5, "evidence": "Low coverage", "is_estimate": False},
            "trend": {"score": 7.0, "evidence": "Estimated demand", "is_estimate": True},
            "urgency": {"score": 8.0, "evidence": "Current cycle", "is_estimate": False},
            "weighted_total": 0.0,
        }
    )
    total = opp.compute_weighted_total(ScoringWeights())
    scores = {
        k: getattr(opp, k).model_dump(mode="json")
        for k in ("novelty", "audience_fit", "brand_fit", "competitor_gap", "trend", "urgency")
    }
    return scores, total


class MockStore:
    """In-memory demo store implementing the same boundary contract as engine.services."""

    default_workspace_slug = "acme-demo"

    def __init__(self) -> None:
        now = datetime.now(UTC)
        self.llm = FakeLLM()
        self.feed_source = FakeFeedSource()
        self.news_search = FakeNewsSearch()
        self.extractor = FakeExtractor()
        self.keyword_provider = FakeKeywordProvider()
        self.publisher = FakePublisher()

        self._users: dict[uuid.UUID, dict[str, Any]] = {
            DEMO_OWNER_ID: {
                "user_id": DEMO_OWNER_ID,
                "email": "owner@example.com",
                "name": "Demo Owner",
                "ui_locale": "pt-BR",
            },
            LUMINA_OWNER_ID: {
                "user_id": LUMINA_OWNER_ID,
                "email": "intruder@lumina.com",
                "name": "Lumina Owner",
                "ui_locale": "en-US",
            },
            DEMO_COOWNER_ID: {
                "user_id": DEMO_COOWNER_ID,
                "email": "coowner@example.com",
                "name": "Co-Owner (Non-Creator)",
                "ui_locale": "pt-BR",
            },
            DEMO_MEMBER_ID: {
                "user_id": DEMO_MEMBER_ID,
                "email": "editor@example.com",
                "name": "Editorial Member",
                "ui_locale": "pt-BR",
            },
        }
        self._workspaces: dict[uuid.UUID, dict[str, Any]] = {
            DEMO_WS_ID: {
                "workspace_id": DEMO_WS_ID,
                "slug": "acme-demo",
                "name": "Acme Content OS",
                "industry_pack": "general",
                "timezone": "America/Sao_Paulo",
                "campaign_id": DEMO_CAMPAIGN_ID,
                "campaign_name": "Optical Growth PT-BR",
                "campaign_created_by": DEMO_OWNER_ID,
                "articles_per_week": 2,
                "locale": "pt-BR",
                "market": "BR",
                "monthly_cap_usd": Decimal("120.00"),
                "run_cap_usd": Decimal("15.00"),
                "monthly_spend_usd": Decimal("12.500000"),
                "active_sources_count": 3,
                "total_sources_count": 4,
                "configured_integrations_count": 4,
                "configured_ports": ["llm", "feed_source", "keyword_provider", "publisher"],
                "latest_run": {
                    "id": DEMO_RUN_ID,
                    "campaign_id": DEMO_CAMPAIGN_ID,
                    "status": "completed",
                    "stage": "report",
                    "cost_usd": Decimal("2.450000"),
                    "created_at": now,
                    "finished_at": now,
                },
            },
            LUMINA_WS_ID: {
                "workspace_id": LUMINA_WS_ID,
                "slug": "lumina",
                "name": "Lumina Eyewear",
                "industry_pack": "general",
                "timezone": "America/New_York",
                "campaign_id": None,
                "campaign_name": None,
                "campaign_created_by": LUMINA_OWNER_ID,
                "articles_per_week": 1,
                "locale": "en-US",
                "market": "US",
                "monthly_cap_usd": Decimal("200.00"),
                "run_cap_usd": Decimal("20.00"),
                "monthly_spend_usd": Decimal("0.000000"),
                "active_sources_count": 1,
                "total_sources_count": 1,
                "configured_integrations_count": 1,
                "configured_ports": ["llm"],
                "latest_run": None,
            },
        }
        self._memberships: dict[tuple[uuid.UUID, uuid.UUID], str] = {
            (DEMO_WS_ID, DEMO_OWNER_ID): "owner",
            (DEMO_WS_ID, DEMO_COOWNER_ID): "owner",
            (DEMO_WS_ID, DEMO_MEMBER_ID): "member",
            (LUMINA_WS_ID, LUMINA_OWNER_ID): "owner",
        }
        self._enabled_capabilities: dict[uuid.UUID, list[str]] = {}
        self._runs: dict[uuid.UUID, dict[str, Any]] = {
            DEMO_RUN_ID: {
                "id": DEMO_RUN_ID,
                "workspace_id": DEMO_WS_ID,
                "campaign_id": DEMO_CAMPAIGN_ID,
                "status": "completed",
                "stage": "report",
                "cost_usd": Decimal("2.450000"),
                "created_at": now,
            },
            DEMO_PREV_RUN_ID: {
                "id": DEMO_PREV_RUN_ID,
                "workspace_id": DEMO_WS_ID,
                "campaign_id": DEMO_CAMPAIGN_ID,
                "status": "completed",
                "stage": "report",
                "cost_usd": Decimal("1.000000"),
                "created_at": now - timedelta(days=7),
            },
        }
        self._documents: dict[uuid.UUID, dict[str, Any]] = {}
        self._documents_by_run: dict[uuid.UUID, list[dict[str, Any]]] = {}
        self._opportunities: dict[uuid.UUID, dict[str, Any]] = {}
        self._brands: dict[uuid.UUID, dict[str, Any]] = {}
        self._briefs: dict[uuid.UUID, dict[str, Any]] = {}
        self._drafts: dict[uuid.UUID, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._seed_demo_opportunities(now)
        self._seed_demo_editorial(now)

    def _seed_demo_opportunities(self, now: datetime) -> None:
        scores, base_total = _mock_rubric_scores()
        for idx, (raw_id, title, url, angle) in enumerate(_DEMO_OPPS_SEED, start=1):
            opp_id = uuid.UUID(raw_id)
            self._opportunities[opp_id] = {
                "id": opp_id,
                "workspace_id": DEMO_WS_ID,
                "run_id": DEMO_RUN_ID,
                "rank": idx,
                "title": title,
                "summary": f"Dados verificados sobre {title}",
                "angle": angle,
                "funnel_stage": "mofu",
                "confidence": "high",
                "total": round(base_total - (idx - 1) * 1.5, 2),
                "scores": scores,
                "original_url": url,
                "published_at": now,
                "date_status": "verified",
                "is_verified_date": True,
                "is_recommended": idx <= 3,
                "already_covered": False,
                "status": "scored",
                "decision_note": None,
                "decided_by": None,
                "decided_at": None,
            }

    def _seed_demo_editorial(self, now: datetime) -> None:
        brand_id = uuid.UUID("01900000-0000-7000-8000-000000000901")
        self._brands[DEMO_WS_ID] = {
            "id": brand_id,
            "workspace_id": DEMO_WS_ID,
            "version": 1,
            "is_current": True,
            "schema_version": 1,
            "payload": {
                "voice": "Consultivo, tecnico e direto no varejo optico brasileiro.",
                "positioning": "Acme Content OS e o sistema especializado na gestao de oticas.",
                "capabilities": ["Controle de estoque por grau", "CRM pos-venda optico"],
                "prohibited_claims": ["Promessa de lucro garantido"],
                "prohibited_phrases": [r"\blucro garantido\b"],
                "cta_rules": ["TOFU educativo", "MOFU diagnostico", "BOFU demo guiada"],
            },
        }
        self._documents[DEMO_DOC_ID] = {
            "id": DEMO_DOC_ID,
            "workspace_id": DEMO_WS_ID,
            "title": "Gestao de Estoque de Lentes Multifocais em Redes Regionais",
            "url": "https://abioptica.example.com/2026/11/01/estoque-multifocal",
            "canonical_url": "https://abioptica.example.com/2026/11/01/estoque-multifocal",
            "published_at": now - timedelta(days=7),
            "date_status": "verified",
            "extracted_text": _SEEDED_DOC_TEXT,
            "summary": "Reposicao semanal reduziu ruptura de multifocais em 18% em 2026.",
            "facts": ["Redes regionais reduziram a ruptura de lentes multifocais em 18% em 2026."],
        }
        scores, base_total = _mock_rubric_scores()
        self._opportunities[DEMO_APPROVED_OPP_ID] = {
            "id": DEMO_APPROVED_OPP_ID,
            "workspace_id": DEMO_WS_ID,
            "run_id": DEMO_PREV_RUN_ID,
            "rank": 1,
            "title": "Reposicao Semanal de Lentes Multifocais em Oticas",
            "summary": _SEEDED_DOC_TEXT,
            "angle": "Benchmark operacional de reposicao semanal por grade",
            "funnel_stage": "mofu",
            "confidence": "high",
            "total": round(base_total, 2),
            "scores": scores,
            "original_url": self._documents[DEMO_DOC_ID]["canonical_url"],
            "published_at": now - timedelta(days=7),
            "date_status": "verified",
            "is_verified_date": True,
            "is_recommended": True,
            "already_covered": False,
            "status": "approved",
            "decision_note": "Tema prioritario para redes regionais",
            "decided_by": DEMO_OWNER_ID,
            "decided_at": now - timedelta(days=7),
        }
        brief = self._build_mock_brief(
            brief_id=DEMO_BRIEF_ID,
            opp=self._opportunities[DEMO_APPROVED_OPP_ID],
            version=1,
            created_at=now - timedelta(days=6),
        )
        self._briefs[DEMO_BRIEF_ID] = brief
        draft = self._build_mock_draft(
            draft_id=DEMO_PASS_DRAFT_ID,
            brief=brief,
            revision=1,
            status="pass",
            created_at=now - timedelta(days=5),
        )
        draft["quality_report"] = {"verdict": "PASS", "score": Decimal("86.5"), "findings": []}
        self._drafts[DEMO_PASS_DRAFT_ID] = draft

    def _build_mock_brief(
        self,
        *,
        brief_id: uuid.UUID,
        opp: dict[str, Any],
        version: int,
        created_at: datetime,
    ) -> dict[str, Any]:
        payload = {
            "schema_version": 1,
            "topic": opp["title"],
            "target_audience": "Donos e gestores de oticas independentes e redes regionais",
            "primary_keyword": "controle de estoque otica multifocal",
            "secondary_keywords": ["reposicao lentes multifocais", "erp otica"],
            "outline": [
                "Cenario atual de ruptura no balcao optico",
                "Padronizacao da reposicao semanal por grade",
                "Proximo passo com diagnostico Acme Content OS",
            ],
            "required_facts": [
                {
                    "fact": _SEEDED_FACT_TEXT,
                    "source_id": str(DEMO_DOC_ID),
                }
            ],
        }
        return {
            "id": brief_id,
            "workspace_id": DEMO_WS_ID,
            "opportunity_id": opp["id"],
            "run_id": opp["run_id"],
            "version": version,
            "payload": payload,
            "created_at": created_at,
        }

    def _build_mock_draft(
        self,
        *,
        draft_id: uuid.UUID,
        brief: dict[str, Any],
        revision: int,
        status: str,
        created_at: datetime,
    ) -> dict[str, Any]:
        doc = self._documents[DEMO_DOC_ID]
        claim_text = "Redes regionais reduziram a ruptura de lentes multifocais em 18% em 2026."
        payload = {
            "schema_version": 1,
            "title": "Reposicao Semanal de Lentes Multifocais no Varejo Optico",
            "meta_description": "Como padronizar a reposicao semanal reduz rupturas em 18%.",
            "slug": f"reposicao-multifocal-{draft_id.hex[:6]}",
            "body_md": (
                "# Reposicao Semanal de Lentes Multifocais\n\n"
                f"{claim_text}\n\n"
                "Solicite um diagnostico operacional com a equipe Acme Content OS."
            ),
            "faq": [],
            "cta": {"text": "Agendar demonstracao guiada"},
            "claims": [{"text": claim_text, "kind": "fact", "source_id": str(doc["id"])}],
        }
        claims = [
            {
                "workspace_id": brief["workspace_id"],
                "draft_id": draft_id,
                "idx": 0,
                "kind": "fact",
                "text": claim_text,
                "document_id": doc["id"],
                "document_title": doc["title"],
                "document_url": doc["url"],
                "document_canonical_url": doc["canonical_url"],
                "document_published_at": doc["published_at"],
                "document_date_status": doc["date_status"],
            }
        ]
        return {
            "id": draft_id,
            "workspace_id": brief["workspace_id"],
            "brief_id": brief["id"],
            "run_id": brief["run_id"],
            "opportunity_id": brief["opportunity_id"],
            "campaign_id": DEMO_CAMPAIGN_ID,
            "revision": revision,
            "status": status,
            "payload": payload,
            "claims": claims,
            "quality_report": None,
            "decision_note": None,
            "decided_by": None,
            "decided_at": None,
            "created_at": created_at,
        }

    @classmethod
    def create_default(cls, env: Mapping[str, str] | None = None) -> MockStore:
        """Create the seeded mock store after verifying non-production policy."""
        validate_mock_policy(mock_flag=True, env=env)
        return cls()

    def check_connectivity(self) -> str:
        """Return diagnostic mode identifier for --check without requiring Postgres."""
        return "mock"

    def get_workspace_id_by_slug(self, slug: str) -> uuid.UUID:
        """Resolve demo workspace UUID from slug."""
        for ws in self._workspaces.values():
            if ws["slug"] == slug:
                return uuid.UUID(str(ws["workspace_id"]))
        msg = f"Mock workspace '{slug}' not found"
        raise KeyError(msg)

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        clean = email.strip().lower()
        return next((u for u in self._users.values() if u["email"].lower() == clean), None)

    def get_user_by_id(self, user_id: uuid.UUID) -> dict[str, Any] | None:
        return self._users.get(user_id)

    def get_workspace_owner_identity(self, *, workspace_id: uuid.UUID) -> dict[str, Any] | None:
        for (ws_id, user_id), role in self._memberships.items():
            if ws_id == workspace_id and role == "owner":
                user = self._users[user_id]
                return {
                    "workspace_id": ws_id,
                    "user_id": user_id,
                    "email": user["email"],
                    "name": user["name"],
                    "role": role,
                }
        return None

    def require_member(self, *, workspace_id: uuid.UUID, user_id: uuid.UUID) -> str:
        role = self._memberships.get((workspace_id, user_id))
        if role is None:
            msg = f"User {user_id} is not a member of workspace {workspace_id}"
            raise PermissionError(msg)
        return role

    def _require_creator_owner(
        self, *, workspace_id: uuid.UUID, user_id: uuid.UUID
    ) -> dict[str, Any]:
        role = self.require_member(workspace_id=workspace_id, user_id=user_id)
        ws = self._workspaces[workspace_id]
        if role != "owner" or str(user_id) != str(ws["campaign_created_by"]):
            msg = f"User {user_id} is forbidden from deciding or publishing for campaign"
            raise PermissionError(msg)
        return ws

    def preflight_spend(
        self,
        *,
        workspace_id: uuid.UUID,
        run_id: uuid.UUID | None = None,
        estimated_cost: Decimal = Decimal("0.000000"),
    ) -> Decimal:
        """Enforce monthly and per-run budget caps on MockStore operations."""
        ws = self._workspaces[workspace_id]
        monthly_cap = Decimal(str(ws["monthly_cap_usd"]))
        monthly_spend = Decimal(str(ws["monthly_spend_usd"]))
        if monthly_spend >= monthly_cap or monthly_spend + estimated_cost > monthly_cap:
            projected = monthly_spend + estimated_cost
            raise budget.MonthlyCapReached(workspace_id, monthly_cap, projected)
        if run_id is not None and run_id in self._runs:
            run_cap = Decimal(str(ws["run_cap_usd"]))
            run_cost = Decimal(str(self._runs[run_id].get("cost_usd", "0.000000")))
            eff_cost = estimated_cost if estimated_cost > Decimal("0") else Decimal("0.000001")
            if run_cost + eff_cost > run_cap:
                raise budget.BudgetCapReached(run_id, run_cap, run_cost + eff_cost)
        return monthly_spend

    def list_accessible_workspaces(self, *, user_id: uuid.UUID) -> list[dict[str, Any]]:
        """List seeded workspaces visible to user_id."""
        rows: list[dict[str, Any]] = []
        for (ws_id, member_uid), role in self._memberships.items():
            if member_uid != user_id:
                continue
            ws = self._workspaces[ws_id]
            rows.append(
                {
                    "workspace_id": ws_id,
                    "slug": ws["slug"],
                    "name": ws["name"],
                    "industry_pack": ws["industry_pack"],
                    "timezone": ws["timezone"],
                    "role": role,
                    "campaign_count": 1 if ws["campaign_id"] is not None else 0,
                    "campaign_id": ws["campaign_id"],
                    "campaign_name": ws["campaign_name"],
                    "locale": ws["locale"],
                    "market": ws["market"],
                    "monthly_cap_usd": ws["monthly_cap_usd"],
                    "run_cap_usd": ws["run_cap_usd"],
                }
            )
        return rows

    def get_workspace_status(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Return status summary for a seeded workspace."""
        self.require_member(workspace_id=workspace_id, user_id=user_id)
        ws = self._workspaces[workspace_id]
        remaining = max(Decimal("0.000000"), ws["monthly_cap_usd"] - ws["monthly_spend_usd"])
        gate1_count = sum(
            1
            for opp in self._opportunities.values()
            if opp["workspace_id"] == workspace_id and opp["status"] == "scored"
        )
        gate2_count = sum(
            1
            for d in self._drafts.values()
            if d["workspace_id"] == workspace_id and d["status"] == "pass"
        )
        return {
            "workspace_id": workspace_id,
            "slug": ws["slug"],
            "name": ws["name"],
            "industry_pack": ws["industry_pack"],
            "timezone": ws["timezone"],
            "locale": ws["locale"],
            "market": ws["market"],
            "monthly_spend_usd": ws["monthly_spend_usd"],
            "monthly_cap_usd": ws["monthly_cap_usd"],
            "monthly_remaining_usd": remaining,
            "run_cap_usd": ws["run_cap_usd"],
            "active_sources_count": ws["active_sources_count"],
            "total_sources_count": ws["total_sources_count"],
            "configured_integrations_count": ws["configured_integrations_count"],
            "configured_ports": list(ws["configured_ports"]),
            "gate1_awaiting_count": gate1_count,
            "gate2_awaiting_count": gate2_count,
            "latest_run": ws["latest_run"],
        }

    def run_research(self, request: ResearchRequest) -> dict[str, Any]:
        """Execute offline research collection in the seeded mock workspace."""
        workspace_id = request.workspace_id
        self.require_member(workspace_id=workspace_id, user_id=request.user_id)
        ws = self._workspaces[workspace_id]
        target_run_id = request.run_id or uuid.uuid4()
        self.preflight_spend(workspace_id=workspace_id, run_id=request.run_id)
        self._runs[target_run_id] = {
            "id": target_run_id,
            "workspace_id": workspace_id,
            "campaign_id": ws["campaign_id"],
            "status": "running",
            "stage": "research",
            "cost_usd": Decimal("0.000000"),
            "created_at": datetime.now(UTC),
        }
        clean_query = (request.query or "optica").strip() or "optica"
        feed_items = self.feed_source.fetch_feed(f"https://feed.{ws['slug']}.example.com/rss")
        search_items = self.news_search.search(
            clean_query, locale=ws["locale"], market=ws["market"]
        )
        docs: list[dict[str, Any]] = []
        for item in [*feed_items, *search_items]:
            extracted = self.extractor.extract(item.url)
            raw_meta = extracted.raw or {}
            docs.append(
                {
                    "document_id": uuid.uuid4(),
                    "record_id": uuid.uuid4(),
                    "record_status": "extracted",
                    "url": item.url,
                    "canonical_url": item.url.split("?")[0],
                    "title": item.headline,
                    "published_at": item.published_at,
                    "date_status": item.verification_status,
                    "fetch_status": "ok",
                    "extracted_passage": str(raw_meta.get("extracted_text") or item.summary),
                    "summary": item.summary,
                    "facts": list(item.facts),
                }
            )
        self._documents_by_run[target_run_id] = docs
        return {
            "workspace_id": workspace_id,
            "run_id": target_run_id,
            "query": request.query.strip() if request.query else None,
            "document_count": len(docs),
            "documents": docs,
        }

    def ingest_research(self, request: IngestResearchRequest) -> dict[str, Any]:
        """Persist external agent-gathered documents into the mock workspace store."""
        workspace_id = request.workspace_id
        self.require_member(workspace_id=workspace_id, user_id=request.user_id)
        ws = self._workspaces[workspace_id]
        target_run_id = request.run_id or uuid.uuid4()
        if target_run_id not in self._runs:
            self._runs[target_run_id] = {
                "id": target_run_id,
                "workspace_id": workspace_id,
                "campaign_id": ws["campaign_id"],
                "status": "completed",
                "stage": "research",
                "cost_usd": Decimal("0.000000"),
                "created_at": datetime.now(UTC),
            }
        doc_ids: list[uuid.UUID] = []
        rec_ids: list[uuid.UUID] = []
        run_docs = self._documents_by_run.setdefault(target_run_id, [])
        for doc_spec in request.documents:
            raw_url = str(doc_spec.get("url") or "").strip()
            if raw_url:
                safe_http.validate_safe_url(raw_url)
            doc_id = uuid.uuid4()
            rec_id = uuid.uuid4()
            doc_ids.append(doc_id)
            rec_ids.append(rec_id)
            run_docs.append(
                {
                    "document_id": doc_id,
                    "record_id": rec_id,
                    "canonical_url": raw_url or f"https://external.macy.local/doc/{doc_id}",
                    "title": str(doc_spec.get("title") or "External Research Document"),
                    "source": str(doc_spec.get("source_name") or "external"),
                    "published_at": str(doc_spec.get("published_at") or datetime.now(UTC)),
                    "date_status": "verified",
                    "extracted_passage": str(doc_spec.get("summary") or ""),
                    "facts": list(doc_spec.get("facts") or []),
                }
            )
        return {
            "run_id": target_run_id,
            "topic": request.topic,
            "document_ids": doc_ids,
            "record_ids": rec_ids,
        }

    def _require_run(self, *, workspace_id: uuid.UUID, run_id: uuid.UUID) -> None:
        if run_id not in self._runs or self._runs[run_id]["workspace_id"] != workspace_id:
            msg = f"Run {run_id} not found"
            raise ValueError(msg)

    def score_topics(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        run_id: uuid.UUID | None = None,
        offline: bool | None = None,
    ) -> dict[str, Any]:
        """Score mock research documents with deterministic rubric sub-scores."""
        del offline
        self.require_member(workspace_id=workspace_id, user_id=user_id)
        ws = self._workspaces[workspace_id]
        target_run_id = run_id or DEMO_RUN_ID
        self._require_run(workspace_id=workspace_id, run_id=target_run_id)
        self.preflight_spend(workspace_id=workspace_id, run_id=target_run_id)
        docs = self._documents_by_run.get(target_run_id, [])
        if docs:
            scores, base_total = _mock_rubric_scores()
            for idx, doc in enumerate(docs, start=1):
                opp_id = uuid.uuid4()
                self._opportunities[opp_id] = {
                    "id": opp_id,
                    "workspace_id": workspace_id,
                    "run_id": target_run_id,
                    "rank": idx,
                    "title": doc["title"],
                    "summary": doc["summary"],
                    "angle": f"Editorial angle for {doc['title']}",
                    "funnel_stage": "mofu",
                    "confidence": "high",
                    "total": round(base_total - (idx - 1), 2),
                    "scores": scores,
                    "original_url": doc["canonical_url"],
                    "published_at": doc["published_at"],
                    "date_status": doc["date_status"],
                    "is_verified_date": doc["date_status"] == "verified",
                    "is_recommended": idx <= max(3, int(ws["articles_per_week"])),
                    "already_covered": False,
                    "status": "scored",
                    "decision_note": None,
                    "decided_by": None,
                    "decided_at": None,
                }
        self._runs[target_run_id]["status"] = "completed"
        items = [
            o
            for o in self._opportunities.values()
            if o["workspace_id"] == workspace_id and o["run_id"] == target_run_id
        ]
        items.sort(key=lambda o: int(o["rank"]))
        return {
            "run_id": target_run_id,
            "workspace_id": workspace_id,
            "campaign_id": ws["campaign_id"],
            "campaign_name": ws["campaign_name"],
            "articles_per_week": int(ws["articles_per_week"]),
            "recommended_count": sum(1 for i in items if i["is_recommended"]),
            "total_count": len(items),
            "items": items,
        }

    def list_topics_for_approval(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        run_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List seeded Gate 1 opportunities strictly in status 'scored'."""
        self.require_member(workspace_id=workspace_id, user_id=user_id)
        if run_id is not None:
            self._require_run(workspace_id=workspace_id, run_id=run_id)
        items = [
            o
            for o in self._opportunities.values()
            if o["workspace_id"] == workspace_id
            and o["status"] == "scored"
            and (run_id is None or o["run_id"] == run_id)
        ]
        items.sort(key=lambda o: float(o["total"]), reverse=True)
        return items[:limit]

    def decide_topic(  # noqa: PLR0913
        self,
        *,
        workspace_id: uuid.UUID,
        opportunity_id: uuid.UUID,
        decision: str,
        user_id: uuid.UUID,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Enforce Gate 1 creator authority and weekly quota on mock opportunities."""
        norm = decision.strip().lower()
        target_status = {
            "approve": "approved",
            "approved": "approved",
            "reject": "rejected",
            "rejected": "rejected",
        }.get(norm)
        if target_status is None:
            msg = f"Invalid decision: '{decision}', expected 'approved' or 'rejected'"
            raise ValueError(msg)

        opp = self._opportunities.get(opportunity_id)
        if opp is None or opp["workspace_id"] != workspace_id:
            msg = f"Opportunity {opportunity_id} not found"
            raise ValueError(msg)
        if opp["status"] != "scored":
            msg = f"Opportunity {opportunity_id} is in status '{opp['status']}', expected 'scored'"
            raise ValueError(msg)

        ws = self._require_creator_owner(workspace_id=workspace_id, user_id=user_id)
        if target_status == "approved":
            cap = int(ws["articles_per_week"])
            approved_count = sum(
                1
                for o in self._opportunities.values()
                if o["workspace_id"] == workspace_id
                and o["run_id"] == opp["run_id"]
                and o["status"] == "approved"
            )
            if approved_count >= cap:
                msg = f"Approval limit reached: campaign allows {cap} approved topic(s) per run"
                raise ValueError(msg)

        opp["status"] = target_status
        opp["decided_by"] = user_id
        opp["decided_at"] = datetime.now(UTC)
        opp["decision_note"] = note
        self._events.append(
            {
                "type": "topic.decided",
                "workspace_id": workspace_id,
                "entity_id": opportunity_id,
                "actor_user_id": user_id,
                "decision": target_status,
            }
        )
        return dict(opp)

    def get_or_create_brief(self, request: BriefRequest) -> dict[str, Any]:
        """Return or create a seeded brief anchored to verified mock evidence docs."""
        self.require_member(workspace_id=request.workspace_id, user_id=request.user_id)
        opp = self._opportunities.get(request.opportunity_id)
        if opp is None or opp["workspace_id"] != request.workspace_id:
            msg = f"Opportunity {request.opportunity_id} not found"
            raise ValueError(msg)

        matching = [
            b for b in self._briefs.values() if b["opportunity_id"] == request.opportunity_id
        ]
        existing = max(matching, key=lambda b: int(b["version"])) if matching else None
        created = False
        if existing is None or request.force_regenerate:
            self.preflight_spend(workspace_id=request.workspace_id, run_id=opp["run_id"])
            new_id = uuid.uuid4()
            existing = self._build_mock_brief(
                brief_id=new_id,
                opp=opp,
                version=(int(existing["version"]) + 1) if existing else 1,
                created_at=datetime.now(UTC),
            )
            self._briefs[new_id] = existing
            created = True

        doc = self._documents[DEMO_DOC_ID]
        cited = [
            {
                "id": doc["id"],
                "title": doc["title"],
                "url": doc["url"],
                "canonical_url": doc["canonical_url"],
                "published_at": doc["published_at"],
                "date_status": doc["date_status"],
            }
        ]
        return {
            **existing,
            "primary_keyword": existing["payload"]["primary_keyword"],
            "outline": existing["payload"]["outline"],
            "created": created,
            "cited_documents": cited,
        }

    def generate_draft(self, request: GenerateDraftRequest) -> dict[str, Any]:
        """Generate an FK-grounded mock draft after preflight budget checks."""
        self.require_member(workspace_id=request.workspace_id, user_id=request.user_id)
        brief = self._briefs.get(request.brief_id)
        if brief is None or brief["workspace_id"] != request.workspace_id:
            msg = f"Brief {request.brief_id} not found"
            raise ValueError(msg)

        self.preflight_spend(workspace_id=request.workspace_id, run_id=brief["run_id"])
        existing_revs = [
            int(d["revision"]) for d in self._drafts.values() if d["brief_id"] == request.brief_id
        ]
        next_rev = (max(existing_revs) + 1) if existing_revs else 0
        draft = self._build_mock_draft(
            draft_id=uuid.uuid4(),
            brief=brief,
            revision=next_rev,
            status="draft",
            created_at=datetime.now(UTC),
        )
        self._drafts[draft["id"]] = draft
        return dict(draft)

    @staticmethod
    def _nonempty_strs(values: Sequence[str] | None) -> list[str]:
        if not values:
            return []
        return [s for raw in values if (s := str(raw).strip())]

    def _require_draft(self, *, workspace_id: uuid.UUID, draft_id: uuid.UUID) -> dict[str, Any]:
        draft = self._drafts.get(draft_id)
        if draft is None or draft["workspace_id"] != workspace_id:
            msg = f"Draft {draft_id} not found"
            raise ValueError(msg)
        return draft

    def _resolve_mock_claim_inputs(
        self, request: VerifyClaimsRequest
    ) -> tuple[list[str], list[str], uuid.UUID | None]:
        claims = self._nonempty_strs(request.claims)
        passages = self._nonempty_strs(request.passages)
        target_run_id = request.run_id

        if request.draft_id is not None:
            draft = self._require_draft(
                workspace_id=request.workspace_id,
                draft_id=request.draft_id,
            )
            target_run_id = target_run_id or draft["run_id"]
            claims = claims or [str(c["text"]) for c in draft["claims"]]
            passages = passages or [str(self._documents[DEMO_DOC_ID]["extracted_text"])]

        if not claims:
            msg = "claims or draft_id with persisted claims is required"
            raise ValueError(msg)
        return claims, passages, target_run_id

    def verify_claims(self, request: VerifyClaimsRequest) -> dict[str, Any]:
        """Verify claims deterministically against mock draft documents or explicit passages."""
        self.require_member(workspace_id=request.workspace_id, user_id=request.user_id)
        claim_texts, passages, target_run_id = self._resolve_mock_claim_inputs(request)
        self.preflight_spend(workspace_id=request.workspace_id, run_id=target_run_id)
        results = claims_mod.verify_claims_against_sources(claim_texts, passages)
        counts = Counter(r.verdict for r in results)
        serialized = [
            {
                "claim": r.claim,
                "verdict": r.verdict,
                "confidence": r.confidence,
                "evidence_quote": r.evidence_quote,
                "cost_usd": r.cost_usd,
                "judge": r.judge,
            }
            for r in results
        ]
        return {
            "workspace_id": request.workspace_id,
            "draft_id": request.draft_id,
            "run_id": target_run_id,
            "verified_count": len(serialized),
            "supported_count": counts["supported"],
            "refuted_count": counts["refuted"],
            "unverifiable_count": counts["unverifiable"],
            "total_cost_usd": Decimal("0.000000"),
            "results": serialized,
        }

    def evaluate_draft(self, request: EvaluateDraftRequest) -> dict[str, Any]:
        """Evaluate mock draft deterministically and route PASS drafts to Gate 2."""
        self.require_member(workspace_id=request.workspace_id, user_id=request.user_id)
        draft = self._require_draft(
            workspace_id=request.workspace_id,
            draft_id=request.draft_id,
        )
        self.preflight_spend(workspace_id=request.workspace_id, run_id=draft["run_id"])
        if request.deterministic_only and isinstance(draft.get("payload"), dict):
            from engine import quality

            brief_id_val = draft.get("brief_id")
            brief_row = (
                self._briefs.get(brief_id_val) if isinstance(brief_id_val, uuid.UUID) else {}
            ) or {}
            report_obj = quality.evaluate(
                draft["payload"],
                brief=brief_row.get("payload") or {"format": "blog"},
                deterministic_only=True,
            )
            new_status = "pass" if report_obj.verdict == "PASS" else "revision"
            draft["status"] = new_status
            findings = [f.model_dump(mode="json") for f in report_obj.findings]
            det_report: dict[str, Any] = {
                "verdict": report_obj.verdict,
                "score": report_obj.score,
                "findings": findings,
            }
            draft["quality_report"] = det_report
            return {
                "draft_id": request.draft_id,
                "run_id": draft["run_id"],
                "status": new_status,
                "revision": draft["revision"],
                "verdict": report_obj.verdict,
                "score": report_obj.score,
                "findings": findings,
                "route": "pass" if report_obj.verdict == "PASS" else "write",
                "surfaced_to_owner": False,
                "topic_returned_to_pool": False,
                "quality_report": det_report,
            }

        draft["status"] = "pass"
        report = {"verdict": "PASS", "score": Decimal("86.5"), "findings": []}
        draft["quality_report"] = report
        return {
            "draft_id": request.draft_id,
            "run_id": draft["run_id"],
            "status": "pass",
            "revision": draft["revision"],
            "verdict": "PASS",
            "score": Decimal("86.5"),
            "findings": [],
            "route": "pass",
            "surfaced_to_owner": False,
            "topic_returned_to_pool": False,
            "quality_report": report,
        }

    def _read_mock_import_markdown(self, request: ImportDraftRequest | AdaptDraftRequest) -> str:
        from engine.production import writer

        if request.file_path is not None:
            return writer.extract_file_to_markdown(request.file_path)
        if request.body_md is not None:
            return request.body_md
        msg = "Either file_path or body_md is required."
        raise ValueError(msg)

    def _resolve_mock_import_brief(
        self, request: ImportDraftRequest, default_title: str
    ) -> tuple[uuid.UUID, uuid.UUID, dict[str, Any], int]:
        from engine.production import writer

        if request.brief_id is None:
            resolved_brief_id = uuid.uuid4()
            run_id = uuid.uuid4()
            brief_payload: dict[str, Any] = {
                "topic": request.topic or default_title,
                "primary_keyword": request.primary_keyword or default_title,
                "format": request.format_slug,
            }
            self._briefs[resolved_brief_id] = {
                "id": resolved_brief_id,
                "workspace_id": request.workspace_id,
                "run_id": run_id,
                "payload": brief_payload,
            }
            return resolved_brief_id, run_id, brief_payload, 0

        brief_row = self._briefs.get(request.brief_id)
        if brief_row is None or brief_row["workspace_id"] != request.workspace_id:
            msg = f"Brief {request.brief_id} not found"
            raise ValueError(msg)
        resolved_brief_id = request.brief_id
        run_id = brief_row["run_id"]
        brief_payload = dict(brief_row.get("payload") or {"format": request.format_slug})
        existing_revs = [
            int(d["revision"])
            for d in self._drafts.values()
            if d.get("brief_id") == resolved_brief_id
        ]
        next_rev = (max(existing_revs) + 1) if existing_revs else 0
        if next_rev > writer.MAX_DRAFT_REVISION:
            msg = f"Brief {resolved_brief_id} already reached max revision 2."
            raise writer.DraftRevisionLimitReachedError(msg)
        return resolved_brief_id, run_id, brief_payload, next_rev

    def import_draft(self, request: ImportDraftRequest) -> dict[str, Any]:
        """Import and evaluate an external .md/.pdf or Markdown draft in the mock store."""
        from engine import quality
        from engine.production import writer

        self.require_member(workspace_id=request.workspace_id, user_id=request.user_id)
        raw_md = self._read_mock_import_markdown(request)
        draft_model = writer.parse_markdown_draft(raw_md, default_title=request.topic)
        resolved_brief_id, run_id, brief_payload, next_rev = self._resolve_mock_import_brief(
            request, draft_model.title
        )

        draft_id = uuid.uuid4()
        report_obj = quality.evaluate(
            draft_model,
            brief=brief_payload,
            deterministic_only=request.deterministic_only,
        )
        new_status = "pass" if report_obj.verdict == "PASS" else "revision"
        findings = [f.model_dump(mode="json") for f in report_obj.findings]
        self._drafts[draft_id] = {
            "id": draft_id,
            "workspace_id": request.workspace_id,
            "brief_id": resolved_brief_id,
            "run_id": run_id,
            "revision": next_rev,
            "status": new_status,
            "payload": draft_model.model_dump(mode="json"),
            "claims": [c.model_dump(mode="json") for c in draft_model.claims],
            "quality_report": {
                "verdict": report_obj.verdict,
                "score": report_obj.score,
                "findings": findings,
            },
            "created_at": datetime.now(UTC),
        }
        return {
            "draft_id": draft_id,
            "brief_id": resolved_brief_id,
            "run_id": run_id,
            "revision": next_rev,
            "status": new_status,
            "verdict": report_obj.verdict,
            "score": report_obj.score,
            "findings": findings,
        }

    def adapt_draft(self, request: AdaptDraftRequest) -> dict[str, Any]:
        """Adapt an exported Markdown draft to the mock workspace brand/persona."""
        from engine import quality, services
        from engine.production import rewrite as rewrite_mod
        from engine.production import writer
        from engine.quality import checks as quality_checks

        self.require_member(workspace_id=request.workspace_id, user_id=request.user_id)
        ws = self._workspaces[request.workspace_id]
        raw_md = self._read_mock_import_markdown(request)
        source_draft = writer.parse_markdown_draft(raw_md)
        brand_row = self.current_brand(workspace_id=request.workspace_id, user_id=request.user_id)
        target_brand = (
            BrandProfile.model_validate(brand_row["payload"])
            if brand_row and brand_row.get("payload")
            else None
        )
        adapted = rewrite_mod.rewrite_draft(
            source_draft,
            mode="adapt_brand",
            primary_keyword=request.primary_keyword or source_draft.title,
            locale=request.locale or ws["locale"],
            target_brand=target_brand,
            llm_adapter=FakeLLM(),
        )
        uniq = quality_checks.check_adaptation_uniqueness(
            source_draft,
            adapted,
            max_similarity=request.max_similarity,
        )
        report_obj = quality.evaluate(
            adapted,
            brand_profile=target_brand,
            format_slug=request.format_slug,
            deterministic_only=request.deterministic_only,
            primary_keyword=request.primary_keyword or adapted.title,
            locale=request.locale or ws["locale"],
            llm_adapter=FakeLLM(),
        )
        draft_id = uuid.uuid4()
        new_status = "pass" if report_obj.verdict == "PASS" else "revision"
        findings = [f.model_dump(mode="json") for f in report_obj.findings]
        self._drafts[draft_id] = {
            "id": draft_id,
            "workspace_id": request.workspace_id,
            "brief_id": uuid.uuid4(),
            "run_id": uuid.uuid4(),
            "revision": 0,
            "status": new_status,
            "payload": adapted.model_dump(mode="json"),
            "claims": [c.model_dump(mode="json") for c in adapted.claims],
            "quality_report": {
                "verdict": report_obj.verdict,
                "score": report_obj.score,
                "findings": findings,
            },
            "created_at": datetime.now(UTC),
        }
        return {
            "draft_id": draft_id,
            "status": new_status,
            "title": adapted.title,
            "slug": adapted.slug,
            "verdict": report_obj.verdict,
            "score": report_obj.score,
            "findings": findings,
            "uniqueness": {
                "check": uniq.check,
                "passed": uniq.passed,
                "detail": uniq.detail,
                **uniq.metadata,
            },
            "adapted_markdown": services.render_draft_markdown(adapted),
        }

    def list_drafts_for_review(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        include_decided: bool = False,
    ) -> list[dict[str, Any]]:
        """List mock drafts in 'pass' (and optionally decided) status."""
        self.require_member(workspace_id=workspace_id, user_id=user_id)
        allowed = ("pass", "approved", "declined", "published") if include_decided else ("pass",)
        rows = [
            {**d, "surfaced_after_max_revisions": False}
            for d in self._drafts.values()
            if d["workspace_id"] == workspace_id and d["status"] in allowed
        ]
        rows.sort(key=lambda d: d["created_at"], reverse=True)
        return rows

    def decide_draft(self, request: DraftDecisionRequest) -> dict[str, Any]:
        """Enforce Gate 2 creator authority and normalized 'approved'/'declined' values."""
        norm = request.decision.strip().lower()
        target = {
            "approve": "approved",
            "approved": "approved",
            "decline": "declined",
            "declined": "declined",
        }.get(norm)
        if target is None:
            msg = f"Invalid decision: '{request.decision}', expected 'approved' or 'declined'"
            raise ValueError(msg)

        draft = self._require_draft(
            workspace_id=request.workspace_id,
            draft_id=request.draft_id,
        )
        self._require_creator_owner(workspace_id=request.workspace_id, user_id=request.user_id)
        if draft["status"] != "pass":
            msg = f"Draft {request.draft_id} is in status '{draft['status']}', expected 'pass'"
            raise ValueError(msg)

        draft["status"] = target
        draft["decided_by"] = request.user_id
        draft["decided_at"] = datetime.now(UTC)
        draft["decision_note"] = request.note
        self._events.append(
            {
                "type": "draft.decided",
                "workspace_id": request.workspace_id,
                "entity_id": request.draft_id,
                "actor_user_id": request.user_id,
                "decision": target,
            }
        )
        return {**draft, "publish_queued": target == "approved", "publication": None}

    def publish_draft(self, request: PublishDraftRequest) -> dict[str, Any]:
        """Publish an approved mock draft forcing mode='draft' in staging."""
        ws = self._workspaces[request.workspace_id]
        self._require_creator_owner(workspace_id=request.workspace_id, user_id=request.user_id)
        draft = self._require_draft(
            workspace_id=request.workspace_id,
            draft_id=request.draft_id,
        )
        if draft["status"] not in ("approved", "published"):
            msg = f"Draft {request.draft_id} is in status '{draft['status']}', expected 'approved'"
            raise ValueError(msg)
        if "publisher" not in ws["configured_ports"]:
            raise IntegrationMissing("publisher")

        eff_mode = "draft" if is_staging_environment(request.env) else (request.mode or "draft")
        destination = request.destination or "fake"
        payload = draft["payload"]
        pub_req = PublishRequest(
            draft_id=request.draft_id,
            draft=ContentDraft(
                schema_version=1,
                title=payload["title"],
                meta_description=payload["meta_description"],
                slug=payload["slug"],
                body_md=payload["body_md"],
                cta=payload["cta"],
                claims=[DraftClaim(**c) for c in payload["claims"]],
            ),
            locale=ws["locale"],
            destination=destination,
            mode=eff_mode,
        )
        res = self.publisher.publish(pub_req)
        draft["status"] = "published"
        return {
            "id": uuid.uuid4(),
            "workspace_id": request.workspace_id,
            "draft_id": request.draft_id,
            "destination": destination,
            "mode": eff_mode,
            "status": res.status,
            "title": payload["title"],
            "external_id": res.external_id,
            "url": res.url,
            "published_at": res.published_at,
            "response": res.raw,
        }

    def current_brand(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Return seeded BrandProfile for a workspace after checking membership."""
        self.require_member(workspace_id=workspace_id, user_id=user_id)
        return self._brands.get(workspace_id)

    def get_weekly_report(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Return seeded weekly scored report and Gate 1/2 counts for a workspace."""
        scored = self.score_topics(workspace_id=workspace_id, user_id=user_id)
        awaiting_topics = self.list_topics_for_approval(workspace_id=workspace_id, user_id=user_id)
        awaiting_drafts = self.list_drafts_for_review(
            workspace_id=workspace_id, user_id=user_id, include_decided=False
        )
        return {
            **scored,
            "awaiting_topics": awaiting_topics,
            "awaiting_drafts_count": len(awaiting_drafts),
        }

    def get_draft_workspace_id(self, *, draft_id: uuid.UUID) -> uuid.UUID | None:
        """Return owning workspace UUID for a seeded draft ID."""
        draft = self._drafts.get(draft_id)
        return uuid.UUID(str(draft["workspace_id"])) if draft is not None else None

    def get_draft_review_bundle(
        self,
        *,
        workspace_id: uuid.UUID,
        draft_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Return Gate 2 review bundle for a seeded draft."""
        self.require_member(workspace_id=workspace_id, user_id=user_id)
        draft = self._require_draft(workspace_id=workspace_id, draft_id=draft_id)
        ws = self._workspaces[workspace_id]
        return {
            "draft": draft,
            "claims": draft["claims"],
            "quality_report": draft["quality_report"],
            "surfaced_after_max_revisions": False,
            "has_publisher": "publisher" in ws["configured_ports"],
        }

    def audit_url(
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        url: str,
        check_external: bool = False,
    ) -> dict[str, Any]:
        """Run deterministic URL audit against mock/patched safe_http after membership check."""
        self.require_member(workspace_id=workspace_id, user_id=user_id)
        report = _run_url_audit_core(url, check_external=check_external)
        return {**report.model_dump(mode="json"), "audit_id": uuid.uuid4()}

    def crawl_site(  # noqa: PLR0913
        self,
        *,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        start_url: str,
        max_pages: int | None = None,
        plan: Literal["free", "paid"] = "free",
    ) -> dict[str, Any]:
        """Run bounded site crawl against mock/patched safe_http after membership check."""
        self.require_member(workspace_id=workspace_id, user_id=user_id)
        cfg = CrawlConfig(plan=plan, max_pages=max_pages)
        report = _run_site_crawl_core(start_url, config=cfg)
        return {**report.model_dump(mode="json"), "site_audit_id": uuid.uuid4()}

    def get_enabled_capabilities(self, *, workspace_id: uuid.UUID) -> list[str]:
        """Return active capability slugs for a mock workspace."""
        raw = self._enabled_capabilities.get(workspace_id)
        return capabilities.normalize_enabled_capabilities(raw)

    def set_enabled_capabilities(
        self,
        *,
        workspace_id: uuid.UUID,
        enabled_capabilities: Sequence[str],
    ) -> list[str]:
        """Persist capability switchboard settings in the mock store."""
        normalized = capabilities.normalize_enabled_capabilities(enabled_capabilities)
        self._enabled_capabilities[workspace_id] = normalized
        return normalized
